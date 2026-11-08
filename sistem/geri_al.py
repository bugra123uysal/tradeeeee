"""Geri çekilme AL'ı — kullanıcının seçtiği kural (2 Kas 2026), backtest ile birebir.

Backtest (43 gün, girişte gün ≥ %10, yanlılıksız): ort +%0,36, %66 kârlı, iki yarıda da artı. Kanıt değil.
  1. Swing 5dk AL'ı — Pine gibi SADECE pozisyon yokken (önceki AL'ın SAT / STOP / SÜRE 20 mum çıkışından sonra)
  2. AL anında gün değişimi ≥ MIN_GUN
  3. AL'dan sonra PENCERE_DK dakika içinde düşük ≤ AL kapanışı × (1 − GERI%)  → "GERİ ÇEKİLME TAMAM — AL"
     alış = min(seviye, dolum mumunun açılışı)
  4. Sonra: yüksek ≥ alış × (1 + HEDEF%) → hedef ; kapanış < alış × (1 − STOP%) → stop (siteden silinir)
     (dolum mumunda sadece kapanış stopu bakılır)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import sinyal as S

GERI = 5.0
PENCERE_DK = 30
HEDEF = 5.0
STOP = 8.0
MIN_GUN = 10.0
STOP_ATR, SURE_MUM = 1.5, 20


@dataclass
class GeriSinyal:
    sym: str
    ad: str
    tur: str                       # BB / ADX
    al_dk: int
    al_fiyat: float
    gun_degisim: float | None
    seviye: float                  # limit alış seviyesi
    durum: str = "bekliyor"        # bekliyor / al / hedef / stop / yok (pencerede geri çekilme olmadı)
    dolum_dk: int | None = None
    dolum_fiyat: float | None = None
    sonuc_dk: int | None = None
    son_fiyat: float | None = None
    eklendi: float | None = None   # AL'ın siteye geldiği an (epoch sn)
    al_bildirim: float | None = None  # "geri çekilme tamam — AL" yazıldığı an (epoch sn)
    gecmis: bool = False           # geriye dönük taramada bulundu
    tepe: float | None = None      # sinyalden sonraki en yüksek fiyat
    mom5: float | None = None      # 'yüksek' sinyali: son 5 mum %
    hacim_kat: float | None = None  # 'yüksek' sinyali: hacim / önceki 30 mum ort.
    yari_dk: int | None = None      # 'yüksek' yarı-yarı çıkış: ilk hedefte yarısının satıldığı dakika
    yari_bildirim: float | None = None
    cikis_fiyat: float | None = None  # 'yüksek' seans sonu (16:00) kapanışında çıkış fiyatı
    kirmizi: float | None = None      # 'yüksek' sinyal ile dolum arası kırmızı mum oranı (dolumdan önce: şu ana kadar)
    alma: str | None = None           # 'yüksek' sinyal sonrası seçim: ALMA nedeni (None = alınabilir)
    karar: bool = False               # dolumda seçim kesinleşti (sonra değişmez)
    obv_ustu: bool | None = None      # kalite etiketi (karar anı): OBV > EMA20(OBV)
    kijun_ustu: bool | None = None    # kalite etiketi (karar anı): kapanış > Kijun-sen(26)

    @property
    def hedef(self) -> float | None:
        return self.dolum_fiyat * (1 + HEDEF / 100) if self.dolum_fiyat else None

    @property
    def stop(self) -> float | None:
        return self.dolum_fiyat * (1 - STOP / 100) if self.dolum_fiyat else None


def pine_al_lari(dks, c, g: S.Gostergeler, gun_bas_dk: int) -> list[tuple[int, str]]:
    """Günün Pine AL'ları: pozisyon yokken gelen AL; çıkış SAT / STOP / SÜRE 20 mum. Pozisyon hâlâ açıksa
    sonraki AL'lar sayılmaz (Pine pyramiding 0)."""
    al = dict(S.al_sinyalleri(c, g))
    bas = int(np.searchsorted(dks, gun_bas_dk))
    out, i = [], bas
    while i < len(c):
        if i not in al:
            i += 1
            continue
        out.append((i, al[i]))
        stop = c[i] - STOP_ATR * g.atr[i]
        k = next((k for k in range(i + 1, len(c))
                  if (c[k] < g.vw[k] and c[k] < g.e50[k]) or c[k] < stop or k - i >= SURE_MUM), None)
        if k is None:
            break
        i = k + 1
    return out


def degerlendir(gs: GeriSinyal, dks, o, h, l, c) -> str | None:
    """Kapanmış mumlarla durumu ilerletir. Değişen yeni durumu döner (yoksa None)."""
    if gs.durum in ("hedef", "stop", "yok"):
        return None
    eski = gs.durum
    m = dks > gs.al_dk
    d, o2, h2, l2, c2 = dks[m], o[m], h[m], l[m], c[m]
    if gs.durum == "bekliyor":
        icinde = np.where((d - gs.al_dk <= PENCERE_DK) & (l2 <= gs.seviye))[0]
        if not len(icinde):
            if len(d) and int(d[-1]) - gs.al_dk >= PENCERE_DK:
                gs.durum = "yok"
            return gs.durum if gs.durum != eski else None
        k = int(icinde[0])
        gs.durum, gs.dolum_dk, gs.dolum_fiyat = "al", int(d[k]), float(min(gs.seviye, o2[k]))
    if gs.durum == "al" and gs.dolum_dk in d:
        k0 = int(np.where(d == gs.dolum_dk)[0][0])
        for k in range(k0, len(d)):
            if k > k0 and h2[k] >= gs.hedef:
                gs.durum, gs.sonuc_dk = "hedef", int(d[k])
                break
            if c2[k] < gs.stop:
                gs.durum, gs.sonuc_dk = "stop", int(d[k])
                break
    return gs.durum if gs.durum != eski else None


def canli_dolum(gs: GeriSinyal, fiyat: float, dk: int) -> bool:
    """Mum kapanmadan, canlı fiyat seviyeye inerse hemen AL yaz (10 sn'lik tarama)."""
    if gs.durum != "bekliyor" or dk - gs.al_dk > PENCERE_DK or fiyat > gs.seviye:
        return False
    gs.durum, gs.dolum_dk, gs.dolum_fiyat = "al", dk, float(gs.seviye)
    return True
