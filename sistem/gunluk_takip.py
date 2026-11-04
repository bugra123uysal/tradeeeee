"""Çoklu Hisse v1 (GÜNLÜK) + devam onayı — web sitesindeki ikinci sayfa (/gunluk).

Kaynak: Desktop/bugra_ralli_avcisi_v6.pine (TradingView adı "Buğra Çoklu Hisse v1", 1D grafik).
  AL   : kapanış BB(20, 2) üst bandını yukarı keser VEYA ADX(14) > 25 ve +DI > −DI yeni başlar
         — sadece pozisyon yokken (pyramiding 0), Pine'daki etiketlerle aynı
  SAT  : kapanış hem VWMA(20) hem EMA(50) altında
  STOP : giriş − 1.5 × ATR(14), kapanış bazlı; süre sınırı yok
ONAY (kullanıcı isteği, 2 Kas 2026): 5 dk sayfasındaki kural günlük mumlarla — AL'dan 2–4 gün
sonra kapanış AL kapanışının en az +%X üstünde, altına hiç inmemiş, SAT yok, mum yeşil.

Evren: v3-dengeli_strateji/config.json (Çoklu v1 sekmesinin hisseleri, ~550).
Veri : Alpaca SIP günlük mumlar (bölünme düzeltmeli). Seans içinde bugünün mumu 15 dk gecikmeli ve
       henüz kapanmamıştır → o mumdaki AL/ONAY "gün içi (kesinleşmedi)" diye işaretlenir.

    python -m sistem.gunluk_takip            # seans boyunca 5 dk'da bir, kapanıştan sonra son tarama
    python -m sistem.gunluk_takip --tek      # tek tarama
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time as _time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

from . import sinyal as S
from .devam_takip import alpaca_mumlar
from .liste_yenile import FILTER_DIR, NY, todays_close

log = logging.getLogger("gunluk")
CIKTI = FILTER_DIR / "gunluk_sinyaller.json"
EVREN_CONFIG = Path(__file__).resolve().parents[2] / "v3-dengeli_strateji" / "config.json"
STOP_ATR = 1.5
GECMIS_GUN = 730          # pozisyon durumunun Pine ile örtüşmesi için ~2 yıl
ONAY_GOSTER_GUN = 10      # onaydan sonra kaç gün kartta kalsın (pozisyon açıkken)
TARAMA_SN = 300
ILK_GUN = "2026-11-02"    # sayfa bu gün kuruldu; daha önceki onayların eklenme saati yok


# ── evren ──
def _sembol(s: str) -> str:
    """'NASDAQ:BRK-B' → 'BRK.B' (Alpaca biçimi)."""
    return s.strip().upper().split(":")[-1].replace("-", ".")


def evren(config: Path = EVREN_CONFIG) -> dict[str, str]:
    """sembol → grup. v3 config'indeki listeler + sembol dosyaları (scanner/scan.py ile aynı sıra)."""
    cfg = json.loads(config.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for key, grup in (("symbols", "Test"), ("coklu_symbols", "Çoklu")):
        for s in cfg.get(key, []):
            if s.strip():
                out.setdefault(_sembol(s), grup)
    for e in cfg.get("symbol_files", []):
        yol = (config.parent / e["path"]).resolve()
        if not yol.exists():
            log.warning("evren dosyası yok: %s", yol)
            continue
        for satir in yol.read_text(encoding="utf-8-sig").splitlines():
            if satir.strip().startswith("#"):
                continue
            for s in satir.split(","):
                if s.strip():
                    out.setdefault(_sembol(s), e.get("group", yol.stem))
    return out


# ── strateji (Pine birebir) ──
@dataclass(frozen=True)
class Islem:
    al_i: int
    tur: str                      # BB / ADX
    stop: float
    cikis_i: int | None = None
    cikis: str | None = None      # SAT / STOP


def strateji(c: np.ndarray, g: S.Gostergeler) -> list[Islem]:
    """Pozisyon takibiyle AL/SAT/STOP. Giriş mumunda çıkış kontrolü yok (process_orders_on_close)."""
    al = dict(S.al_sinyalleri(c, g))
    out: list[Islem] = []
    acik: Islem | None = None
    for i in range(len(c)):
        if acik is None:
            if i in al:
                acik = Islem(i, al[i], float(c[i] - STOP_ATR * g.atr[i]))
            continue
        neden = ("SAT" if c[i] < g.vw[i] and c[i] < g.e50[i]
                 else "STOP" if c[i] < acik.stop else None)
        if neden:
            out.append(Islem(acik.al_i, acik.tur, acik.stop, i, neden))
            acik = None
    if acik is not None:
        out.append(acik)
    return out


# ── hisse değerlendirme ──
def _tarih(bar: dict) -> str:
    return bar["t"][:10]


def hisse_sinyalleri(sym: str, grup: str, bars: list[dict], min_artis: float,
                     gecici_tarih: str | None) -> list[dict]:
    """Son günlerdeki AL'lar için onay durumu. gecici_tarih: henüz kapanmamış mumun tarihi."""
    if len(bars) < S.EMA_LEN + 2:
        return []
    o, h, l, c, v = (np.array([b[k] for b in bars], float) for k in "ohlcv")
    tarihler = [_tarih(b) for b in bars]
    g = S.gostergeler(o, h, l, c, v)
    son = len(c) - 1
    gecici = gecici_tarih is not None and tarihler[son] == gecici_tarih
    out = []
    for isl in strateji(c, g):
        if son - isl.al_i > S.ONAY_MAX + ONAY_GOSTER_GUN:
            continue
        durum, j, neden = S.onay_karari(o, c, g, isl.al_i, min_artis)
        if durum == "iptal" or (durum == "onay" and isl.cikis_i is not None):
            continue                                   # kapanan / tutmayan sinyalleri gösterme
        if durum == "bekliyor" and son - isl.al_i > S.ONAY_MAX:
            continue
        if j is not None and (c[j + 1:] < c[j]).any():
            continue                                   # onaydan sonra giriş altında kapandı → zararda, gösterme
        bas = j if j is not None else isl.al_i
        out.append({
            "sym": sym, "grup": grup, "tur": isl.tur, "durum": durum, "neden": neden,
            "al_tarih": tarihler[isl.al_i], "al_fiyat": float(c[isl.al_i]),
            "onay_tarih": tarihler[j] if j is not None else None,
            "onay_fiyat": float(c[j]) if j is not None else None,
            "son_tarih": tarihler[son], "son_fiyat": float(c[son]),
            "tepe": float(h[bas:].max()), "stop": round(isl.stop, 4),
            "gun": son - isl.al_i, "onaydan_gun": son - j if j is not None else None,
            "gecici": gecici and son in (isl.al_i, j),
            "macd_ok": bool(g.macd[son] > g.macd_sig[son]),
            "adx": round(float(g.adx[son]), 1),
            "ema50_ustu": bool(c[son] > g.e50[son]),
            "gun_degisim": round((c[son] / c[son - 1] - 1) * 100, 2),
        })
    return out


def tara(semboller: dict[str, str], min_artis: float, simdi: datetime) -> list[dict]:
    bas = (simdi - timedelta(days=GECMIS_GUN)).replace(hour=0, minute=0, second=0, microsecond=0)
    veriler = alpaca_mumlar(list(semboller), bas, zaman="1Day")
    kapanis = todays_close(simdi.date())
    acik_gun = kapanis is not None and simdi.time() < kapanis
    gecici_tarih = simdi.date().isoformat() if acik_gun else None
    out = []
    for sym, bars in veriler.items():
        try:
            out.extend(hisse_sinyalleri(sym, semboller.get(sym, ""), bars, min_artis, gecici_tarih))
        except (KeyError, ValueError) as e:
            log.warning("%s: %s", sym, e)
    log.info("tarandı: %d / %d hisse verili, %d sinyal (%d onay)", len(veriler), len(semboller), len(out),
             sum(s["durum"] == "onay" for s in out))
    out.sort(key=lambda s: s["onay_tarih"] or s["al_tarih"], reverse=True)
    return sorted(out, key=lambda s: s["durum"] != "onay")


def eklenme_isle(sinyaller: list[dict], onceki: dict[str, float | None], simdi: float,
                 ilk_gun: str) -> dict[str, float | None]:
    """Her onaya siteye ilk girdiği anı (epoch sn) yazar. Sistem kurulmadan önceki onaylar: None."""
    yeni = dict(onceki)
    for s in sinyaller:
        if s["durum"] != "onay":
            continue
        anahtar = f"{s['sym']}|{s['al_tarih']}"
        if anahtar not in yeni:
            yeni[anahtar] = simdi if s["onay_tarih"] >= ilk_gun else None
        s["eklendi"] = yeni[anahtar]
    return yeni


def _onceki_eklenme() -> dict[str, float | None]:
    try:
        return json.loads(CIKTI.read_text(encoding="utf-8")).get("eklenme", {})
    except (OSError, ValueError):
        return {}


def kaydet(sinyaller: list[dict], min_artis: float, evren_sayi: int) -> None:
    eklenme = eklenme_isle(sinyaller, _onceki_eklenme(), _time.time(), ILK_GUN)
    veri = {"guncel": datetime.now().astimezone().isoformat(timespec="seconds"), "min_artis": min_artis,
            "evren": evren_sayi, "onay_min": S.ONAY_MIN, "onay_max": S.ONAY_MAX, "sinyaller": sinyaller,
            "eklenme": eklenme}
    tmp = CIKTI.with_suffix(".tmp")
    tmp.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CIKTI)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-artis", type=float, default=float(os.getenv("GUNLUK_MIN_ARTIS", "2.0")))
    ap.add_argument("--aralik", type=int, default=TARAMA_SN, help="tarama aralığı (sn)")
    ap.add_argument("--tek", action="store_true", help="tek tarama yapıp çık")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(FILTER_DIR / "gunluk_takip.log", encoding="utf-8")])
    load_dotenv(FILTER_DIR.parent / ".env")
    semboller = evren()
    log.info("günlük takip: %d hisse · onay %d–%d gün, +%%%.1f", len(semboller), S.ONAY_MIN, S.ONAY_MAX,
             a.min_artis)
    kapanis = todays_close(datetime.now(NY).date())
    while True:
        simdi = datetime.now(NY)
        try:
            kaydet(tara(semboller, a.min_artis, simdi), a.min_artis, len(semboller))
        except Exception as e:  # noqa: BLE001 — ağ hatası döngüyü durdurmasın
            log.warning("tarama hatası: %s", e)
        if a.tek or kapanis is None or simdi.time() >= kapanis:
            return
        _time.sleep(a.aralik)


if __name__ == "__main__":
    main()
