"""yüksek'in KENDİ sinyalleri arasında seçim (7 Kas 2026) — Pine değişmez, filtre sinyalden SONRA (site/Telegram katmanı).

İşlemler = TV'deki 'yüksek' varsayılanı (gün %5–40, günün ilk sinyali) ve −%5 geri çekilmesi DOLAN sinyaller,
+5/−8, 16:00, NET %. Her işlem için iki tür özellik:
  * sinyal anı  : saat, gün %, hacim katı, rvol, ivme, VWAP/EMA50 uzaklığı, ADX, BBW, ATR, gün tepesine uzaklık …
  * ALIŞ anı    : (sinyal ile dolum arası — Pine'a filtre koyarken kullanılamayan bilgi)
      dk_dolum   sinyalden dolumun kaç dk sürdüğü       on_kosu  dolumdan önce sinyalden yukarı ne kadar gitti %
      vwap_dolum dolum fiyatının VWAP'a uzaklığı %      hacim_geri geri çekilme mumlarının hacmi / sinyal mumu hacmi
      rsi_dolum  RSI(14) dolumdan önceki mumda           ema_dolum  EMA9 > EMA20 (dolumdan önceki mum)
      kirmizi_geri geri çekilmedeki kırmızı mum oranı    gun_dolum dolum fiyatında gün değişimi %
Her özellik 5 dilim; dilim ortalaması eğitim/sınav ayrı. İKİ dönemde de aynı yönde olan dilimler güvenilir.
Ayrıca sinyal ÖNCESİ filtre (yüksek_2 araştırması) ile sinyal SONRASI filtrenin farkı.

    py -m backtest.yuksek_sonfiltre   → sonuc/yuksek_sonfiltre.txt (+ .pkl işlem tablosu)
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest.yuksek2_ara import EGITIM_ORAN, HAVUZ, islem, ozet, satir, secim  # noqa: E402
from sistem import sinyal as S  # noqa: E402

KOK = Path(__file__).parent
VERI = KOK / "data_tam"
RAPOR = KOK / "sonuc" / "yuksek_sonfiltre.txt"
TABLO = KOK / "sonuc" / "yuksek_sonfiltre.pkl"
YUKSEK = {"gun": (5, 40), "saat_min": 0.0, "rvol_min": 0.0, "hacim_min": 2.0, "dolar30_min": 0.0, "hod_min": -100.0}
SINYAL_OZ = {"saat": "saat (NY)", "gun": "gün % (sinyal)", "hacim_kat": "hacim / 30 mum", "rvol": "gün hacmi / 20g",
             "dolar_30": "30 dk $ hacim", "mom5": "5 mum ivme %", "mom30": "30 mum ivme %", "vwap_uz": "VWAP uzaklık %",
             "e50_uz": "EMA50 uzaklık %", "adx": "ADX", "bbw": "BB genişlik %", "atr_y": "ATR %",
             "hod_uz": "gün tepesine %", "fiyat": "fiyat $"}
ALIS_OZ = {"dk_dolum": "dolum süresi dk", "on_kosu": "dolum öncesi koşu %", "vwap_dolum": "dolum VWAP uzaklık %",
           "hacim_geri": "geri çekilme hacmi / sinyal", "rsi_dolum": "RSI(14) dolumda", "ema_dolum": "EMA9>EMA20 dolumda",
           "kirmizi_geri": "geri çekilmede kırmızı oranı", "gun_dolum": "gün % (dolum)"}


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up, dn = S._rma(np.maximum(d, 0), n), S._rma(np.maximum(-d, 0), n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return 100 - 100 / (1 + up / dn)


def dolum_ozellikleri(c, o, v, vw, e9, e20, rs, i: int, k0: int, sinyal_c: float, giris: float, pc: float) -> dict:
    """i = sinyal mumu, k0 = dolum mumu. Hepsi k0 açılışından/öncesinden bilinebilir (dolum fiyatı hariç = limit)."""
    ara = slice(i + 1, k0)                  # sinyal ile dolum arası mumlar (dolum mumu hariç)
    n_ara = k0 - i - 1
    return {"on_kosu": (max(c[i:k0].max(), sinyal_c) / sinyal_c - 1) * 100,
            "vwap_dolum": (giris / vw[k0 - 1] - 1) * 100,
            "hacim_geri": float(v[ara].mean() / max(v[i], 1e-9)) if n_ara > 0 else np.nan,
            "rsi_dolum": float(rs[k0 - 1]), "ema_dolum": float(e9[k0 - 1] > e20[k0 - 1]),
            "kirmizi_geri": float((c[ara] < o[ara]).mean()) if n_ara > 0 else np.nan,
            "gun_dolum": (giris / pc - 1) * 100}


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
        o, c, v = (g[k].to_numpy(float) for k in ("open", "close", "volume"))
        i = int(np.searchsorted(dks, r.dk))
        k0 = int(np.searchsorted(dks, r.dk + r.dk_g5))
        if i >= len(dks) or dks[i] != r.dk or k0 >= len(dks) or k0 <= i:
            continue
        vw = np.cumsum(c * v) / np.maximum(np.cumsum(v), 1e-9)
        seviye = c[i] * 0.95
        giris = min(seviye, o[k0])
        pc = r.fiyat / (1 + r.gun / 100)
        out.append({"idx": r.Index, **dolum_ozellikleri(c, o, v, vw, S.ema(c, 9), S.ema(c, 20), rsi(c), i, k0,
                                                         c[i], giris, pc)})
    return out


def islemler() -> pd.DataFrame:
    p = pd.read_pickle(HAVUZ)
    t = islem(secim(p, YUKSEK), "g5", "")
    isler = [(str(VERI / "dk" / f"{gun}.pkl"), g) for gun, g in t.groupby("gun_tarih")]
    with ProcessPoolExecutor(10) as ex:
        rows = [x for r in ex.map(gun_isle, isler) for x in r]
    ek = pd.DataFrame(rows).set_index("idx")
    return t.join(ek).assign(dk_dolum=t.dk_g5)


def dilim_satirlari(t: pd.DataFrame, kesim, oz: dict[str, str]) -> list[str]:
    out = []
    for ozl, ad in oz.items():
        x = t.dropna(subset=[ozl]).assign(eg=lambda z: z.gun_tarih < kesim)
        if x[ozl].nunique() <= 2:
            dilim = x[ozl].astype(int).astype(str)
        else:
            dilim = pd.qcut(x[ozl], 5, duplicates="drop")
        tab = x.groupby([dilim, "eg"], observed=True).R.agg(["mean", "size"]).unstack()
        parcalar = []
        for iv, r in tab.iterrows():
            etiket = iv if isinstance(iv, str) else f"{iv.left:.3g}–{iv.right:.3g}"
            e, s = r[("mean", True)], r[("mean", False)]
            isaret = " ✓" if e > 0 and s > 0 else (" ✗" if e < 0 and s < 0 else "")
            parcalar.append(f"{etiket}: {e:+.2f}/{s:+.2f}{isaret}")
        out += [f"  {ad}", "      " + " | ".join(parcalar)]
    return out


def rapor(t: pd.DataFrame) -> str:
    gunler = sorted(t.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * EGITIM_ORAN)]
    eg, sn = t[t.gun_tarih < kesim], t[t.gun_tarih >= kesim]
    out = [f"yüksek SİNYALLERİ ARASINDA SEÇİM · {gunler[0]} – {gunler[-1]} · {len(t)} işlem (dolan sinyaller)",
           f"eğitim < {kesim} ≤ sınav · NET % · ✓ = iki dönemde de artı, ✗ = iki dönemde de eksi", "",
           "== yüksek (hepsi) ==", satir("eğitim", ozet(eg)), satir("sınav", ozet(sn)), "",
           "== SİNYAL ANI özellikleri — dilim ortalaması eğitim/sınav ==", *dilim_satirlari(t, kesim, SINYAL_OZ), "",
           "== ALIŞ ANI özellikleri (sinyal sonrası bilgi) ==", *dilim_satirlari(t, kesim, ALIS_OZ), ""]
    sonra = (t.saat >= 11.5) & (t.gun <= 20) & (t.hacim_kat >= 3)
    out += ["== yüksek_2 kuralı SİNYAL SONRASI uygulanırsa (yüksek'in kendi sinyali: 11:30+ · gün ≤ 20 · hacim ≥ 3×) ==",
            satir("eğitim", ozet(eg[sonra[eg.index]])), satir("sınav", ozet(sn[sonra[sn.index]])),
            "  (aynı kural sinyal ÖNCESİ uygulanınca: eğitim +0,53 · sınav +0,50 — yuksek2_ara)", ""]
    return "\n".join(out)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    t = islemler()
    t.to_pickle(TABLO)
    txt = rapor(t)
    RAPOR.write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
