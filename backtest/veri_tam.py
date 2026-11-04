"""TAM backtest verisi (4 Kas 2026): 04:00–20:00 NY, premarket ve seans sonrası hareketleri DAHİL — yanlılıksız.

backtest/data_genis'in iki eksiğini kapatır:
  1. Aday kuralı sadece günlük (normal seans) mumuna bakıyordu → premarket'te +%5 olup açılışta sönen günler
     yoktu (premarket sinyallerinde kaybedenler düşüyordu). Burada aday = önceki kapanıştan +%5 ve üstü;
     bakılan değerler: açılış, seans tepesi, premarket tepesi ve seans sonrası tepesi (Alpaca 1 saatlik
     uzatılmış seans mumlarından).
  2. Dakika mumları 16:00'da bitiyordu → seans sonrası (16:00–20:00) da alınır.
data_genis'te zaten olan 04:00–16:00 mumları yeniden indirilmez, sadece eksikler eklenir. Kaldığı yerden devam eder.

Çıktı: backtest/data_tam/{evren.pkl, gunluk.pkl, saatlik.pkl, dk/YYYY-MM-DD.pkl}

    py -m backtest.veri_tam --start 2025-11-03 --end 2026-11-03
"""
from __future__ import annotations

import argparse
import logging
import pickle
import shutil
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from backtest import veri_genis as VG

log = logging.getLogger("veri_tam")
KOK = Path(__file__).parent
ESKI = KOK / "data_genis"
OUT = KOK / "data_tam"
SAAT_CHUNK = 100
AH_CHUNK = 200          # seans sonrası seyrek → istek başına daha çok sembol
ESIK = VG.MIN_GAP
FIYAT_MAX = 40.0        # önceki kapanış üst sınırı (küçük/orta hisseler; sadece önceden bilinen bilgi → yanlılık yok)
PARALEL = 8             # eşzamanlı istek (dakikada 190 sınırı yine korunur)


class _Paralel(VG._Istemci):
    """veri_genis istemcisi + iş parçacığı güvenli dakika sınırı (istekler tek tek gidince saatler sürüyordu)."""

    def __init__(self) -> None:
        super().__init__()
        self.kilit = threading.Lock()

    def _sira(self) -> None:
        with self.kilit:
            simdi = _time.time()
            self.zamanlar = [t for t in self.zamanlar if simdi - t < 60]
            if len(self.zamanlar) >= VG.DAKIKA_ISTEK_MAX:
                _time.sleep(60 - (simdi - self.zamanlar[0]) + 0.1)
            self.zamanlar.append(_time.time())

    def get(self, url: str, params: dict) -> dict | list:
        for deneme in range(6):
            self._sira()
            try:
                r = requests.get(url, params=params, headers=self.h, timeout=60)
            except requests.RequestException as e:
                log.warning("ağ hatası (%s), tekrar %d", e, deneme + 1)
                _time.sleep(5 * (deneme + 1))
                continue
            if r.status_code == 429:
                _time.sleep(15)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"{url} 6 denemede alınamadı")

    def coklu(self, gruplar: list[list[str]], timeframe: str, bas: datetime, son: datetime) -> list[dict]:
        with ThreadPoolExecutor(PARALEL) as ex:
            return [r for parca in ex.map(lambda g: self.bars(g, timeframe, bas, son), gruplar) for r in parca]


def saatlik(ist: _Paralel, gun: pd.DataFrame, bas: date, son: date) -> pd.DataFrame:
    """Hisse-gün başına premarket (04–09:30) ve seans sonrası (16–20) en yüksek fiyat. Ay ay, kaldığı yerden."""
    parca_dir = OUT / "saatlik_parca"
    parca_dir.mkdir(parents=True, exist_ok=True)
    ay = date(bas.year, bas.month, 1)
    while ay <= son:
        sonraki = date(ay.year + (ay.month == 12), ay.month % 12 + 1, 1)
        dosya = parca_dir / f"{ay:%Y-%m}.pkl"
        if not dosya.exists():
            ayki = gun[(gun["day"] >= ay) & (gun["day"] < sonraki)]
            semboller = sorted(ayki.loc[(ayki["prev_close"] >= VG.MIN_PREV_CLOSE) & (ayki["prev_close"] <= FIYAT_MAX),
                                        "symbol"].unique())
            gruplar = [semboller[i:i + SAAT_CHUNK] for i in range(0, len(semboller), SAAT_CHUNK)]
            rows = ist.coklu(gruplar, "1Hour", datetime.combine(ay, time(4), VG.NY),
                             datetime.combine(min(sonraki - timedelta(days=1), son), time(20), VG.NY))
            d = VG._df(rows) if rows else pd.DataFrame(columns=["symbol", "timestamp", "high"])
            if len(d):
                d["day"] = d["timestamp"].dt.date
                saat = d["timestamp"].dt.hour + d["timestamp"].dt.minute / 60
                d["pm"] = d["high"].where(saat < 9.5)
                d["ah"] = d["high"].where(saat >= 16)
                d = d.groupby(["symbol", "day"], observed=True)[["pm", "ah"]].max().reset_index()
            d.to_pickle(dosya)
            log.info("saatlik %s: %d hisse-gün", f"{ay:%Y-%m}", len(d))
        ay = sonraki
    return pd.concat([pd.read_pickle(f) for f in sorted(parca_dir.glob("*.pkl"))], ignore_index=True)


