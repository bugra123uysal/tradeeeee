"""'yüksek-1' araştırması — ADAY tablosu (4 Kas 2026).

Her gün, her hisse için günün TÜM Swing 5dk AL mumları (gün değişimi %3–60 arası) sinyal anı özellikleriyle
ve birçok giriş/çıkış varyantının GERÇEKÇİ sonucuyla kaydedilir. Kural araması sonra bu tablo üzerinde
(backtest/yuksek1_arastir.py) hızlıca yapılır — her kural için "o gün o hissede kuralı sağlayan İLK AL" alınır.

Gerçekçilik (yuksek_bt.py ile aynı): limit alış ancak düşük ≤ seviye × (1 − %0,5) olursa dolar, alış =
min(seviye, dolum mumu açılışı). Hedef dolum mumundan SONRA, stop 1 dk kapanışla. 16:00'da kapanır.
Getiriler BRÜT yazılır — maliyet (komisyon %0,2 + kayma %0,5) aramada düşülür.

    py -m backtest.yuksek1_veri --veri data_genis
    py -m backtest.yuksek1_veri --veri data
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from sistem import sinyal as S

KOK = Path(__file__).parent
NY_TZ = "America/New_York"
DOLUM_ALTI = 0.5
PENCERE_DK = 30
GUN_ARALIK = (3.0, 60.0)
GIRISLER = {"hemen": None, "g3": 3.0, "g5": 5.0, "g8": 8.0}      # hemen = sonraki mum açılışı
HEDEFLER = (3.0, 5.0, 10.0, 20.0, None)                          # None = hedef yok (stop / seans sonu)
STOPLAR = (5.0, 8.0)


def _cikis(k0: int, giris: float, h: np.ndarray, c: np.ndarray, hedef: float | None, stop: float) -> float:
    """Dolum mumu k0'dan itibaren brüt getiri %. Hedef k>k0'da yüksekle, stop her mumda kapanışla (aynı mumda önce hedef)."""
    hh, cc = h[k0:], c[k0:]
    st = np.flatnonzero(cc < giris * (1 - stop / 100))
    k_st = int(st[0]) if len(st) else len(cc)
    if hedef is not None:
        hd = np.flatnonzero(hh[1:] >= giris * (1 + hedef / 100))
        k_hd = int(hd[0]) + 1 if len(hd) else len(cc)
        if k_hd <= k_st and k_hd < len(cc):
            return hedef
    if k_st < len(cc):
        return (cc[k_st] / giris - 1) * 100
    return (cc[-1] / giris - 1) * 100


def _sonuclar(i: int, dks, o, h, l, c, son: int) -> dict:
    out = {}
    for ad, geri in GIRISLER.items():
        if geri is None:
            k0 = i + 1
            if k0 >= son or dks[k0] - dks[i] > 5:
                continue
            giris = float(o[k0])
        else:
            seviye = c[i] * (1 - geri / 100)
            j = np.arange(i + 1, son)
            m = (dks[j] - dks[i] <= PENCERE_DK) & (l[j] <= seviye * (1 - DOLUM_ALTI / 100))
            if not m.any():
                continue
            k0 = int(j[np.argmax(m)])
            giris = float(min(seviye, o[k0]))
        out[f"d_{ad}"] = 1
        out[f"dk_{ad}"] = int(dks[k0] - dks[i])
        out[f"tepe_{ad}"] = (h[k0:son].max() / giris - 1) * 100
        for hd in HEDEFLER:
            for st in STOPLAR:
                out[f"r_{ad}_{hd or 'x'}_{st:g}"] = _cikis(k0, giris, h[:son], c[:son], hd, st)
    return out


def gun_isle(arg: tuple[str, str, dict]) -> list[dict]:
    yol, day, prev = arg
    df = pd.read_pickle(yol)
    if df.empty:
        return []
    gun_bas = int(pd.Timestamp(f"{day} 04:00", tz=NY_TZ).timestamp() // 60)
    acilis = int(pd.Timestamp(f"{day} 09:30", tz=NY_TZ).timestamp() // 60)
    seans_son = int(pd.Timestamp(f"{day} 16:00", tz=NY_TZ).timestamp() // 60)
    rows = []
    for sym, d in df.groupby("symbol", sort=False, observed=True):
        pv = prev.get(sym)
        if pv is None or len(d) < S.EMA_LEN:
            continue
        pc, adv = pv
        if not pc or np.isnan(pc):
            continue
        dks = (d["timestamp"].astype("int64").to_numpy() // 60_000_000).astype(np.int64)
        o, h, l, c, v = (d[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        g = S.gostergeler(o, h, l, c, v)
        bas = int(np.searchsorted(dks, gun_bas))
        son = int(np.searchsorted(dks, seans_son))
        mid = S.sma(c, S.BB_LEN)
        with np.errstate(invalid="ignore", divide="ignore"):
            bbw = (g.bb_up - mid) * 2 / mid * 100
        vw = np.full(len(c), np.nan)
        vw[bas:] = np.cumsum(c[bas:] * v[bas:]) / np.maximum(np.cumsum(v[bas:]), 1e-9)
        hist = g.macd - g.macd_sig
        dolar = np.cumsum(np.where(np.arange(len(c)) >= bas, c * v, 0.0))
        hod = np.maximum.accumulate(np.where(np.arange(len(c)) >= bas, h, 0.0))
        sira = 0
        for i, tur in S.al_sinyalleri(c, g):
            if i < bas or i < 31 or i >= son:
                continue
            sira += 1
            gun = (c[i] / pc - 1) * 100
            if not GUN_ARALIK[0] <= gun <= GUN_ARALIK[1]:
                continue
            r = {"gun_tarih": day, "sym": sym, "sira": sira, "tur": tur, "dk": int(dks[i]),
                 "saat": (int(dks[i]) - acilis) / 60 + 9.5, "fiyat": float(c[i]), "gun": gun,
                 "mom5": (c[i] / c[i - 5] - 1) * 100, "hist_pos": bool(hist[i] > 0),
                 "hist_art": bool(hist[i] > hist[i - 1]), "vwap_uz": (c[i] / vw[i] - 1) * 100,
                 "hacim_kat": v[i] / max(v[i - 30:i].mean(), 1e-9), "bbw": float(bbw[i]),
                 "adx": float(g.adx[i]), "hod_uz": (c[i] / hod[i] - 1) * 100,
                 "gun_tepe": (hod[i] / pc - 1) * 100, "dolar_kum": float(dolar[i]),
                 "rvol": float(dolar[i] / (adv * pc)) if adv and adv > 0 else np.nan,
                 "e50_uz": (c[i] / g.e50[i] - 1) * 100, "atr_y": float(g.atr[i] / c[i] * 100)}
            r.update(_sonuclar(i, dks, o, h, l, c, son))
            rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--veri", default="data_genis")
    ap.add_argument("--is", dest="isci", type=int, default=10)
    a = ap.parse_args()
    veri = KOK / a.veri
    gunluk = pd.read_pickle(veri / "gunluk.pkl")
    adv = gunluk["adv20"] if "adv20" in gunluk else pd.Series(np.nan, index=gunluk.index)
    prev: dict[str, dict] = {}
    for sym, day, pc, ad in zip(gunluk.symbol, gunluk.day.astype(str), gunluk.prev_close, adv):
        prev.setdefault(day, {})[sym] = (pc, ad)
    isler = [(str(f), f.stem, prev.get(f.stem, {})) for f in sorted((veri / "dk").glob("*.pkl"))]
    rows = []
    with ProcessPoolExecutor(a.isci) as ex:
        for n, r in enumerate(ex.map(gun_isle, isler), 1):
            rows.extend(r)
            if n % 10 == 0:
                print(f"{n}/{len(isler)} gün · {len(rows)} aday", flush=True)
    d = pd.DataFrame(rows)
    (KOK / "sonuc").mkdir(exist_ok=True)
    d.to_pickle(KOK / "sonuc" / f"yuksek1_aday_{a.veri}.pkl")
    print(f"bitti: {len(d)} aday → sonuc/yuksek1_aday_{a.veri}.pkl")


if __name__ == "__main__":
    main()
