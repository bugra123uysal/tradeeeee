"""'yüksek' sinyali — pine/yuksek.pine ile aynı kural (2 Kas 2026). Siteye /yuksek sekmesi.

Sinyal (günde en çok GUNLUK_MAX, hepsi aynı 1 dk mumda):
  Swing 5dk AL'ı (BB kesişi / ADX başlangıcı) · gün değişimi %GUN_MIN–GUN_MAX · son 5 mum ≥ +%MOM_MIN
  · MACD histogramı > 0 ve artıyor · kapanış seans VWAP'ı üstünde · hacim ≥ HACIM_KAT × önceki 30 mum ort.
  · Bollinger genişliği ≥ %BBW_MIN
Sonra geri çekilme alışı (geri_al.degerlendir ile aynı): 30 dk içinde sinyal kapanışı −%5 → AL,
hedef +%5, kapanış stopu −%8.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from . import geri_al as G
from . import sinyal as S

GUN_MIN, GUN_MAX = 5.0, 40.0
MOM_MIN = 2.0
HACIM_KAT = 2.0
BBW_MIN = 3.0
GUNLUK_MAX = 1
# Ek filtre (3 Kas 2026, backtest/filtre_test.py, 43 gün): sinyal anında gün > %20 olanlar ort −%0,50
# (9 haftanın 3'ü +); ≤ %20 olanlar ort +%1,06 (9/9 hafta +), gün ≥ %10 alt kümesinde +%0,93.
# Sitede bu sinyaller "ALMA" diye işaretlenir (silinmez, ses/bildirim yok). Kapatmak için: None
FILTRE_GUN_MAX: float | None = 20.0
# Yarı-yarı çıkış (3–4 Kas 2026, backtest/kosu.py + yuksek_bt.py): koşan hissede pozisyonun yarısı içeride kalsın.
# Alış → yüksek ≥ +HEDEF1 → YARISI satılır ("yarim") → yüksek ≥ +HEDEF2 → kalan da satılır ("hedef").
# Kapanış < alış −%STOP (G.STOP) → kalan satılır ("stop"). Gerçekçi 43 günde (gün ≤ 20): +5 → +0,22%, yarı/yarı → +0,48%.
HEDEF1 = 5.0
HEDEF2 = 20.0
# Seans sonu (4 Kas 2026): Pine 15:59 mumunda, kağıt hesap 15:55'te kapatıyor; site de 16:00'dan sonraki mumlara
# (seans sonrası işlem) bakmasın — yoksa WORX 3 Kas gibi 19:49'daki koşuyu "hedef" sayıp sonucu iyi gösterir.
SEANS_SON = (16, 0)
NY = ZoneInfo("America/New_York")


def seans_sonu_dk(al_dk: int) -> int:
    """Sinyalin NY gününün 16:00'ı (epoch dakika). Sinyal 16:00'dan sonraysa o günün 16:00'ı → işlem yok."""
    t = datetime.fromtimestamp(al_dk * 60, NY).replace(hour=SEANS_SON[0], minute=SEANS_SON[1], second=0, microsecond=0)
    return int(t.timestamp() // 60)


def degerlendir(gs, dks, o, h, l, c, seans_son_dk: int | None = None, simdi_dk: int | None = None) -> str | None:
    """geri_al.degerlendir'in yarı-yarı çıkışlı hâli. Değişen yeni durumu döner (yoksa None).

    seans_son_dk verilirse (16:00 NY, Pine ve kağıt hesapla aynı) sadece ondan ÖNCEKİ mumlara bakılır; seans
    bitince açık pozisyon "seans" olur (son seans mumunun kapanışından çıkış, cikis_fiyat), dolmamış emir "yok".
    simdi_dk: şu anki dakika (mum gelmeyen sığ hissede seansın bittiğini anlamak için)."""
    if gs.durum in ("hedef", "stop", "yok", "seans"):
        return None
    eski = gs.durum
    bitti = False
    if seans_son_dk is not None:
        son = max(int(dks[-1]) if len(dks) else 0, simdi_dk or 0)
        bitti = son >= seans_son_dk
        m = dks < seans_son_dk
        dks, o, h, l, c = dks[m], o[m], h[m], l[m], c[m]
    _ilerlet(gs, dks, o, h, l, c)
    if bitti and gs.durum == "bekliyor":
        gs.durum = "yok"
    elif bitti and gs.durum in ("al", "yarim") and len(c):
        gs.durum, gs.sonuc_dk, gs.cikis_fiyat = "seans", int(dks[-1]), float(c[-1])
    return gs.durum if gs.durum != eski else None


def _ilerlet(gs, dks, o, h, l, c) -> None:
    if gs.durum == "bekliyor":
        G.degerlendir(gs, dks, o, h, l, c)            # dolum mantığı aynı; hedefi aşağıda biz değerlendiririz
        if gs.durum in ("hedef", "stop") and gs.dolum_dk is not None:
            gs.durum, gs.sonuc_dk = "al", None
    if gs.durum in ("al", "yarim") and gs.dolum_dk is not None:
        m = dks >= gs.dolum_dk
        d, h2, c2 = dks[m], h[m], c[m]
        h1, hh2, st = (gs.dolum_fiyat * (1 + HEDEF1 / 100), gs.dolum_fiyat * (1 + HEDEF2 / 100),
                       gs.dolum_fiyat * (1 - G.STOP / 100))
        durum, yari_dk, sonuc = "al", None, None
        for k in range(len(d)):
            if k > 0 and durum == "al" and h2[k] >= h1:
                durum, yari_dk = "yarim", int(d[k])
            if k > 0 and durum == "yarim" and h2[k] >= hh2:
                durum, sonuc = "hedef", int(d[k])
                break
            if c2[k] < st:
                durum, sonuc = "stop", int(d[k])
                break
        gs.durum, gs.sonuc_dk = durum, sonuc
        gs.yari_dk = yari_dk if yari_dk is not None else gs.yari_dk


def yuksek_sinyalleri(dks, o, h, l, c, v, g: S.Gostergeler, onceki: float, gun_bas_dk: int) -> list[int]:
    """Günün 'yüksek' sinyal mum indeksleri (en çok GUNLUK_MAX)."""
    bas = int(np.searchsorted(dks, gun_bas_dk))
    if not onceki or len(c) - bas < 6:
        return []
    mid = S.sma(c, S.BB_LEN)
    with np.errstate(invalid="ignore", divide="ignore"):
        bbw = (g.bb_up - mid) * 2 / mid * 100
    vg, cg = v[bas:], c[bas:]
    vwap = np.full(len(c), np.nan)
    vwap[bas:] = np.cumsum(cg * vg) / np.maximum(np.cumsum(vg), 1e-9)
    hist = g.macd - g.macd_sig
    out = []
    for i, _ in S.al_sinyalleri(c, g):
        if i < bas or i < 31:
            continue
        gun = (c[i] / onceki - 1) * 100
        kosul = (GUN_MIN <= gun <= GUN_MAX
                 and (c[i] / c[i - 5] - 1) * 100 >= MOM_MIN
                 and hist[i] > 0 and hist[i] > hist[i - 1]
                 and c[i] > vwap[i]
                 and v[i] >= HACIM_KAT * v[i - 30:i].mean()
                 and bbw[i] >= BBW_MIN)
        if kosul:
            out.append(i)
            if len(out) >= GUNLUK_MAX:
                break
    return out
