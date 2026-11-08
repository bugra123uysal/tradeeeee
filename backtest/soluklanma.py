"""Soluklanma kırılımı (yeni yüksek_2, 7 Kas 2026) — "hisse yükseldi, hafif soluklandı, tekrar yukarı kırdı".

GOGO 6 Kas 2026 örneği: 09:30–09:42 +%25 direk → 09:42–10:06 2,78–2,85 dar kutu → 10:12 hacimle kırılım → 3,17;
sonra 10:14–10:42 3,03–3,19 kutu → 10:44 kırılım → 3,58.

Kalıp (i = kırılım mumu, hepsi i'den ÖNCEKİ mumlarla — ileriye bakış yok):
  kutu   : son N mumun (i−N … i−1) en yüksek tepesi `ust`, en düşük dibi `alt`; (ust−alt)/ust ≤ R %
  direk  : kutudan önceki P mumun en düşük dibi `dip` → ust/dip − 1 ≥ I %  (önce sert yükseliş)
           ve kutu direğin üst yarısında: alt ≥ dip + (ust − dip)/2   (geri çekilme direğin yarısını geçmedi)
  kırılım: kapanış > ust · hacim ≥ HK × kutu ortalama hacmi · kapanış VWAP üstü · gün değişimi ≥ %5
Giriş: sonraki mum açılışı. Çıkışlar (BRÜT %, 16:00'da kapanır):
  5_8     : +%5 hedef (tepe) / kapanış girişin −%8 altı
  kutu_10 : stop = kapanış kutu dibinin (alt) altında / +%10 hedef
  kutu_2r : stop = kutu dibi / hedef = giriş + 2 × (giriş − alt)
  kutu_iz : stop = kutu dibi / iz süren: kapanış tepenin %10 altında
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DIREK_P = 30
CIKISLAR = ("5_8", "kutu_10", "kutu_2r", "kutu_iz")


def _kaydir(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def kutu_ozellikleri(h: np.ndarray, l: np.ndarray, v: np.ndarray, n: int, p: int = DIREK_P) -> dict[str, np.ndarray]:
    """i için: ust/alt = i−n … i−1 aralığı, dip = i−n−p … i−n−1 aralığı, kutu hacim ortalaması."""
    hs, ls, vs = pd.Series(h), pd.Series(l), pd.Series(v)
    ust = _kaydir(hs.rolling(n).max().to_numpy(), 1)
    alt = _kaydir(ls.rolling(n).min().to_numpy(), 1)
    dip = _kaydir(ls.rolling(p).min().to_numpy(), n + 1)
    vort = _kaydir(vs.rolling(n).mean().to_numpy(), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return {"ust": ust, "alt": alt, "dip": dip, "kutu_r": (ust - alt) / ust * 100,
                "direk": (ust / dip - 1) * 100, "yari": alt >= dip + (ust - dip) / 2, "vort": vort}


def kirilimlar(c: np.ndarray, v: np.ndarray, vw: np.ndarray, gun: np.ndarray, oz: dict, r_max: float,
               i_min: float, hk: float, gun_min: float = 5.0) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        m = ((oz["kutu_r"] <= r_max) & (oz["direk"] >= i_min) & oz["yari"] & (c > oz["ust"])
             & (v >= hk * oz["vort"]) & (c > vw) & (gun >= gun_min))
    return np.flatnonzero(m)


def kutu_icleri(c: np.ndarray, vw: np.ndarray, gun: np.ndarray, oz: dict, r_max: float, i_min: float,
                gun_min: float = 5.0) -> np.ndarray:
    """KUTU İÇİ alış: j mumunda (i−n … j−1) kutu şartları İLK kez sağlanıyor (soluklanma oluştu), son kapanış
    kutunun içinde ve VWAP üstünde → j açılışında al (kırılımı beklemeden). Döner j indexleri."""
    with np.errstate(invalid="ignore"):
        ok = (oz["kutu_r"] <= r_max) & (oz["direk"] >= i_min) & oz["yari"]
        c1, vw1, g1 = _kaydir(c, 1), _kaydir(vw, 1), _kaydir(gun, 1)
        m = ok & ~_kaydir(ok.astype(float), 1).astype(bool) & (c1 <= oz["ust"]) & (c1 > vw1) & (g1 >= gun_min)
    return np.flatnonzero(m)


def cikis(o: np.ndarray, h: np.ndarray, c: np.ndarray, k0: int, son: int, alt: float, tur: str) -> tuple[float, int]:
    """k0 = giriş mumu (açılışta alındı), son = seans sonu indexi (hariç). Döner (brüt %, çıkış indexi)."""
    giris = o[k0]
    cc, hh = c[k0:son], h[k0:son]
    if not len(cc):
        return np.nan, k0
    if tur == "5_8":
        stop_m, hedef = cc < giris * 0.92, giris * 1.05
    else:
        stop_m = cc < alt * 0.995
        hedef = giris * 1.10 if tur == "kutu_10" else giris + 2 * (giris - alt) if tur == "kutu_2r" else np.inf
    if tur == "kutu_iz":
        tepe = np.maximum.accumulate(np.maximum(hh, giris))
        stop_m = stop_m | (cc < tepe * 0.90)
    k_st = int(np.argmax(stop_m)) if stop_m.any() else len(cc)
    hd = np.flatnonzero(hh >= hedef)
    k_hd = int(hd[0]) if len(hd) else len(cc)
    if k_hd < len(cc) and k_hd <= k_st:
        return (hedef / giris - 1) * 100, k0 + k_hd
    k = min(k_st, len(cc) - 1)
    return (cc[k] / giris - 1) * 100, k0 + k
