"""yüksek_2 araştırması (7 Kas 2026) — koşan hisselere (CHSN +%248, STAK, CHOW, SERV gibi) girebilmek.

Mevcut 'yüksek'in iki kusuru (data_tam, 1 yıl):
  * −%5 geri çekilme alışı sinyallerin ~%77'sinde hiç dolmuyor → en güçlü koşular kaçıyor.
  * +%5 hedef koşuyu kesiyor, −%8 kapanış stopu ilk sallantıda çıkarıyor (CHSN 3 Kas: tepe +%248, sonuç −%11,6).

Burada:
  1. Havuz = yüksek çekirdeği (Swing AL + VWAP üstü + MACD hist pozitif/artan + 5 mum ivme ≥ %2 + hacim ≥ 2× + BBW ≥ %3),
     gün değişimi %5–80 (üst sınır geniş). Kaynak: sonuc/aday_tam_data_tam.pkl.
  2. Her aday için dakika verisinden YENİ giriş/çıkış sonuçları (BRÜT %):
       giriş  hemen = sonraki mum açılışı · kir = sinyal mumunun tepesi 15 dk içinde kırılırsa (stop-alış,
              dolum = max(açılış, tepe)) — geri çekilmeyi beklemez, koşan hisseye girer
       çıkış  ilk stop −S% (1 dk kapanış) + iz süren stop: kapanış, girişten beri görülen en yüksek fiyatın T% altına
              inerse sat · seans sonu 16:00 ("s16") veya 20:00 ("s20").
  3. Kural araması SADECE eğitimde (ilk %60 gün), sınav bir kez raporlanır. Maliyet algo_ara ile aynı.

    py -m backtest.yuksek2_ara --hesapla      # sonuçları hesapla (bir kez, ~birkaç dk)
    py -m backtest.yuksek2_ara                # ara + rapor → sonuc/yuksek2_ara.txt
"""
from __future__ import annotations

import argparse
import itertools
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

KOK = Path(__file__).parent
VERI = KOK / "data_tam"
HAVUZ = KOK / "sonuc" / "yuksek2_havuz.pkl"
RAPOR = KOK / "sonuc" / "yuksek2_ara.txt"
MALIYET_RTH, MALIYET_UZ = 0.7, 1.5
EGITIM_ORAN = 0.6
KIR_PENCERE = 15
CIKISLAR = ((8, 10), (10, 15), (12, 15), (12, 20), (15, 20), (15, 25))     # (ilk stop %, iz stop %)
SEANSLAR = {"s16": "16:00", "s20": "20:00"}
OZEL = ("CHOW", "CHSN", "STAK", "SERV")
MIN_ISLEM = 200


def havuz_maskesi(d: pd.DataFrame) -> pd.Series:
    return ((d.gun >= 5) & (d.gun <= 80) & (d.vwap_uz > 0) & d.hist_pos & d.hist_art
            & (d.mom5 >= 2) & (d.hacim_kat >= 2) & (d.bbw >= 3))


def iz_stop(c: np.ndarray, h: np.ndarray, giris: float, stop: float, iz: float) -> tuple[float, float]:
    """Girişten (index 0) sonra: kapanış < giriş·(1−stop) veya kapanış < tepe·(1−iz) → o kapanışta sat; yoksa son kapanış.
    Tepe = o ana kadarki en yüksek 'high' (giriş mumu dahil). Döner: (sonuç %, tepe %)."""
    if not len(c):
        return np.nan, np.nan
    tepe = np.maximum.accumulate(np.maximum(h, giris))
    cik = (c < giris * (1 - stop / 100)) | (c < tepe * (1 - iz / 100))
    k = int(np.argmax(cik)) if cik.any() else len(c) - 1
    return (c[k] / giris - 1) * 100, (h.max() / giris - 1) * 100


def giris_bul(i: int, dks, o, h, son: int, tur: str) -> tuple[int, float] | None:
    if tur == "hemen":
        k0 = i + 1
        if k0 >= son or dks[k0] - dks[i] > 5:
            return None
        return k0, float(o[k0])
    seviye = h[i]
    j = np.arange(i + 1, son)
    m = (dks[j] - dks[i] <= KIR_PENCERE) & (h[j] > seviye)
    if not m.any():
        return None
    k0 = int(j[np.argmax(m)])
    return k0, float(max(o[k0], seviye))


