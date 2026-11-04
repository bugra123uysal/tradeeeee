"""Yeni algoritma araması + sınav (4 Kas 2026) — backtest/aday_tam.py tablosu üzerinde.

Kural seçimi SADECE eğitimde (ilk %60 gün); sınav (son %40) seçimden sonra bir kez raporlanır.
Maliyet: dolum normal seansta (09:30–16:00) %0,7, premarket / seans sonrası %1,5 (geniş spread). Kısa (açığa satış)
için ek %0,5 (ödünç ücreti / bulunabilirlik) — sadece araştırma.
Her kural: o gün o hissede kuralı sağlayan İLK AL mumu (günde hisse başına en çok 1 sinyal, "yüksek" gibi).

    py -m backtest.algo_ara                       # varsayılan veri: data_tam
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

KOK = Path(__file__).parent
MALIYET_RTH, MALIYET_UZ, KISA_EK = 0.7, 1.5, 0.5
EGITIM_ORAN = 0.6
MIN_ISLEM = 150


@dataclass(frozen=True)
class Kural:
    gun_min: float = 5
    gun_max: float = 40
    mom_min: float = 2
    hacim_min: float = 2
    bbw_min: float = 3
    vwap_ustu: bool = True
    macd: bool = True
    giris: str = "g5"
    cikis: str = "5_8"
    seans: str = "s16"
    yon: str = "uzun"

    @property
    def sutun(self) -> str:
        return f"{'k' if self.yon == 'kisa' else 'r_' + self.giris}_{self.cikis}_{self.seans}"

    def ad(self) -> str:
        return (f"{self.yon} · gün {self.gun_min:g}–{self.gun_max:g} · mom≥{self.mom_min:g} · hacim≥{self.hacim_min:g}× · "
                f"bbw≥{self.bbw_min:g}{' · VWAP üstü' if self.vwap_ustu else ''}{' · MACD+' if self.macd else ''} · "
                f"{self.giris if self.yon == 'uzun' else 'hemen sat'} · çıkış {self.cikis} · {self.seans}")


def sinyaller(d: pd.DataFrame, k: Kural) -> pd.DataFrame:
    m = (d.gun >= k.gun_min) & (d.gun <= k.gun_max) & (d.mom5 >= k.mom_min) & (d.hacim_kat >= k.hacim_min) & (d.bbw >= k.bbw_min)
    if k.vwap_ustu:
        m &= d.vwap_uz > 0
    if k.macd:
        m &= d.hist_pos & d.hist_art
    return d[m].sort_values("dk").groupby(["gun_tarih", "sym"], sort=False).head(1)


def islemler(d: pd.DataFrame, k: Kural) -> pd.DataFrame:
    s = sinyaller(d, k)
    giris = "hemen" if k.yon == "kisa" else k.giris
    s = s[s[f"d_{giris}"] == 1] if f"d_{giris}" in s else s.iloc[:0]
    dolum_saat = s.saat + s[f"dk_{giris}"] / 60
    rth = (dolum_saat >= 9.5) & (dolum_saat < 16)
    maliyet = np.where(rth, MALIYET_RTH, MALIYET_UZ) + (KISA_EK if k.yon == "kisa" else 0)
    return s.assign(R=s[k.sutun] - maliyet, rth=rth).dropna(subset=["R"])


def ozet(t: pd.DataFrame) -> dict:
    if not len(t):
        return {"n": 0}
    w = t.R.groupby(pd.to_datetime(t.gun_tarih).dt.strftime("%G-%V").values).mean()
    ay = t.R.groupby(t.gun_tarih.str[:7]).mean()
    kirp = t.R.clip(upper=t.R.quantile(0.99))           # birkaç dev koşu ortalamayı şişirmesin (seçim için)
    return {"n": len(t), "ort": t.R.mean(), "kirpik": kirp.mean(), "medyan": t.R.median(), "karli": (t.R > 0).mean(),
            "hafta+": (w > 0).mean(), "ay+": f"{int((ay > 0).sum())}/{len(ay)}", "toplam": t.R.sum(),
            "kotu5": t.R.nsmallest(max(1, len(t) // 20)).mean()}


def satir(ad: str, o: dict) -> str:
    if not o.get("n"):
        return f"  {ad:30s}    0 işlem"
    return (f"  {ad:30s} {o['n']:5d} işlem | ort {o['ort']:+6.2f}% | medyan {o['medyan']:+6.2f} | kârlı %{o['karli'] * 100:3.0f}"
            f" | kırpık {o['kirpik']:+5.2f} | + hafta %{o['hafta+'] * 100:3.0f} | + ay {o['ay+']} | en kötü %5 ort {o['kotu5']:+6.1f}")


def izgara() -> list[Kural]:
    uzun = [Kural(gun_min=a, gun_max=b, mom_min=m, hacim_min=hk, giris=g, cikis=c, seans=s)
            for a, b in ((5, 20), (5, 40), (10, 40), (20, 80))
            for m, hk in ((2, 2), (0, 1.5))
            for g in ("hemen", "g3", "g5", "g8", "g12")
            for c in ("5_8", "10_8", "20_8", "10_12", "20_12", "x_8")
            for s in ("s16", "s20")]
    kisa = [Kural(gun_min=a, gun_max=b, mom_min=m, hacim_min=hk, yon="kisa", cikis=c, seans=s)
            for a, b in ((20, 80), (30, 80), (10, 40))
            for m, hk in ((2, 2), (0, 1.5))
            for c in ("5_8", "10_8", "10_15", "20_15", "x_15")
            for s in ("s16", "s20")]
    return uzun + kisa


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--veri", default="data_tam")
    ap.add_argument("--en-iyi", type=int, default=12)
    a = ap.parse_args()
    d = pd.read_pickle(KOK / "sonuc" / f"aday_tam_{a.veri}.pkl")
    gunler = sorted(d.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * EGITIM_ORAN)]
    egitim, sinav = d[d.gun_tarih < kesim], d[d.gun_tarih >= kesim]
    s = [f"YENİ ALGORİTMA ARAMASI · veri {a.veri} · {gunler[0]} – {gunler[-1]} ({len(gunler)} gün)",
         f"eğitim < {kesim} ≤ sınav · maliyet RTH %{MALIYET_RTH:g} / uzatılmış seans %{MALIYET_UZ:g} (kısa +%{KISA_EK:g})", ""]

    sim = Kural(gun_max=20)
    s += ["ŞU ANKİ 'yüksek' (gün ≤ 20, g5, +5/−8, 16:00)",
          satir("eğitim", ozet(islemler(egitim, sim))), satir("sınav", ozet(islemler(sinav, sim))), ""]

    sonuc = []
    for k in izgara():
        o = ozet(islemler(egitim, k))
        if o.get("n", 0) >= MIN_ISLEM:
            sonuc.append((o["kirpik"] * min(1.0, o["hafta+"] / 0.6), k, o))
    sonuc.sort(key=lambda x: -x[0])
    for yon in ("uzun", "kisa"):
        en_iyi = [x for x in sonuc if x[1].yon == yon][:a.en_iyi]
        s += [f"EĞİTİMDE EN İYİ {len(en_iyi)} — {yon.upper()} (seçim: en iyi %1 kırpılmış ort × hafta tutarlılığı)", "-" * 60]
        for _, k, o in en_iyi:
            s += [k.ad(), satir("eğitim", o), satir("SINAV", ozet(islemler(sinav, k)))]
        s.append("")
    metin = "\n".join(s)
    (KOK / "sonuc" / f"algo_ara_{a.veri}.txt").write_text(metin, encoding="utf-8")
    print(metin)


if __name__ == "__main__":
    main()
