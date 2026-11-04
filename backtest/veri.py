"""Backtest verisi (Alpaca SIP, premarket dahil) — yerel önbelleğe yazar.

Adımlar:
  1. Hisse evreni: NASDAQ / NYSE / AMEX, işlem gören, ETF/fon olmayan semboller.
  2. Günlük mumlar (ham fiyat) → backtest/data/gunluk.pkl
  3. "Hareketli gün" adayları: açılış önceki kapanışın ≥ %5 üstünde VEYA gün içi
     tepe ≥ %10 üstünde (listedeki gibi açılıştan sonra yükselenleri de yakalamak için).
  4. Adayların o günkü 1 dk mumları 04:00–16:00 NY → backtest/data/dk/YYYY-MM-DD.pkl

Kullanım:
    python -m backtest.veri --start 2026-09-01 --end 2026-10-30
"""
from __future__ import annotations

import argparse
import logging
import os
import pickle
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest
from dotenv import load_dotenv

from sistem.engine import NY

log = logging.getLogger("veri")

DATA_DIR = Path(__file__).parent / "data"
MINUTE_DIR = DATA_DIR / "dk"
EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}
FUND_NAME = re.compile(r"\b(ETFs?|ETNs?|Fund|Trust)\b|ProShares|iShares|Direxion", re.I)
DAILY_CHUNK = 400
MINUTE_CHUNK = 60
ADV_LOOKBACK_DAYS = 45          # ADV20 için başlangıçtan önce alınacak takvim günü
MIN_OPEN_GAP = 0.05
MIN_HIGH_GAP = 0.10
MIN_PREV_CLOSE = 0.20


def clients() -> tuple[StockHistoricalDataClient, TradingClient]:
    load_dotenv()
    key, secret = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
    return StockHistoricalDataClient(key, secret), TradingClient(key, secret, paper=True, raw_data=True)


def universe(trading: TradingClient) -> dict[str, str]:
    """sembol → borsa (TradingView öneki için)."""
    assets = trading.get_all_assets(GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE))
    return {a["symbol"]: a["exchange"] for a in assets
            if a["exchange"] in EXCHANGES and a["tradable"] and a["symbol"].isalpha()
            and len(a["symbol"]) <= 5 and not FUND_NAME.search(a["name"] or "")}


def daily_bars(hist: StockHistoricalDataClient, symbols: list[str], start: date, end: date) -> pd.DataFrame:
    frames = []
    for i in range(0, len(symbols), DAILY_CHUNK):
        chunk = symbols[i:i + DAILY_CHUNK]
        req = StockBarsRequest(symbol_or_symbols=chunk, timeframe=TimeFrame.Day,
                               start=datetime.combine(start, time()), end=datetime.combine(end, time(23)),
                               feed=DataFeed.SIP, adjustment=Adjustment.SPLIT)
        df = hist.get_stock_bars(req).df
        if not df.empty:
            frames.append(df)
        log.info("günlük %d/%d", min(i + DAILY_CHUNK, len(symbols)), len(symbols))
    out = pd.concat(frames).reset_index()
    out["day"] = out["timestamp"].dt.tz_convert(NY).dt.date
    return out[["symbol", "day", "open", "high", "low", "close", "volume"]]


def add_daily_features(d: pd.DataFrame) -> pd.DataFrame:
    """Önceki kapanış ve önceki 20 günün ortalama hacmi (o günü içermez)."""
    d = d.sort_values(["symbol", "day"]).copy()
    g = d.groupby("symbol")
    d["prev_close"] = g["close"].shift(1)
    d["adv20"] = g["volume"].transform(lambda v: v.shift(1).rolling(20, min_periods=10).mean())
    return d


def mover_days(d: pd.DataFrame, start: date) -> pd.DataFrame:
    m = d[(d["day"] >= start) & (d["prev_close"] >= MIN_PREV_CLOSE)]
    gap = m["open"] / m["prev_close"] - 1
    hi = m["high"] / m["prev_close"] - 1
    return m[(gap >= MIN_OPEN_GAP) | (hi >= MIN_HIGH_GAP)]


def minute_bars_for_day(hist: StockHistoricalDataClient, day: date, symbols: list[str]) -> pd.DataFrame:
    start = datetime.combine(day, time(4), NY)
    end = datetime.combine(day, time(16), NY)
    frames = []
    for i in range(0, len(symbols), MINUTE_CHUNK):
        req = StockBarsRequest(symbol_or_symbols=symbols[i:i + MINUTE_CHUNK], timeframe=TimeFrame.Minute,
                               start=start, end=end, feed=DataFeed.SIP, adjustment=Adjustment.SPLIT)
        df = hist.get_stock_bars(req).df
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames).reset_index()
    out["timestamp"] = out["timestamp"].dt.tz_convert(NY)
    return out[["symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    MINUTE_DIR.mkdir(parents=True, exist_ok=True)
    hist, trading = clients()

    uni = universe(trading)
    log.info("evren: %d hisse", len(uni))
    pickle.dump(uni, open(DATA_DIR / "evren.pkl", "wb"))

    d = add_daily_features(daily_bars(hist, sorted(uni), a.start - timedelta(days=ADV_LOOKBACK_DAYS), a.end))
    d.to_pickle(DATA_DIR / "gunluk.pkl")
    movers = mover_days(d, a.start)
    log.info("hareketli gün: %d (%d gün)", len(movers), movers["day"].nunique())

    for day, grp in movers.groupby("day"):
        path = MINUTE_DIR / f"{day}.pkl"
        if path.exists():
            continue
        bars = minute_bars_for_day(hist, day, sorted(grp["symbol"]))
        bars.to_pickle(path)
        log.info("%s: %d hisse, %d mum", day, grp["symbol"].nunique(), len(bars))


if __name__ == "__main__":
    main()
