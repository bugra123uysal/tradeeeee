"""Sinyal motoru — pine/coklu_swing_intraday_macd.pine kurallarının birebir aynısı.

  AL   : [kapanış BB(20,2) üst bandını yukarı keser
          VEYA ADX(14) > 25 ve +DI > −DI durumu yeni başlar]
         VE (açıksa) MACD(12,26,9) > sinyal
         VE (açıksa) önceki gün kapanışına göre artış ≥ min_gain %
  SAT  : kapanış hem VWMA(20) hem EMA(50) altında
  STOP : giriş − 1.5 × ATR(14) (kapanış bazlı)
  SÜRE : max_bars mum doldu
  GÜN SONU : normal seansın son mumunda açık işlem kapanır

Pine `process_orders_on_close = true` ile çalışır: emir mumun kapanış fiyatından
dolar ve giriş mumunda çıkış kontrol edilmez (strategy.position_size henüz 0).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .indicators import ATR, BB, DMI, EMA, MACD, VWMA

NY = ZoneInfo("America/New_York")

PRE_OPEN_MIN = 4 * 60            # 04:00 NY
REG_OPEN_MIN = 9 * 60 + 30       # 09:30 NY
REG_CLOSE_MIN = 16 * 60          # 16:00 NY
EARLY_CLOSE_MIN = 13 * 60        # yarım gün kapanışı 13:00 NY

# NYSE yarım günleri. Canlı sistem açılışta Alpaca takviminden günceller (set_early_closes).
_early_closes: set[date] = {
    date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
    date(2026, 11, 27), date(2026, 12, 24),
    date(2027, 11, 26),
}


def set_early_closes(days: set[date]) -> None:
    """Yerleşik listeye takvimden gelen yarım günleri ekler."""
    global _early_closes
    _early_closes = _early_closes | set(days)


def close_minute(ts: datetime) -> int:
    return EARLY_CLOSE_MIN if ts.astimezone(NY).date() in _early_closes else REG_CLOSE_MIN


@dataclass(frozen=True)
class Params:
    timeframe_min: int = 1
    use_macd: bool = True
    allow_pre: bool = False
    min_gain: float = 0.0
    max_bars: int = 20
    eod_exit: bool = True
    use_stop: bool = True
    stop_atr: float = 1.5
    bb_len: int = 20
    bb_mult: float = 2.0
    di_len: int = 14
    adx_min: float = 25.0
    vw_len: int = 20
    ema_len: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    macd_sig: int = 9


@dataclass(frozen=True)
class Signal:
    symbol: str
    kind: str                     # AL / SAT / STOP / SÜRE / GÜN SONU
    bar_time: datetime            # mumun AÇILIŞ zamanı
    price: float
    detail: str
    day_change: float | None = None
    pnl_pct: float | None = None
    entry_price: float | None = None


def session_minute(ts: datetime) -> int:
    t = ts.astimezone(NY)
    return t.hour * 60 + t.minute


def is_regular(ts: datetime) -> bool:
    return REG_OPEN_MIN <= session_minute(ts) < close_minute(ts)


def is_premarket(ts: datetime) -> bool:
    return PRE_OPEN_MIN <= session_minute(ts) < REG_OPEN_MIN


def is_last_regular_bar(ts: datetime, tf: int) -> bool:
    return session_minute(ts) == close_minute(ts) - tf


class SymbolEngine:
    def __init__(self, symbol: str, params: Params, prev_close: float | None = None,
                 prev_close_day: date | None = None):
        p = params
        self.symbol, self.p = symbol, p
        self.bb = BB(p.bb_len, p.bb_mult)
        self.dmi = DMI(p.di_len, p.di_len)
        self.vwma = VWMA(p.vw_len)
        self.ema = EMA(p.ema_len)
        self.atr = ATR(14)
        self.macd = MACD(p.macd_fast, p.macd_slow, p.macd_sig)
        # tarayıcıdan gelen önceki kapanış yalnızca BUGÜNÜN mumları için geçerli
        self.given_prev_close = prev_close
        self.given_day = prev_close_day or datetime.now(NY).date()

        self.bar_index = -1
        self.prev_close_val: float | None = None     # crossover için close[1]
        self.prev_bb_up: float | None = None
        self.prev_adx_on = False
        self.day: date | None = None
        self.last_reg_close: float | None = None     # en son normal seans mumunun kapanışı
        self.day_ref: float | None = None            # bugünün "önceki gün kapanışı"

        self.in_pos = False
        self.entry_price = 0.0
        self.entry_bar = 0
        self.stop_px: float | None = None

    # ------------------------------------------------------------------
    def warmup(self, df: pd.DataFrame) -> None:
        """Geçmiş mumlarla göstergeleri ve pozisyon durumunu ısıtır (sinyal yollamaz)."""
        for ts, r in df.iterrows():
            self.on_bar(ts, r.open, r.high, r.low, r.close, r.volume)

    def _roll_day(self, d: date) -> None:
        if d == self.day:
            return
        self.day = d
        if d == self.given_day and self.given_prev_close:
            self.day_ref = self.given_prev_close
        else:
            self.day_ref = self.last_reg_close       # en son normal seans kapanışı = önceki gün

    def on_bar(self, ts: datetime, o: float, h: float, l: float, c: float, v: float) -> Signal | None:
        p = self.p
        self.bar_index += 1
        self._roll_day(ts.astimezone(NY).date())

        _, bb_up, _ = self.bb.update(c)
        di_plus, di_minus, adx = self.dmi.update(h, l, c)
        vw = self.vwma.update(c, v)
        e50 = self.ema.update(c)
        atr = self.atr.update(h, l, c)
        macd_line, sig_line, _ = self.macd.update(c)

        day_chg = None if not self.day_ref else (c / self.day_ref - 1) * 100
        gain_ok = p.min_gain <= 0 or (day_chg is not None and day_chg >= p.min_gain)
        adx_on = adx is not None and adx > p.adx_min and di_plus > di_minus
        entry_bb = (bb_up is not None and self.prev_bb_up is not None and self.prev_close_val is not None
                    and c > bb_up and self.prev_close_val <= self.prev_bb_up)
        entry_adx = adx_on and not self.prev_adx_on
        macd_ok = (not p.use_macd) or (sig_line is not None and macd_line > sig_line)
        exit_sig = vw is not None and e50 is not None and c < vw and c < e50

        regular = is_regular(ts)
        in_window = regular or (p.allow_pre and is_premarket(ts))
        last_bar = is_last_regular_bar(ts, p.timeframe_min)

        sig = None
        if not self.in_pos:
            if in_window and not (p.eod_exit and last_bar) and (entry_bb or entry_adx) and macd_ok and gain_ok:
                self.in_pos, self.entry_price, self.entry_bar = True, c, self.bar_index
                self.stop_px = c - p.stop_atr * atr if (p.use_stop and atr is not None) else None
                why = "BB üst bant kırılımı" if entry_bb else f"ADX {adx:.1f} > {p.adx_min:g}, +DI > −DI"
                if p.use_macd:
                    why += f" · MACD {macd_line:.4g} > {sig_line:.4g}"
                if self.stop_px is not None:
                    why += f" · stop {self.stop_px:.4g}"
                sig = Signal(self.symbol, "AL", ts, c, why, day_chg, entry_price=c)
        else:
            kind = why = None
            if exit_sig:
                kind, why = "SAT", f"Kapanış VWMA20 ({vw:.4g}) ve EMA50 ({e50:.4g}) altında"
            elif p.use_stop and self.stop_px is not None and c < self.stop_px:
                kind, why = "STOP", f"Kapanış stop seviyesinin ({self.stop_px:.4g}) altında"
            elif p.max_bars > 0 and self.bar_index - self.entry_bar >= p.max_bars:
                kind, why = "SÜRE", f"{p.max_bars} mum doldu"
            elif p.eod_exit and last_bar:
                kind, why = "GÜN SONU", "Normal seansın son mumu"
            if kind:
                pnl = (c / self.entry_price - 1) * 100
                sig = Signal(self.symbol, kind, ts, c, why, day_chg, pnl_pct=pnl, entry_price=self.entry_price)
                self.in_pos, self.stop_px = False, None

        if regular:
            self.last_reg_close = c
        self.prev_close_val, self.prev_bb_up, self.prev_adx_on = c, bb_up, adx_on
        return sig


class BarAggregator:
    """1 dk mumları `tf` dakikalık mumlara birleştirir.

    Bir mum, içindeki son dakika geldiği anda kapanır (bir sonrakini beklemez).
    Son dakika hiç gelmezse (işlem yoksa) mum, sonraki kovadan ilk dakika gelince kapanır.
    """

    def __init__(self, tf: int):
        self.tf = tf
        self.cur: list | None = None             # [başlangıç, o, h, l, c, v]

    def _bucket(self, ts: datetime) -> datetime:
        ts = pd.Timestamp(ts)
        return ts.floor(f"{self.tf}min").to_pydatetime()

    def add(self, ts, o, h, l, c, v) -> list[tuple]:
        if self.tf == 1:
            return [(ts, o, h, l, c, v)]
        out = []
        b = self._bucket(ts)
        if self.cur is not None and self.cur[0] != b:
            out.append(tuple(self.cur))
            self.cur = None
        if self.cur is None:
            self.cur = [b, o, h, l, c, v]
        else:
            self.cur = [b, self.cur[1], max(self.cur[2], h), min(self.cur[3], l), c, self.cur[5] + v]
        if pd.Timestamp(ts).to_pydatetime() + timedelta(minutes=1) >= b + timedelta(minutes=self.tf):
            out.append(tuple(self.cur))
            self.cur = None
        return out
