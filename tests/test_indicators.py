import math

import numpy as np
import pandas as pd
import pytest

from sistem.indicators import ATR, BB, DMI, EMA, MACD, RMA, SMA, VWMA


@pytest.fixture
def ohlcv():
    rng = np.random.default_rng(7)
    close = 10 + np.cumsum(rng.normal(0, 0.1, 300))
    high = close + rng.uniform(0, 0.1, 300)
    low = close - rng.uniform(0, 0.1, 300)
    vol = rng.integers(1_000, 50_000, 300).astype(float)
    return pd.DataFrame({"high": high, "low": low, "close": close, "volume": vol})


def run(ind, *cols):
    return [ind.update(*vals) for vals in zip(*cols)]


def test_sma_matches_pandas_rolling(ohlcv):
    got = run(SMA(20), ohlcv.close)
    want = ohlcv.close.rolling(20).mean()
    assert got[18] is None
    assert np.allclose(got[19:], want[19:])


def test_ema_seeds_with_first_value_and_uses_alpha():
    ema = EMA(3)
    assert ema.update(10.0) == 10.0
    assert ema.update(20.0) == pytest.approx(15.0)     # alpha = 0.5


def test_rma_seeds_with_sma():
    rma = RMA(3)
    assert rma.update(1.0) is None
    assert rma.update(2.0) is None
    assert rma.update(3.0) == pytest.approx(2.0)
    assert rma.update(5.0) == pytest.approx(2.0 + (5.0 - 2.0) / 3)


def test_bb_uses_population_stdev(ohlcv):
    got = run(BB(20, 2.0), ohlcv.close)
    mid = ohlcv.close.rolling(20).mean()
    sd = ohlcv.close.rolling(20).std(ddof=0)
    assert np.allclose([g[1] for g in got[19:]], (mid + 2 * sd)[19:])


def test_vwma(ohlcv):
    got = run(VWMA(20), ohlcv.close, ohlcv.volume)
    pv = (ohlcv.close * ohlcv.volume).rolling(20).sum()
    want = pv / ohlcv.volume.rolling(20).sum()
    assert np.allclose(got[19:], want[19:])


def test_atr_first_tr_is_high_minus_low(ohlcv):
    atr = ATR(14)
    vals = run(atr, ohlcv.high, ohlcv.low, ohlcv.close)
    h, l, c = ohlcv.high, ohlcv.low, ohlcv.close
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    tr.iloc[0] = h.iloc[0] - l.iloc[0]
    assert vals[13] == pytest.approx(tr.iloc[:14].mean())


def test_dmi_values_are_bounded(ohlcv):
    got = run(DMI(14, 14), ohlcv.high, ohlcv.low, ohlcv.close)
    plus, minus, adx = got[-1]
    assert 0 <= plus <= 100 and 0 <= minus <= 100 and 0 <= adx <= 100
    first_adx = next(i for i, g in enumerate(got) if g[2] is not None)
    assert first_adx == 14 + 14 - 1          # tr ilk mumda na + iki RMA ısınması


def test_macd_line_is_fast_minus_slow(ohlcv):
    got = run(MACD(12, 26, 9), ohlcv.close)
    fast = ohlcv.close.ewm(span=12, adjust=False).mean()
    slow = ohlcv.close.ewm(span=26, adjust=False).mean()
    assert math.isclose(got[-1][0], fast.iloc[-1] - slow.iloc[-1], rel_tol=1e-9)
