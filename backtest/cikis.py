"""Onaylı girişlerde (AL + 2–4 mum, +%2) hangi ÇIKIŞ kuralı hareketi en iyi yakalar? (2 Kas 2026)

Giriş: backtest/devam.py ile aynı (sadece seans, onay mumu kapanışı). Her çıkış kuralı aynı girişlere
uygulanır; aynı hissede çıkıştan önce yeni giriş yok (her kural kendi meşguliyetini tutar).
"yakalama%" = (çıkış − giriş) / (girişten sonraki 60 dk tepesi − giriş), tepe > giriş olanlarda.

    py -m backtest.cikis          (pandas → sistem python)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.devam import DK, KOMISYON, REG_CLOSE, REG_OPEN
from sistem import sinyal as S

MIN_ARTIS = 2.0


@dataclass(frozen=True)
class Cikis:
    ad: str
    swing: bool = False            # SAT (VWMA20 & EMA50 altı) + STOP 1.5 ATR + 20 mum
    al_alti: bool = False          # kapanış AL kapanışının altına inerse
    giris_alti: bool = False       # kapanış giriş (onay) fiyatının altına inerse
    iz_yuzde: float = 0.0          # tepe kapanıştan % geri çekilme
    iz_atr: float = 0.0            # tepe kapanıştan k×ATR geri çekilme
    ema9_alti: bool = False
    hedef: float = 0.0             # % kâr hedefi (yüksek dokununca)
    max_mum: int = 0


CIKISLAR = [
    Cikis("Swing (SAT/STOP/20 mum) — mevcut", swing=True),
    Cikis("AL altı + iz %3", al_alti=True, iz_yuzde=3),
    Cikis("AL altı + iz %5", al_alti=True, iz_yuzde=5),
    Cikis("AL altı + iz %8", al_alti=True, iz_yuzde=8),
    Cikis("AL altı + iz %12", al_alti=True, iz_yuzde=12),
    Cikis("AL altı + iz 2×ATR", al_alti=True, iz_atr=2),
    Cikis("AL altı + iz 3×ATR", al_alti=True, iz_atr=3),
    Cikis("AL altı + EMA9 altı", al_alti=True, ema9_alti=True),
    Cikis("giriş altı + iz %5", giris_alti=True, iz_yuzde=5),
    Cikis("giriş altı + iz %8", giris_alti=True, iz_yuzde=8),
    Cikis("AL altı + hedef %10", al_alti=True, hedef=10),
    Cikis("AL altı + hedef %20 + iz %8", al_alti=True, hedef=20, iz_yuzde=8),
    Cikis("AL altı + iz %5 + 30 mum", al_alti=True, iz_yuzde=5, max_mum=30),
]


def _cikis_mumu(x: Cikis, j: int, al_c: float, o, h, c, g, e9, son: int) -> tuple[int, float]:
    giris = c[j]
    stop = giris - 1.5 * g.atr[j]
    tepe = giris
    for k in range(j + 1, son + 1):
        if x.hedef and h[k] >= giris * (1 + x.hedef / 100):
            return k, giris * (1 + x.hedef / 100)
        tepe = max(tepe, c[k])
        if x.swing and ((c[k] < g.vw[k] and c[k] < g.e50[k]) or c[k] < stop or k - j >= 20):
            return k, c[k]
        if (x.al_alti and c[k] < al_c) or (x.giris_alti and c[k] < giris):
            return k, c[k]
        if x.iz_yuzde and c[k] < tepe * (1 - x.iz_yuzde / 100):
            return k, c[k]
        if x.iz_atr and c[k] < tepe - x.iz_atr * g.atr[k]:
            return k, c[k]
        if x.ema9_alti and c[k] < e9[k]:
            return k, c[k]
        if x.max_mum and k - j >= x.max_mum:
            return k, c[k]
    return son, c[son]


def girisler(o, c, g, reg) -> list[tuple[int, int]]:
    """[(AL indeksi, onay indeksi)] — seans içi onaylar."""
    out = []
    for i, _ in S.al_sinyalleri(c, g):
        durum, j, _ = S.onay_karari(o, c, g, i, MIN_ARTIS)
        if durum == "onay" and reg[j]:
            out.append((i, j))
    return out


def simule(df: pd.DataFrame) -> dict[str, list[dict]]:
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    ts = df["timestamp"].dt.time.to_numpy()
    reg = np.array([REG_OPEN <= t < REG_CLOSE for t in ts])
    if not reg.any():
        return {}
    son = int(np.where(reg)[0].max())
    g = S.gostergeler(o, h, l, c, v)
    e9 = S.ema(c, 9)
    gir = [(i, j) for i, j in girisler(o, c, g, reg) if j < son]
    out: dict[str, list[dict]] = {}
    for x in CIKISLAR:
        mesgul = -1
        for i, j in gir:
            if j <= mesgul:
                continue
            k, px = _cikis_mumu(x, j, c[i], o, h, c, g, e9, son)
            mesgul = k
            tepe60 = h[j + 1:j + 61].max() if j + 1 <= son else c[j]
            out.setdefault(x.ad, []).append({
                "net": (px / c[j] - 1) * 100 - KOMISYON,
                "pot": (tepe60 / c[j] - 1) * 100, "sure": k - j, "t": ts[j]})
    return out


def main() -> None:
    dosyalar = sorted(DK.glob("*.pkl"))
    sonuc: dict[str, list[dict]] = {x.ad: [] for x in CIKISLAR}
    for f in dosyalar:
        for sym, df in pd.read_pickle(f).groupby("symbol"):
            if len(df) < 60:
                continue
            for ad, rows in simule(df.sort_values("timestamp")).items():
                sonuc[ad] += [{**r, "sym": sym, "day": f.stem} for r in rows]
        print(".", end="", flush=True, file=sys.stderr)
    print(file=sys.stderr)
    satirlar = [f"{len(dosyalar)} gün · {dosyalar[0].stem} → {dosyalar[-1].stem} · giriş: AL + 2–4 mum onay, +%{MIN_ARTIS}",
                "", f"{'çıkış kuralı':36} {'işlem':>6} {'kârlı%':>7} {'ort%':>7} {'medyan%':>8} {'toplam%':>8} "
                f"{'ort mum':>7} {'yakalama%':>9}"]
    for ad, rows in sonuc.items():
        n = np.array([r["net"] for r in rows])
        pot = np.array([r["pot"] for r in rows])
        ok = pot > 1
        yak = np.median((n[ok] + KOMISYON) / pot[ok] * 100) if ok.any() else np.nan
        satirlar.append(f"{ad:36} {len(n):>6} {100 * (n > 0).mean():>7.1f} {n.mean():>7.2f} {np.median(n):>8.2f} "
                        f"{n.sum():>8.0f} {np.mean([r['sure'] for r in rows]):>7.1f} {yak:>9.0f}")
    metin = "\n".join(satirlar)
    print(metin)
    (Path(__file__).parent / "sonuc" / "cikis_ozet.txt").write_text(metin + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
