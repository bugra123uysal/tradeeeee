"""Soluklanma kırılımı 1 yıl testi (7 Kas 2026) — data_tam, tüm hareketli hisseler, eğitim/sınav.

    py -m backtest.soluklanma_ara --hesapla    # dakika verisinden tüm kırılımlar → sonuc/soluklanma_sinyal.pkl
    py -m backtest.soluklanma_ara              # arama + rapor → sonuc/soluklanma_ara.txt
Kalıp ve çıkışlar: backtest/soluklanma.py. Aynı hissede aynı anda tek pozisyon; çıkıştan sonra yeni kırılım alınabilir
(günde en çok 3). NET % = brüt − maliyet (RTH %0,7 / uzatılmış seans %1,5).
"""
from __future__ import annotations

import argparse
import itertools
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import soluklanma as SL  # noqa: E402
from backtest.yuksek2_ara import HAVUZ  # noqa: E402

KOK = Path(__file__).parent
VERI = KOK / "data_tam"
SINYAL = KOK / "sonuc" / "soluklanma_sinyal.pkl"
RAPOR = KOK / "sonuc" / "soluklanma_ara.txt"
MALIYET_RTH, MALIYET_UZ = 0.7, 1.5
EGITIM_ORAN = 0.6
NLER = (10, 15, 20)
GEVSEK = {"r_max": 8.0, "i_min": 10.0, "hk": 1.5}
IZGARA = {"n": NLER, "r_max": (3.0, 5.0, 8.0), "i_min": (10.0, 20.0), "hk": (1.5, 2.0, 3.0),
          "saat_min": (4.0, 9.5, 11.5), "gun_max": (40.0, 80.0, 1000.0)}
GUNDE_EN_COK = 3
MIN_ISLEM = 150
OZEL = ("GOGO", "CHOW", "CHSN", "STAK", "SERV")


