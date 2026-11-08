from datetime import date, datetime, timedelta

import pytest

from sistem.engine import NY, BarAggregator, Params, SymbolEngine, is_last_regular_bar


def ny(h, m, d=date(2026, 10, 30)):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=NY)


def feed(eng, start, closes, vol=1000.0):
    sigs = []
    for i, c in enumerate(closes):
        s = eng.on_bar(start + timedelta(minutes=i), c, c + 0.01, c - 0.01, c, vol)
        if s:
            sigs.append(s)
    return sigs


def flat_then_breakout(n_flat=60):
    return [10.0 + (0.001 if i % 2 else 0) for i in range(n_flat)] + [10.5]


def test_breakout_gives_al_in_regular_session():
    eng = SymbolEngine("T", Params(use_macd=False), prev_close=9.0, prev_close_day=date(2026, 10, 30))
    sigs = feed(eng, ny(10, 0), flat_then_breakout())
    assert [s.kind for s in sigs] == ["AL"]
    assert sigs[0].price == 10.5
    assert sigs[0].day_change == pytest.approx((10.5 / 9.0 - 1) * 100)


def test_no_entry_in_premarket_by_default():
    eng = SymbolEngine("T", Params(use_macd=False), prev_close=9.0, prev_close_day=date(2026, 10, 30))
    assert feed(eng, ny(7, 0), flat_then_breakout()) == []


def test_premarket_entry_when_allowed():
    eng = SymbolEngine("T", Params(use_macd=False, allow_pre=True), prev_close=9.0,
                       prev_close_day=date(2026, 10, 30))
    assert [s.kind for s in feed(eng, ny(7, 0), flat_then_breakout())] == ["AL"]


def test_min_gain_blocks_entry():
    eng = SymbolEngine("T", Params(use_macd=False, min_gain=50), prev_close=9.0,
                       prev_close_day=date(2026, 10, 30))
    assert feed(eng, ny(10, 0), flat_then_breakout()) == []


def test_exit_after_drop_below_vwma_and_ema():
    eng = SymbolEngine("T", Params(use_macd=False))
    sigs = feed(eng, ny(10, 0), flat_then_breakout() + [10.6, 9.0])
    assert [s.kind for s in sigs] == ["AL", "SAT"]
    assert sigs[1].pnl_pct == pytest.approx((9.0 / 10.5 - 1) * 100)


def test_max_bars_exit():
    eng = SymbolEngine("T", Params(use_macd=False, max_bars=3, use_stop=False))
    sigs = feed(eng, ny(10, 0), flat_then_breakout() + [10.6, 10.7, 10.8, 10.9])
    assert [s.kind for s in sigs] == ["AL", "SÜRE"]


def test_eod_exit_on_last_regular_bar():
    eng = SymbolEngine("T", Params(use_macd=False, max_bars=0, use_stop=False))
    closes = flat_then_breakout() + [10.6 + i * 0.01 for i in range(3)]
    start = ny(16, 0) - timedelta(minutes=len(closes))
    sigs = feed(eng, start, closes)
    assert [s.kind for s in sigs] == ["AL", "GÜN SONU"]


def test_day_change_uses_previous_regular_close_from_history():
    eng = SymbolEngine("T", Params(use_macd=False), prev_close_day=date(2000, 1, 1))
    feed(eng, ny(15, 58, date(2026, 10, 29)), [8.0, 8.0])
    sig = feed(eng, ny(10, 0), flat_then_breakout())[0]
    assert sig.day_change == pytest.approx((sig.price / 8.0 - 1) * 100)


def test_last_regular_bar_depends_on_timeframe():
    assert is_last_regular_bar(ny(15, 59), 1)
    assert is_last_regular_bar(ny(15, 55), 5)
    assert not is_last_regular_bar(ny(15, 59), 5)


def test_half_day_closes_at_13():
    half = date(2025, 11, 28)
    assert is_last_regular_bar(ny(12, 59, half), 1)
    assert not is_last_regular_bar(ny(15, 59, half), 1)


def test_aggregator_closes_bucket_on_last_minute():
    agg = BarAggregator(5)
    out = []
    for i in range(5):
        out += agg.add(ny(10, i), 1 + i, 2 + i, 0.5, 1.5 + i, 10)
    assert out == [(ny(10, 0), 1, 6, 0.5, 5.5, 50)]


def test_aggregator_flushes_incomplete_bucket_when_next_starts():
    agg = BarAggregator(5)
    assert agg.add(ny(10, 0), 1, 2, 0.5, 1.5, 10) == []
    out = agg.add(ny(10, 6), 3, 4, 2, 3.5, 20)
    assert out == [(ny(10, 0), 1, 2, 0.5, 1.5, 10)]


def test_aggregator_passthrough_for_1min():
    assert BarAggregator(1).add(ny(10, 0), 1, 2, 0, 1, 5) == [(ny(10, 0), 1, 2, 0, 1, 5)]
