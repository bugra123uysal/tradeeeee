"""EDGAR dosyaları gün içi sonucu ayırt ediyor mu? (6 Kas 2026)

Aday = yükselme-adayları tarayıcısı (data_tam, 1 yıl): premarket son fiyatı önceki kapanışa göre > %10 ·
premarket hacmi > 100 bin · float ≤ 20 M · önceki kapanış ≥ 1 $.
Sonuçlar (BRÜT %):
  * acilis_kapanis : 09:30 açılış → 16:00 kapanış (gap tutuldu mu)
  * kirilim        : 09:35 sonrası 1 dk kapanış premarket tepesini geçer + hacim ≥ 2× önceki 5 mum →
                     sonraki mum açılışında al; +%10 hedef / −%8 stop (1 dk kapanış) / 16:00
EDGAR (sadece 09:30'dan ÖNCE kabul edilen dosyalar — ileriye bakış yok): son 30 gün kayıt/ihraç/13D-G/Form 4/8-K,
ve son 24 saatte (önceki gün 09:30 → bugün 09:30) 8-K / ihraç / kayıt.

    py -m backtest.edgar_bt
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sistem import edgar as E  # noqa: E402

log = logging.getLogger("edgar_bt")
KOK = Path(__file__).parent
VERI = KOK / "data_tam"
SINAV = "2026-06-11"
PM_DEGISIM, PM_HACIM, MAX_FLOAT, MIN_FIYAT = 10.0, 100_000, 20e6, 1.0
HEDEF, STOP, HACIM_KAT, HACIM_ONCEKI = 10.0, 8.0, 2.0, 5


def _dk(ts: pd.Series) -> np.ndarray:
    return (ts.dt.hour * 60 + ts.dt.minute).to_numpy()


def kirilim(dks, o, h, c, v, pm_tepe: float) -> float:
    seans = np.flatnonzero((dks >= 9 * 60 + 35) & (dks < 16 * 60))
    for k in seans[c[seans] > pm_tepe]:
        onceki = v[max(0, k - HACIM_ONCEKI):k]
        if not len(onceki) or onceki.mean() <= 0 or v[k] < HACIM_KAT * onceki.mean():
            continue
        son = seans[-1]
        if k + 1 > son:
            return np.nan
        giris = o[k + 1]
        cc, hh = c[k + 1:son + 1], h[k + 1:son + 1]
        st = np.flatnonzero(cc < giris * (1 - STOP / 100))
        hd = np.flatnonzero(hh >= giris * (1 + HEDEF / 100))
        k_st = st[0] if len(st) else len(cc)
        k_hd = hd[0] if len(hd) else len(cc)
        if k_hd < len(cc) and k_hd <= k_st:
            return HEDEF
        return (cc[min(k_st, len(cc) - 1)] / giris - 1) * 100
    return np.nan                                   # kırılım yok → işlem yok


def adaylar() -> pd.DataFrame:
    gunluk = pd.read_pickle(VERI / "gunluk.pkl")
    onceki = gunluk.set_index(["symbol", "day"])["prev_close"]
    flt = pd.read_pickle(VERI / "float.pkl")
    satir = []
    for p in sorted((VERI / "dk").glob("*.pkl")):
        gun = p.stem
        d = pd.read_pickle(p)
        d["dk"] = _dk(d["timestamp"])
        for sym, g in d.groupby("symbol", sort=False):
            pc = onceki.get((sym, pd.Timestamp(gun).date()), onceki.get((sym, gun)))
            if pc is None or not pc or np.isnan(pc) or pc < MIN_FIYAT or flt.get(sym, np.inf) > MAX_FLOAT:
                continue
            pm = g[g.dk < 570]
            if pm.empty or pm.volume.sum() <= PM_HACIM:
                continue
            pm_deg = (pm.close.iloc[-1] / pc - 1) * 100
            if pm_deg <= PM_DEGISIM:
                continue
            s = g[(g.dk >= 570) & (g.dk < 960)]
            if s.empty:
                continue
            dks, o, h, c, v = (g.dk.to_numpy(), g.open.to_numpy(), g.high.to_numpy(), g.close.to_numpy(),
                               g.volume.to_numpy().astype(float))
            satir.append({"gun": gun, "sym": sym, "onceki": pc, "pm_deg": pm_deg, "pm_hacim": pm.volume.sum(),
                          "float_m": flt.get(sym, np.nan) / 1e6,
                          "acilis_kapanis": (s.close.iloc[-1] / s.open.iloc[0] - 1) * 100,
                          "kirilim": kirilim(dks, o, h, c, v, pm.high.max())})
        log.info("%s: toplam %d aday", gun, len(satir))
    return pd.DataFrame(satir)


def edgar_ekle(df: pd.DataFrame, ed: E.Edgar) -> pd.DataFrame:
    kayitlar = {}
    for i, sym in enumerate(sorted(df.sym.unique())):
        try:
            kayitlar[sym] = ed.hisse(sym, eski_sayfalar=True) if ed.cik(sym) else None
        except Exception as e:                      # noqa: BLE001 — tek hisse hatası testi durdurmasın
            log.warning("%s: %s", sym, e)
            kayitlar[sym] = None
        if i % 100 == 0:
            log.info("EDGAR %d/%d", i, df.sym.nunique())
    rows = []
    for r in df.itertuples():
        lst = kayitlar.get(r.sym)
        an = datetime.combine(pd.Timestamp(r.gun).date(), time(9, 30), E.NY)
        if lst is None:
            rows.append({"cik_var": False})
            continue
        o30, o1 = E.ozet(lst, an, 30), E.ozet(lst, an, 1)
        o180 = E.ozet(lst, an, 180)
        rows.append({"cik_var": True, **{f"{k}30": v for k, v in o30.items()},
                     **{f"{k}1": v for k, v in o1.items()}, "kayit180": o180["kayit"], "ihrac180": o180["ihrac"]})
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def _satir(ad: str, x: pd.Series) -> str:
    x = x.dropna()
    if not len(x):
        return f"  {ad:<44} n=0"
    return (f"  {ad:<44} n={len(x):>4}  ort {x.mean():+6.2f}  medyan {x.median():+6.2f}  "
            f"kârlı %{(x > 0).mean() * 100:4.0f}")


def rapor(df: pd.DataFrame) -> str:
    d = df[df.cik_var].copy()
    gruplar = {
        "HEPSİ (CIK bulunan)": d.index == d.index,
        "30 günde kayıt (S-1/S-3/F-1/F-3) VAR": d.kayit30 > 0,
        "30 günde kayıt YOK": d.kayit30 == 0,
        "30 günde ihraç (424B) VAR": d.ihrac30 > 0,
        "30 günde ihraç YOK": d.ihrac30 == 0,
        "180 günde kayıt veya ihraç VAR": (d.kayit180 + d.ihrac180) > 0,
        "180 günde kayıt ve ihraç YOK (temiz)": (d.kayit180 + d.ihrac180) == 0,
        "son 24 saatte 424B (ihraç)": d.ihrac1 > 0,
        "son 24 saatte 8-K": d.olay1 > 0,
        "son 24 saatte hiç dosya yok": (d.olay1 + d.ihrac1 + d.kayit1) == 0,
        "30 günde 13D/13G VAR": d.buyuk_ortak30 > 0,
        "30 günde 13D/13G YOK": d.buyuk_ortak30 == 0,
        "30 günde Form 4 VAR": d.iceriden30 > 0,
        "30 günde Form 4 YOK": d.iceriden30 == 0,
    }
    out = [f"EDGAR geçmiş testi — {df.gun.min()} … {df.gun.max()} · aday {len(df)} · EDGAR'da bulunan {len(d)}",
           f"(eğitim < {SINAV} ≤ sınav; getiriler brüt %, maliyet düşülmedi)", ""]
    for sonuc, aciklama in (("acilis_kapanis", "09:30 açılıştan al → 16:00 sat"),
                            ("kirilim", "premarket tepesi kırılımı + hacim ≥2× → +10/−8")):
        for donem, m in (("TÜM YIL", d.index == d.index), ("EĞİTİM", d.gun < SINAV), ("SINAV", d.gun >= SINAV)):
            out.append(f"== {aciklama} · {donem} ==")
            out += [_satir(ad, d.loc[g & m, sonuc]) for ad, g in gruplar.items()]
            out.append("")
    return "\n".join(out)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    load_dotenv(KOK.parent / ".env")
    ara = KOK / "sonuc" / "edgar_adaylar.pkl"
    df = pd.read_pickle(ara) if ara.exists() else adaylar()
    df.to_pickle(ara)
    df = edgar_ekle(df, E.Edgar(VERI / "edgar", ttl=None))
    df.to_csv(KOK / "sonuc" / "edgar_bt.csv", index=False)
    txt = rapor(df)
    (KOK / "sonuc" / "edgar_bt.txt").write_text(txt, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