def _sym_isle(day: str, sym: str, g: pd.DataFrame, pc: float, t16: int) -> list[dict]:
    dks = g["timestamp"].astype("int64").to_numpy() // 60_000_000
    o, h, l, c, v = (g[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    vw = np.cumsum(c * v) / np.maximum(np.cumsum(v), 1e-9)
    gun = (c / pc - 1) * 100
    son = int(np.searchsorted(dks, t16))
    acilis = t16 - 390
    out = []
    for n in NLER:
        oz = SL.kutu_ozellikleri(h, l, v, n)
        for i in SL.kirilimlar(c, v, vw, gun, oz, **GEVSEK):
            k0 = i + 1
            if k0 >= son or dks[k0] - dks[i] > 5:
                continue
            r = {"gun_tarih": day, "sym": sym, "n": n, "dk": int(dks[i]), "saat": (int(dks[i]) - acilis) / 60 + 9.5,
                 "saat_giris": (int(dks[k0]) - acilis) / 60 + 9.5, "fiyat": float(c[i]), "gun": float(gun[i]),
                 "kutu_r": float(oz["kutu_r"][i]), "direk": float(oz["direk"][i]), "hk": float(v[i] / oz["vort"][i]),
                 "tepe": (h[k0:son].max() / o[k0] - 1) * 100}
            for tur in SL.CIKISLAR:
                r[f"r_{tur}"], k = SL.cikis(o, h, c, k0, son, float(oz["alt"][i]), tur)
                r[f"cik_{tur}"] = int(dks[k])
            out.append(r)
    return out


def gun_isle(arg: tuple[str, str, dict]) -> list[dict]:
    yol, day, prev = arg
    df = pd.read_pickle(yol)
    if df.empty:
        return []
    t16 = int(pd.Timestamp(f"{day} 16:00", tz="America/New_York").timestamp() // 60)
    out = []
    for sym, g in df.groupby("symbol", sort=False, observed=True):
        pc = prev.get(sym)
        if pc and not np.isnan(pc) and len(g) > 60:
            out += _sym_isle(day, sym, g, pc, t16)
    return out


def hesapla(isci: int) -> None:
    gunluk = pd.read_pickle(VERI / "gunluk.pkl")
    prev: dict[str, dict] = {}
    for sym, day, pc in zip(gunluk.symbol, gunluk.day.astype(str), gunluk.prev_close):
        prev.setdefault(day, {})[sym] = pc
    isler = [(str(f), f.stem, prev.get(f.stem, {})) for f in sorted((VERI / "dk").glob("*.pkl"))]
    rows = []
    with ProcessPoolExecutor(isci) as ex:
        for k, r in enumerate(ex.map(gun_isle, isler), 1):
            rows.extend(r)
            if k % 25 == 0:
                print(f"{k}/{len(isler)} gün · {len(rows)} kırılım", flush=True)
    pd.DataFrame(rows).to_pickle(SINYAL)
    print(f"bitti: {len(rows)} kırılım → {SINYAL.name}")


def secim(s: pd.DataFrame, k: dict, cikis: str) -> pd.DataFrame:
    m = ((s.n == k["n"]) & (s.kutu_r <= k["r_max"]) & (s.direk >= k["i_min"]) & (s.hk >= k["hk"])
         & (s.saat >= k["saat_min"]) & (s.gun <= k["gun_max"]))
    x = s[m].sort_values(["gun_tarih", "sym", "dk"])
    tut, son_cik, say, anahtar = [], -1, 0, None
    for r in x[["gun_tarih", "sym", "dk", f"cik_{cikis}"]].itertuples():
        if (r.gun_tarih, r.sym) != anahtar:
            anahtar, son_cik, say = (r.gun_tarih, r.sym), -1, 0
        if r.dk <= son_cik or say >= GUNDE_EN_COK:
            continue
        tut.append(r.Index)
        son_cik, say = r[4], say + 1
    t = x.loc[tut]
    rth = (t.saat_giris >= 9.5) & (t.saat_giris < 16)
    return t.assign(R=t[f"r_{cikis}"] - np.where(rth, MALIYET_RTH, MALIYET_UZ))


def ozet(t: pd.DataFrame) -> dict:
    if not len(t):
        return {"n": 0}
    ay = t.groupby(pd.to_datetime(t.gun_tarih).dt.to_period("M")).R.mean()
    return {"n": len(t), "ort": t.R.mean(), "med": t.R.median(), "karli": (t.R > 0).mean() * 100,
            "ay_arti": int((ay > 0).sum()), "ay": len(ay), "top": t.R.sum(), "en_iyi": t.R.max()}


def satir(ad: str, o: dict) -> str:
    if not o.get("n"):
        return f"  {ad:<8} n=0"
    return (f"  {ad:<8} n={o['n']:>5}  ort {o['ort']:+6.2f}  medyan {o['med']:+6.2f}  kârlı %{o['karli']:3.0f}  "
            f"artı ay {o['ay_arti']}/{o['ay']}  toplam {o['top']:+8.0f}  en iyi {o['en_iyi']:+6.0f}")


def kural_ad(k: dict, cikis: str) -> str:
    gm = "sınırsız" if k["gun_max"] >= 1000 else f"≤ %{k['gun_max']:g}"
    return (f"kutu {k['n']} mum ≤ %{k['r_max']:g} · direk ≥ %{k['i_min']:g} · hacim ≥ {k['hk']:g}× · saat ≥ {k['saat_min']:g} · "
            f"gün {gm} · çıkış {cikis}")


def gri_sonrasi(s: pd.DataFrame, k: dict, cikis: str) -> pd.DataFrame:
    """yüksek (TV varsayılanı) sinyal verdi, −%5 geri çekilme DOLMADI (sitede gri) → aynı gün sonraki soluklanma kırılımı."""
    p = pd.read_pickle(HAVUZ)
    y = p[(p.gun >= 5) & (p.gun <= 40)].sort_values("dk").groupby(["gun_tarih", "sym"], sort=False).head(1)
    gri = y[y.d_g5 != 1][["gun_tarih", "sym", "dk"]].rename(columns={"dk": "y_dk"})
    t = secim(s, k, cikis).merge(gri, on=["gun_tarih", "sym"])
    return t[t.dk > t.y_dk]


def ara(en_iyi: int) -> str:
    s = pd.read_pickle(SINYAL)
    gunler = sorted(s.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * EGITIM_ORAN)]
    eg, sn = s[s.gun_tarih < kesim], s[s.gun_tarih >= kesim]
    out = [f"SOLUKLANMA KIRILIMI · {gunler[0]} – {gunler[-1]} ({len(gunler)} gün) · {len(s)} ham kırılım",
           f"eğitim < {kesim} ≤ sınav · NET % · aynı hissede tek pozisyon, günde en çok {GUNDE_EN_COK}", ""]
    sonuc = []
    anahtar = list(IZGARA)
    for deg in itertools.product(*IZGARA.values()):
        k = dict(zip(anahtar, deg))
        for cikis in SL.CIKISLAR:
            o = ozet(secim(eg, k, cikis))
            if o["n"] >= MIN_ISLEM:
                sonuc.append((o["ort"], o, k, cikis))
    sonuc.sort(key=lambda x: -x[0])
    saglam = [x for x in sonuc if x[1]["ay_arti"] >= 0.75 * x[1]["ay"]]
    out.append(f"denenen {len(sonuc)} · eğitimde aylık istikrarlı {len(saglam)}")
    out.append(f"== EĞİTİMDE EN İYİ {en_iyi} (istikrarlı) → SINAV ==")
    for _, o, k, cikis in saglam[:en_iyi]:
        out += [kural_ad(k, cikis), satir("eğitim", o), satir("SINAV", ozet(secim(sn, k, cikis)))]
    out.append("")

    taban = {"n": 10, "r_max": 5.0, "i_min": 10.0, "hk": 2.0, "saat_min": 4.0, "gun_max": 1000.0}
    out.append("== GOGO'yu yakalayan temel ayar (kutu 10 mum ≤ %5 · direk ≥ %10 · hacim ≥ 2×), çıkışlara göre ==")
    for cikis in SL.CIKISLAR:
        out += [f" çıkış {cikis}", satir("eğitim", ozet(secim(eg, taban, cikis))), satir("sınav", ozet(secim(sn, taban, cikis)))]
    out.append("")
    out.append("== Saate göre (temel ayar, kutu_iz) ==")
    t = secim(s, taban, "kutu_iz")
    for ad, a, b in (("premarket", 0, 9.5), ("09:30–10:30", 9.5, 10.5), ("10:30–11:30", 10.5, 11.5), ("11:30–16:00", 11.5, 16)):
        x = t[(t.saat >= a) & (t.saat < b)]
        out += [f" {ad}", satir("eğitim", ozet(x[x.gun_tarih < kesim])), satir("sınav", ozet(x[x.gun_tarih >= kesim]))]
    out.append("")
    out.append("== 'yüksek' sinyali verdi, geri çekilme olmadı (gri) → sonra soluklanma kırılımı (temel ayar) ==")
    for cikis in SL.CIKISLAR:
        g = gri_sonrasi(s, taban, cikis)
        out += [f" çıkış {cikis}", satir("eğitim", ozet(g[g.gun_tarih < kesim])), satir("sınav", ozet(g[g.gun_tarih >= kesim]))]
    out.append("")
    out.append("== Fotoğraftaki hisseler (temel ayar, kutu_iz, tüm yıl) ==")
    x = t[t.sym.isin(OZEL)]
    out += [f"  {r.gun_tarih} {r.sym:<5} {int(r.saat):02d}:{round(r.saat % 1 * 60):02d} gün %{r.gun:5.1f} "
            f"kutu %{r.kutu_r:3.1f} direk %{r.direk:4.0f} → {r.R:+6.1f}" for r in x.itertuples()] or ["  (yok)"]
    return "\n".join(out)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hesapla", action="store_true")
    ap.add_argument("--is", dest="isci", type=int, default=10)
    ap.add_argument("--en-iyi", type=int, default=12)
    a = ap.parse_args()
    if a.hesapla:
        hesapla(a.isci)
        return
    txt = ara(a.en_iyi)
    RAPOR.write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
