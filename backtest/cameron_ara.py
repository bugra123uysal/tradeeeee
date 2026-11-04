"""Cameron kalıbı (backtest/cameron_bt.py) — videolardaki filtrelerle değerlendirme, eğitim / sınav.

Maliyet: dolum normal seansta %0,7, uzatılmış seansta %1,5 (komisyon + spread + kayma). Alış stop emri kırılımda
dolduğu için gerçek kayma daha büyük olabilir.

    py -m backtest.cameron_ara --veri data_tam
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.algo_ara import ozet, satir

KOK = Path(__file__).parent

FILTRELER = {
    "fiyat 2–20 $": lambda d: d.fiyat.between(2, 20),
    "gün ≥ %10": lambda d: d.gun >= 10,
    "gün ≥ %30": lambda d: d.gun >= 30,
    "RVOL ≥ 5×": lambda d: d.rvol >= 5,
    "float < 20 M": lambda d: d.float_m < 20,
    "float < 10 M": lambda d: d.float_m < 10,
    "07:00–10:00": lambda d: d.saat.between(7, 10),
    "hedef(HOD) ≥ 2×risk": lambda d: d.hod_r >= 2,
    "risk ≤ %5": lambda d: d.risk_y <= 5,
}
CAMERON = ["fiyat 2–20 $", "gün ≥ %10", "RVOL ≥ 5×", "float < 20 M"]


def maliyetli(d: pd.DataFrame, sutun: str) -> pd.DataFrame:
    rth = d.saat.between(9.5, 16, inclusive="left")
    return d.assign(R=d[sutun] - np.where(rth, 0.7, 1.5)).dropna(subset=["R"])


def ilk_n(d: pd.DataFrame, n: int) -> pd.DataFrame:
    return d.sort_values("dk").groupby(["gun_tarih", "sym"], sort=False).head(n)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--veri", default="data_tam")
    a = ap.parse_args()
    d = pd.read_pickle(KOK / "sonuc" / f"cameron_{a.veri}.pkl")
    gunler = sorted(d.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * 0.6)]
    eg, sn = d[d.gun_tarih < kesim], d[d.gun_tarih >= kesim]
    s = [f"CAMERON 'ilk geri çekilme' · {a.veri} · {gunler[0]} – {gunler[-1]} · {len(d)} tetik · eğitim < {kesim} ≤ sınav", ""]

    def blok(baslik: str, maske_f, sutun: str = "2r_s16", n: int = 99) -> None:
        s.append(baslik)
        for ad, x in (("eğitim", eg), ("sınav", sn)):
            s.append(satir(ad, ozet(maliyetli(ilk_n(x[maske_f(x)], n), sutun))))

    hepsi = lambda x: np.ones(len(x), bool)
    tam = lambda x: np.logical_and.reduce([FILTRELER[f](x) for f in CAMERON])
    blok("TÜM tetikler (filtresiz), 2R", hepsi)
    for f in FILTRELER:
        blok(f"tek filtre: {f}, 2R", FILTRELER[f])
    s.append("")
    s.append("CAMERON 5 SÜTUN (fiyat 2–20, gün ≥10, RVOL ≥5, float <20M; haber hariç) — çıkış / saat / ilk işlem")
    for sutun in ("2r_s16", "2rI_s16", "ilkk_s16", "yari_s16", "yariI_s16", "2r_s20", "yari_s20"):
        blok(f"  çıkış {sutun}", tam, sutun)
    blok("  + 07:00–10:00, 2R", lambda x: tam(x) & FILTRELER["07:00–10:00"](x))
    blok("  + 07:00–10:00 + hissede ilk işlem, 2R", lambda x: tam(x) & FILTRELER["07:00–10:00"](x), n=1)
    blok("  + hedef ≥ 2×risk + 07–10, 2R", lambda x: tam(x) & FILTRELER["07:00–10:00"](x) & FILTRELER["hedef(HOD) ≥ 2×risk"](x))
    blok("  + gün ≥ 30 + float < 10M + 07–10 (en kaliteli), yarı", lambda x: tam(x) & FILTRELER["gün ≥ %30"](x)
         & FILTRELER["float < 10 M"](x) & FILTRELER["07:00–10:00"](x), "yari_s16")
    blok("  + gün ≥ 30 + float < 10M + 07–10 (en kaliteli), 2R", lambda x: tam(x) & FILTRELER["gün ≥ %30"](x)
         & FILTRELER["float < 10 M"](x) & FILTRELER["07:00–10:00"](x))
    metin = "\n".join(s)
    (KOK / "sonuc" / f"cameron_ara_{a.veri}.txt").write_text(metin, encoding="utf-8")
    print(metin)


if __name__ == "__main__":
    main()