def gun_isle(arg: tuple[str, str, pd.DataFrame]) -> list[dict]:
    yol, day, adaylar = arg
    df = pd.read_pickle(yol)
    t = lambda hm: int(pd.Timestamp(f"{day} {hm}", tz="America/New_York").timestamp() // 60)
    sonlar_dk = {k: t(v) for k, v in SEANSLAR.items()}
    out = []
    gruplar = {s: g for s, g in df.groupby("symbol", sort=False, observed=True)}
    for r in adaylar.itertuples():
        g = gruplar.get(r.sym)
        if g is None:
            continue
        dks = g["timestamp"].astype("int64").to_numpy() // 60_000_000
        o, h, c = (g[k].to_numpy(float) for k in ("open", "high", "close"))
        i = int(np.searchsorted(dks, r.dk))
        if i >= len(dks) or dks[i] != r.dk:
            continue
        sonlar = {k: int(np.searchsorted(dks, x)) for k, x in sonlar_dk.items()}
        sat = {"idx": r.Index}
        for tur in ("hemen", "kir"):
            gb = giris_bul(i, dks, o, h, sonlar["s20"], tur)
            if gb is None:
                continue
            k0, giris = gb
            sat[f"d_{tur}"] = 1
            sat[f"saat_{tur}"] = r.saat + (dks[k0] - dks[i]) / 60
            for sn, son in sonlar.items():
                if k0 >= son:
                    continue
                for st, iz in CIKISLAR:
                    sat[f"r_{tur}_{st}_{iz}_{sn}"], sat[f"tp_{tur}_{sn}"] = iz_stop(c[k0:son], h[k0:son], giris, st, iz)
        out.append(sat)
    return out


def hesapla(isci: int) -> None:
    d = pd.read_pickle(KOK / "sonuc" / "aday_tam_data_tam.pkl")
    sutunlar = ["gun_tarih", "sym", "dk", "saat", "fiyat", "gun", "mom5", "mom30", "vwap_uz", "hacim_kat", "bbw", "adx",
                "hod_uz", "gun_tepe", "dolar_kum", "dolar_30", "rvol", "e50_uz", "atr_y", "kirmizi5",
                "tepe_hemen_s16", "r_g5_5_8_s16", "d_g5", "dk_g5"]
    p = d.loc[havuz_maskesi(d), sutunlar].copy()
    del d
    isler = [(str(VERI / "dk" / f"{gun}.pkl"), gun, g) for gun, g in p.groupby("gun_tarih")]
    rows = []
    with ProcessPoolExecutor(isci) as ex:
        for n, r in enumerate(ex.map(gun_isle, isler), 1):
            rows.extend(r)
            if n % 25 == 0:
                print(f"{n}/{len(isler)} gün", flush=True)
    s = pd.DataFrame(rows).set_index("idx")
    p.join(s).to_pickle(HAVUZ)
    print(f"bitti: {len(p)} aday → {HAVUZ.name}")


# ── arama ──
KURAL_IZGARA = {
    "gun": ((5, 20), (5, 40), (5, 80), (10, 80), (15, 80)),
    "saat_min": (4.0, 9.5, 10.0, 11.5),
    "rvol_min": (0.0, 0.5, 1.0, 2.0),
    "hacim_min": (2.0, 3.0, 5.0),
    "dolar30_min": (0.0, 250e3, 1e6),
    "hod_min": (-100.0, -3.0),
}


def secim(p: pd.DataFrame, kural: dict) -> pd.DataFrame:
    m = ((p.gun >= kural["gun"][0]) & (p.gun <= kural["gun"][1]) & (p.saat >= kural["saat_min"])
         & (p.rvol.fillna(0) >= kural["rvol_min"]) & (p.hacim_kat >= kural["hacim_min"])
         & (p.dolar_30 >= kural["dolar30_min"]) & (p.hod_uz >= kural["hod_min"]))
    return p[m].sort_values("dk").groupby(["gun_tarih", "sym"], sort=False).head(1)


def islem(s: pd.DataFrame, giris: str, cikis: str) -> pd.DataFrame:
    sutun = f"r_{giris}_{cikis}"
    if giris == "g5":                               # mevcut yüksek (karşılaştırma)
        s = s[s.d_g5 == 1]
        saat = s.saat + s.dk_g5 / 60
        r = s.r_g5_5_8_s16
    else:
        s = s[s[f"d_{giris}"] == 1]
        saat, r = s[f"saat_{giris}"], s[sutun]
    rth = (saat >= 9.5) & (saat < 16)
    return s.assign(R=r - np.where(rth, MALIYET_RTH, MALIYET_UZ)).dropna(subset=["R"])


def ozet(t: pd.DataFrame) -> dict:
    if not len(t):
        return {"n": 0}
    ay = t.groupby(pd.to_datetime(t.gun_tarih).dt.to_period("M")).R.mean()
    return {"n": len(t), "ort": t.R.mean(), "med": t.R.median(), "karli": (t.R > 0).mean() * 100,
            "ay_arti": (ay > 0).sum(), "ay": len(ay), "top": t.R.sum(), "en_iyi": t.R.max(),
            "kosu": (t.tepe_hemen_s16 >= 30).mean() * 100}


def satir(ad: str, o: dict) -> str:
    if not o.get("n"):
        return f"  {ad:<8} n=0"
    return (f"  {ad:<8} n={o['n']:>5}  ort {o['ort']:+6.2f}  medyan {o['med']:+6.2f}  kârlı %{o['karli']:3.0f}  "
            f"artı ay {o['ay_arti']}/{o['ay']}  toplam {o['top']:+8.0f}  en iyi {o['en_iyi']:+6.0f}  koşu≥30 %{o['kosu']:4.1f}")


def kural_ad(k: dict, giris: str, cikis: str) -> str:
    return (f"gün {k['gun'][0]}–{k['gun'][1]} · saat ≥ {k['saat_min']:g} · rvol ≥ {k['rvol_min']:g} · hacim ≥ {k['hacim_min']:g}× · "
            f"30dk $ ≥ {k['dolar30_min'] / 1e3:g}K · gün tepesine ≥ {k['hod_min']:g}% · giriş {giris} · çıkış {cikis}")


def ozel_hisseler(p: pd.DataFrame, k: dict, giris: str, cikis: str) -> list[str]:
    s = islem(secim(p, k), giris, cikis)
    x = s[s.sym.isin(OZEL)]
    return [f"    {r.gun_tarih} {r.sym:<5} {int(r.saat):02d}:{round(r.saat % 1 * 60):02d}  gün %{r.gun:5.1f}  "
            f"tepe +{r.tepe_hemen_s16:6.1f}  sonuç {r.R:+6.1f}" for r in x.itertuples()] or ["    (yok)"]


def ara(en_iyi: int) -> str:
    p = pd.read_pickle(HAVUZ)
    gunler = sorted(p.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * EGITIM_ORAN)]
    eg, sn = p[p.gun_tarih < kesim], p[p.gun_tarih >= kesim]
    out = [f"yüksek_2 ARAMASI · {gunler[0]} – {gunler[-1]} ({len(gunler)} gün) · havuz {len(p)} aday",
           f"eğitim < {kesim} ≤ sınav · NET % (maliyet RTH %{MALIYET_RTH:g} / uzatılmış %{MALIYET_UZ:g})",
           "koşu≥30 = sinyalden sonra fiyat 16:00'a kadar en az +%30 gördü", ""]

    yuk = {"gun": (5, 20), "saat_min": 4.0, "rvol_min": 0.0, "hacim_min": 2.0, "dolar30_min": 0.0, "hod_min": -100.0}
    out += ["ŞU ANKİ 'yüksek' (gün 5–20, −%5 geri çekilme, +5/−8, 16:00)",
            satir("eğitim", ozet(islem(secim(eg, yuk), "g5", ""))), satir("sınav", ozet(islem(secim(sn, yuk), "g5", ""))), ""]

    cikislar = [f"{st}_{iz}_{s}" for st, iz in CIKISLAR for s in SEANSLAR]
    sonuc = []
    anahtar = list(KURAL_IZGARA)
    for degerler in itertools.product(*KURAL_IZGARA.values()):
        k = dict(zip(anahtar, degerler))
        s_eg = secim(eg, k)
        if len(s_eg) < MIN_ISLEM:
            continue
        for giris in ("hemen", "kir"):
            for cikis in cikislar:
                o = ozet(islem(s_eg, giris, cikis))
                if o["n"] >= MIN_ISLEM:
                    sonuc.append((o["ort"], o, k, giris, cikis))
    sonuc.sort(key=lambda x: -x[0])

    # aşırı uyumu azalt: eğitimde aylık istikrar şartı (ayların ≥ %75'i artı)
    saglam = [x for x in sonuc if x[1]["ay_arti"] >= 0.75 * x[1]["ay"]]
    out.append(f"denenen {len(sonuc)} kural×giriş×çıkış · eğitimde aylık istikrarlı {len(saglam)}")
    out.append(f"== EĞİTİMDE EN İYİ {en_iyi} (istikrarlı) → SINAV ==")
    for _, o, k, giris, cikis in saglam[:en_iyi]:
        out += [kural_ad(k, giris, cikis), satir("eğitim", o), satir("SINAV", ozet(islem(secim(sn, k), giris, cikis)))]
    out.append("")

    out.append("== Fotoğraftaki hisseler (TÜM YIL) — en iyi kuralda ==")
    if saglam:
        _, _, k, giris, cikis = saglam[0]
        out += ozel_hisseler(p, k, giris, cikis)
    out += ["", *profil(p, kesim)]
    return "\n".join(out)


