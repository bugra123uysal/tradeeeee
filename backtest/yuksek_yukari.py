"""yüksek sinyali → izlemeye al → yukarı devam ederse GİR (7–8 Kas 2026, kullanıcı fikri: "gri kartlar").

yüksek'in gerçek sinyalleri (TV varsayılanı gün %5–40, günün ilk sinyali; data_tam 1 yıl). Sinyalden sonra 30 dk:
  * YUKARI: yüksek ≥ sinyal kapanışı × (1 + X%) olan ilk mum → stop-alış, dolum = max(açılış, seviye)
  * GERİ   : düşük ≤ sinyal kapanışı × 0,95 (mevcut kural, karşılaştırma için)
"İlk gelen" mantığı: hangisi önce olursa o işlem (ikisi aynı mumda → GERİ sayılır, temkinli).
Çıkışlar (16:00'da kapanır): 5_8 (+5 / −8 kapanış), yari (yarısı +5, kalanı +20, −8 kapanış), iz10 (−8 stop +
tepenin %10 altında kapanış), x8 (sadece −8 stop, hedef yok). NET % = brüt − maliyet (RTH %0,7 / uzatılmış %1,5).

    py -m backtest.yuksek_yukari   → sonuc/yuksek_yukari.txt
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest.yuksek2_ara import EGITIM_ORAN, HAVUZ, secim  # noqa: E402

KOK = Path(__file__).parent
VERI = KOK / "data_tam"
RAPOR = KOK / "sonuc" / "yuksek_yukari.txt"
YUKSEK = {"gun": (5, 40), "saat_min": 0.0, "rvol_min": 0.0, "hacim_min": 2.0, "dolar30_min": 0.0, "hod_min": -100.0}
ESIKLER = (1.0, 3.0, 5.0)
PENCERE = 30
GERI = 5.0
CIKISLAR = ("5_8", "yari", "iz10", "x8")
MALIYET_RTH, MALIYET_UZ = 0.7, 1.5


def cikis(h: np.ndarray, c: np.ndarray, giris: float, tur: str) -> float:
    """h, c: giriş mumundan seans sonuna. Giriş mumunda hedefe bakılmaz (sadece kapanış stopu)."""
    if not len(c):
        return np.nan
    stop = giris * 0.92
    tepe = giris
    yari = False
    for k in range(len(c)):
        tepe = max(tepe, h[k])
        if k > 0 and tur == "5_8" and h[k] >= giris * 1.05:
            return 5.0
        if k > 0 and tur == "yari":
            if not yari and h[k] >= giris * 1.05:
                yari = True
            if yari and h[k] >= giris * 1.20:
                return 12.5
        if c[k] < stop or (tur == "iz10" and c[k] < tepe * 0.90):
            r = (c[k] / giris - 1) * 100
            return (5.0 + r) / 2 if yari else r
    r = (c[-1] / giris - 1) * 100
    return (5.0 + r) / 2 if yari else r


def ilk_olay(dks, o, h, l, i: int, son: int, esik: float, sc: float) -> tuple[str, int, float] | None:
    """Sinyal i'den sonra PENCERE dk içinde ilk gelen: ('geri' | 'yukari', mum, dolum fiyatı)."""
    geri, yuk = sc * (1 - GERI / 100), sc * (1 + esik / 100)
    for j in range(i + 1, son):
        if dks[j] - dks[i] > PENCERE:
            return None
        if l[j] <= geri:
            return "geri", j, float(min(geri, o[j]))
        if h[j] >= yuk:
            return "yukari", j, float(max(yuk, o[j]))
    return None



