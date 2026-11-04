"""AL sinyalinden 2–4 mum sonra "devam eden yükseliş" girişini test eder (CHOW örneği, 2 Kas 2026).

Veri: backtest/data/dk/*.pkl (python -m backtest.veri ile indirilmiş, 04:00–16:00 NY 1 dk).
Çıkış kuralları Swing 5dk ile aynı: SAT (kapanış < VWMA20 ve < EMA50), STOP (giriş − 1,5×ATR),
SÜRE (20 mum), GÜN SONU. Komisyon gidiş-dönüş %0,2.

pandas gerektirir → venv'de değil (Uygulama Denetimi), sistem python ile çalıştır:
    py -m backtest.devam
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

from sistem import sinyal as S

DK = Path(__file__).parent / "data" / "dk"
KOMISYON = 0.2          # % gidiş-dönüş
STOP_ATR, MAX_MUM = 1.5, 20
REG_OPEN, REG_CLOSE = time(9, 30), time(16, 0)


@dataclass(frozen=True)
class Varyant:
    ad: str
    onay: bool = True
    min_artis: float = S.ONAY_MIN_ARTIS
    sadece_seans: bool = True        # giriş sadece 09:30–16:00
    min_gun: float = 0.0             # giriş anında önceki kapanışa göre en az % artış
    bitis: time = time(16, 0)        # bu saatten sonra giriş yok
    macd: bool = False
    vwap_ustu: bool = False          # kapanış seans VWAP'ı (04:00'dan) üstünde
    min_fiyat: float = 0.0
    ilk_n: int = 0                   # günün ilk N girişi (0 = sınırsız)


def simule(df: pd.DataFrame, prev_close: float | None, v: Varyant) -> list[dict]:
    o, h, l, c, vol = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    ts = df["timestamp"].dt.time.to_numpy()
    g = S.gostergeler(o, h, l, c, vol)
    svwap = np.cumsum(c * vol) / np.maximum(np.cumsum(vol), 1)
    reg = np.array([REG_OPEN <= t < REG_CLOSE for t in ts])
    last_reg = np.where(reg)[0].max() if reg.any() else len(c) - 1
    out, busy_until = [], -1
    for i, kind in S.al_sinyalleri(c, g):
        j = S.onay_mumu(o, c, g, i) if v.onay else i
        if v.onay and j is not None and (c[j] / c[i] - 1) * 100 < v.min_artis:
            j = None
        if j is None or j <= busy_until or j >= last_reg:
            continue
        if v.sadece_seans and not reg[j]:
            continue
        if ts[j] >= v.bitis or c[j] < v.min_fiyat:
            continue
        gun = (c[j] / prev_close - 1) * 100 if prev_close else np.nan
        if v.min_gun and not (gun >= v.min_gun):
            continue
        if v.macd and not (g.macd[j] > g.macd_sig[j]):
            continue
        if v.vwap_ustu and not (c[j] > svwap[j]):
            continue
        entry, stop = c[j], c[j] - STOP_ATR * g.atr[j]
        k = j + 1
        while k <= last_reg:
            if (c[k] < g.vw[k] and c[k] < g.e50[k]) or c[k] < stop or k - j >= MAX_MUM:
                break
            k += 1
        k = min(k, last_reg)
        out.append({"t": ts[j], "tur": kind, "giris": entry, "cikis": c[k],
                    "net": (c[k] / entry - 1) * 100 - KOMISYON,
                    "max30": (h[j + 1:j + 31].max() / entry - 1) * 100 if j + 1 < len(c) else 0.0,
                    "gun": gun})
        busy_until = k
        if v.ilk_n and len(out) >= v.ilk_n:
            break
    return out


VARYANTLAR = [
    Varyant("AL anında gir (mevcut script)", onay=False),
    Varyant("onay 2-4 mum, +%1", min_artis=1.0),
    Varyant("onay 2-4 mum, +%2", min_artis=2.0),
    Varyant("onay 2-4 mum, +%3", min_artis=3.0),
    Varyant("onay +%2, gün ≥ %20", min_artis=2.0, min_gun=20),
    Varyant("onay +%2, gün ≥ %20, 11:00 öncesi", min_artis=2.0, min_gun=20, bitis=time(11, 0)),
    Varyant("onay +%2, gün ≥ %20, ilk giriş", min_artis=2.0, min_gun=20, ilk_n=1),
    Varyant("onay +%2, VWAP üstü, MACD", min_artis=2.0, vwap_ustu=True, macd=True),
    Varyant("onay +%2, gün ≥ %20, VWAP üstü, MACD", min_artis=2.0, min_gun=20, vwap_ustu=True, macd=True),
    Varyant("onay +%2, gün ≥ %20, VWAP, MACD, 11:00 öncesi", min_artis=2.0, min_gun=20,
            vwap_ustu=True, macd=True, bitis=time(11, 0)),
]


def main() -> None:
    gunluk = pd.read_pickle(DK.parent / "gunluk.pkl")
    prev = {(r.symbol, str(r.day)): r.prev_close for r in gunluk.itertuples()}
    dosyalar = sorted(DK.glob("*.pkl"))
    sonuc = {v.ad: [] for v in VARYANTLAR}
    for f in dosyalar:
        day = f.stem
        for sym, df in pd.read_pickle(f).groupby("symbol"):
            if len(df) < 60:
                continue
            df = df.sort_values("timestamp")
            pc = prev.get((sym, day))
            for v in VARYANTLAR:
                sonuc[v.ad] += [{**x, "sym": sym, "day": day} for x in simule(df, pc, v)]
        print(".", end="", flush=True, file=sys.stderr)
    print(file=sys.stderr)
    print(f"{len(dosyalar)} gün · {dosyalar[0].stem} → {dosyalar[-1].stem}\n")
    print(f"{'varyant':48} {'işlem':>6} {'kârlı%':>7} {'ort%':>7} {'medyan%':>8} {'toplam%':>8} {'30dk max ort%':>13}")
    for ad, rows in sonuc.items():
        if not rows:
            print(f"{ad:48} {0:>6}")
            continue
        n = np.array([r["net"] for r in rows])
        m = np.array([r["max30"] for r in rows])
        print(f"{ad:48} {len(n):>6} {100 * (n > 0).mean():>7.1f} {n.mean():>7.2f} {np.median(n):>8.2f} "
              f"{n.sum():>8.0f} {m.mean():>13.1f}")
    pd.DataFrame(sonuc["onay +%2, gün ≥ %20, ilk giriş"]).to_csv(
        Path(__file__).parent / "sonuc" / "devam_islemler.csv", index=False)


if __name__ == "__main__":
    main()
