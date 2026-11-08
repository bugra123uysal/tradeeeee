"""Telegram bildirimleri — sitedeki iki bölüm:
  * GİRİLECEK HİSSELER — TAMAM, ŞARTLAR SAĞLANIYOR, GİR  (yüksek sinyali geri çekilmede doldu, ALMA filtresine takılmadı)
  * Yükselme Adayları  (listeye yeni giren hisse · karar DEVAM ADAYI olunca)
Aynı mesaj iki kez gitmesin diye gönderilenler gün gün dosyada tutulur (takipçi yeniden başlasa da)."""
from __future__ import annotations

import html
import json
import logging
import time as _time
from pathlib import Path
from typing import Callable

from . import geri_al as G
from . import yuksek as Y

log = logging.getLogger("bildirim")

TAZE_SN = 15 * 60          # bundan eski dolumlar (ör. yeniden başlatmada geçmişten bulunan) gönderilmez
KARAR_DEVAM = "DEVAM ADAYI"


def _f(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:.2f}" if x >= 10 else f"{x:.3f}" if x >= 1 else f"{x:.4f}"


def _kisa(sym: str) -> str:
    return sym.split(":")[-1]


def _isaretli(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1f}%"


def girilecek(x: G.GeriSinyal) -> bool:
    """Sitedeki 'girilecek' ile aynı: geri çekilmede doldu, dolumda seçim kesinleşti ve ALMA nedeni yok
    (gün ≤ %20 · sinyal ≥ 11:30 · sakin geri çekilme — sistem/yuksek.py alma_nedeni)."""
    if x.dolum_fiyat is None or x.durum != "al":
        return False
    return x.karar and not x.alma


def gir_mesaji(x: G.GeriSinyal) -> str:
    p = x.dolum_fiyat
    return (f"🟢 <b>TAMAM — ŞARTLAR SAĞLANIYOR, GİR: {html.escape(_kisa(x.sym))}</b>\n"
            f"Alış: <b>{_f(p)}</b> (sinyal {_f(x.al_fiyat)}, gün {_isaretli(x.gun_degisim)})\n"
            f"Yarısını sat: {_f(p * (1 + Y.HEDEF1 / 100))} (+%{Y.HEDEF1:g})\n"
            f"Kalanı sat: {_f(p * (1 + Y.HEDEF2 / 100))} (+%{Y.HEDEF2:g})\n"
            f"Stop: {_f(p * (1 - G.STOP / 100))} (−%{G.STOP:g})\n"
            f"Seçim ✓ 11:30 sonrası · sakin geri çekilme"
            + (f" (mumların %{x.kirmizi * 100:.0f}'i kırmızı)" if x.kirmizi is not None else "")
            + f"\n{kalite_yazi(x)} · kaynak: {'TradingView alarmı' if x.tur == 'TV' else 'Python tüm piyasa taraması'}")


def kalite_yazi(x: G.GeriSinyal) -> str:
    """OBV > EMA20 ve Kijun üstü: ikisi de ✓ → A, biri → B, hiçbiri → C (testte A/B daha iyi; eleme yapmaz)."""
    if x.obv_ustu is None or x.kijun_ustu is None:
        return "Kalite: —"
    puan = int(x.obv_ustu) + int(x.kijun_ustu)
    return (f"Kalite {'CBA'[puan]}: OBV {'✓' if x.obv_ustu else '✗'} · Kijun {'✓' if x.kijun_ustu else '✗'}")


def aday_mesaji(a, baslik: str) -> str:
    haber = (f"📰 {html.escape(a.haberler[0].get('baslik', ''))[:160]}" if a.haberler
             else "📰 Haber yok — sebebi bilinmiyor")
    pm_tepe = f" · PM tepe {_f(a.pm_tepe)}" if a.pm_tepe else ""
    flt = f" · float {a.float_m:.1f}M" if a.float_m else ""
    return (f"{baslik}: <b>{html.escape(_kisa(a.sym))}</b> {html.escape(a.ad)[:40]}\n"
            f"Fiyat {_f(a.fiyat)} · premarket {_isaretli(a.pm_degisim)}{pm_tepe}{flt}\n"
            f"Karar: <b>{html.escape(a.karar)}</b> — {html.escape(a.neden)}\n{haber}")


class Bildirici:
    def __init__(self, gonder: Callable[[str], None], dosya: Path, gun: str):
        self.gonder, self.dosya = gonder, dosya
        self.gun, self.gonderilen = gun, self._yukle(gun)

    def _yukle(self, gun: str) -> set[str]:
        try:
            d = json.loads(self.dosya.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return set(d.get("anahtarlar", [])) if d.get("gun") == gun else set()

    def _kaydet(self) -> None:
        try:
            self.dosya.write_text(json.dumps({"gun": self.gun, "anahtarlar": sorted(self.gonderilen)},
                                             ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            log.warning("bildirim kaydı yazılamadı: %s", e)

    def _bir_kez(self, anahtar: str, metin: str | None) -> bool:
        """metin None → gönderme, sadece 'gönderildi' say (eski/geçersiz olay)."""
        if anahtar in self.gonderilen:
            return False
        self.gonderilen.add(anahtar)
        if metin is not None:
            self.gonder(metin)
        self._kaydet()
        return metin is not None

    def yeni_gun(self, gun: str) -> None:
        if gun != self.gun:
            self.gun, self.gonderilen = gun, set()

    def yuksek(self, liste: list[G.GeriSinyal], simdi: float | None = None) -> None:
        simdi = _time.time() if simdi is None else simdi
        for x in liste:
            if x.dolum_dk is None:
                continue
            taze = simdi - x.dolum_dk * 60 <= TAZE_SN
            self._bir_kez(f"gir|{x.sym}|{x.al_dk}", gir_mesaji(x) if taze and girilecek(x) else None)

    def yukselme(self, adaylar) -> None:
        for a in adaylar:
            self._bir_kez(f"aday|{a.sym}", aday_mesaji(a, "🚀 Yükselme Adayı listeye girdi"))
            if a.karar == KARAR_DEVAM:
                self._bir_kez(f"devam|{a.sym}", aday_mesaji(a, "✅ DEVAM ADAYI — şartlar sağlandı"))
