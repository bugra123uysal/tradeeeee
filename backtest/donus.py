"""'Yükselmeye başlayınca gir' testi (4 Kas 2026, PLAG 19 Eki örneği: geri çekilme alışı 1 dk'da stop oldu,
sonra 2,68 $'a gitti). 'yüksek' sinyalinden sonra farklı GİRİŞ kuralları; çıkışlar aynı (+5/−8 ve yarı-yarı).

Girişler (hepsi ileriye bakmadan; onay mumu kapandıktan sonra SONRAKİ mumun açılışından girilir):
  V0 limit −%5 (şu anki)          : düşük ≤ seviye×(1−%0,5) → seviyeden dolum
  V1 dönüş mumu                   : −%5 geri çekilme oldu (30 dk içinde), sonra yeşil mum + kapanış > önceki mumun tepesi
  V2 sinyal fiyatını geri aldı    : geri çekilmeden sonra kapanış ≥ sinyal kapanışı
  V3 VWAP üstüne dönüş            : geri çekilmede VWAP altına indi, sonra kapanış > seans VWAP'ı
  V4 sinyal mumu tepesini kırdı   : geri çekilmeden sonra kapanış > sinyal mumunun tepesi (yeni kırılım)
  V5 V0 + stop sonrası tekrar giriş: V0 stop olursa, kapanış V0 alış fiyatının üstüne dönünce bir kez daha gir
Onay penceresi sinyalden sonra ONAY_DK dakika (V5'te 15:00'e kadar). 15:30 sonrası giriş yok, 16:00'da kapanır.

    py -m backtest.donus [--veri data_genis]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.kosu import Cikis, getiri
from sistem import geri_al as G
from sistem import sinyal as S
from sistem import yuksek as Y

KOK = Path(__file__).parent
NY_TZ = "America/New_York"
DOLUM_ALTI = 0.5
ONAY_DK = 90
C5, C20 = Cikis("a"), Cikis("b", hedef=20)


def _cikislar(k0: int, giris: float, h, c, vw) -> dict:
    r5, r20 = getiri(C5, k0, giris, h, c, vw), getiri(C20, k0, giris, h, c, vw)
    return {"+5": r5, "yarı": (r5 + r20) / 2}


def _stop_mumu(k0: int, giris: float, c, son: int) -> int | None:
    for k in range(k0, son):
        if c[k] < giris * (1 - G.STOP / 100):
            return k
    return None


def girisler(i: int, dks, o, h, l, c, vw, son: int, son_giris: int) -> dict[str, tuple[int, float] | None]:
    seviye = c[i] * (1 - G.GERI / 100)
    pencere = [k for k in range(i + 1, son) if dks[k] - dks[i] <= G.PENCERE_DK]
    dokunus = next((k for k in pencere if l[k] <= seviye * (1 - DOLUM_ALTI / 100)), None)
    out: dict[str, tuple[int, float] | None] = {f"V{n}": None for n in range(6)}
    if dokunus is None:
        return out
    out["V0"] = (dokunus, float(min(seviye, o[dokunus])))
    onay = [k for k in range(dokunus, son_giris - 1) if dks[k] - dks[i] <= ONAY_DK]
    vwap_alti = False
    for k in onay:
        vwap_alti = vwap_alti or c[k] < vw[k]
        sonraki = (k + 1, float(o[k + 1]))
        if out["V1"] is None and k > dokunus and c[k] > o[k] and c[k] > h[k - 1]:
            out["V1"] = sonraki
        if out["V2"] is None and k > dokunus and c[k] >= c[i]:
            out["V2"] = sonraki
        if out["V3"] is None and vwap_alti and c[k] > vw[k] and k > dokunus:
            out["V3"] = sonraki
        if out["V4"] is None and k > dokunus and c[k] > h[i]:
            out["V4"] = sonraki
    k0, g0 = out["V0"]
    ks = _stop_mumu(k0, g0, c, son)
    if ks is not None:
        k = next((k for k in range(ks + 1, son_giris - 1) if c[k] > g0), None)
        if k is not None:
            out["V5"] = (k + 1, float(o[k + 1]))
    return out


def islemler(veri: Path) -> pd.DataFrame:
    gunluk = pd.read_pickle(veri / "gunluk.pkl")
    prev = {(r.symbol, str(r.day)): r.prev_close for r in gunluk.itertuples()}
    out = []
    for f in sorted((veri / "dk").glob("*.pkl")):
        day = f.stem
        df = pd.read_pickle(f)
        if df.empty:
            continue
        gun_bas = int(pd.Timestamp(f"{day} 04:00", tz=NY_TZ).timestamp() // 60)
        seans_son = int(pd.Timestamp(f"{day} 16:00", tz=NY_TZ).timestamp() // 60)
        giris_son = int(pd.Timestamp(f"{day} 15:30", tz=NY_TZ).timestamp() // 60)
        for sym, d in df.groupby("symbol", sort=False, observed=True):
            pc = prev.get((sym, day))
            if not pc or pd.isna(pc) or len(d) < S.EMA_LEN:
                continue
            dks = (d["timestamp"].astype("int64").to_numpy() // 60_000_000).astype(np.int64)
            o, h, l, c, v = (d[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
            g = S.gostergeler(o, h, l, c, v)
            bas = int(np.searchsorted(dks, gun_bas))
            son, son_g = int(np.searchsorted(dks, seans_son)), int(np.searchsorted(dks, giris_son))
            vw = np.full(len(c), np.nan)
            vw[bas:] = np.cumsum(c[bas:] * v[bas:]) / np.maximum(np.cumsum(v[bas:]), 1e-9)
            for i in Y.yuksek_sinyalleri(dks, o, h, l, c, v, g, pc, gun_bas):
                kayit = {"gun_tarih": day, "sym": sym, "gun": (c[i] / pc - 1) * 100}
                for ad, gr in girisler(i, dks, o, h, l, c, vw, son, son_g).items():
                    if gr is None or gr[0] >= son:
                        continue
                    for cx, r in _cikislar(gr[0], gr[1], h[:son], c[:son], vw[:son]).items():
                        kayit[f"{ad} {cx}"] = r
                out.append(kayit)
    return pd.DataFrame(out)


ADLAR = {"V0": "limit −%5 (şu anki)", "V1": "dönüş mumu", "V2": "sinyal fiyatını geri aldı",
         "V3": "VWAP üstüne dönüş", "V4": "sinyal mumu tepesini kırdı", "V5": "stop sonrası tekrar giriş (ek işlem)"}


def rapor(d: pd.DataFrame) -> str:
    gunler = sorted(d.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * 0.6)]
    s = [f"'Yükselmeye başlayınca gir' — {gunler[0]} – {gunler[-1]} · {len(gunler)} gün · {len(d)} sinyal",
         "kayma: limit girişlerde %0,5, piyasa (sonraki açılış) girişlerinde %0,5 ve %1 · komisyon %0,2 dahil", ""]
    for baslik, a in (("SİNYALDE GÜN ≤ %20 (sitedeki filtre)", d[d.gun <= 20]), ("TÜM SİNYALLER", d)):
        s += [baslik, "-" * len(baslik)]
        hafta = pd.to_datetime(a.gun_tarih).dt.strftime("%G-%V")
        for v, ad in ADLAR.items():
            for cx in ("+5", "yarı"):
                kol = f"{v} {cx}"
                if kol not in a:
                    continue
                r = a[kol].dropna()
                kaymalar = (0.5,) if v == "V0" else (0.5, 1.0)
                for kay in kaymalar:
                    x = r - kay
                    w = x.groupby(hafta[x.index].values).mean()
                    once, sonra = x[a.gun_tarih[x.index] < kesim], x[a.gun_tarih[x.index] >= kesim]
                    s.append(f"  {ad:38s} {cx:5s} kayma %{kay:<3g} {len(x):4d} işlem | ort {x.mean():+6.2f}% | "
                             f"kârlı %{(x > 0).mean() * 100:3.0f} | + hafta {int((w > 0).sum())}/{len(w)} | "
                             f"ilk %60 {once.mean():+.2f} / son %40 {sonra.mean():+.2f}")
        s.append("")
    return "\n".join(s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--veri", default="data")
    a = ap.parse_args()
    d = islemler(KOK / a.veri)
    (KOK / "sonuc").mkdir(exist_ok=True)
    d.to_csv(KOK / "sonuc" / f"donus_{a.veri}.csv", index=False)
    metin = rapor(d)
    (KOK / "sonuc" / f"donus_{a.veri}.txt").write_text(metin, encoding="utf-8")
    print(metin)


if __name__ == "__main__":
    main()
