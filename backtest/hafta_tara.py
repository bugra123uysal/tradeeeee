"""Bir haftayı (veya gün aralığını) gün gün tarar: yüksek hangi hisselere sinyal verirdi, hangilerine GİR / ALMA,
getirileri (8 Kas 2026, kullanıcı isteği). Canlı sistemle AYNI kurallar: sistem/yuksek.py (sinyal, geri çekilme,
yarı-yarı çıkış, alma_nedeni).

Evren: NASDAQ/NYSE/AMEX hisseleri (Alpaca), önceki kapanış 0,2–40 $; gün içinde (04:00–16:00, premarket DAHİL —
saatlik uzatılmış seans mumları) en az bir kez önceki kapanışın +%5 üstüne çıkanlar. Böylece premarket'te yükselip
sönenler de girer (sadece kazananları seçme yanlılığı yok). Göstergeler önceki günle ısıtılır (Pine gibi).
Getiriler BRÜT % (maliyet düşülmedi). TradingView'den gerçekten gelen alarmlar işaretlenir (alarm geçmişi varsa).

    py -m backtest.hafta_tara --bas 2026-11-02 --son 2026-11-06
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import veri_genis as VG  # noqa: E402
from sistem import geri_al as G  # noqa: E402
from sistem import sinyal as S  # noqa: E402
from sistem import yuksek as Y  # noqa: E402

log = logging.getLogger("hafta_tara")
KOK = Path(__file__).parent
NY = VG.NY
ESIK, FIYAT_MIN, FIYAT_MAX = 5.0, 0.20, 40.0
SAAT_CHUNK = 100


def _dk(ts: pd.Series) -> np.ndarray:
    x = ts.astype("int64").to_numpy()
    return x // (60_000_000_000 if ts.dtype.unit == "ns" else 60_000_000)


def sonuc_yari(x: G.GeriSinyal, dks, c) -> float | None:
    """Sitedeki yarı-yarı çıkış: yarısı +5, kalanı +20, −8 kapanış stopu, 16:00."""
    if x.dolum_fiyat is None:
        return None
    p = x.dolum_fiyat
    if x.durum == "hedef":
        return (Y.HEDEF1 + Y.HEDEF2) / 2
    if x.durum == "stop":
        r = (c[dks == x.sonuc_dk][0] / p - 1) * 100
    elif x.durum == "seans":
        r = (x.cikis_fiyat / p - 1) * 100
    else:
        return None
    return (Y.HEDEF1 + r) / 2 if x.yari_dk is not None else r


def sonuc_pine(x: G.GeriSinyal, dks, h, c, son_dk: int) -> float | None:
    """Pine strateji varsayılanı: tüm pozisyon +5 hedef / −8 kapanış stopu / 16:00."""
    if x.dolum_dk is None:
        return None
    p = x.dolum_fiyat
    m = (dks >= x.dolum_dk) & (dks < son_dk)
    hh, cc = h[m], c[m]
    for k in range(len(cc)):
        if k > 0 and hh[k] >= p * 1.05:
            return 5.0
        if cc[k] < p * 0.92:
            return (cc[k] / p - 1) * 100
    return (cc[-1] / p - 1) * 100 if len(cc) else None


def hisse_degerlendir(sym: str, d: pd.DataFrame, pc: float, gun: date) -> dict:
    dks = _dk(d["timestamp"])
    o, h, l, c, v = (d[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    gun_bas = int(datetime.combine(gun, time(4), NY).timestamp() // 60)
    bugun = dks >= gun_bas
    kayit = {"gun": str(gun), "hisse": sym, "gun_tepe": (h[bugun].max() / pc - 1) * 100,
             "gun_kapanis": (c[bugun][-1] / pc - 1) * 100}
    idx = Y.yuksek_sinyalleri(dks, o, h, l, c, v, S.gostergeler(o, h, l, c, v), pc, gun_bas)
    if not idx:
        return {**kayit, "durum": "sinyal yok"}
    i = idx[0]
    x = G.GeriSinyal(sym, "", "TV", int(dks[i]), float(c[i]), (c[i] / pc - 1) * 100,
                     seviye=round(float(c[i]) * (1 - G.GERI / 100), 6))
    son_dk = Y.seans_sonu_dk(x.al_dk)
    Y.degerlendir(x, dks, o, h, l, c, seans_son_dk=son_dk, simdi_dk=son_dk + 1)
    if x.dolum_dk is not None:
        x.kirmizi = Y.kirmizi_orani(dks, o, c, x.al_dk, x.dolum_dk)
    alma = Y.alma_nedeni(x, x.kirmizi, x.dolum_dk is not None)
    sonra = h[(dks > x.al_dk) & (dks < son_dk)]
    saat = lambda dk: datetime.fromtimestamp(dk * 60, NY).strftime("%H:%M")
    if x.al_dk >= son_dk:
        karar = "seans sonrası sinyal (işlem yok)"
    elif x.dolum_dk is None:
        karar = "geri çekilme olmadı"
    else:
        karar = "GİR" if not alma else "ALMA: " + alma
    return {**kayit, "durum": "sinyal", "sinyal": saat(x.al_dk), "sinyal_gun": x.gun_degisim,
            "alis": saat(x.dolum_dk) if x.dolum_dk else None, "kirmizi": x.kirmizi, "karar": karar,
            "sonra_tepe": (sonra.max() / x.al_fiyat - 1) * 100 if len(sonra) else None,
            "site": sonuc_yari(x, dks, c), "pine": sonuc_pine(x, dks, h, c, son_dk)}


def hareketliler(ist: VG._Istemci, semboller: list[str], gunler: list[date]) -> tuple[dict, dict]:
    """→ ({gün: [sembol]}, {(sembol, gün): önceki kapanış}). Premarket dahil gün içi tepe ≥ +ESIK%."""
    gd = VG.gunluk(ist, semboller, gunler[0] - timedelta(days=10), gunler[-1])
    pc = {(s, d): p for s, d, p in zip(gd.symbol, gd.day, gd.prev_close) if p and FIYAT_MIN <= p <= FIYAT_MAX}
    adaylar = sorted({s for s, d in pc if d in gunler})
    rows = []
    for k in range(0, len(adaylar), SAAT_CHUNK):
        rows += ist.bars(adaylar[k:k + SAAT_CHUNK], "1Hour", datetime.combine(gunler[0], time(4), NY),
                         datetime.combine(gunler[-1], time(16), NY))
        log.info("saatlik %d/%d", min(k + SAAT_CHUNK, len(adaylar)), len(adaylar))
    sa = VG._df(rows)
    sa["day"] = sa.timestamp.dt.date
    sa = sa[sa.timestamp.dt.hour < 16]
    tepe = sa.groupby(["symbol", "day"]).high.max()
    out: dict[date, list[str]] = {g: [] for g in gunler}
    for (s, d), hmax in tepe.items():
        p = pc.get((s, d))
        if p and d in out and hmax >= p * (1 + ESIK / 100):
            out[d].append(s)
    return out, pc


def onceki_islem_gunu(gun: date, gunler_hepsi: list[date]) -> date:
    once = [g for g in gunler_hepsi if g < gun]
    return once[-1] if once else gun - timedelta(days=1)


def tara(bas: date, son: date) -> pd.DataFrame:
    ist = VG._Istemci()
    semboller = sorted(VG.evren(ist))
    log.info("evren %d sembol", len(semboller))
    takvim = [bas + timedelta(days=k) for k in range(-7, (son - bas).days + 1)]
    gunler = [g for g in takvim if g >= bas and g.weekday() < 5]
    hareket, pc = hareketliler(ist, semboller, gunler)
    islem_gunleri = sorted({d for _, d in pc})
    out = []
    for gun in gunler:
        liste = hareket.get(gun, [])
        log.info("%s: %d hareketli hisse", gun, len(liste))
        if not liste:
            continue
        dun = onceki_islem_gunu(gun, islem_gunleri)
        rows = []
        for k in range(0, len(liste), VG.MINUTE_CHUNK):
            rows += ist.bars(liste[k:k + VG.MINUTE_CHUNK], "1Min", datetime.combine(dun, time(4), NY),
                             datetime.combine(gun, time(16), NY))
        dk = VG._df(rows)
        for sym, d in dk.groupby("symbol", sort=False):
            p = pc.get((sym, gun))
            if p and len(d) > 31:
                out.append(hisse_degerlendir(sym, d.sort_values("timestamp"), p, gun))
    return pd.DataFrame(out)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--bas", type=date.fromisoformat, required=True)
    ap.add_argument("--son", type=date.fromisoformat, required=True)
    a = ap.parse_args()
    t = tara(a.bas, a.son)
    yol = KOK / "sonuc" / f"hafta_{a.bas}_{a.son}.csv"
    t.round(2).to_csv(yol, index=False, encoding="utf-8-sig")
    t.to_pickle(yol.with_suffix(".pkl"))
    print(t.groupby(["gun", "durum"]).size().unstack(fill_value=0).to_string())
    print(f"→ {yol}")


if __name__ == "__main__":
    main()
