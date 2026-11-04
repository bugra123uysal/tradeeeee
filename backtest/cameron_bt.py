"""Ross Cameron 'ilk geri çekilme — yeni tepe yapan ilk mum' kalıbı, videolardaki kurallarla (backtest/cameron_kurallar.md).

1 dk mumlarla, her hissede gün boyu (04:00–20:00, saat filtresi aramada) TÜM tetikler bulunur ve işlem sonucu yazılır:
  Kalıp (tetik mumu i, önceki mum i−1):
    * Son PB_MAX+1 mumda bir tepe H (j_H), i−1 tepe değil (geri çekilme 1..PB_MAX mum), geri çekilme dibi L = min(low[j_H+1..i−1])
    * Yükseliş: H, önceki SURGE_W mumun dibi S'nin en az %SURGE_MIN üstü · geri çekilme (H−L)/(H−S) ≤ %50
    * i−1'de kapanış ≥ 9 EMA, L > VWAP, MACD > 0 ve MACD > sinyal ("açık")
    * Tetik: high[i] > high[i−1] → alış = max(açılış[i], high[i−1] + 1 sent) (alış stop emri), stop = L
  Çıkışlar (brüt %, alış fiyatına göre; maliyet aramada):
    * "2R"   : hedef alış + 2×risk (limit), stop L
    * "ilkk" : ilk KIRMIZI 1 dk mum kapanışında çık (alış mumundan sonra), stop L  (video #1 kuralı)
    * "yari" : yarısı HOD (H) ya da +1R hangisi yüksekse; sonra stop başa baş, kalan ilk kırmızı mum kapanışında
  Alış mumunda dip ≤ L ise stop sayılır (kötümser). Seans sonu 16:00 ve 20:00 iki türlü.
Bir hissede aynı anda tek pozisyon; çıkıştan sonra yeni tetik alınabilir (aramada "hissede ilk N işlem" sınırı).

    py -m backtest.cameron_bt --veri data_tam
"""
from __future__ import annotations

import argparse
import pickle
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from sistem import sinyal as S

KOK = Path(__file__).parent
NY_TZ = "America/New_York"
PB_MAX = 5
SURGE_W = 10
SURGE_MIN = 5.0
RETRACE_MAX = 0.5
GUN_MIN = 5.0
FIYAT = (1.0, 50.0)       # geniş tutulur; 2–20 $ aramada
SENT = 0.01


def _ilk(maske: np.ndarray, bas: int = 0) -> int:
    k = np.flatnonzero(maske[bas:])
    return int(k[0]) + bas if len(k) else -1


def _cikis_2r(i, giris, L, h, l, c, son) -> float:
    hedef = giris + 2 * (giris - L)
    for k in range(i, son):
        if l[k] <= L:
            return (L / giris - 1) * 100
        if k > i and h[k] >= hedef:
            return (hedef / giris - 1) * 100
    return (c[son - 1] / giris - 1) * 100


def _cikis_ilkk(i, giris, L, o, l, c, son) -> float:
    for k in range(i, son):
        if l[k] <= L:
            return (L / giris - 1) * 100
        if k > i and c[k] < o[k]:
            return (c[k] / giris - 1) * 100
    return (c[son - 1] / giris - 1) * 100


def _cikis_yari(i, giris, L, H, o, h, l, c, son) -> float:
    risk = giris - L
    h1 = max(H, giris + risk)
    yari, stop = None, L
    for k in range(i, son):
        if l[k] <= stop:
            p = (stop / giris - 1) * 100
            return p if yari is None else (yari + p) / 2
        if yari is None and k > i and h[k] >= h1:
            yari, stop = (h1 / giris - 1) * 100, giris
            continue
        if yari is not None and c[k] < o[k]:
            return (yari + (c[k] / giris - 1) * 100) / 2
    p = (c[son - 1] / giris - 1) * 100
    return p if yari is None else (yari + p) / 2


