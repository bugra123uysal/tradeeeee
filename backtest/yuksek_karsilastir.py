"""yüksek ↔ yüksek_2 hisse hisse karşılaştırma (7 Kas 2026) — data_tam, 1 yıl, NET % (maliyet düşülmüş).

  yüksek   = TV'deki varsayılanlar: gün %5–40 · saat sınırı yok · hacim ≥ 2× · −%5 geri çekilme · +5/−8 · 16:00
  yüksek_2 = gün %5–20 · 11:30–16:00 · hacim ≥ 3× · aynı giriş/çıkış
Kaynak: backtest/yuksek2_ara.py havuzu (önce `py -m backtest.yuksek2_ara --hesapla`).

    py -m backtest.yuksek_karsilastir   → sonuc/yuksek_karsilastir.txt
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest.yuksek2_ara import EGITIM_ORAN, HAVUZ, OZEL, islem, ozet, satir, secim  # noqa: E402

KOK = Path(__file__).parent
RAPOR = KOK / "sonuc" / "yuksek_karsilastir.txt"
KURALLAR = {
    "yüksek": {"gun": (5, 40), "saat_min": 0.0, "rvol_min": 0.0, "hacim_min": 2.0, "dolar30_min": 0.0, "hod_min": -100.0},
    "yüksek_2": {"gun": (5, 20), "saat_min": 11.5, "rvol_min": 0.0, "hacim_min": 3.0, "dolar30_min": 0.0, "hod_min": -100.0},
}
EN_COK = 30


def islemler(p: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {ad: islem(secim(p, k), "g5", "") for ad, k in KURALLAR.items()}


def saat(x: float) -> str:
    return f"{int(x):02d}:{round(x % 1 * 60):02d}"


def hisse_tablosu(t1: pd.DataFrame, t2: pd.DataFrame) -> pd.DataFrame:
    a = t1.groupby("sym").R.agg(n1="size", top1="sum", ort1="mean")
    b = t2.groupby("sym").R.agg(n2="size", top2="sum", ort2="mean")
    return a.join(b, how="outer").fillna({"n1": 0, "top1": 0, "n2": 0, "top2": 0})


def rapor() -> str:
    p = pd.read_pickle(HAVUZ)
    gunler = sorted(p.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * EGITIM_ORAN)]
    t = islemler(p)
    t1, t2 = t["yüksek"], t["yüksek_2"]
    out = [f"yüksek ↔ yüksek_2 · {gunler[0]} – {gunler[-1]} ({len(gunler)} gün) · NET % · eğitim < {kesim} ≤ sınav", ""]

    out.append("== GENEL ==")
    for ad, x in t.items():
        out += [ad, satir("tüm yıl", ozet(x)), satir("eğitim", ozet(x[x.gun_tarih < kesim])),
                satir("sınav", ozet(x[x.gun_tarih >= kesim]))]
    out.append("")

    out.append("== AY AY (ort % · işlem) ==")
    ay = lambda x: x.groupby(pd.to_datetime(x.gun_tarih).dt.to_period("M")).R.agg(["mean", "size"])
    a1, a2 = ay(t1), ay(t2)
    for m in a1.index.union(a2.index):
        r1 = a1.loc[m] if m in a1.index else None
        r2 = a2.loc[m] if m in a2.index else None
        f = lambda r: f"{r['mean']:+6.2f} ({int(r['size']):>3})" if r is not None else "     —      "
        out.append(f"  {m}   yüksek {f(r1)}   yüksek_2 {f(r2)}")
    out.append("")

    h = hisse_tablosu(t1, t2)
    ikisi = h[(h.n1 > 0) & (h.n2 > 0)]
    out += ["== HİSSE HİSSE ==",
            f"  yüksek {int((h.n1 > 0).sum())} hissede, yüksek_2 {int((h.n2 > 0).sum())} hissede işlem açtı · ikisinde de olan {len(ikisi)}",
            f"  ortak hisselerde yüksek_2 toplamı daha iyi: {int((ikisi.top2 > ikisi.top1).sum())} / {len(ikisi)} hisse",
            f"  hisse başına toplam artıda: yüksek %{(h[h.n1 > 0].top1 > 0).mean() * 100:.0f} · "
            f"yüksek_2 %{(h[h.n2 > 0].top2 > 0).mean() * 100:.0f}", ""]
    for baslik, sirala in (("EN ÇOK İŞLEM (yüksek)", "n1"), ("EN ÇOK İŞLEM (yüksek_2)", "n2")):
        out.append(f"  {baslik} — ilk {EN_COK}")
        out.append(f"    {'hisse':<6} {'yüksek n':>8} {'toplam':>8} {'ort':>6} │ {'yüksek_2 n':>10} {'toplam':>8} {'ort':>6}")
        for sym, r in h.sort_values(sirala, ascending=False).head(EN_COK).iterrows():
            o2 = f"{r.ort2:+6.2f}" if r.n2 else "     —"
            o1 = f"{r.ort1:+6.2f}" if r.n1 else "     —"
            out.append(f"    {sym:<6} {int(r.n1):>8} {r.top1:+8.1f} {o1} │ {int(r.n2):>10} {r.top2:+8.1f} {o2}")
        out.append("")

    out.append("== FOTOĞRAFTAKİ HİSSELER (CHOW · CHSN · STAK · SERV) — tüm yıl, her işlem ==")
    for sym in OZEL:
        out.append(f"  {sym}")
        for ad, x in t.items():
            xs = x[x.sym == sym].sort_values(["gun_tarih", "dk"])
            satirlar = [f"{r.gun_tarih} {saat(r.saat)} gün %{r.gun:4.1f} → {r.R:+5.1f}" for r in xs.itertuples()]
            out.append(f"    {ad:<9} {len(xs)} işlem · toplam {xs.R.sum():+6.1f}")
            out += [f"      {s}" for s in satirlar]
    out.append("")

    ayni = t1.merge(t2[["gun_tarih", "sym"]], on=["gun_tarih", "sym"])
    sadece1 = t1.merge(t2[["gun_tarih", "sym"]], on=["gun_tarih", "sym"], how="left", indicator=True)
    sadece1 = sadece1[sadece1._merge == "left_only"]
    out += ["== yüksek_2 NEYİ ELİYOR? (yüksek işlemleri) ==",
            satir("ikisi de", ozet(ayni)), satir("elenen", ozet(sadece1))]
    for ad, m in (("sabah", sadece1.saat < 11.5), ("gün>20", sadece1.gun > 20), ("hacim<3×", sadece1.hacim_kat < 3)):
        out.append(satir(ad, ozet(sadece1[m])))
    return "\n".join(out)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    txt = rapor()
    RAPOR.write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
