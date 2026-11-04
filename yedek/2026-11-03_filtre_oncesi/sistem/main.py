"""Buğra Günlük Trade Sistemi — canlı çalıştırıcı.

  1) Her SCAN_INTERVAL saniyede yükselen hisseleri tarar (premarket dahil).
  2) Yeni bulunan hisse için geçmiş mumları çekip göstergeleri ısıtır,
     Alpaca websocket'ine abone olur.
  3) Her mum KAPANDIĞI anda Pine kurallarını çalıştırır; sinyal varsa
     Telegram'a hemen yollar ve sinyaller.csv dosyasına yazar.

Çalıştırma:  python -m sistem.main
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetCalendarRequest
from dotenv import load_dotenv

from zoneinfo import ZoneInfo

from .engine import NY, BarAggregator, Params, Signal, SymbolEngine, set_early_closes
from .scanner import GainerScanner
from .telegram import Telegram

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("sistem")


def env(name, default, cast=str):
    v = os.getenv(name)
    if v is None or v == "":
        return default
    if cast is bool:
        return v.strip().lower() in ("1", "true", "evet", "yes", "on")
    return cast(v)


KEY, SECRET = env("ALPACA_API_KEY", ""), env("ALPACA_SECRET_KEY", "")
FEED = DataFeed.SIP if env("ALPACA_FEED", "iex").lower() == "sip" else DataFeed.IEX
TF = env("TIMEFRAME_MIN", 1, int)
P = Params(
    timeframe_min=TF,
    use_macd=env("MACD_FILTER", True, bool),
    allow_pre=env("PREMARKET_ENTRY", False, bool),
    min_gain=env("MIN_GAIN_PCT", 5.0, float),
    max_bars=env("MAX_BARS", 20, int),
    eod_exit=env("EOD_EXIT", True, bool),
)
SCAN_INTERVAL = env("SCAN_INTERVAL_SEC", 60, int)
MAX_SYMBOLS = env("MAX_SYMBOLS", 30, int)   # Alpaca ücretsiz planda websocket limiti 30 sembol
CSV_PATH = ROOT / "sinyaller.csv"

TR = ZoneInfo("Europe/Istanbul")
EMOJI = {"AL": "🟢", "SAT": "🔴", "STOP": "🟠", "SÜRE": "⚪", "GÜN SONU": "🟣"}


class System:
    def __init__(self):
        if not KEY or not SECRET:
            raise SystemExit(".env dosyasına ALPACA_API_KEY ve ALPACA_SECRET_KEY yaz.")
        self.tg = Telegram(env("TELEGRAM_BOT_TOKEN", ""), env("TELEGRAM_CHAT_ID", ""))
        self.hist = StockHistoricalDataClient(KEY, SECRET)
        self.stream = StockDataStream(KEY, SECRET, feed=FEED)
        self.scanner = GainerScanner(
            KEY, SECRET, FEED, min_gain=P.min_gain,
            min_price=env("MIN_PRICE", 0.2, float), max_price=env("MAX_PRICE", 500.0, float),
            min_volume=env("MIN_VOLUME", 5_000, float), max_symbols=MAX_SYMBOLS)
        self.engines: dict[str, SymbolEngine] = {}
        self.aggs: dict[str, BarAggregator] = {}
        self.pending: dict[str, list] = {}       # ısınma bitene kadar gelen canlı mumlar
        self.lock = threading.Lock()
        self.day = datetime.now(NY).date()
        self.today_signals: list[Signal] = []
        self.summary_sent = False
        self._load_calendar()

    def _load_calendar(self):
        """Yarım günleri (13:00 kapanış) Alpaca borsa takviminden al."""
        try:
            today = datetime.now(NY).date()
            days = TradingClient(KEY, SECRET, paper=True).get_calendar(
                GetCalendarRequest(start=today - timedelta(days=10), end=today + timedelta(days=60)))
            early = {d.date for d in days if d.close.time() < dtime(16, 0)}
            if early:
                set_early_closes(early)
            log.info("borsa takvimi yüklendi, yarım günler: %s", sorted(early) or "yok")
        except Exception as e:  # noqa: BLE001
            log.warning("borsa takvimi alınamadı, yerleşik yarım gün listesi kullanılıyor: %s", e)

    # ---------------------------------------------------------- tarama
    def scan_loop(self):
        while True:
            try:
                self._new_day_check()
                movers = self.scanner.scan()
                for m in movers:
                    if m.symbol not in self.engines and len(self.engines) < MAX_SYMBOLS:
                        self._add_symbol(m)
                self._daily_summary_check()
            except Exception as e:  # noqa: BLE001
                log.exception("tarama hatası: %s", e)
            time.sleep(SCAN_INTERVAL)

    def _add_symbol(self, m):
        sym = m.symbol
        with self.lock:
            self.pending[sym] = []
        self.stream.subscribe_bars(self.on_bar, sym)       # önce abone ol, mum kaçmasın
        eng = SymbolEngine(sym, P, prev_close=m.prev_close)
        try:
            req = StockBarsRequest(symbol_or_symbols=sym, feed=FEED,
                                   timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                                   start=datetime.now(timezone.utc) - timedelta(days=5))
            df = self.hist.get_stock_bars(req).df
            if not df.empty:
                df = df.xs(sym, level=0) if "symbol" in df.index.names else df
                df = df.iloc[:-1]                       # henüz kapanmamış olabilecek son mum
                agg = BarAggregator(TF)
                rows = []
                for ts, r in df.iterrows():
                    rows += agg.add(ts, r.open, r.high, r.low, r.close, r.volume)
                hdf = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
                eng.warmup(hdf)
                self.aggs[sym] = agg
                last_ts = df.index[-1]
            else:
                self.aggs[sym] = BarAggregator(TF)
                last_ts = None
        except Exception as e:  # noqa: BLE001
            log.warning("%s geçmiş veri alınamadı: %s", sym, e)
            self.aggs[sym] = BarAggregator(TF)
            last_ts = None
        with self.lock:
            self.engines[sym] = eng
            buffered = self.pending.pop(sym, [])
        for b in buffered:
            if last_ts is None or b.timestamp > last_ts:
                self._process(b, latency_check=False)
        log.info("izlemeye alındı: %s (+%.1f%%, %.4g$)", sym, m.change_pct, m.price)
        self.tg.send(f"👀 <b>{sym}</b> izlemede · gün <b>{m.change_pct:+.1f}%</b> · {m.price:.4g}$")

    # ---------------------------------------------------------- canlı mum
    async def on_bar(self, bar):
        with self.lock:
            if bar.symbol in self.pending:
                self.pending[bar.symbol].append(bar)
                return
        self._process(bar)

    def _process(self, bar, latency_check=True):
        sym = bar.symbol
        eng, agg = self.engines.get(sym), self.aggs.get(sym)
        if not eng:
            return
        for ts, o, h, l, c, v in agg.add(bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume):
            sig = eng.on_bar(ts, o, h, l, c, v)
            if sig:
                close_time = ts + timedelta(minutes=TF)
                lag = (datetime.now(timezone.utc) - close_time.astimezone(timezone.utc)).total_seconds()
                self._emit(sig, lag if latency_check else None)

    def _emit(self, s: Signal, lag: float | None):
        self.today_signals.append(s)
        t = s.bar_time.astimezone(NY)
        lines = [f"{EMOJI.get(s.kind, '•')} <b>{s.kind}  {s.symbol}</b>  {s.price:.4g}$"]
        tc = t + timedelta(minutes=TF)          # mumun kapanış saati
        info = f"{TF}dk mum kapanışı {tc:%H:%M} NY ({tc.astimezone(TR):%H:%M} TR)"
        if s.day_change is not None:
            info = f"Gün {s.day_change:+.1f}% · " + info
        lines.append(info)
        if s.pnl_pct is not None:
            lines.append(f"İşlem sonucu: <b>{s.pnl_pct:+.2f}%</b> (giriş {s.entry_price:.4g}$)")
        lines.append(s.detail)
        if lag is not None:
            lines.append(f"⏱ mum kapanışından {lag:.1f} sn sonra")
        self.tg.send("\n".join(lines))
        log.info("SİNYAL %s %s %.4g lag=%s", s.kind, s.symbol, s.price, lag)
        new = not CSV_PATH.exists()
        with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["zaman_ny", "sembol", "sinyal", "fiyat", "gun_degisim_%", "islem_%", "aciklama", "gecikme_sn"])
            w.writerow([t.isoformat(), s.symbol, s.kind, s.price,
                        None if s.day_change is None else round(s.day_change, 2),
                        None if s.pnl_pct is None else round(s.pnl_pct, 2), s.detail,
                        None if lag is None else round(lag, 1)])

    # ---------------------------------------------------------- gün yönetimi
    def _new_day_check(self):
        today = datetime.now(NY).date()
        if today != self.day:
            syms = list(self.engines)
            if syms:
                self.stream.unsubscribe_bars(*syms)
            self.engines.clear(); self.aggs.clear(); self.today_signals.clear()
            self.day, self.summary_sent = today, False
            log.info("yeni gün: izleme listesi sıfırlandı")

    def _daily_summary_check(self):
        now = datetime.now(NY)
        if self.summary_sent or now.time() < dtime(16, 5):
            return
        self.summary_sent = True
        closed = [s for s in self.today_signals if s.pnl_pct is not None]
        if not self.today_signals:
            return
        win = sum(1 for s in closed if s.pnl_pct > 0)
        tot = sum(s.pnl_pct for s in closed)
        body = "\n".join(f"{s.symbol} {s.kind} {s.pnl_pct:+.2f}%" for s in closed[-25:])
        self.tg.send(f"📊 <b>Gün özeti</b> · {len(closed)} işlem · {win} kazanç · toplam {tot:+.2f}%\n{body}")

    # ---------------------------------------------------------- başlat
    def run(self):
        self.tg.send(
            f"✅ Sistem başladı · {TF}dk · MACD filtresi {'AÇIK' if P.use_macd else 'KAPALI'} · "
            f"premarket giriş {'AÇIK' if P.allow_pre else 'KAPALI'} · min artış {P.min_gain:g}% · "
            f"veri {FEED.value.upper()}")
        threading.Thread(target=self.scan_loop, daemon=True).start()
        # Websocket koparsa alpaca-py kendisi yeniden bağlanır; tamamen düşerse burada yeniden başlatılır.
        while True:
            try:
                self.stream.run()
            except Exception as e:  # noqa: BLE001
                log.exception("akış düştü, 5 sn sonra yeniden bağlanıyor: %s", e)
                time.sleep(5)


if __name__ == "__main__":
    System().run()