def _hisse(day, sym, d, pc, adv, flt, sinirlar) -> list[dict]:
    gun_bas, acilis, s16, s20 = sinirlar
    dks = (d["timestamp"].astype("int64").to_numpy() // 60_000_000).astype(np.int64)
    if d["timestamp"].dtype.unit == "ns":
        dks //= 1000
    o, h, l, c, v = (d[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    n = len(c)
    if n < 40:
        return []
    bas = int(np.searchsorted(dks, gun_bas))
    son16, son20 = int(np.searchsorted(dks, s16)), int(np.searchsorted(dks, s20))
    e9 = S.ema(c, 9)
    g = S.gostergeler(o, h, l, c, v)                         # MACD 12/26/9 (Swing 5dk ile aynı hesap)
    macd, sig = np.nan_to_num(g.macd), np.nan_to_num(g.macd_sig)
    vw = np.full(n, np.nan)
    vw[bas:] = np.cumsum(c[bas:] * v[bas:]) / np.maximum(np.cumsum(v[bas:]), 1e-9)
    cumv = np.cumsum(np.where(np.arange(n) >= bas, v, 0.0))
    hod = np.maximum.accumulate(np.where(np.arange(n) >= bas, h, 0.0))
    W = PB_MAX + 1
    pen = sliding_window_view(h, W)                         # pen[k] = h[k..k+W-1]
    jrel = pen.argmax(axis=1)
    k1 = np.arange(W, n)                                    # tetik i = k1, pencere i−W..i−1
    jH = (k1 - W) + jrel[k1 - W]
    aday = ((jH < k1 - 1) & (h[k1] > h[k1 - 1]) & (k1 - 1 >= bas) & (c[k1 - 1] >= e9[k1 - 1])
            & (macd[k1 - 1] > 0) & (macd[k1 - 1] > sig[k1 - 1]) & (k1 < son20)
            & (c[k1 - 1] / pc - 1 >= GUN_MIN / 100) & (c[k1 - 1] >= FIYAT[0]) & (c[k1 - 1] <= FIYAT[1]))
    rows, mesgul = [], -1
    for i, j in zip(k1[aday], jH[aday]):
        if i <= mesgul or j - SURGE_W < bas:
            continue
        H, L = h[j], l[j + 1:i].min()
        Sd = l[j - SURGE_W:j + 1].min()
        if H / Sd - 1 < SURGE_MIN / 100 or (H - L) / (H - Sd) > RETRACE_MAX or L <= vw[i - 1]:
            continue
        giris = max(o[i], h[i - 1] + SENT)
        if giris <= L:
            continue
        r = {"gun_tarih": day, "sym": sym, "dk": int(dks[i]), "saat": (int(dks[i]) - acilis) / 60 + 9.5,
             "fiyat": giris, "gun": (c[i - 1] / pc - 1) * 100, "rvol": cumv[i - 1] / adv if adv else np.nan,
             "float_m": flt / 1e6 if flt else np.nan, "risk_y": (giris / L - 1) * 100,
             "hod_r": (hod[i - 1] - giris) / (giris - L), "yukselis": (H / Sd - 1) * 100,
             "geri": (H - L) / (H - Sd), "pb_mum": int(i - 1 - j), "hacim_kat": v[i - 1] / max(v[max(bas, i - 31):i - 1].mean(), 1),
             "dolar_kum": float((c[bas:i] * v[bas:i]).sum())}
        r["mum_ici_dip"] = bool(l[i] <= L)                    # alış mumunda L altı: sıra belirsiz
        Li = min(L, l[i])                                      # iyimser: dip kırılımdan önce → stop o dip, kontrol i+1'den
        for ad, sn in (("s16", son16), ("s20", son20)):
            if i >= sn:
                continue
            r[f"2r_{ad}"] = _cikis_2r(i, giris, L, h, l, c, sn)
            r[f"ilkk_{ad}"] = _cikis_ilkk(i, giris, L, o, l, c, sn)
            r[f"yari_{ad}"] = _cikis_yari(i, giris, L, H, o, h, l, c, sn)
            if i + 1 < sn:
                r[f"2rI_{ad}"] = _cikis_2r(i + 1, giris, Li, h, l, c, sn) if h[i] < giris + 2 * (giris - Li) else 2 * (giris / Li - 1) * 100
                r[f"yariI_{ad}"] = _cikis_yari(i + 1, giris, Li, H, o, h, l, c, sn)
        # pozisyon kapanana kadar yeni tetik yok (en uzun çıkış = 'yari' yaklaşık; 2R'nin bitişi kullanılır)
        stop_k = _ilk(l <= L, i)
        hedef_k = _ilk(h >= giris + 2 * (giris - L), i + 1)
        bitis = [x for x in (stop_k, hedef_k) if x >= 0]
        mesgul = min(bitis) if bitis else son20
        rows.append(r)
    return rows


def gun_isle(arg) -> list[dict]:
    yol, day, prev, flt = arg
    df = pd.read_pickle(yol)
    if df.empty:
        return []
    t = lambda hm: int(pd.Timestamp(f"{day} {hm}", tz=NY_TZ).timestamp() // 60)
    sinirlar = (t("04:00"), t("09:30"), t("16:00"), t("20:00"))
    out = []
    for sym, d in df.groupby("symbol", sort=False, observed=True):
        sym = str(sym)
        pv = prev.get(sym)
        if pv is None or not pv[0] or np.isnan(pv[0]):
            continue
        out += _hisse(day, sym, d, pv[0], pv[1], flt.get(sym), sinirlar)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--veri", default="data_tam")
    a = ap.parse_args()
    veri = KOK / a.veri
    gunluk = pd.read_pickle(veri / "gunluk.pkl")
    prev: dict[str, dict] = {}
    for sym, day, pc, ad in zip(gunluk.symbol, gunluk.day.astype(str), gunluk.prev_close, gunluk.adv20):
        prev.setdefault(day, {})[sym] = (pc, ad)
    fyol = KOK / "data_tam" / "float.pkl"
    flt = pickle.load(open(fyol, "rb")) if fyol.exists() else {}
    isler = [(str(f), f.stem, prev.get(f.stem, {}), flt) for f in sorted((veri / "dk").glob("*.pkl"))]
    rows = []
    with ProcessPoolExecutor(10) as ex:
        for n, r in enumerate(ex.map(gun_isle, isler), 1):
            rows.extend(r)
            if n % 20 == 0:
                print(f"{n}/{len(isler)} gün · {len(rows)} işlem", flush=True)
    d = pd.DataFrame(rows)
    d.to_pickle(KOK / "sonuc" / f"cameron_{a.veri}.pkl")
    print(f"bitti: {len(d)} işlem → sonuc/cameron_{a.veri}.pkl")


if __name__ == "__main__":
    main()
