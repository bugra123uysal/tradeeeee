"""GENİŞ ve SEÇİMSİZ backtest verisi (4 Kas 2026) — Alpaca SIP REST, sadece requests + pandas (sistem python).

Eski veriden (backtest/veri.py) farkları:
  * Aday günü kuralı: gün içi tepe ≥ önceki kapanış +%5 VEYA açılış ≥ +%5. 'yüksek' sinyali gün ≥ %5
    istediği için piyasa saatindeki her olası sinyal veride olur (eski kural +%10 tepe istiyordu → yanlılık).
    Sadece premarket'te +%5 olup açılışa kadar sönenler eksik kalabilir (günlük mumda premarket yok).
  * Evren: AKTİF + İŞLEMDEN KALKMIŞ hisseler (hayatta kalanlar yanlılığı olmasın).
  * Uzun dönem (varsayılan 1 yıl), kaldığı yerden devam eder.

Çıktı: backtest/data_genis/{evren.pkl, gunluk.pkl, dk/YYYY-MM-DD.pkl}  (eski backtest/data'ya dokunmaz)

    py -m backtest.veri_genis --start 2025-11-03 --end 2026-10-30
"""
from __future__ import annotations

import argparse
import logging
import os
import pickle
import re
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

log = logging.getLogger("veri_genis")
NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).parent / "data_genis"
ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}
FUND_NAME = re.compile(r"\b(ETFs?|ETNs?|Fund|Trust)\b|ProShares|iShares|Direxion", re.I)
DAILY_CHUNK = 200
MINUTE_CHUNK = 50
MIN_GAP = 0.05
MIN_PREV_CLOSE = 0.20
ADV_LOOKBACK_DAYS = 45
DAKIKA_ISTEK_MAX = 190          # Alpaca ücretsiz: 200 istek / dk


class _Istemci:
    def __init__(self) -> None:
        load_dotenv(ROOT / ".env")
        self.h = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
                  "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
        self.zamanlar: list[float] = []

    def get(self, url: str, params: dict) -> dict | list:
        for deneme in range(6):
            simdi = _time.time()
            self.zamanlar = [t for t in self.zamanlar if simdi - t < 60]
            if len(self.zamanlar) >= DAKIKA_ISTEK_MAX:
                _time.sleep(60 - (simdi - self.zamanlar[0]) + 0.1)
            self.zamanlar.append(_time.time())
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

    def bars(self, symbols: list[str], timeframe: str, start: datetime, end: datetime) -> list[dict]:
        out, token = [], None
        while True:
            p = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start.isoformat(),
                 "end": end.isoformat(), "adjustment": "split", "feed": "sip", "limit": 10000}
            if token:
                p["page_token"] = token
            d = self.get(BARS_URL, p)
            for sym, bs in (d.get("bars") or {}).items():
                out.extend({"symbol": sym, **b} for b in bs)
            token = d.get("next_page_token")
            if not token:
                return out


def evren(ist: _Istemci) -> dict[str, str]:
    out = {}
    for durum in ("active", "inactive"):
        for a in ist.get(ASSETS_URL, {"status": durum, "asset_class": "us_equity"}):
            if (a.get("exchange") in EXCHANGES and a["symbol"].isalpha() and len(a["symbol"]) <= 5
                    and not FUND_NAME.search(a.get("name") or "")):
                out.setdefault(a["symbol"], a["exchange"])
    return out


def _df(rows: list[dict]) -> pd.DataFrame:
    d = pd.DataFrame(rows).rename(columns={"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close",
                                           "v": "volume", "vw": "vwap"})
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True).dt.tz_convert(NY)
    return d


def gunluk(ist: _Istemci, semboller: list[str], bas: date, son: date) -> pd.DataFrame:
    rows = []
    for i in range(0, len(semboller), DAILY_CHUNK):
        rows += ist.bars(semboller[i:i + DAILY_CHUNK], "1Day", datetime.combine(bas, time(), NY),
                         datetime.combine(son, time(23), NY))
        log.info("günlük %d/%d", min(i + DAILY_CHUNK, len(semboller)), len(semboller))
    d = _df(rows)
    d["day"] = d["timestamp"].dt.date
    d = d.sort_values(["symbol", "day"])
    g = d.groupby("symbol")
    d["prev_close"] = g["close"].shift(1)
    d["adv20"] = g["volume"].transform(lambda v: v.shift(1).rolling(20, min_periods=10).mean())
    return d[["symbol", "day", "open", "high", "low", "close", "volume", "prev_close", "adv20"]]


def adaylar(d: pd.DataFrame, bas: date) -> pd.DataFrame:
    m = d[(d["day"] >= bas) & (d["prev_close"] >= MIN_PREV_CLOSE)]
    return m[(m["open"] / m["prev_close"] - 1 >= MIN_GAP) | (m["high"] / m["prev_close"] - 1 >= MIN_GAP)]


def dakika(ist: _Istemci, gun: date, semboller: list[str]) -> pd.DataFrame:
    rows = []
    for i in range(0, len(semboller), MINUTE_CHUNK):
        rows += ist.bars(semboller[i:i + MINUTE_CHUNK], "1Min", datetime.combine(gun, time(4), NY),
                         datetime.combine(gun, time(16), NY))
    if not rows:
        return pd.DataFrame()
    d = _df(rows)[["symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap"]]
    d["symbol"] = d["symbol"].astype("category")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(Path(__file__).parent / "veri_genis.log",
                                                                               encoding="utf-8")])
    (OUT / "dk").mkdir(parents=True, exist_ok=True)
    ist = _Istemci()
    yol_g = OUT / "gunluk.pkl"
    d = pd.read_pickle(yol_g) if yol_g.exists() else None
    if d is not None and d["day"].max() < a.end:
        log.info("günlük veri %s'e kadar — %s'e uzatılıyor", d["day"].max(), a.end)
        d = None                                     # dakika dosyaları korunur, sadece günlük yeniden alınır
    if d is None:
        uni = evren(ist)
        log.info("evren (aktif + kalkmış): %d hisse", len(uni))
        pickle.dump(uni, open(OUT / "evren.pkl", "wb"))
        d = gunluk(ist, sorted(uni), a.start - timedelta(days=ADV_LOOKBACK_DAYS), a.end)
        d.to_pickle(yol_g)
    ad = adaylar(d, a.start)
    ad = ad[ad["day"] <= a.end]
    log.info("aday hisse-gün: %d (%d gün)", len(ad), ad["day"].nunique())
    for gun, grp in ad.groupby("day"):
        yol = OUT / "dk" / f"{gun}.pkl"
        if yol.exists():
            continue
        bars = dakika(ist, gun, sorted(grp["symbol"]))
        tmp = yol.with_suffix(".tmp")
        bars.to_pickle(tmp, compression=None)
        tmp.replace(yol)
        log.info("%s: %d hisse, %d mum", gun, grp["symbol"].nunique(), len(bars))
    log.info("bitti")


if __name__ == "__main__":
    main()
