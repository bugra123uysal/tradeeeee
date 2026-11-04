"""Ross Cameron kurallarını ve liste özelliklerini test eder.

Önce: python -m backtest.veri --start ... --end ...
Sonra: python -m backtest.calistir

Çıktılar backtest/sonuc/ altına yazılır:
  islemler.csv   — her Cameron işlemi (varyant, hisse, gün, giriş/çıkış, getiri)
  adaylar.csv    — tarayıcı koşullarını geçen her (hisse, gün) + özellikleri + sonuçları
"""
from __future__ import annotations

import logging
import pickle
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

from sistem import tv_cdp

from .cameron import Bar, Params, simulate_day, variant
from .veri import DATA_DIR, MINUTE_DIR

log = logging.getLogger("calistir")
OUT = Path(__file__).parent / "sonuc"

VARIANTS: dict[str, Params] = {
    "Cameron tam (2-20$, +%10, float<20M)": Params(),
    "Cameron, float şartı yok": variant(max_float=None),
    "Cameron, 1$ altı dahil (0,2-20$)": variant(min_price=0.2),
    "Cameron, sadece 09:30-11:00": variant(start=time(9, 30)),
}
PM_CHECK = time(9, 25)
PM_MIN_PRICE, PM_MIN_GAIN, PM_MIN_VOL = 0.2, 0.05, 2000
TR_MIN_GAIN, TR_MIN_VOL, TR_MIN_RELVOL = 0.05, 500_000, 1.5
OPEN = time(9, 30)
CLOSE = time(16, 0)


def load_floats(daily: pd.DataFrame, uni: dict[str, str], symbols: set[str]) -> dict[str, float | None]:
    path = DATA_DIR / "float.pkl"
    if path.exists():
        return pickle.load(open(path, "rb"))
    tick = {f"{uni[s]}:{s}": s for s in symbols if s in uni}
    raw = tv_cdp.floats(sorted(tick))
    out = {tick[k]: v for k, v in raw.items()}
    pickle.dump(out, open(path, "wb"))
    return out


def to_bars(df: pd.DataFrame) -> list[Bar]:
    return [Bar(t.time(), o, h, l, c, v) for t, o, h, l, c, v in
            zip(df["timestamp"], df["open"], df["high"], df["low"], df["close"], df["volume"])]


def features(df: pd.DataFrame, prev_close: float, adv20: float | None) -> dict:
    """Tarayıcı koşulları ve sonuç ölçüleri (tek hisse, tek gün)."""
    t = df["timestamp"].dt.time
    pre = df[t < OPEN]
    reg = df[(t >= OPEN) & (t < CLOSE)]
    f: dict = {}
    pm = pre[pre["timestamp"].dt.time <= PM_CHECK]
    if len(pm):
        price = pm["close"].iloc[-1]
        f.update(pm_price=price, pm_gain=price / prev_close - 1, pm_vol=pm["volume"].sum(),
                 pm_high_fade=price / pm["high"].max() - 1)
        f["pm_ok"] = price > PM_MIN_PRICE and f["pm_gain"] > PM_MIN_GAIN and f["pm_vol"] > PM_MIN_VOL
    else:
        f["pm_ok"] = False
    if len(reg):
        o = reg["open"].iloc[0]
        first90 = reg[reg["timestamp"].dt.time < time(11, 0)]
        f.update(open=o, open_gap=o / prev_close - 1, close=reg["close"].iloc[-1],
                 open_to_close=reg["close"].iloc[-1] / o - 1,
                 open_to_high90=first90["high"].max() / o - 1 if len(first90) else np.nan,
                 open_to_1100=first90["close"].iloc[-1] / o - 1 if len(first90) else np.nan,
                 open_to_low90=first90["low"].min() / o - 1 if len(first90) else np.nan)
        cum = pre["volume"].sum() + reg["volume"].cumsum()
        chg = reg["close"] / prev_close - 1
        ok = (chg > TR_MIN_GAIN) & (cum > TR_MIN_VOL) & (cum > TR_MIN_RELVOL * (adv20 or np.inf))
        if ok.any():
            i = ok.idxmax()
            p = reg.loc[i, "close"]
            f.update(tr_ok=True, tr_time=reg.loc[i, "timestamp"].strftime("%H:%M"),
                     tr_price=p, tr_to_close=reg["close"].iloc[-1] / p - 1)
        else:
            f["tr_ok"] = False
    return f


def run() -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = pd.read_pickle(DATA_DIR / "gunluk.pkl").set_index(["symbol", "day"])
    uni = pickle.load(open(DATA_DIR / "evren.pkl", "rb"))
    files = sorted(MINUTE_DIR.glob("*.pkl"))
    all_syms: set[str] = set()
    for p in files:
        all_syms |= set(pd.read_pickle(p)["symbol"].unique()) if p.stat().st_size > 1000 else set()
    floats = load_floats(daily, uni, all_syms)
    log.info("float: %d / %d hisse için bulundu", sum(v is not None for v in floats.values()), len(floats))

    trades, cands = [], []
    for p in files:
        day = pd.Timestamp(p.stem).date()
        m = pd.read_pickle(p)
        if m.empty:
            continue
        for sym, df in m.groupby("symbol", sort=False):
            if (sym, day) not in daily.index:
                continue
            row = daily.loc[(sym, day)]
            prev, adv = row["prev_close"], row["adv20"]
            if not prev or np.isnan(prev):
                continue
            adv = None if np.isnan(adv) else adv
            df = df.sort_values("timestamp")
            bars = to_bars(df)
            fl = floats.get(sym)
            f = features(df, prev, adv)
            rec = {"symbol": sym, "day": day, "prev_close": prev, "adv20": adv, "float": fl, **f}
            for name, params in VARIANTS.items():
                ts = simulate_day(bars, prev, adv, fl, params)
                for t in ts:
                    trades.append({"variant": name, "symbol": sym, "day": day,
                                   "entry_time": t.entry_time.strftime("%H:%M"), "entry": t.entry,
                                   "stop": t.stop, "target": t.target, "exit_time": t.exit_time.strftime("%H:%M"),
                                   "exit": t.exit, "reason": t.reason, "ret": t.ret,
                                   "net": t.net(params.commission), "r": t.r_multiple})
                rec[f"cam_{list(VARIANTS).index(name)}_net"] = sum(t.net(params.commission) for t in ts)
                rec[f"cam_{list(VARIANTS).index(name)}_n"] = len(ts)
            cands.append(rec)
        log.info("%s tamam", day)
    OUT.mkdir(exist_ok=True)
    tr, cd = pd.DataFrame(trades), pd.DataFrame(cands)
    tr.to_csv(OUT / "islemler.csv", index=False, sep=";", encoding="utf-8-sig")
    cd.to_csv(OUT / "adaylar.csv", index=False, sep=";", encoding="utf-8-sig")
    return tr, cd


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run()
