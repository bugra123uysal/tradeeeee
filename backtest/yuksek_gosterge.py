"""Kullanıcının önerdiği göstergeler yüksek işlemlerini ayırt ediyor mu? (8 Kas 2026)

İşlemler: backtest/yuksek_sonfiltre.py tablosu (yüksek'in −%5 geri çekilmesi dolan 6888 işlemi, 1 yıl, NET %).
Her işlem için iki anda ölçülür: SİNYAL mumu (s_) ve KARAR anı = dolumdan önceki mum (k_). 1 dk mumlar, gün 04:00'tan.
  Gator (Bill Williams): Alligator = medyan fiyatın SMMA'sı — çene 13 (8 ileri), diş 8 (5 ileri), dudak 5 (3 ileri).
        gator_genis = (|çene−diş| + |diş−dudak|) / fiyat %  ·  gator_acilan = iki çubuk da büyüyor (yeşil/yeşil)
        alligator_dizilim = dudak > diş > çene (yükseliş dizilimi)
  Magic Trend (CCI 20 + ATR 5 × 1): mt_mavi = CCI ≥ 0 (çizgi mavi), mt_ustu = kapanış > çizgi
  Kijun-sen (Ichimoku 26): kijun_ustu = kapanış > (26 mum tepe + dip)/2
  OBV: obv_egim = son 20 mumda OBV değişimi / son 20 mum hacmi (−1…+1) · obv_ema_ustu = OBV > EMA20(OBV)
  ATR(14) % = ATR / fiyat
  ZigZag (yeniden çizmeyen: 5 sol / 5 sağ mum pivotları, sadece onaylanmışlar): zz_yukselen = son dip > önceki dip
        VE son tepe > önceki tepe

    py -m backtest.yuksek_gosterge   → sonuc/yuksek_gosterge.txt
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest.yuksek2_ara import EGITIM_ORAN  # noqa: E402
from sistem import sinyal as S  # noqa: E402

KOK = Path(__file__).parent
VERI = KOK / "data_tam"
TABLO = KOK / "sonuc" / "yuksek_sonfiltre.pkl"
RAPOR = KOK / "sonuc" / "yuksek_gosterge.txt"
PIVOT = 5


def smma(x: np.ndarray, n: int) -> np.ndarray:
    return S._rma(x, n)


def kaydir(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[k:] = x[:len(x) - k]
    return out


def gator(h: np.ndarray, l: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    med = (h + l) / 2
    cene, dis, dudak = kaydir(smma(med, 13), 8), kaydir(smma(med, 8), 5), kaydir(smma(med, 5), 3)
    return np.abs(cene - dis), np.abs(dis - dudak), cene, dis, dudak


def magic_trend(h, l, c, cci_n: int = 20, atr_n: int = 5, kat: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    tp = c
    sma = pd.Series(tp).rolling(cci_n).mean().to_numpy()
    md = pd.Series(tp).rolling(cci_n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        cci = (tp - sma) / (0.015 * md)
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
    atr = pd.Series(tr).rolling(atr_n).mean().to_numpy()
    mt = np.zeros(len(c))
    for i in range(len(c)):
        onceki = mt[i - 1] if i else 0.0
        if np.isnan(cci[i]) or np.isnan(atr[i]):
            mt[i] = onceki
        elif cci[i] >= 0:
            yukari = l[i] - atr[i] * kat
            mt[i] = onceki if yukari < onceki else yukari
        else:
            asagi = h[i] + atr[i] * kat
            mt[i] = onceki if (asagi > onceki and onceki != 0) else asagi
    return mt, cci


def pivotlar(x: np.ndarray, tepe: bool, n: int = PIVOT) -> np.ndarray:
    """Onaylanmış pivot: i'de pivot varsa onay anı i+n. Döner: her mum için o ana kadar onaylanmış pivot indeksleri."""
    s = pd.Series(x)
    pencere = s.rolling(2 * n + 1, center=True)
    ekstrem = (pencere.max() if tepe else pencere.min()).to_numpy()
    return np.flatnonzero(x == ekstrem)


def zz_yukselen(h, l, j: int) -> float:
    """j anında onaylanmış (pivot + PIVOT ≤ j) son iki dip ve son iki tepe yükseliyor mu?"""
    ht = [p for p in pivotlar(h, True) if p + PIVOT <= j]
    lt = [p for p in pivotlar(l, False) if p + PIVOT <= j]
    if len(ht) < 2 or len(lt) < 2:
        return np.nan
    return float(l[lt[-1]] > l[lt[-2]] and h[ht[-1]] > h[ht[-2]])


