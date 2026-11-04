"""'yüksek' sinyali — pine/yuksek.pine ile aynı kural (2 Kas 2026). Siteye /yuksek sekmesi.

Sinyal (günde en çok GUNLUK_MAX, hepsi aynı 1 dk mumda):
  Swing 5dk AL'ı (BB kesişi / ADX başlangıcı) · gün değişimi %GUN_MIN–GUN_MAX · son 5 mum ≥ +%MOM_MIN
  · MACD histogramı > 0 ve artıyor · kapanış seans VWAP'ı üstünde · hacim ≥ HACIM_KAT × önceki 30 mum ort.
  · Bollinger genişliği ≥ %BBW_MIN
Sonra geri çekilme alışı (geri_al.degerlendir ile aynı): 30 dk içinde sinyal kapanışı −%5 → AL,
hedef +%5, kapanış stopu −%8.
"""
from __future__ import annotations

import numpy as np

from . import sinyal as S

GUN_MIN, GUN_MAX = 5.0, 40.0
MOM_MIN = 2.0
HACIM_KAT = 2.0
BBW_MIN = 3.0
GUNLUK_MAX = 1


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
