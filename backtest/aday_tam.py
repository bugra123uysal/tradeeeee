"""Yeni algoritma araştırması — ADAY tablosu, TAM veri (04:00–20:00, yanlılıksız) · 4 Kas 2026.

Her gün, her hisse için günün TÜM Swing 5dk AL mumları (gün değişimi %3–80, 04:00–19:30 arası, saat kısıtı yok)
sinyal anı özellikleriyle ve giriş/çıkış varyantlarının GERÇEKÇİ brüt sonucuyla kaydedilir:
  * Uzun: giriş hemen (sonraki mum açılışı) veya geri çekilme limiti (−%3/5/8/12, 30 dk; dolum = düşük ≤ seviye×(1−%0,5),
    alış = min(seviye, açılış)). Çıkış: hedef (yüksekle, dolum mumundan sonra) / stop (1 dk kapanış) / seans sonu.
  * Kısa (açığa satış, sadece araştırma): sonraki mum açılışından sat; hedef aşağıda (düşükle) / stop yukarıda (kapanış).
  * Seans sonu iki türlü: 16:00 ("s16") ve 20:00 ("s20", seans sonrası dahil).
Maliyet aramada düşülür (komisyon + kayma), getiriler BRÜT %.

    py -m backtest.aday_tam --veri data_tam
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
GUN_ARALIK = (3.0, 80.0)
GIRISLER = {"hemen": None, "g3": 3.0, "g5": 5.0, "g8": 8.0, "g12": 12.0}
UZUN_CIKIS = ((5, 8), (10, 8), (20, 8), (None, 8), (10, 12), (20, 12))   # (hedef, stop); None = hedef yok
KISA_CIKIS = ((5, 8), (10, 8), (10, 15), (20, 15), (None, 15))
SEANSLAR = {"s16": "16:00", "s20": "20:00"}


def _uzun(k0: int, giris: float, h, c, hedef, stop) -> float:
    hh, cc = h[k0:], c[k0:]
    if not len(cc):
        return np.nan
    st = np.flatnonzero(cc < giris * (1 - stop / 100))
    k_st = int(st[0]) if len(st) else len(cc)
    if hedef is not None:
        hd = np.flatnonzero(hh[1:] >= giris * (1 + hedef / 100))
        k_hd = int(hd[0]) + 1 if len(hd) else len(cc)
        if k_hd <= k_st and k_hd < len(cc):
            return float(hedef)
    k = min(k_st, len(cc) - 1)
    return (cc[k] / giris - 1) * 100


def _kisa(k0: int, giris: float, l, c, hedef, stop) -> float:
    ll, cc = l[k0:], c[k0:]
    if not len(cc):
        return np.nan
    st = np.flatnonzero(cc > giris * (1 + stop / 100))
    k_st = int(st[0]) if len(st) else len(cc)
    if hedef is not None:
        hd = np.flatnonzero(ll[1:] <= giris * (1 - hedef / 100))
        k_hd = int(hd[0]) + 1 if len(hd) else len(cc)
        if k_hd <= k_st and k_hd < len(cc):
            return float(hedef)
    k = min(k_st, len(cc) - 1)
    return (1 - cc[k] / giris) * 100


def _sonuclar(i: int, dks, o, h, l, c, sonlar: dict[str, int]) -> dict:
    out = {}
    son20 = sonlar["s20"]
    for ad, geri in GIRISLER.items():
        if geri is None:
            k0 = i + 1
            if k0 >= son20 or dks[k0] - dks[i] > 5:
                continue
            giris = float(o[k0])
        else:
            seviye = c[i] * (1 - geri / 100)
            j = np.arange(i + 1, son20)
            m = (dks[j] - dks[i] <= PENCERE_DK) & (l[j] <= seviye * (1 - DOLUM_ALTI / 100))
            if not m.any():
                continue
            k0 = int(j[np.argmax(m)])
            giris = float(min(seviye, o[k0]))
        out[f"d_{ad}"] = 1
        out[f"dk_{ad}"] = int(dks[k0] - dks[i])
        for sn, son in sonlar.items():
            if k0 >= son:
                continue
            out[f"tepe_{ad}_{sn}"] = (h[k0:son].max() / giris - 1) * 100
            for hd, st in UZUN_CIKIS:
                out[f"r_{ad}_{hd or 'x'}_{st}_{sn}"] = _uzun(k0, giris, h[:son], c[:son], hd, st)
            if ad == "hemen":
                for hd, st in KISA_CIKIS:
                    out[f"k_{hd or 'x'}_{st}_{sn}"] = _kisa(k0, giris, l[:son], c[:son], hd, st)
    return out


def gun_isle(arg: tuple[str, str, dict]) -> list[dict]:
    yol, day, prev = arg
    df = pd.read_pickle(yol)
    if df.empty:
        return []
    t = lambda hm: int(pd.Timestamp(f"{day} {hm}", tz=NY_TZ).timestamp() // 60)
    gun_bas, acilis, son_sinyal = t("04:00"), t("09:30"), t("19:30")
    sonlar_dk = {k: t(v) for k, v in SEANSLAR.items()}
    rows = []
    for sym, d in df.groupby("symbol", sort=False, observed=True):
        pv = prev.get(sym)
        if pv is None or len(d) < S.EMA_LEN:
            continue
        pc, adv = pv
        if not pc or np.isnan(pc):
            continue
        dks = (d["timestamp"].astype("int64").to_numpy() // 60_000_000).astype(np.int64)
        if d["timestamp"].dtype.unit == "ns":
            dks = dks // 1000
        o, h, l, c, v = (d[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        g = S.gostergeler(o, h, l, c, v)
        bas = int(np.searchsorted(dks, gun_bas))
        sonlar = {k: int(np.searchsorted(dks, x)) for k, x in sonlar_dk.items()}
        mid = S.sma(c, S.BB_LEN)
        with np.errstate(invalid="ignore", divide="ignore"):
            bbw = (g.bb_up - mid) * 2 / mid * 100
        idx = np.arange(len(c))
        vw = np.full(len(c), np.nan)
        vw[bas:] = np.cumsum(c[bas:] * v[bas:]) / np.maximum(np.cumsum(v[bas:]), 1e-9)
        hist = g.macd - g.macd_sig
        dolar = np.cumsum(np.where(idx >= bas, c * v, 0.0))
        hod = np.maximum.accumulate(np.where(idx >= bas, h, 0.0))
        lod = np.minimum.accumulate(np.where(idx >= bas, l, np.inf))
        sira = 0
        for i, tur in S.al_sinyalleri(c, g):
            if i < bas or i < 31 or dks[i] >= son_sinyal:
                continue
            sira += 1
            gun = (c[i] / pc - 1) * 100
            if not GUN_ARALIK[0] <= gun <= GUN_ARALIK[1]:
                continue
            k30 = max(bas, i - 30)
            r = {"gun_tarih": day, "sym": sym, "sira": sira, "tur": tur, "dk": int(dks[i]),
                 "saat": (int(dks[i]) - acilis) / 60 + 9.5, "fiyat": float(c[i]), "gun": gun,
                 "mom5": (c[i] / c[i - 5] - 1) * 100, "mom30": (c[i] / c[k30] - 1) * 100,
                 "hist_pos": bool(hist[i] > 0), "hist_art": bool(hist[i] > hist[i - 1]),
                 "vwap_uz": (c[i] / vw[i] - 1) * 100, "hacim_kat": v[i] / max(v[i - 30:i].mean(), 1e-9),
                 "bbw": float(bbw[i]), "adx": float(g.adx[i]), "hod_uz": (c[i] / hod[i] - 1) * 100,
                 "gun_tepe": (hod[i] / pc - 1) * 100, "gun_dip": (lod[i] / pc - 1) * 100,
                 "dolar_kum": float(dolar[i]), "dolar_30": float((c[k30:i + 1] * v[k30:i + 1]).sum()),
                 "rvol": float(dolar[i] / (adv * pc)) if adv and adv > 0 else np.nan,
                 "e50_uz": (c[i] / g.e50[i] - 1) * 100, "atr_y": float(g.atr[i] / c[i] * 100),
                 "mum_say": int(i - bas), "kirmizi5": int((c[i - 5:i] < o[i - 5:i]).sum())}
            r.update(_sonuclar(i, dks, o, h, l, c, sonlar))
            rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--veri", default="data_tam")
    ap.add_argument("--is", dest="isci", type=int, default=10)
    a = ap.parse_args()
    veri = KOK / a.veri
    gunluk = pd.read_pickle(veri / "gunluk.pkl")
    prev: dict[str, dict] = {}
    for sym, day, pc, ad in zip(gunluk.symbol, gunluk.day.astype(str), gunluk.prev_close, gunluk.adv20):
        prev.setdefault(day, {})[sym] = (pc, ad)
    isler = [(str(f), f.stem, prev.get(f.stem, {})) for f in sorted((veri / "dk").glob("*.pkl"))]
    rows = []
    with ProcessPoolExecutor(a.isci) as ex:
        for n, r in enumerate(ex.map(gun_isle, isler), 1):
            rows.extend(r)
            if n % 20 == 0:
                print(f"{n}/{len(isler)} gün · {len(rows)} aday", flush=True)
    d = pd.DataFrame(rows)
    d.to_pickle(KOK / "sonuc" / f"aday_tam_{a.veri}.pkl")
    print(f"bitti: {len(d)} aday → sonuc/aday_tam_{a.veri}.pkl")


if __name__ == "__main__":
    main()
