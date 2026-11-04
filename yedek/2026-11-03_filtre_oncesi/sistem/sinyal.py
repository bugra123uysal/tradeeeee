"""Swing 5dk AL sinyali + "devam eden yükseliş" (onay) kuralı — saf numpy, pandas YOK.

pandas'ı Windows Uygulama Denetimi engelliyor; bu modül sadece numpy kullanır.

AL (TradingView "Buğra Çoklu Hisse Swing 5dk" ile aynı, MACD filtresi yok):
  kapanış BB(20, 2) üst bandını yukarı keser          → "BB"
  VEYA ADX(14) > 25 ve +DI > −DI durumu yeni başlar   → "ADX"

ONAY (kullanıcının fikri, 2 Kas 2026 — CHOW örneği): AL'dan sonraki 2–4 mum içinde
yükseliş sürüyorsa iyi giriş. Bir AL, k. mumda (ONAY_MIN ≤ k ≤ ONAY_MAX) onaylanır, eğer:
  * kapanış AL mumunun kapanışından en az ONAY_MIN_ARTIS % yukarıda,
  * AL'dan beri hiçbir kapanış AL kapanışının altına inmemiş,
  * kapanış VWMA(20) ve EMA(50) üstünde (SAT koşulu oluşmamış),
  * son mum yeşil (kapanış ≥ açılış).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BB_LEN, BB_MULT = 20, 2.0
DI_LEN, ADX_MIN = 14, 25.0
VW_LEN, EMA_LEN = 20, 50
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
ONAY_MIN, ONAY_MAX = 2, 4          # AL'dan sonra kaçıncı mumlar
ONAY_MIN_ARTIS = 1.0               # % — AL kapanışına göre en az bu kadar yukarıda


def _rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder ortalaması (Pine ta.rma)."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = np.nanmean(x[:n])
    a = 1.0 / n
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = x[:n].mean()
    a = 2.0 / (n + 1)
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def stdev(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = x[i - n + 1:i + 1].std()          # Pine ta.stdev: popülasyon (ddof=0)
    return out


def dmi(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int):
    up = np.diff(h, prepend=np.nan)
    dn = -np.diff(l, prepend=np.nan)
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev_c = np.roll(c, 1)
    tr = np.maximum(h - l, np.maximum(abs(h - prev_c), abs(l - prev_c)))
    tr[0] = h[0] - l[0]
    plus[0] = minus[0] = 0.0
    atr = _rma(tr, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        dip = 100 * _rma(plus, n) / atr
        dim = 100 * _rma(minus, n) / atr
        dx = 100 * abs(dip - dim) / (dip + dim)
    dx = np.nan_to_num(dx, nan=0.0)
    first = np.argmax(~np.isnan(atr)) if np.any(~np.isnan(atr)) else len(c)
    adx = np.full(len(c), np.nan)
    adx[first:] = _rma(dx[first:], n)
    return dip, dim, adx, atr


@dataclass(frozen=True)
class Gostergeler:
    bb_up: np.ndarray
    vw: np.ndarray
    e50: np.ndarray
    adx: np.ndarray
    dip: np.ndarray
    dim: np.ndarray
    macd: np.ndarray
    macd_sig: np.ndarray
    atr: np.ndarray


def gostergeler(o, h, l, c, v) -> Gostergeler:
    c = np.asarray(c, float)
    mid = sma(c, BB_LEN)
    bb_up = mid + BB_MULT * stdev(c, BB_LEN)
    v = np.asarray(v, float)
    with np.errstate(invalid="ignore", divide="ignore"):
        vw = sma(c * v, VW_LEN) / sma(v, VW_LEN)
    dip, dim, adx, atr = dmi(np.asarray(h, float), np.asarray(l, float), c, DI_LEN)
    m = ema(c, MACD_FAST) - ema(c, MACD_SLOW)
    ms = np.full(len(c), np.nan)
    ok = ~np.isnan(m)
    if ok.sum() >= MACD_SIG:
        ms[ok] = ema(m[ok], MACD_SIG)
    return Gostergeler(bb_up, vw, ema(c, EMA_LEN), adx, dip, dim, m, ms, atr)


def al_sinyalleri(c, g: Gostergeler) -> list[tuple[int, str]]:
    """[(mum indeksi, "BB"|"ADX")] — pozisyon takibi yok, her ham AL sinyali."""
    c = np.asarray(c, float)
    with np.errstate(invalid="ignore"):
        bb = (c > g.bb_up) & (np.roll(c, 1) <= np.roll(g.bb_up, 1))
        adx_on = (g.adx > ADX_MIN) & (g.dip > g.dim)
        adx_yeni = adx_on & ~np.roll(adx_on, 1)
    bb[0] = adx_yeni[0] = False
    out = []
    for i in range(len(c)):
        if bb[i]:
            out.append((i, "BB"))
        elif adx_yeni[i]:
            out.append((i, "ADX"))
    return out


def onay_karari(o, c, g: Gostergeler, al_i: int,
                min_artis: float = ONAY_MIN_ARTIS) -> tuple[str, int | None, str]:
    """AL'dan sonraki mumlarda onay kuralı → (durum, onay indeksi, neden).

    durum: "onay" | "iptal" | "bekliyor" (veri henüz ONAY_MAX mumu kapsamıyor).
    """
    ref = c[al_i]
    for k in range(1, ONAY_MAX + 1):
        j = al_i + k
        if j >= len(c):
            return "bekliyor", None, f"{k - 1}. mum bitti, bekleniyor"
        if c[j] < ref:
            return "iptal", None, "AL kapanışının altına indi"
        if c[j] < g.vw[j] and c[j] < g.e50[j]:
            return "iptal", None, "SAT koşulu oluştu (VWMA20 ve EMA50 altı)"
        artis = (c[j] / ref - 1) * 100
        if k >= ONAY_MIN and artis >= min_artis and c[j] >= o[j]:
            return "onay", j, f"{k}. mumda +%{artis:.1f}"
    return "iptal", None, f"{ONAY_MAX} mumda onay gelmedi"


def onay_mumu(o, c, g: Gostergeler, al_i: int, min_artis: float = ONAY_MIN_ARTIS) -> int | None:
    """AL'dan sonra onay kuralının ilk sağlandığı mum indeksi (yoksa None)."""
    return onay_karari(o, c, g, al_i, min_artis)[1]
