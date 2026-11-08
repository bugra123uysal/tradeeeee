"""EDGAR dosyaları "yüksek" sinyallerinin sonucunu ayırt ediyor mu? (7 Kas 2026)

İşlemler = backtest/algo_ara.py'deki ŞU ANKİ 'yüksek' (gün ≤ 20, g5 giriş, +5/−8, 16:00), data_tam (1 yıl, yanlılıksız),
maliyet düşülmüş NET %. EDGAR sayımları SİNYAL ANINDAN ÖNCE kabul edilen dosyalarla (ileriye bakış yok).
Eğitim/sınav ayrımı algo_ara ile aynı (ilk %60 gün eğitim).

    py -m backtest.edgar_yuksek_bt
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import algo_ara as A  # noqa: E402
from sistem import edgar as E  # noqa: E402

log = logging.getLogger("edgar_yuksek_bt")
KOK = Path(__file__).parent
STOP_ESIK = -7.5          # brüt sonuç bunun altındaysa "stop yedi / çöktü" say


def sinyal_ani(gun_tarih, saat: float) -> datetime:
    """'2026-11-06' + 10.25 → 2026-11-06 10:15 NY."""
    g = pd.Timestamp(gun_tarih).date()
    return datetime(g.year, g.month, g.day, tzinfo=E.NY) + timedelta(minutes=round(saat * 60))


def edgar_ekle(t: pd.DataFrame, ed: E.Edgar) -> pd.DataFrame:
    kayitlar: dict[str, list | None] = {}
    semboller = sorted(t.sym.unique())
    for i, sym in enumerate(semboller):
        try:
            kayitlar[sym] = ed.hisse(sym, eski_sayfalar=True) if ed.cik(sym) else None
        except Exception as e:  # noqa: BLE001 — tek hisse hatası testi durdurmasın
            log.warning("%s: %s", sym, e)
            kayitlar[sym] = None
        if i % 100 == 0:
            log.info("EDGAR %d/%d", i, len(semboller))
    rows = []
    for r in t.itertuples():
        lst = kayitlar.get(r.sym)
        if lst is None:
            rows.append({"cik_var": False})
            continue
        an = sinyal_ani(r.gun_tarih, r.saat)
        o30, o1 = E.ozet(lst, an, 30), E.ozet(lst, an, 1)
        rows.append({"cik_var": True, **{f"{k}30": v for k, v in o30.items()},
                     **{f"{k}1": v for k, v in o1.items()}})
    return pd.concat([t.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def _satir(ad: str, x: pd.DataFrame) -> str:
    if not len(x):
        return f"  {ad:<40} n=   0"
    return (f"  {ad:<40} n={len(x):>4}  ort {x.R.mean():+6.2f}  medyan {x.R.median():+6.2f}  "
            f"kârlı %{(x.R > 0).mean() * 100:3.0f}  çöken %{(x.brut <= STOP_ESIK).mean() * 100:3.0f}")


def gruplar(d: pd.DataFrame) -> dict[str, pd.Series]:
    temiz = (d.ihrac30 == 0) & (d.kayit30 == 0)
    return {
        "HEPSİ (CIK bulunan)": d.index == d.index,
        "son 24 saatte 8-K VAR": d.olay1 > 0,
        "son 24 saatte 8-K YOK": d.olay1 == 0,
        "30 günde 424B (ihraç) VAR": d.ihrac30 > 0,
        "30 günde 424B YOK": d.ihrac30 == 0,
        "30 günde S-1/S-3 kayıt VAR": d.kayit30 > 0,
        "30 günde kayıt+ihraç YOK (temiz)": temiz,
        "FİLTRE: 424B yok": d.ihrac30 == 0,
        "FİLTRE: 424B yok + 24s 8-K var": (d.ihrac30 == 0) & (d.olay1 > 0),
        "30 günde Form 4 VAR": d.iceriden30 > 0,
        "30 günde 13D/13G VAR": d.buyuk_ortak30 > 0,
    }


def rapor(df: pd.DataFrame, kesim) -> str:
    d = df[df.cik_var].copy()
    out = [f"EDGAR × 'yüksek' (gün ≤ 20, g5, +5/−8, 16:00) · {df.gun_tarih.min()} … {df.gun_tarih.max()}",
           f"işlem {len(df)} · EDGAR'da bulunan {len(d)} · eğitim < {kesim} ≤ sınav",
           f"R = NET % (maliyet düşülmüş) · çöken = brüt ≤ {STOP_ESIK:g}% (stop)", ""]
    donemler = (("TÜM YIL", d.gun_tarih == d.gun_tarih), ("EĞİTİM", d.gun_tarih < kesim), ("SINAV", d.gun_tarih >= kesim))
    for donem, m in donemler:
        out.append(f"== {donem} ==")
        out += [_satir(ad, d[g & m]) for ad, g in gruplar(d).items()]
        out.append("")
    out.append("== Saat dilimine göre (TÜM YIL) — 8-K etkisi saatten bağımsız mı? ==")
    dilim = pd.cut(d.saat, [0, 9.5, 11.5, 16, 24], labels=["premarket", "09:30–11:30", "11:30–16:00", "seans sonrası"])
    for ad in dilim.cat.categories:
        x = d[dilim == ad]
        out += [_satir(f"{ad} · hepsi", x), _satir(f"{ad} · 8-K var", x[x.olay1 > 0]),
                _satir(f"{ad} · 424B var", x[x.ihrac30 > 0])]
    return "\n".join(out)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    load_dotenv(KOK.parent / ".env")
    d = pd.read_pickle(KOK / "sonuc" / "aday_tam_data_tam.pkl")
    gunler = sorted(d.gun_tarih.unique())
    kesim = gunler[int(len(gunler) * A.EGITIM_ORAN)]
    k = A.Kural(gun_max=20)
    t = A.islemler(d, k)
    t = t.assign(brut=t[k.sutun])[["gun_tarih", "sym", "saat", "fiyat", "gun", "R", "brut", "rth"]]
    del d
    df = edgar_ekle(t, E.Edgar(KOK / "data_tam" / "edgar", ttl=None))
    df.to_csv(KOK / "sonuc" / "edgar_yuksek_bt.csv", index=False)
    txt = rapor(df, kesim)
    (KOK / "sonuc" / "edgar_yuksek_bt.txt").write_text(txt, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