PROFIL_OZELLIK = {"gun": "gün değişimi %", "saat": "saat (NY)", "rvol": "gün hacmi / 20g ort.", "hacim_kat": "mum hacmi / 30 mum",
                  "dolar_30": "son 30 dk $ hacim", "mom5": "5 mum ivme %", "mom30": "30 mum ivme %", "vwap_uz": "VWAP uzaklık %",
                  "hod_uz": "gün tepesine uzaklık %", "adx": "ADX", "bbw": "BB genişlik %", "e50_uz": "EMA50 uzaklık %",
                  "atr_y": "ATR %", "kirmizi5": "son 5 mumda kırmızı", "fiyat": "fiyat $"}


def profil(p: pd.DataFrame, kesim) -> list[str]:
    """TÜM hisseler: günün ilk havuz sinyalinde, sonradan +%30 koşanlar ile koşmayanlar neyle ayrışıyor?
    Her özellik 5 dilime bölünür; dilim başına koşu oranı eğitim ve sınavda ayrı (iki dönemde de aynı yöndeyse güvenilir)."""
    s = p.sort_values("dk").groupby(["gun_tarih", "sym"], sort=False).head(1).dropna(subset=["tepe_hemen_s16"])
    s = s.assign(kosu=s.tepe_hemen_s16 >= 30, egitim=s.gun_tarih < kesim)
    out = [f"== KOŞAN HİSSELERİN PROFİLİ — tüm hisseler, günün ilk sinyali (n={len(s)}, koşu≥30 oranı "
           f"%{s.kosu.mean() * 100:.1f}) ==",
           "  her özellik 5 eşit dilim · koşu oranı % (eğitim / sınav) · medyan koşan vs koşmayan"]
    for ozl, ad in PROFIL_OZELLIK.items():
        x = s.dropna(subset=[ozl])
        try:
            dilim = pd.qcut(x[ozl], 5, duplicates="drop")
        except ValueError:
            continue
        oran = x.groupby([dilim, "egitim"], observed=True).kosu.mean().unstack() * 100
        hucre = " | ".join(f"{iv.left:.3g}–{iv.right:.3g}: {r.get(True, np.nan):4.1f}/{r.get(False, np.nan):4.1f}"
                           for iv, r in oran.iterrows())
        med = x.groupby("kosu")[ozl].median()
        out.append(f"  {ad:<24} koşan {med.get(True, np.nan):10.3g} · koşmayan {med.get(False, np.nan):10.3g}")
        out.append(f"      {hucre}")
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hesapla", action="store_true")
    ap.add_argument("--is", dest="isci", type=int, default=10)
    ap.add_argument("--en-iyi", type=int, default=15)
    a = ap.parse_args()
    if a.hesapla:
        hesapla(a.isci)
        return
    txt = ara(a.en_iyi)
    RAPOR.write_text(txt, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
