"""CHSN örneği (3 Kas 2026): 'yüksek' sinyali → geri çekilme alışı → +%5 hedef geldi, ama hisse alıştan
+%260 gitti. Soru: (1) böyle büyük koşular sinyal/dolum anında ayırt edilebiliyor mu,
(2) kârı koşturan çıkış kuralları +%5 hedeften ortalamada iyi mi?

Giriş canlıdakiyle aynı: sistem/yuksek.py sinyali → 30 dk içinde −%5 limit alış. Çıkışlar dolum mumundan sonra,
kapanmış mumlarla (ileriye bakmadan). Komisyon gidiş-dönüş %0,2. Seans sonu (16:00) kapanışında kapatılır.

    py -m backtest.kosu     (pandas → sistem python)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from sistem import geri_al as G
from sistem import sinyal as S
from sistem import yuksek as Y

DATA = Path(__file__).parent / "data"
SONUC = Path(__file__).parent / "sonuc"
KOMISYON = 0.2
NY_TZ = "America/New_York"
KOSU_ESIK = 30.0       # dolumdan gün içi tepe ≥ %30 → "büyük koşu"


@dataclass(frozen=True)
class Cikis:
    ad: str
    hedef: float | None = 5.0       # yüksek ≥ dolum × (1+hedef) → hedeften çık
    stop: float = 8.0               # kapanış < dolum × (1−stop)
    iz: float | None = None         # kapanış < tepe kapanış × (1−iz) → çık (sadece kârdayken / stop yerine)
    yari: bool = False              # yarısı hedefte, yarısı iz ile
    vwap: bool = False              # kapanış seans VWAP altı → çık (dolumdan sonra VWAP üstüne çıkmışsa)


CIKISLAR = [
    Cikis("+%5 hedef / −%8 stop (şu anki)"),
    Cikis("+%10 hedef / −%8 stop", hedef=10),
    Cikis("+%20 hedef / −%8 stop", hedef=20),
    Cikis("iz %10 (tepe kapanıştan)", hedef=None, iz=10),
    Cikis("iz %15", hedef=None, iz=15),
    Cikis("iz %20", hedef=None, iz=20),
    Cikis("yarısı +%5, yarısı iz %15", yari=True, iz=15),
    Cikis("yarısı +%5, yarısı iz %20", yari=True, iz=20),
    Cikis("VWAP altı kapanış / −%8 stop", hedef=None, vwap=True),
    Cikis("yarısı +%5, yarısı VWAP altı", yari=True, vwap=True),
]


def _koş(x: Cikis, k0: int, giris: float, h, c, vw, hedef_aktif: bool) -> float:
    """Tek bacak getirisi (%), dolum mumu k0. hedef_aktif=False → hedef yok (iz/vwap/stop/gün sonu)."""
    tepe, vwap_ustu = c[k0], c[k0] > vw[k0]
    for k in range(k0, len(c)):
        if hedef_aktif and x.hedef and k > k0 and h[k] >= giris * (1 + x.hedef / 100):
            return x.hedef
        if c[k] < giris * (1 - x.stop / 100):
            return (c[k] / giris - 1) * 100
        tepe = max(tepe, c[k])
        vwap_ustu = vwap_ustu or c[k] > vw[k]
        if x.iz and k > k0 and c[k] < tepe * (1 - x.iz / 100) and tepe > giris:
            return (c[k] / giris - 1) * 100
        if x.vwap and k > k0 and vwap_ustu and c[k] < vw[k]:
            return (c[k] / giris - 1) * 100
    return (c[-1] / giris - 1) * 100


def getiri(x: Cikis, k0: int, giris: float, h, c, vw) -> float:
    if x.yari:
        a = _koş(Cikis("", hedef=5.0, stop=x.stop), k0, giris, h, c, vw, True)
        b = _koş(Cikis("", hedef=None, stop=x.stop, iz=x.iz, vwap=x.vwap), k0, giris, h, c, vw, False)
        return (a + b) / 2 - KOMISYON
    return _koş(x, k0, giris, h, c, vw, x.hedef is not None) - KOMISYON


def islemler() -> pd.DataFrame:
    gunluk = pd.read_pickle(DATA / "gunluk.pkl")
    prev = {(r.symbol, str(r.day)): r.prev_close for r in gunluk.itertuples()}
    flt = pd.read_pickle(DATA / "float.pkl")
    out = []
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
            bas = int(np.searchsorted(dks, gun_bas))
            vw = np.full(len(c), np.nan)
            vw[bas:] = np.cumsum(c[bas:] * v[bas:]) / np.maximum(np.cumsum(v[bas:]), 1e-9)
            for i in Y.yuksek_sinyalleri(dks, o, h, l, c, v, g, pc, gun_bas):
                x = G.GeriSinyal(sym, "", "YÜKSEK", int(dks[i]), float(c[i]), 0.0,
                                 seviye=float(c[i]) * (1 - G.GERI / 100))
                G.degerlendir(x, dks, o, h, l, c)
                if x.dolum_fiyat is None:
                    continue
                son = int(np.searchsorted(dks, seans_son))           # 16:00 sonrası yok
                k0 = int(np.where(dks == x.dolum_dk)[0][0])
                if k0 >= son:
                    continue
                hh, cc, vv = h[:son], c[:son], vw[:son]
                giris = x.dolum_fiyat
                kayit = {"gun_tarih": day, "sym": sym, "fiyat": c[i], "gun": (c[i] / pc - 1) * 100,
                         "float_m": flt.get(sym, np.nan) / 1e6 if flt.get(sym) else np.nan,
                         "saat": pd.Timestamp(int(dks[i]) * 60, unit="s", tz="UTC").tz_convert(NY_TZ).hour,
                         "dolar_kum": float((c[bas:i + 1] * v[bas:i + 1]).sum()),
                         "tepe": (hh[k0:].max() / giris - 1) * 100}
                for cx in CIKISLAR:
                    kayit[cx.ad] = getiri(cx, k0, giris, hh, cc, vv)
                out.append(kayit)
    return pd.DataFrame(out)


def rapor(d: pd.DataFrame) -> str:
    d = d.assign(hafta=pd.to_datetime(d.gun_tarih).dt.isocalendar().week)
    gunler = sorted(d.gun_tarih.unique())
    orta = gunler[len(gunler) // 2 - 1]
    kosu = d.tepe >= KOSU_ESIK
    s = [f"'yüksek' + geri çekilme alışı — çıkış kuralları · {gunler[0]} – {gunler[-1]} · {len(d)} dolan işlem",
         f"büyük koşu (dolumdan gün içi tepe ≥ %{KOSU_ESIK:g}): {int(kosu.sum())} işlem (%{kosu.mean() * 100:.0f})", ""]
    for baslik, alt in (("TÜM DOLUMLAR", d), ("SİNYALDE GÜN ≥ %10 (yanlılıksız)", d[d.gun >= 10]),
                        ("GÜN ≤ %20 FİLTRESİYLE (sitedeki)", d[d.gun <= 20])):
        s += [baslik, "-" * len(baslik)]
        for cx in CIKISLAR:
            r = alt[cx.ad]
            w = alt.groupby("hafta")[cx.ad].mean()
            y1, y2 = r[alt.gun_tarih <= orta].mean(), r[alt.gun_tarih > orta].mean()
            s.append(f"{cx.ad:34s} ort {r.mean():+6.2f}% | kârlı %{(r > 0).mean() * 100:3.0f} | yarılar {y1:+.2f} / {y2:+.2f}"
                     f" | + hafta {int((w > 0).sum())}/{len(w)} | koşularda ort {r[alt.tepe >= KOSU_ESIK].mean():+6.1f}%")
        s.append("")
    s += ["BÜYÜK KOŞU sinyal anında ayırt edilebiliyor mu? (koşu oranı, grup başına)", "-" * 40]
    for ad, grup in (("fiyat < 1 $", d.fiyat < 1), ("fiyat 1–3 $", d.fiyat.between(1, 3)), ("fiyat > 3 $", d.fiyat > 3),
                     ("gün ≤ %15", d.gun <= 15), ("gün %15–25", d.gun.between(15, 25)), ("gün > %25", d.gun > 25),
                     ("float < 5 M", d.float_m < 5), ("float 5–20 M", d.float_m.between(5, 20)), ("float > 20 M", d.float_m > 20),
                     ("premarket (saat < 9)", d.saat < 9), ("09–11", d.saat.between(9, 10)), ("11 sonrası", d.saat >= 11),
                     ("dolar hacmi < 250 bin", d.dolar_kum < 2.5e5), ("dolar hacmi ≥ 1 M", d.dolar_kum >= 1e6)):
        g = d[grup]
        s.append(f"{ad:24s} {len(g):4d} işlem · koşu %{(g.tepe >= KOSU_ESIK).mean() * 100:4.1f}")
    return "\n".join(s)


def main() -> None:
    d = islemler()
    SONUC.mkdir(exist_ok=True)
    d.to_csv(SONUC / "kosu_islemler.csv", index=False)
    metin = rapor(d)
    (SONUC / "kosu.txt").write_text(metin, encoding="utf-8")
    print(metin)


if __name__ == "__main__":
    main()
