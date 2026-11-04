"""Ross Cameron tarzı momentum kuralları — 1 dk mumlarda gün içi simülasyon.

Hisse seçimi ("5 sütun", haber hariç — geçmişte test edilemez):
  * fiyat min_price–max_price $
  * önceki kapanışa göre ≥ min_gain yükselmiş
  * göreceli hacim: girişe kadarki toplam hacim ≥ min_relvol × 20 günlük ort. hacim
  * float < max_float (max_float None ise uygulanmaz; float bilinmiyorsa geçer —
    çoğu yeni halka arz, ör. AIOK, TradingView'da float'sız ve küçük float'lı)

Giriş ("micro pullback" / ilk yeni tepe mumu):
  * gün tepesi (04:00'dan beri) yapılır, sonra en az 1 mum yeni tepe yapmaz (geri çekilme)
  * bir mum önceki mumun tepesini aşınca AL (önceki mumun tepesinden; açılış daha yüksekse açılıştan)
  * geri çekilmenin dibi VWAP'ın üstünde olmalı
  * stop = geri çekilmenin dibi, risk ≤ max_risk_pct, hedef = giriş + rr × risk
  * yalnızca start–end saatleri arasında (NY), hisse başına günde en fazla max_trades işlem,
    max_losses zarardan sonra o hisse o gün bırakılır

Çıkış: stop, hedef ya da eod_exit saatinde kapanış. Aynı mumda stop ve hedef ikisi
birden görülürse stop sayılır (temkinli). Giriş mumunda yalnızca kapanış stop altındaysa çıkılır.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time
from typing import Sequence


@dataclass(frozen=True)
class Params:
    min_price: float = 2.0
    max_price: float = 20.0
    min_gain: float = 0.10
    min_relvol: float = 1.0
    max_float: float | None = 20e6
    start: time = time(7, 0)
    end: time = time(11, 0)
    eod_exit: time = time(15, 55)
    rr: float = 2.0
    max_risk_pct: float = 0.10
    max_trades: int = 3
    max_losses: int = 2
    commission: float = 0.002       # gidiş-dönüş toplam


@dataclass(frozen=True)
class Bar:
    t: time
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Trade:
    entry_time: time
    entry: float
    stop: float
    target: float
    exit_time: time
    exit: float
    reason: str                    # STOP / HEDEF / SÜRE

    @property
    def ret(self) -> float:
        return self.exit / self.entry - 1

    def net(self, commission: float) -> float:
        return self.ret - commission

    @property
    def r_multiple(self) -> float:
        return (self.exit - self.entry) / (self.entry - self.stop)


@dataclass(frozen=True)
class _Setup:
    hod: float
    pb_low: float | None           # None → geri çekilme henüz yok
    trigger: float | None          # önceki mumun tepesi


def qualifies(price: float, prev_close: float, cum_vol: float, adv20: float | None,
              float_shares: float | None, p: Params) -> bool:
    if not (p.min_price <= price <= p.max_price) or prev_close <= 0:
        return False
    if price / prev_close - 1 < p.min_gain:
        return False
    if not adv20 or cum_vol < p.min_relvol * adv20:
        return False
    if p.max_float is not None and float_shares is not None and float_shares >= p.max_float:
        return False
    return True


def _next_setup(s: _Setup | None, b: Bar) -> _Setup:
    if s is None or b.high > s.hod:
        return _Setup(hod=b.high if s is None else max(s.hod, b.high), pb_low=None, trigger=None)
    low = b.low if s.pb_low is None else min(s.pb_low, b.low)
    return _Setup(hod=s.hod, pb_low=low, trigger=b.high)


def _exit_on(bar: Bar, stop: float, target: float, entry_bar: bool) -> tuple[float, str] | None:
    if entry_bar:
        return (stop, "STOP") if bar.close <= stop else None
    if bar.open <= stop:
        return bar.open, "STOP"
    if bar.low <= stop:
        return stop, "STOP"
    if bar.open >= target:
        return bar.open, "HEDEF"
    if bar.high >= target:
        return target, "HEDEF"
    return None


def simulate_day(bars: Sequence[Bar], prev_close: float, adv20: float | None,
                 float_shares: float | None, p: Params = Params()) -> list[Trade]:
    """bars: tek hissenin tek günü, 04:00'dan itibaren sıralı 1 dk mumlar."""
    trades: list[Trade] = []
    setup: _Setup | None = None
    cum_vol = cum_pv = 0.0
    pos: dict | None = None
    losses = 0
    day_hod = 0.0
    for b in bars:
        vwap_prev = cum_pv / cum_vol if cum_vol else None
        if pos is not None:
            hit = _exit_on(b, pos["stop"], pos["target"], entry_bar=False)
            if hit is None and b.t >= p.eod_exit:
                hit = (b.close, "SÜRE")
            if hit is not None:
                trades.append(Trade(pos["t"], pos["entry"], pos["stop"], pos["target"], b.t, hit[0], hit[1]))
                losses += hit[0] < pos["entry"]
                pos, setup = None, _Setup(day_hod, None, None)
        elif (setup is not None and setup.trigger is not None and b.high > setup.trigger
              and p.start <= b.t < p.end and len(trades) < p.max_trades and losses < p.max_losses
              and vwap_prev is not None and setup.pb_low > vwap_prev):
            entry = max(setup.trigger, b.open)
            stop = setup.pb_low
            risk = entry - stop
            if 0 < risk <= p.max_risk_pct * entry and qualifies(entry, prev_close, cum_vol, adv20, float_shares, p):
                target = entry + p.rr * risk
                hit = _exit_on(b, stop, target, entry_bar=True)
                if hit is not None:
                    trades.append(Trade(b.t, entry, stop, target, b.t, hit[0], hit[1]))
                    losses += 1
                    setup = _Setup(max(day_hod, b.high), None, None)
                else:
                    pos = {"t": b.t, "entry": entry, "stop": stop, "target": target}
        day_hod = max(day_hod, b.high)
        cum_vol += b.volume
        cum_pv += b.volume * (b.high + b.low + b.close) / 3
        if pos is None:
            setup = _next_setup(setup, b)
        elif setup is not None:
            setup = _Setup(day_hod, None, None)
    return trades


def variant(**kw) -> Params:
    return replace(Params(), **kw)
