from datetime import time

from backtest.cameron import Bar, Params, qualifies, simulate_day

P = Params(max_float=None, min_relvol=0.0)


def bar(hh, mm, o, h, l, c, v=10_000):
    return Bar(time(hh, mm), o, h, l, c, v)


def run_up(start_min=0):
    """07:00'dan itibaren 5,0 → 5,5 yükseliş, sonra 2 mumluk geri çekilme."""
    return [
        bar(7, start_min + 0, 4.60, 4.80, 4.55, 4.75),
        bar(7, start_min + 1, 5.15, 5.50, 5.10, 5.45),   # gün tepesi 5,50
        bar(7, start_min + 2, 5.45, 5.46, 5.30, 5.32),   # geri çekilme
        bar(7, start_min + 3, 5.32, 5.38, 5.28, 5.35),   # geri çekilme, dip 5,28, tetik 5,38
    ]


def test_qualifies_rejects_small_gain_and_price_out_of_range():
    assert not qualifies(5.0, 4.8, 1e6, 1e5, None, P)          # +%4
    assert not qualifies(25.0, 10.0, 1e6, 1e5, None, P)        # fiyat > 20
    assert qualifies(5.0, 4.0, 1e6, 1e5, None, P)


def test_qualifies_requires_low_float_when_set():
    p = Params(min_relvol=0.0)
    assert qualifies(5.0, 4.0, 1e6, 1e5, None, p)              # float bilinmiyor → geçer
    assert not qualifies(5.0, 4.0, 1e6, 1e5, 30e6, p)
    assert qualifies(5.0, 4.0, 1e6, 1e5, 8e6, p)


def test_qualifies_relative_volume():
    p = Params(max_float=None, min_relvol=1.0)
    assert not qualifies(5.0, 4.0, 50_000, 100_000, None, p)
    assert qualifies(5.0, 4.0, 150_000, 100_000, None, p)


def test_breakout_after_pullback_hits_target():
    bars = run_up() + [
        bar(7, 4, 5.36, 5.42, 5.35, 5.41),   # 5,38 aşıldı → giriş 5,38, stop 5,28, hedef 5,58
        bar(7, 5, 5.41, 5.60, 5.40, 5.58),   # hedef
    ]
    trades = simulate_day(bars, prev_close=4.0, adv20=1e5, float_shares=None, p=P)
    assert len(trades) == 1
    t = trades[0]
    assert (t.entry, t.stop, t.reason) == (5.38, 5.28, "HEDEF")
    assert abs(t.exit - 5.58) < 1e-9
    assert abs(t.r_multiple - 2.0) < 1e-9


def test_stop_out_and_same_bar_conflict_counts_as_stop():
    bars = run_up() + [
        bar(7, 4, 5.36, 5.42, 5.35, 5.41),
        bar(7, 5, 5.41, 5.70, 5.20, 5.30),   # hem hedef hem stop → STOP
    ]
    t = simulate_day(bars, 4.0, 1e5, None, P)[0]
    assert t.reason == "STOP" and t.exit == 5.28


def test_no_entry_before_start_time():
    bars = [Bar(time(6, b.t.minute), b.open, b.high, b.low, b.close, b.volume) for b in run_up()]
    bars.append(bar(6, 4, 5.36, 5.42, 5.35, 5.41))
    assert simulate_day(bars, 4.0, 1e5, None, P) == []


def test_no_entry_when_pullback_below_vwap():
    heavy = [bar(7, 0, 5.60, 5.62, 5.58, 5.60, 10_000_000)]   # VWAP ≈ 5,60
    bars = heavy + [Bar(time(7, b.t.minute + 1), b.open, b.high + 0.2, b.low, b.close, b.volume)
                    for b in run_up()] + [bar(7, 5, 5.36, 5.62, 5.35, 5.41)]
    assert simulate_day(bars, 4.0, 1e5, None, P) == []


def test_eod_exit_closes_open_position():
    bars = run_up() + [bar(7, 4, 5.36, 5.42, 5.35, 5.41), bar(15, 55, 5.40, 5.45, 5.39, 5.44)]
    t = simulate_day(bars, 4.0, 1e5, None, P)[0]
    assert t.reason == "SÜRE" and t.exit == 5.44


def test_commission_reduces_net_return():
    bars = run_up() + [bar(7, 4, 5.36, 5.42, 5.35, 5.41), bar(7, 5, 5.41, 5.60, 5.40, 5.58)]
    t = simulate_day(bars, 4.0, 1e5, None, P)[0]
    assert abs(t.net(0.002) - (t.ret - 0.002)) < 1e-12