def ozellikler(o, h, l, c, v, j: int, on: str) -> dict:
    gu, ga, cene, dis, dudak = gator(h, l)
    mt, cci = magic_trend(h, l, c)
    kij = (pd.Series(h).rolling(26).max() + pd.Series(l).rolling(26).min()).to_numpy() / 2
    obv = np.cumsum(np.sign(np.diff(c, prepend=c[0])) * v)
    obv_ema = S.ema(obv, 20)
    atr = S.dmi(h, l, c, 14)[3]
    v20 = v[max(0, j - 20):j].sum()
    return {f"{on}gator_genis": (gu[j] + ga[j]) / c[j] * 100,
            f"{on}gator_acilan": float(gu[j] > gu[j - 1] and ga[j] > ga[j - 1]),
            f"{on}alligator_dizilim": float(dudak[j] > dis[j] > cene[j]),
            f"{on}mt_mavi": float(cci[j] >= 0), f"{on}mt_ustu": float(c[j] > mt[j]),
            f"{on}kijun_ustu": float(c[j] > kij[j]),
            f"{on}obv_egim": (obv[j] - obv[max(0, j - 20)]) / v20 if v20 > 0 else np.nan,
            f"{on}obv_ema_ustu": float(obv[j] > obv_ema[j]),
            f"{on}atr_y": atr[j] / c[j] * 100, f"{on}zz_yukselen": zz_yukselen(h, l, j)}


def gun_isle(arg: tuple[str, pd.DataFrame]) -> list[dict]:
    yol, t = arg
    df = pd.read_pickle(yol)
    gruplar = {s: g for s, g in df.groupby("symbol", sort=False, observed=True)}
    out = []
    for r in t.itertuples():
        g = gruplar.get(r.sym)
        if g is None:
            continue
        dks = g["timestamp"].astype("int64").to_numpy() // 60_000_000
        o, h, l, c, v = (g[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        i = int(np.searchsorted(dks, r.dk))
        k0 = int(np.searchsorted(dks, r.dk + r.dk_g5))
        if i >= len(dks) or dks[i] != r.dk or k0 >= len(dks) or k0 <= i or i < 40:
            continue
        out.append({"idx": r.Index, **ozellikler(o, h, l, c, v, i, "s_"), **ozellikler(o, h, l, c, v, k0 - 1, "k_")})
    return out


def hesapla() -> pd.DataFrame:
    t = pd.read_pickle(TABLO)
    isler = [(str(VERI / "dk" / f"{gun}.pkl"), g) for gun, g in t.groupby("gun_tarih")]
    with ProcessPoolExecutor(10) as ex:
        rows = [x for r in ex.map(gun_isle, isler) for x in r]
    return t.join(pd.DataFrame(rows).set_index("idx"))


def ozet_satir(ad: str, x: pd.DataFrame, kesim) -> str:
    if len(x) < 20:
        return f"    {ad:<34} n={len(x):>4}  (az)"
    e, s = x[x.gun_tarih < kesim].R, x[x.gun_tarih >= kesim].R
    ay = x.groupby(pd.to_datetime(x.gun_tarih).dt.to_period("M")).R.mean()
    isaret = " ✓" if e.mean() > 0 and s.mean() > 0 else (" ✗" if e.mean() < 0 and s.mean() < 0 else "")
    return (f"    {ad:<34} n={len(x):>4}  eğitim {e.mean():+6.2f}  sınav {s.mean():+6.2f}  kârlı %{(x.R > 0).mean() * 100:3.0f}"
            f"  artı ay {(ay > 0).sum()}/{len(ay)}{isaret}")


def bolumler(x: pd.DataFrame, kesim, baslik: str) -> list[str]:
    out = [f"== {baslik} (n={len(x)}) ==", ozet_satir("HEPSİ", x, kesim)]
    for on, an in (("s_", "SİNYAL anı"), ("k_", "KARAR anı (dolumdan önce)")):
        out.append(f"  -- {an} --")
        for ad in ("gator_acilan", "alligator_dizilim", "mt_mavi", "mt_ustu", "kijun_ustu", "obv_ema_ustu", "zz_yukselen"):
            col = on + ad
            for deger, et in ((1.0, "EVET"), (0.0, "hayır")):
                out.append(ozet_satir(f"{ad} {et}", x[x[col] == deger], kesim))
        for ad in ("gator_genis", "obv_egim", "atr_y"):
            col = on + ad
            q = x[col].quantile([1 / 3, 2 / 3]).to_numpy()
            for et, m in ((f"{ad} düşük (<{q[0]:.3g})", x[col] < q[0]),
                          (f"{ad} orta", (x[col] >= q[0]) & (x[col] < q[1])),
                          (f"{ad} yüksek (≥{q[1]:.3g})", x[col] >= q[1])):
                out.append(ozet_satir(et, x[m], kesim))
    return out + [""]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    t = hesapla()
    t.to_pickle(KOK / "sonuc" / "yuksek_gosterge.pkl")
    gunler = sorted(t.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * EGITIM_ORAN)]
    gir = (t.saat >= 11.5) & (t.gun <= 20) & (t.kirmizi_geri <= 0.43)
    txt = "\n".join([f"GÖSTERGE TESTİ · {gunler[0]} – {gunler[-1]} · NET % · eğitim < {kesim} ≤ sınav · "
                     "✓ iki dönemde artı, ✗ iki dönemde eksi", "",
                     *bolumler(t, kesim, "yüksek'in TÜM dolan işlemleri"),
                     *bolumler(t[gir], kesim, "MEVCUT GİR seçimi (gün ≤20 · 11:30+ · sakin geri çekilme) — gösterge EK fayda sağlıyor mu?")])
    RAPOR.write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
