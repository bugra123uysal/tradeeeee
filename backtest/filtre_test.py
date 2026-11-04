"""'yüksek' sinyali + geri çekilme alışı için EK HİSSE FİLTRELERİ testi (3 Kas 2026).

Kural canlıdakiyle aynı: sistem/yuksek.py sinyali (Pine yüksek) → 30 dk içinde −%5 limit alış →
+%5 hedef / kapanış −%8 stop / gün sonu kapanış. Komisyon gidiş-dönüş %0,2.
Her filtre sinyal anında, ileriye bakmadan uygulanır. Dolmayan (geri çekilme olmayan) sinyal işlem sayılmaz.

Veri seçim yanlılığı: backtest/data "gün içi tepe ≥ %10" hisselerden oluşuyor → girişte gün ≥ %10
alt kümesi ayrıca raporlanır (yanlılıksız sayılabilecek kısım). Spread / haber / SEC bildirimi bu veride yok.

    py -m backtest.filtre_test     (pandas → sistem python)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from sistem import geri_al as G
from sistem import sinyal as S
from sistem import yuksek as Y

DATA = Path(__file__).parent / "data"
SONUC = Path(__file__).parent / "sonuc"
KOMISYON = 0.2
NY_TZ = "America/New_York"


def _ozellikler(i: int, dks, h, l, c, v, bas: int, pc: float, fl: float | None, adv: float | None) -> dict:
    dolar = c[bas:i + 1] * v[bas:i + 1]
    vw = np.cumsum(dolar)[-1] / max(np.sum(v[bas:i + 1]), 1e-9)
    son30 = slice(max(bas, i - 29), i + 1)
    t = pd.Timestamp(int(dks[i]) * 60, unit="s", tz="UTC").tz_convert(NY_TZ).time()
    return {"fiyat": c[i], "gun": (c[i] / pc - 1) * 100, "dolar_kum": float(dolar.sum()),
            "dolar_30": float((c[son30] * v[son30]).sum()), "vwap_uzak": (c[i] / vw - 1) * 100,
            "aralik": float(np.median((h[son30] - l[son30]) / c[son30]) * 100),
            "float_m": fl / 1e6 if fl else np.nan, "rvol": float(v[bas:i + 1].sum() / adv) if adv else np.nan,
            "saat": t.hour * 60 + t.minute, "premarket": t < time(9, 30)}


def _sonuc(x: G.GeriSinyal, dks, c) -> float | None:
    if x.dolum_fiyat is None:
        return None
    if x.durum == "hedef":
        cik = x.hedef
    elif x.durum == "stop":
        cik = float(c[np.where(dks == x.sonuc_dk)[0][0]])
    else:                                            # gün sonu
        cik = float(c[-1])
    return (cik / x.dolum_fiyat - 1) * 100 - KOMISYON


def islemler() -> pd.DataFrame:
    gunluk = pd.read_pickle(DATA / "gunluk.pkl")
    prev = {(r.symbol, str(r.day)): r.prev_close for r in gunluk.itertuples()}
    adv = {(r.symbol, str(r.day)): r.adv20 for r in gunluk.itertuples()}
    flt = pd.read_pickle(DATA / "float.pkl")
    out = []
    for f in sorted((DATA / "dk").glob("*.pkl")):
        day = f.stem
        df = pd.read_pickle(f)
        gun_bas = int(pd.Timestamp(f"{day} 04:00", tz=NY_TZ).timestamp() // 60)
        for sym, d in df.groupby("symbol", sort=False):
            pc = prev.get((sym, day))
            if not pc or pd.isna(pc) or len(d) < S.EMA_LEN:
                continue
            dks = (d["timestamp"].astype("int64").to_numpy() // 60_000_000).astype(np.int64)
            o, h, l, c, v = (d[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
            g = S.gostergeler(o, h, l, c, v)
            bas = int(np.searchsorted(dks, gun_bas))
            for i in Y.yuksek_sinyalleri(dks, o, h, l, c, v, g, pc, gun_bas):
                x = G.GeriSinyal(sym, "", "YÜKSEK", int(dks[i]), float(c[i]), 0.0,
                                 seviye=float(c[i]) * (1 - G.GERI / 100))
                G.degerlendir(x, dks, o, h, l, c)
                a = adv.get((sym, day))
                out.append({"gun_tarih": day, "sym": sym, "durum": x.durum, "net": _sonuc(x, dks, c),
                            **_ozellikler(i, dks, h, l, c, v, bas, pc, flt.get(sym), None if pd.isna(a) else a)})
    return pd.DataFrame(out)


@dataclass(frozen=True)
class Filtre:
    ad: str
    kosul: Callable[[pd.DataFrame], pd.Series]


FILTRELER = [
    Filtre("— filtresiz (şu anki hal)", lambda d: d.fiyat > 0),
    Filtre("dolar hacmi (04:00'dan) ≥ 50 bin $", lambda d: d.dolar_kum >= 5e4),
    Filtre("dolar hacmi (04:00'dan) ≥ 250 bin $", lambda d: d.dolar_kum >= 2.5e5),
    Filtre("dolar hacmi (04:00'dan) ≥ 1 milyon $", lambda d: d.dolar_kum >= 1e6),
    Filtre("dolar hacmi (son 30 dk) ≥ 100 bin $", lambda d: d.dolar_30 >= 1e5),
    Filtre("dolar hacmi (son 30 dk) ≥ 500 bin $", lambda d: d.dolar_30 >= 5e5),
    Filtre("likidite: 1 dk mum aralığı medyanı ≤ %1,5", lambda d: d.aralik <= 1.5),
    Filtre("likidite: 1 dk mum aralığı medyanı ≤ %3", lambda d: d.aralik <= 3),
    Filtre("fiyat ≥ 1 $", lambda d: d.fiyat >= 1),
    Filtre("fiyat < 1 $", lambda d: d.fiyat < 1),
    Filtre("float ≥ 20 milyon", lambda d: d.float_m >= 20),
    Filtre("float < 20 milyon", lambda d: d.float_m < 20),
    Filtre("VWAP'ın en çok %15 üstü", lambda d: d.vwap_uzak <= 15),
    Filtre("VWAP'ın en çok %8 üstü", lambda d: d.vwap_uzak <= 8),
    Filtre("gün değişimi ≤ %20", lambda d: d.gun <= 20),
    Filtre("rvol (bugün / 20 gün ort.) ≥ 1", lambda d: d.rvol >= 1),
    Filtre("son 30 dk hariç (15:30 sonrası yok)", lambda d: d.saat < 15 * 60 + 30),
    Filtre("sadece piyasa saati (09:30+)", lambda d: ~d.premarket),
    Filtre("sadece premarket", lambda d: d.premarket),
    Filtre("BİRLEŞİK: dolar 30dk ≥ 100 bin + aralık ≤ %3", lambda d: (d.dolar_30 >= 1e5) & (d.aralik <= 3)),
    Filtre("BİRLEŞİK: dolar 04:00 ≥ 250 bin + VWAP ≤ %15", lambda d: (d.dolar_kum >= 2.5e5) & (d.vwap_uzak <= 15)),
]


def _ozet(t: pd.DataFrame, yarilar: list[str]) -> str:
    dolan = t[t.net.notna()]
    if not len(dolan):
        return f"{len(t):5d} sinyal |     0 işlem"
    y1 = dolan[dolan.gun_tarih <= yarilar[0]].net.mean()
    y2 = dolan[dolan.gun_tarih > yarilar[0]].net.mean()
    return (f"{len(t):5d} sinyal | {len(dolan):4d} işlem | ort {dolan.net.mean():+6.2f}% | "
            f"kârlı %{(dolan.net > 0).mean() * 100:3.0f} | 1. yarı {y1:+6.2f}% · 2. yarı {y2:+6.2f}% | "
            f"toplam {dolan.net.sum():+7.1f}%")


def rapor(d: pd.DataFrame) -> str:
    gunler = sorted(d.gun_tarih.unique())
    orta = [gunler[len(gunler) // 2 - 1]]
    satir = [f"'yüksek' + geri çekilme alışı — ek filtre testi · {gunler[0]} – {gunler[-1]} ({len(gunler)} gün)",
             "işlem = geri çekilmede dolan sinyal · net: komisyon %0,2 düşülmüş · iki yarı da + olan filtre işe yarar aday", ""]
    for baslik, alt in (("TÜM SİNYALLER (veri seçimi yüzünden iyimser olabilir)", d),
                        ("SİNYALDE GÜN ≥ %10 (yanlılıksız kısım)", d[d.gun >= 10])):
        satir += [baslik, "-" * len(baslik)]
        for f in FILTRELER:
            satir.append(f"{f.ad:48s} {_ozet(alt[f.kosul(alt).fillna(False)], orta)}")
        satir.append("")
    return "\n".join(satir)


def main() -> None:
    d = islemler()
    SONUC.mkdir(exist_ok=True)
    d.to_csv(SONUC / "filtre_test_islemler.csv", index=False)
    metin = rapor(d)
    (SONUC / "filtre_test.txt").write_text(metin, encoding="utf-8")
    print(metin)


if __name__ == "__main__":
    main()