def adaylar(gun: pd.DataFrame, saat: pd.DataFrame, bas: date) -> pd.DataFrame:
    m = gun[(gun["day"] >= bas) & gun["prev_close"].between(VG.MIN_PREV_CLOSE, FIYAT_MAX)].merge(saat, on=["symbol", "day"], how="left")
    tepe = m[["open", "high", "pm", "ah"]].max(axis=1)
    return m[tepe / m["prev_close"] - 1 >= ESIK]


def dakika(ist: _Paralel, gun: date, semboller: list[str], bas_saat: time, chunk: int) -> pd.DataFrame:
    gruplar = [semboller[i:i + chunk] for i in range(0, len(semboller), chunk)]
    rows = ist.coklu(gruplar, "1Min", datetime.combine(gun, bas_saat, VG.NY), datetime.combine(gun, time(20), VG.NY))
    if not rows:
        return pd.DataFrame()
    return VG._df(rows)[["symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap"]]


def gun_isle(ist: _Paralel, gun: date, semboller: list[str]) -> None:
    hedef = OUT / "dk" / f"{gun}.pkl"
    if hedef.exists():
        return
    eski_yol = ESKI / "dk" / f"{gun}.pkl"
    eski = pd.read_pickle(eski_yol) if eski_yol.exists() else pd.DataFrame()
    var = set(eski["symbol"].astype(str).unique()) if len(eski) else set()
    yeni = sorted(set(semboller) - var)
    eski_ist = sorted(set(semboller) & var)
    parcalar = ([eski[eski["symbol"].astype(str).isin(set(semboller))].assign(symbol=lambda x: x["symbol"].astype(str))]
                if len(eski) else [])                     # data_genis'te olmayan gün (3 Haz sonrası) → hepsi yeni
    parcalar.append(dakika(ist, gun, yeni, time(4), VG.MINUTE_CHUNK))
    parcalar.append(dakika(ist, gun, eski_ist, time(16), AH_CHUNK))
    d = pd.concat([p for p in parcalar if len(p)], ignore_index=True)
    if len(d):
        d = d.drop_duplicates(["symbol", "timestamp"]).sort_values(["symbol", "timestamp"])
        d["symbol"] = d["symbol"].astype("category")
    hedef.parent.mkdir(parents=True, exist_ok=True)
    d.to_pickle(hedef)
    log.info("%s: %d hisse (%d yeni), %d mum", gun, len(semboller), len(yeni), len(d))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    a = ap.parse_args()
    load_dotenv(KOK.parent / ".env")
    OUT.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(KOK / "veri_tam.log", encoding="utf-8"), logging.StreamHandler()])
    ist = _Paralel()
    if not (OUT / "evren.pkl").exists():
        shutil.copy(ESKI / "evren.pkl", OUT / "evren.pkl")
    with open(OUT / "evren.pkl", "rb") as f:
        semboller = sorted(pickle.load(f))
    if not (OUT / "gunluk.pkl").exists():
        VG.gunluk(ist, semboller, a.start - timedelta(days=45), a.end).to_pickle(OUT / "gunluk.pkl")
    gun = pd.read_pickle(OUT / "gunluk.pkl")
    saat = saatlik(ist, gun, a.start, a.end)
    saat.to_pickle(OUT / "saatlik.pkl")
    ad = adaylar(gun, saat, a.start)
    log.info("aday hisse-gün: %d (%d gün)", len(ad), ad["day"].nunique())
    for g, x in sorted(ad.groupby("day")):
        if g <= a.end:
            gun_isle(ist, g, sorted(x["symbol"].unique()))
    log.info("bitti")


if __name__ == "__main__":
    main()
