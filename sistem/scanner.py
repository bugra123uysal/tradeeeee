"""Yükselen hisse tarayıcı (Alpaca).

Aday listesi: Alpaca "en çok yükselenler" + "en aktifler".
Her aday için anlık görüntü (snapshot) alınır, önceki gün kapanışına göre
değişim hesaplanır ve fiyat / değişim / hacim eşikleriyle süzülür.
Premarket'te de çalışır: o saatte günlük mum henüz dünündür, değişim son
işlem fiyatına göre hesaplanır, hacim eşiği uygulanmaz.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

from alpaca.data.enums import DataFeed, MostActivesBy
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.requests import MarketMoversRequest, MostActivesRequest, StockSnapshotRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from .engine import NY

log = logging.getLogger("scanner")

TOP_GAINERS = 50          # Alpaca üst sınırı 50
TOP_ACTIVES = 100
SNAPSHOT_CHUNK = 100
# filtreler/*.json ile aynı: sadece hisse, NASDAQ / NYSE / AMEX (ARCA, BATS = ETF borsaları hariç)
EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}
FUND_NAME = re.compile(r"\b(ETFs?|ETNs?|Fund)\b|ProShares|iShares|Direxion", re.I)


@dataclass(frozen=True)
class Mover:
    symbol: str
    price: float
    change_pct: float
    prev_close: float
    volume: float


def is_plain_ticker(sym: str) -> bool:
    """Varant / hak / ünite gibi uzantılı sembolleri ele (ABCDW, ABCD.U …)."""
    return sym.isalpha() and len(sym) <= 5


class GainerScanner:
    def __init__(self, key: str, secret: str, feed: DataFeed, min_gain: float,
                 min_price: float, max_price: float, min_volume: float, max_symbols: int):
        self.screener = ScreenerClient(key, secret)
        self.hist = StockHistoricalDataClient(key, secret)
        # raw_data: alpaca-py enum'u yeni borsaları (ör. TXSE) tanımayıp hata veriyor
        self.trading = TradingClient(key, secret, paper=True, raw_data=True)
        self.feed = feed
        self.min_gain, self.min_price, self.max_price = min_gain, min_price, max_price
        self.min_volume, self.max_symbols = min_volume, max_symbols
        self.stocks: set[str] | None = None
        self.stocks_day: date | None = None

    def _stock_universe(self) -> set[str] | None:
        """Günde bir kez: NASDAQ/NYSE/AMEX'te işlem gören, fon/ETF olmayan semboller."""
        today = datetime.now(NY).date()
        if self.stocks is not None and self.stocks_day == today:
            return self.stocks
        try:
            assets = self.trading.get_all_assets(
                GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE))
            self.stocks = {a["symbol"] for a in assets
                           if a["exchange"] in EXCHANGES and a["tradable"] and not FUND_NAME.search(a["name"])}
            self.stocks_day = today
            log.info("hisse evreni: %d sembol", len(self.stocks))
        except Exception as e:  # noqa: BLE001
            log.warning("varlık listesi alınamadı, ETF süzgeci bu tur atlandı: %s", e)
        return self.stocks

    def candidates(self) -> list[str]:
        syms: set[str] = set()
        movers = self.screener.get_market_movers(MarketMoversRequest(top=TOP_GAINERS))
        syms.update(m.symbol for m in movers.gainers)
        actives = self.screener.get_most_actives(MostActivesRequest(top=TOP_ACTIVES, by=MostActivesBy.VOLUME))
        syms.update(a.symbol for a in actives.most_actives)
        universe = self._stock_universe()
        return sorted(s for s in syms if is_plain_ticker(s) and (universe is None or s in universe))

    def scan(self) -> list[Mover]:
        syms = self.candidates()
        today = datetime.now(NY).date()
        out: list[Mover] = []
        for i in range(0, len(syms), SNAPSHOT_CHUNK):
            chunk = syms[i:i + SNAPSHOT_CHUNK]
            snaps = self.hist.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=chunk, feed=self.feed))
            for sym, s in snaps.items():
                m = self._evaluate(sym, s, today)
                if m:
                    out.append(m)
        out.sort(key=lambda m: m.change_pct, reverse=True)
        log.info("tarama: %d aday, %d uygun", len(syms), len(out))
        return out[: self.max_symbols]

    def _evaluate(self, sym: str, s, today) -> Mover | None:
        if not s or not s.latest_trade or not s.daily_bar:
            return None
        daily_is_today = s.daily_bar.timestamp.astimezone(NY).date() == today
        if daily_is_today:
            if not s.previous_daily_bar:
                return None
            # seans içi/sonrası: günlük mumun kapanışı (seans sonrası tek tük hatalı işlemler
            # yanıltmasın — ör. INO 30 Ekim: gün 0,60 $ ama 16:44'te 1,90 $ işlem)
            price = float(s.daily_bar.close)
            prev, volume = float(s.previous_daily_bar.close), float(s.daily_bar.volume)
        else:                                   # premarket: günlük mum hâlâ dünün
            price = float(s.latest_trade.price)
            prev, volume = float(s.daily_bar.close), None
        if prev <= 0 or not (self.min_price <= price <= self.max_price):
            return None
        chg = (price / prev - 1) * 100
        if chg < self.min_gain:
            return None
        if volume is not None and volume < self.min_volume:
            return None
        return Mover(sym, price, chg, prev, volume or 0.0)