def gun_isle(arg: tuple[str, str, pd.DataFrame]) -> list[dict]:
    yol, day, t = arg
    df = pd.read_pickle(yol)
    t16 = int(pd.Timestamp(f"{day} 16:00", tz="America/New_York").timestamp() // 60)
    acilis = t16 - 390
    gruplar = {s: g for s, g in df.groupby("symbol", sort=False, observed=True)}
    out = []
    for r in t.itertuples():
        g = gruplar.get(r.sym)
        if g is None:
            continue
        dks = g["timestamp"].astype("int64").to_numpy() // 60_000_000
        o, h, l, c = (g[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        i = int(np.searchsorted(dks, r.dk))
        son = int(np.searchsorted(dks, t16))
        if i >= len(dks) or dks[i] != r.dk or i + 1 >= son:
            continue
        satir = {"idx": r.Index}
        for esik in ESIKLER:
            ol = ilk_olay(dks, o, h, l, i, son, esik, float(c[i]))
            if ol is None:
                continue
            tur, k0, giris = ol
            satir[f"olay_{esik:g}"] = tur
            satir[f"saat_{esik:g}"] = (dks[k0] - acilis) / 60 + 9.5
            for cx in CIKISLAR:
                satir[f"r_{esik:g}_{cx}"] = cikis(h[k0:son], c[k0:son], giris, cx)
        out.append(satir)
    return out


def islemler() -> pd.DataFrame:
    p = pd.read_pickle(HAVUZ)
    t = secim(p, YUKSEK)
    isler = [(str(VERI / "dk" / f"{gun}.pkl"), gun, g) for gun, g in t.groupby("gun_tarih")]
    with ProcessPoolExecutor(10) as ex:
        rows = [x for r in ex.map(gun_isle, isler) for x in r]
    return t.join(pd.DataFrame(rows).set_index("idx"))


def net(x: pd.DataFrame, esik: float, cx: str) -> pd.Series:
    s = x[f"saat_{esik:g}"]
    rth = (s >= 9.5) & (s < 16)
    return x[f"r_{esik:g}_{cx}"] - np.where(rth, MALIYET_RTH, MALIYET_UZ)


def satir(ad: str, x: pd.DataFrame, esik: float, cx: str, kesim) -> str:
    r = net(x, esik, cx).dropna()
    if not len(r):
        return f"  {ad:<44} n=0"
    eg, sn = r[x.loc[r.index, "gun_tarih"] < kesim], r[x.loc[r.index, "gun_tarih"] >= kesim]
    ay = r.groupby(pd.to_datetime(x.loc[r.index, "gun_tarih"]).dt.to_period("M")).mean()
    return (f"  {ad:<44} n={len(r):>5}  eğitim {eg.mean():+6.2f}  sınav {sn.mean():+6.2f}  kârlı %{(r > 0).mean() * 100:3.0f}"
            f"  artı ay {(ay > 0).sum()}/{len(ay)}  toplam {r.sum():+7.0f}")


def rapor(t: pd.DataFrame) -> str:
    gunler = sorted(t.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * EGITIM_ORAN)]
    out = [f"yüksek → İZLE → YUKARI DEVAM EDERSE GİR · {gunler[0]} – {gunler[-1]} · {len(t)} yüksek sinyali",
           f"eğitim < {kesim} ≤ sınav · NET % · ilk gelen olay (geri −%5 / yukarı +X%) 30 dk içinde", ""]
    for esik in ESIKLER:
        o = t[f"olay_{esik:g}"]
        out.append(f"== YUKARI eşiği +%{esik:g}: ilk olay yukarı {int((o == 'yukari').sum())} · geri {int((o == 'geri').sum())}"
                   f" · hiçbiri {int(o.isna().sum())} ==")
        yuk = t[o == "yukari"]
        for cx in CIKISLAR:
            out.append(satir(f"YUKARI +%{esik:g} gir · çıkış {cx}", yuk, esik, cx, kesim))
        for ad, m in (("  └ sinyal ≥ 11:30", yuk.saat >= 11.5), ("  └ sinyal < 11:30", yuk.saat < 11.5),
                      ("  └ 11:30+ ve gün ≤ 20", (yuk.saat >= 11.5) & (yuk.gun <= 20))):
            out.append(satir(ad + " · çıkış yari", yuk[m], esik, "yari", kesim))
        out.append(satir(f"karşılaştırma: GERİ −%5 (ilk olay geri) · 5_8", t[o == "geri"], esik, "5_8", kesim))
        ikisi = t[o.notna()]
        out.append(satir("İKİSİ BİRDEN (hangisi önce) · yari", ikisi, esik, "yari", kesim))
        out.append("")
    return "\n".join(out)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    t = islemler()
    t.to_pickle(KOK / "sonuc" / "yuksek_yukari.pkl")
    txt = rapor(t)
    RAPOR.write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
