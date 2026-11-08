"""Gece (seans sonrası) yükselişleri — NY kapanışından 20:00'ye kadar (TR kış saati 00:00–04:00) tarar (6 Kas 2026).

Neden: CIIT 5 Kas 17:00 NY'de bilanço açıkladı, 15 dakikada 1,55 → 2,58 $ (+%60). Takipçi 16:00'da kapandığı için
görmedi; sabah 04:00 premarket'te listeye girdiğinde hareket bitmişti (giriş −%7,4 ile çıktı).

Tarayıcı (TradingView, sayfa oturumuyla): seans sonrası değişim > MIN_DEGISIM · seans sonrası hacim > MIN_HACIM ·
float ≤ MAX_FLOAT · kapanış MIN_FIYAT–MAX_FIYAT $. Her yeni hisse için haber başlığı (16:00'dan beri) alınır ve Telegram'a
"🌙 GECE YÜKSELİŞİ" gider; aynı hisse değişimi BASAMAK eşiklerini (+%30, +%60, +%100) geçtikçe bir kez daha bildirilir.
Bilgi amaçlıdır — gece girişinin kârlı olduğu test EDİLMEDİ (düşük hacim, geniş spread, sabah sönme riski).

    python -m sistem.gece          # kapanıştan 20:00 NY'ye kadar 30 sn'de bir, sonra kapanır
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import threading
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path

from dotenv import load_dotenv

from . import tv_cdp
from . import yukselme as YU
from .bildirim import Bildirici, _f, _isaretli, _kisa
from .liste_yenile import todays_close
from .telegram import Telegram

log = logging.getLogger("gece")
ROOT = Path(__file__).resolve().parent.parent
FILTER_DIR = ROOT / "filtreler"
NY = YU.NY

GECE_BITIS = time(20, 0)          # seans sonrası işlem 20:00 NY'de biter
MIN_DEGISIM = 10.0                # %
MIN_HACIM = 50_000                # seans sonrası hacim (CIIT 17:05 NY'de ~20 bin, 17:10'da ~100 bin)
MAX_FLOAT = 20_000_000
MIN_FIYAT, MAX_FIYAT = 1.0, 20.0     # 6 Kas: 1 $ altı çok oynak (kullanıcı)
BASAMAK = (30.0, 60.0, 100.0)     # bu değişimleri geçince "güçleniyor" bildirimi
LISTE_ADI = "bugraaa_gece"        # TradingView izleme listesi (her gece o gecenin hisseleriyle değişir)
KOLONLAR = ["name", "description", "close", "postmarket_close", "postmarket_change", "postmarket_volume",
            "postmarket_high", "float_shares_outstanding_current"]
SORGU = {
    "markets": ["america"],
    "filter": [{"left": "postmarket_change", "operation": "greater", "right": MIN_DEGISIM},
               {"left": "postmarket_volume", "operation": "greater", "right": MIN_HACIM},
               {"left": "float_shares_outstanding_current", "operation": "eless", "right": MAX_FLOAT},
               {"left": "close", "operation": "in_range", "right": [MIN_FIYAT, MAX_FIYAT]}],
    "sort": {"sortBy": "postmarket_change", "sortOrder": "desc"},
    "range": [0, 50],
}


@dataclass
class GeceHisse:
    sym: str
    ad: str = ""
    kapanis: float | None = None         # 16:00 kapanışı
    fiyat: float | None = None           # son seans sonrası fiyatı
    degisim: float | None = None         # %
    tepe: float | None = None
    hacim: float | None = None
    float_m: float | None = None
    ilk_gorulme: float = 0.0             # epoch sn
    haberler: list[dict] = field(default_factory=list)


def satirdan(r: dict, simdi: float) -> GeceHisse:
    flt = r.get("float_shares_outstanding_current")
    return GeceHisse(r["s"], ad=r.get("description") or r.get("name") or "", kapanis=r.get("close"),
                     fiyat=r.get("postmarket_close"), degisim=r.get("postmarket_change"),
                     tepe=r.get("postmarket_high"), hacim=r.get("postmarket_volume"),
                     float_m=flt / 1e6 if flt else None, ilk_gorulme=simdi)


def gece_mi(simdi_ny: datetime, kapanis: time | None) -> bool:
    """Bugün işlem günüyse kapanış (normalde 16:00, yarım günde 13:00) ile 20:00 NY arası."""
    return kapanis is not None and kapanis <= simdi_ny.time() < GECE_BITIS


def gecilen_basamak(degisim: float | None) -> float | None:
    gecen = [b for b in BASAMAK if degisim is not None and degisim >= b]
    return max(gecen) if gecen else None


def mesaj(h: GeceHisse, baslik: str) -> str:
    haber = (f"📰 {html.escape(h.haberler[0].get('baslik', ''))[:160]}" if h.haberler
             else "📰 Haber yok — sebebi bilinmiyor")
    hacim = f" · hacim {h.hacim / 1e3:,.0f}K" if h.hacim else ""
    flt = f" · float {h.float_m:.1f}M" if h.float_m else ""
    tepe = f" · tepe {_f(h.tepe)}" if h.tepe else ""
    return (f"{baslik}: <b>{html.escape(_kisa(h.sym))}</b> {html.escape(h.ad)[:40]}\n"
            f"Kapanış {_f(h.kapanis)} → <b>{_f(h.fiyat)}</b> ({_isaretli(h.degisim)}){tepe}{hacim}{flt}\n"
            f"{haber}\n⚠️ Gece hacmi düşük, spread geniş; sabah sönebilir — test edilmedi.")


class GeceTakip:
    def __init__(self, port: int, bildirici: Bildirici):
        self.port, self.bildirici = port, bildirici
        self.hisseler: dict[str, GeceHisse] = {}
        self._son_liste: list[str] = []

    def tara(self) -> list[dict]:
        res = tv_cdp.request(YU.SCAN_URL, "POST", {**SORGU, "columns": KOLONLAR}, text_plain=True, port=self.port)
        return [{"s": r["s"], **dict(zip(KOLONLAR, r["d"]))} for r in (res or {}).get("data", [])]

    def _haber(self, sym: str, simdi: datetime) -> list[dict]:
        try:
            return YU.haberler(sym, simdi, self.port)
        except (tv_cdp.TVError, ValueError, KeyError) as e:
            log.warning("%s haber alınamadı: %s", sym, e)
            return []

    def adim(self, simdi: datetime) -> None:
        for r in self.tara():
            yeni = satirdan(r, simdi.timestamp())
            eski = self.hisseler.get(yeni.sym)
            if eski is None:
                yeni.haberler = self._haber(yeni.sym, simdi)
                log.info("🌙 %s %s → %s (%s) hacim %s", yeni.sym, _f(yeni.kapanis), _f(yeni.fiyat),
                         _isaretli(yeni.degisim), yeni.hacim)
            else:
                yeni.ilk_gorulme, yeni.haberler = eski.ilk_gorulme, eski.haberler
            self.hisseler[yeni.sym] = yeni
            self.bildir(yeni)
        self.liste_guncelle()
        self.kaydet()

    def liste_sembolleri(self) -> list[str]:
        """Gece görülen TÜM hisseler (sonradan %10'un altına düşenler de kalır), en çok yükselen başta."""
        return [h.sym for h in sorted(self.hisseler.values(), key=lambda x: -(x.degisim or 0))]

    def liste_guncelle(self) -> None:
        """TradingView izleme listesi LISTE_ADI'nı gece görülen hisselerle değiştirir (değişiklik varsa)."""
        semboller = self.liste_sembolleri()
        if not semboller or semboller == self._son_liste:
            return
        try:
            listeler = {x["name"]: x for x in tv_cdp.custom_lists(self.port)}
            if LISTE_ADI in listeler:
                tv_cdp.replace_list(listeler[LISTE_ADI]["id"], semboller, self.port)
            else:
                tv_cdp.create_list(LISTE_ADI, semboller, self.port)
        except tv_cdp.TVError as e:
            log.warning("%s güncellenemedi: %s", LISTE_ADI, e)
            return
        self._son_liste = semboller
        log.info("%s: %d hisse", LISTE_ADI, len(semboller))

    def bildir(self, h: GeceHisse) -> None:
        self.bildirici._bir_kez(f"gece|{h.sym}", mesaj(h, "🌙 GECE YÜKSELİŞİ"))
        b = gecilen_basamak(h.degisim)
        if b is not None:
            self.bildirici._bir_kez(f"gece|{h.sym}|{b:g}", mesaj(h, f"🌙🔥 GECE +%{b:g} ÜSTÜ"))

    def kaydet(self) -> None:
        veri = {"guncelleme": _time.time(), "hisseler": [asdict(h) for h in sorted(
            self.hisseler.values(), key=lambda x: -(x.degisim or 0))]}
        try:
            (FILTER_DIR / "gece.json").write_text(json.dumps(veri, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            log.warning("gece.json yazılamadı: %s", e)


def _web(port: int) -> None:
    """Takipçi 16:00 NY'de kapanınca site de kapanıyordu → gece taraması sürerken siteyi bu süreç açık tutar."""
    from .devam_web import baslat
    while True:                                # takipçi açıkken port dolu: o kapanınca siteyi devral
        try:
            baslat(port)
            return
        except OSError:
            _time.sleep(20)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aralik", type=int, default=30, help="tarama aralığı (sn)")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--web-port", type=int, default=8765)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(FILTER_DIR / "gece.log", encoding="utf-8")])
    load_dotenv(ROOT / ".env")
    threading.Thread(target=_web, args=(a.web_port,), daemon=True).start()
    gun = datetime.now(NY).date()
    kapanis = todays_close(gun)
    tg = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", ""))
    t = GeceTakip(a.port, Bildirici(tg.send, FILTER_DIR / "gece_gonderilen.json", str(gun)))
    log.info("gece taraması: kapanış %s → %s NY (değişim > %%%g, hacim > %d)", kapanis, GECE_BITIS,
             MIN_DEGISIM, MIN_HACIM)
    from .video_ekran import EkranAlici
    ekran = EkranAlici(a.web_port)                 # günün videosu için /gece ekran görüntüsü
    while True:
        simdi = datetime.now(NY)
        ekran.kontrol(simdi)
        if kapanis is None or simdi.time() >= GECE_BITIS:
            log.info("gece seansı bitti / işlem günü değil — çıkılıyor")
            break
        if gece_mi(simdi, kapanis):
            try:
                t.adim(simdi)
            except tv_cdp.TVError as e:
                log.warning("%s", e)
        _time.sleep(a.aralik)
    _time.sleep(15)          # Telegram kuyruğu boşalsın


if __name__ == "__main__":
    main()
