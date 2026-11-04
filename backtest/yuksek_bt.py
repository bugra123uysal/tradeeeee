"""ANA BACKTEST — 'yüksek' + geri çekilme alışı, GERÇEKÇİ varsayımlarla (4 Kas 2026). Bundan sonra kararlar bununla.

Varsayılanlar (backtest/gercekci.py stres testinden):
  * Limit alış ancak düşük ≤ seviye × (1 − DOLUM_ALTI%) olursa dolar ("değince" değil, altına inince).
  * Her işlemden gidiş-dönüş KOMISYON + KAYMA düşülür (spread / kötü dolum için).
  * Sinyalde gün ≤ Y.FILTRE_GUN_MAX (sitedeki filtre). Seans 16:00'da kapatılır.
Çıkışlar: +%5 hedef / −%8 kapanış stopu (şu anki) ve yarısı +%5 / yarısı +%20.
Rapor: toplam, aylık, ilk %60 / son %40 (zaman sırasıyla — kurallar ilk kısma bakılarak seçildiyse son kısım
"görülmemiş veri" sayılır), gün ≥ %10 alt kümesi.

    py -m backtest.yuksek_bt                     # backtest/data (43 gün, eski seçimli veri)
    py -m backtest.yuksek_bt --veri data_genis   # 1 yıl, seçimsiz veri (backtest/veri_genis.py)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.kosu import Cikis, getiri
from sistem import geri_al as G
from sistem import sinyal as S
from sistem import yuksek as Y

KOK = Path(__file__).parent
NY_TZ = "America/New_York"
DOLUM_ALTI = 0.5      # %
KAYMA = 0.5           # % gidiş-dönüş (komisyon getiri() içinde ayrıca %0,2)
CIKISLAR = {"+5 / −8": Cikis("a"), "+20 / −8": Cikis("b", hedef=20)}


def islemler(veri: Path, dolum_alti: float) -> pd.DataFrame:
    gunluk = pd.read_pickle(veri / "gunluk.pkl")
    prev = {(r.symbol, str(r.day)): r.prev_close for r in gunluk.itertuples()}
    out = []
    for f in sorted((veri / "dk").glob("*.pkl")):
        day = f.stem
        df = pd.read_pickle(f)
        if df.empty:
            continue
        gun_bas = int(pd.Timestamp(f"{day} 04:00", tz=NY_TZ).timestamp() // 60)
        seans_son = int(pd.Timestamp(f"{day} 16:00", tz=NY_TZ).timestamp() // 60)
        for sym, d in df.groupby("symbol", sort=False, observed=True):
            pc = prev.get((sym, day))
            if not pc or pd.isna(pc) or len(d) < S.EMA_LEN:
                continue
            dks = (d["timestamp"].astype("int64").to_numpy() // 60_000_000).astype(np.int64)
            o, h, l, c, v = (d[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
            g = S.gostergeler(o, h, l, c, v)
            son = int(np.searchsorted(dks, seans_son))
            vw = np.full(len(c), np.nan)
            for i in Y.yuksek_sinyalleri(dks, o, h, l, c, v, g, pc, gun_bas):
                gun = (c[i] / pc - 1) * 100
                seviye = float(c[i]) * (1 - G.GERI / 100)
                m = (dks > dks[i]) & (dks - dks[i] <= G.PENCERE_DK) & (l <= seviye * (1 - dolum_alti / 100))
                k = np.where(m)[0]
                kayit = {"gun_tarih": day, "sym": sym, "gun": gun, "fiyat": float(c[i]), "doldu": False}
                if len(k) and k[0] < son:
                    k0 = int(k[0])
                    giris = float(min(seviye, o[k0]))
                    kayit["doldu"] = True
                    kayit["tepe"] = (h[k0:son].max() / giris - 1) * 100
                    for ad, cx in CIKISLAR.items():
                        kayit[ad] = getiri(cx, k0, giris, h[:son], c[:son], vw[:son])
                out.append(kayit)
    d = pd.DataFrame(out)
    if "+5 / −8" in d:
        d["yarı +5 / yarı +20"] = (d["+5 / −8"] + d["+20 / −8"]) / 2
    return d


def _satir(ad: str, r: pd.Series, gunler: pd.Series) -> str:
    if not len(r):
        return f"  {ad:22s}   0 işlem"
    hafta = pd.to_datetime(gunler).dt.strftime("%G-%V")
    w = r.groupby(hafta.values).mean()
    return (f"  {ad:22s} {len(r):5d} işlem | ort {r.mean():+6.2f}% | medyan {r.median():+6.2f}% | kârlı %{(r > 0).mean() * 100:3.0f}"
            f" | + hafta {int((w > 0).sum())}/{len(w)} | toplam {r.sum():+8.1f}%")


def rapor(d: pd.DataFrame, kayma: float, baslik: str) -> str:
    gunler = sorted(d.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * 0.6)]
    cikislar = ["+5 / −8", "yarı +5 / yarı +20", "+20 / −8"]
    s = [baslik, f"{gunler[0]} – {gunler[-1]} · {len(gunler)} gün · {len(d)} sinyal · "
                 f"dolum: seviye −%{DOLUM_ALTI:g} altı · kayma %{kayma:g} + komisyon %0,2", ""]
    gruplar = [("TÜMÜ", d), ("gün ≤ %20 (sitedeki filtre)", d[d.gun <= (Y.FILTRE_GUN_MAX or 1e9)]),
               ("gün > %20 (ALMA denenler)", d[d.gun > (Y.FILTRE_GUN_MAX or 1e9)]),
               ("gün ≥ %10 ve ≤ %20", d[(d.gun >= 10) & (d.gun <= (Y.FILTRE_GUN_MAX or 1e9))])]
    for ad, a in gruplar:
        a = a[a.doldu]
        s.append(f"{ad} — dolan {len(a)}")
        for cx in cikislar:
            s.append(_satir(cx, a[cx] - kayma, a.gun_tarih))
        once, sonra = a[a.gun_tarih < kesim], a[a.gun_tarih >= kesim]
        s.append(f"  ilk %60 / son %40 (+5): {(once['+5 / −8'] - kayma).mean():+.2f}% / {(sonra['+5 / −8'] - kayma).mean():+.2f}%"
                 f" · (yarı/yarı): {(once['yarı +5 / yarı +20'] - kayma).mean():+.2f}% / "
                 f"{(sonra['yarı +5 / yarı +20'] - kayma).mean():+.2f}%")
        s.append("")
    a = d[d.doldu & (d.gun <= (Y.FILTRE_GUN_MAX or 1e9))]
    s.append("AYLIK (gün ≤ %20, +5 / yarı-yarı)")
    for ay, x in a.groupby(a.gun_tarih.str[:7]):
        s.append(f"  {ay}: {len(x):4d} işlem · {(x['+5 / −8'] - kayma).mean():+6.2f}% · "
                 f"{(x['yarı +5 / yarı +20'] - kayma).mean():+6.2f}%")
    return "\n".join(s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--veri", default="data", help="backtest altındaki veri klasörü (data / data_genis)")
    ap.add_argument("--kayma", type=float, default=KAYMA)
    a = ap.parse_args()
    d = islemler(KOK / a.veri, DOLUM_ALTI)
    (KOK / "sonuc").mkdir(exist_ok=True)
    d.to_csv(KOK / "sonuc" / f"yuksek_bt_{a.veri}.csv", index=False)
    metin = rapor(d, a.kayma, f"'yüksek' + geri çekilme — gerçekçi backtest · veri: {a.veri}")
    (KOK / "sonuc" / f"yuksek_bt_{a.veri}.txt").write_text(metin, encoding="utf-8")
    print(metin)


if __name__ == "__main__":
    main()
