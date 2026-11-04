"""SÜRE mi SAT mı? Kullanıcı gözlemi (2 Kas 2026, CHOW 30 Eki ekran görüntüsü):
güçlü yükselişte Swing 5dk AL'dan sonra SAT gelmez, işlem 20 mumda SÜRE ile kapanır; yatay/kararsız
hisseler ise AL'dan birkaç mum sonra SAT verir (sık AL-SAT).

Bu betik Pine'daki gibi pozisyonlu simülasyon yapar (AL sadece pozisyon yokken, çıkış SAT / STOP /
SÜRE 20 mum / seans sonu) ve:
  1. çıkış nedenine ve AL→çıkış mum sayısına göre işlemleri, büyük koşu olan/olmayan diye ölçer,
  2. ileriye bakmayan giriş/çıkış varyantlarını dener:
     - "hayatta kalan" giriş: AL'dan k mum sonra hâlâ SAT/STOP yoksa ve kapanış AL'ın üstündeyse gir
     - günün önceki işlemleri: önceki işlem SÜRE ile kapandıysa / bugün çok AL-SAT olduysa
     - SÜRE çıkışını kaldır (sadece SAT/STOP/seans sonu)

Seçim yanlılığı: veri "gün içi tepe ≥ %10" ile seçildiğinden girişte gün% ≥ 10 olanlar ayrıca raporlanır.

    py -m backtest.sure          (pandas → sistem python)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.devam import DK, KOMISYON, REG_CLOSE, REG_OPEN
from sistem import sinyal as S

STOP_ATR, SURE = 1.5, 20
SONUC = Path(__file__).parent / "sonuc"


def _cikis(i: int, c, g, son: int, sure: int | None, stop: float) -> tuple[int, str]:
    """i'de girilen pozisyonun çıkışı → (mum, neden)."""
    for k in range(i + 1, son + 1):
        if c[k] < g.vw[k] and c[k] < g.e50[k]:
            return k, "SAT"
        if c[k] < stop:
            return k, "STOP"
        if sure and k - i >= sure:
            return k, "SÜRE"
    return son, "GÜN SONU"


def islemler(df: pd.DataFrame, prev_close: float | None) -> list[dict]:
    """Pine Swing 5dk: AL sadece pozisyon yokken; her işlem için özellikler + varyant sonuçları."""
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    ts = df["timestamp"].dt.time.to_numpy()
    reg = np.array([REG_OPEN <= t < REG_CLOSE for t in ts])
    if not reg.any() or not prev_close:
        return []
    son = int(np.where(reg)[0].max())
    g = S.gostergeler(o, h, l, c, v)
    al = dict(S.al_sinyalleri(c, g))
    out, i, gecmis = [], 0, []
    while i < son:
        if i not in al:
            i += 1
            continue
        stop = c[i] - STOP_ATR * g.atr[i]
        k, neden = _cikis(i, c, g, son, SURE, stop)
        k_suresiz, neden_suresiz = _cikis(i, c, g, son, None, stop)
        r = {
            "saat": ts[i].hour + ts[i].minute / 60, "pre": not reg[i], "fiyat": c[i], "tur": al[i],
            "gun": (c[i] / prev_close - 1) * 100,
            "onceki_n": len(gecmis),                                   # bugün bu hissede önceki işlem sayısı
            "onceki_sure": bool(gecmis) and gecmis[-1][0] == "SÜRE",   # önceki işlem SÜRE ile mi kapandı
            "onceki_mum": gecmis[-1][1] if gecmis else np.nan,         # önceki işlem kaç mum sürdü
            "kisa_sat_n": sum(1 for n, m in gecmis if n in ("SAT", "STOP") and m <= 5),
            "neden": neden, "mum": k - i,
            "net": (c[k] / c[i] - 1) * 100 - KOMISYON,
            "net_suresiz": (c[k_suresiz] / c[i] - 1) * 100 - KOMISYON, "neden_suresiz": neden_suresiz,
            "pot60": (h[i + 1:i + 61].max() / c[i] - 1) * 100 if i + 1 <= son else 0.0,
        }
        for kk in (2, 3, 5, 8):                                         # hayatta kalan giriş
            j = i + kk
            if k > j and j < son and c[j] > c[i]:
                k2, _ = _cikis(j, c, g, son, None, stop)
                r[f"hk{kk}"] = (c[k2] / c[j] - 1) * 100 - KOMISYON
                k3, _ = _cikis(j, c, g, son, SURE, stop)
                r[f"hk{kk}_sure"] = (c[k3] / c[j] - 1) * 100 - KOMISYON
            else:
                r[f"hk{kk}"] = r[f"hk{kk}_sure"] = np.nan
        out.append(r)
        gecmis.append((neden, k - i))
        i = k + 1
    return out


