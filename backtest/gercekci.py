"""Dolum gerçekçiliği stres testi (4 Kas 2026): limit alış fiyata 'değince' değil, seviyenin ALTINA
inince dolar (trade-through) + her işlemde ek kayma (spread/slippage). Kural: yüksek → −%5 geri çekilme
→ +%5 hedef / −%8 stop (ve yarısı +%5 / yarısı +%20). Sinyalde gün ≤ %20 (sitedeki filtre).

    py -m backtest.gercekci
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from backtest.kosu import Cikis, getiri, KOMISYON
from sistem import geri_al as G
from sistem import sinyal as S
from sistem import yuksek as Y

DATA = Path(__file__).parent / "data"
NY_TZ = "America/New_York"
ALTI = [0.0, 0.5, 1.0]            # seviyenin % kaç altına inmeli (trade-through)
KAYMA = [0.0, 0.5, 1.0, 2.0]       # gidiş-dönüş ek kayma %
CIK = {"+5": Cikis("+5"), "yarı +5 / yarı +20": Cikis("y", yari=False, hedef=20)}


def main() -> None:
    gunluk = pd.read_pickle(DATA / "gunluk.pkl")
    prev = {(r.symbol, str(r.day)): r.prev_close for r in gunluk.itertuples()}
    satir = []
    for f in sorted((DATA / "dk").glob("*.pkl")):
        day = f.stem
        df = pd.read_pickle(f)
        gun_bas = int(pd.Timestamp(f"{day} 04:00", tz=NY_TZ).timestamp() // 60)
        seans_son = int(pd.Timestamp(f"{day} 16:00", tz=NY_TZ).timestamp() // 60)
        for sym, d in df.groupby("symbol", sort=False):
            pc = prev.get((sym, day))
            if not pc or pd.isna(pc) or len(d) < S.EMA_LEN:
                continue
            dks = (d["timestamp"].astype("int64").to_numpy() // 60_000_000).astype(np.int64)
            o, h, l, c, v = (d[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
            g = S.gostergeler(o, h, l, c, v)
            son = int(np.searchsorted(dks, seans_son))
            vw = np.full(len(c), np.nan)
            for i in Y.yuksek_sinyalleri(dks, o, h, l, c, v, g, pc, gun_bas):
                if (c[i] / pc - 1) * 100 > 20:
                    continue
                seviye = float(c[i]) * (1 - G.GERI / 100)
                for alti in ALTI:
                    m = (dks > dks[i]) & (dks - dks[i] <= G.PENCERE_DK) & (l <= seviye * (1 - alti / 100))
                    k = np.where(m)[0]
                    if not len(k) or k[0] >= son:
                        continue
                    k0 = int(k[0])
                    giris = float(min(seviye, o[k0]))
                    r5 = getiri(Cikis("a"), k0, giris, h[:son], c[:son], vw[:son])
                    r20 = getiri(Cikis("b", hedef=20), k0, giris, h[:son], c[:son], vw[:son])
                    satir.append({"gun": day, "alti": alti, "+5": r5, "yarı +5 / yarı +20": (r5 + r20) / 2})
    d = pd.DataFrame(satir)
    d["hafta"] = pd.to_datetime(d.gun).dt.isocalendar().week
    print("dolum kuralı                kayma | işlem | +5 hedef ort (+hafta) | yarı +5/+20 ort (+hafta)")
    for alti in ALTI:
        a = d[d.alti == alti]
        for kayma in KAYMA:
            parca = []
            for col in ("+5", "yarı +5 / yarı +20"):
                r = a[col] - kayma
                w = (a.assign(r=r)).groupby("hafta").r.mean()
                parca.append(f"{r.mean():+6.2f}% ({int((w > 0).sum())}/{len(w)})")
            ad = "değince" if alti == 0 else f"seviye −%{alti:g} altına inince"
            print(f"{ad:27s} %{kayma:<4g}| {len(a):5d} | {parca[0]:>21s} | {parca[1]:>22s}")


if __name__ == "__main__":
    main()