def _satir(ad: str, x: pd.Series) -> str:
    x = x.dropna()
    if not len(x):
        return f"{ad:52} {0:>6}"
    return f"{ad:52} {len(x):>6} {100 * (x > 0).mean():>7.1f} {x.mean():>7.2f} {x.median():>8.2f} {x.sum():>8.0f}"


def rapor(d: pd.DataFrame) -> str:
    L = [f"{d.day.nunique()} gün · {len(d)} işlem (Pine gibi pozisyonlu, AL sadece pozisyon yokken)", ""]
    d = d.assign(buyuk=d.pot60 >= 20)
    L.append("── 1. Çıkış nedeni: büyük koşu (60 dk içinde +%20) olan / olmayan işlemler")
    L.append(pd.crosstab(d.neden, d.buyuk, normalize="columns").mul(100).round(1)
             .rename(columns={True: "büyük koşu %", False: "diğer %"}).to_string())
    L.append("\n── 2. AL → SAT/STOP kaç mumda? (SÜRE/GÜN SONU hariç)")
    ss = d[d.neden.isin(["SAT", "STOP"])]
    L.append(ss.groupby("buyuk").mum.describe(percentiles=[.25, .5, .75]).round(1)
             .rename(index={True: "büyük koşu", False: "diğer"}).to_string())
    L.append("   SAT/STOP'un ilk N mumda gelme oranı:  " + "  ".join(
        f"≤{n}: %{(ss.mum <= n).mean() * 100:.0f}" for n in (2, 3, 5, 8, 12)))
    L.append("\n── 3. Giriş/çıkış varyantları (net %, komisyon dahil)")
    L.append(f"{'varyant':52} {'işlem':>6} {'kârlı%':>7} {'ort%':>7} {'medyan%':>8} {'toplam%':>8}")
    for etiket, m in (("TÜMÜ", d.gun > -1e9), ("girişte gün ≥ %10 (yanlılıksız)", d.gun >= 10)):
        x = d[m]
        L.append(f"[{etiket}]")
        L.append(_satir("  AL'da gir, SAT/STOP/SÜRE 20 (mevcut script)", x.net))
        L.append(_satir("  AL'da gir, SÜRE yok (SAT/STOP/seans sonu)", x.net_suresiz))
        for kk in (2, 3, 5, 8):
            L.append(_satir(f"  AL+{kk} mumda hâlâ SAT yok & AL üstü → gir, SÜRE yok", x[f"hk{kk}"]))
            L.append(_satir(f"  AL+{kk} mumda hâlâ SAT yok & AL üstü → gir, SÜRE 20", x[f"hk{kk}_sure"]))
        L.append(_satir("  önceki işlem SÜRE ile kapandı → AL'da gir, SÜRE yok", x[x.onceki_sure].net_suresiz))
        L.append(_satir("  günün ilk işlemi → AL'da gir, SÜRE yok", x[x.onceki_n == 0].net_suresiz))
        L.append(_satir("  bugün ≤5 mumda SAT/STOP olmuş (sık AL-SAT) → SÜRE yok", x[x.kisa_sat_n >= 1].net_suresiz))
        L.append(_satir("  bugün hiç kısa SAT yok → SÜRE yok", x[x.kisa_sat_n == 0].net_suresiz))
        L.append(_satir("  hiç kısa SAT yok & AL+3 hayatta → SÜRE yok", x[x.kisa_sat_n == 0].hk3))
    gunler = sorted(d.day.unique())
    y = gunler[len(gunler) // 2]
    u = d[d.gun >= 10]
    L.append("\n── 4. Zaman bölünmesi (gün ≥ %10): ilk yarı / ikinci yarı")
    for kol in ("net", "net_suresiz", "hk3", "hk5"):
        a, b = u[u.day < y][kol].dropna(), u[u.day >= y][kol].dropna()
        L.append(f"  {kol:12} ilk yarı ort {a.mean():+.2f}% (n={len(a)})  ikinci yarı ort {b.mean():+.2f}% (n={len(b)})")
    return "\n".join(L)


def main() -> None:
    gunluk = pd.read_pickle(DK.parent / "gunluk.pkl")
    prev = {(r.symbol, str(r.day)): r.prev_close for r in gunluk.itertuples()}
    rows = []
    for f in sorted(DK.glob("*.pkl")):
        for sym, df in pd.read_pickle(f).groupby("symbol"):
            if len(df) >= 60:
                rows += [{**r, "sym": sym, "day": f.stem}
                         for r in islemler(df.sort_values("timestamp"), prev.get((sym, f.stem)))]
        print(".", end="", flush=True, file=sys.stderr)
    print(file=sys.stderr)
    d = pd.DataFrame(rows)
    d.to_pickle(SONUC / "sure_islemler.pkl")
    metin = rapor(d)
    print(metin)
    (SONUC / "sure_ozet.txt").write_text(metin + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
