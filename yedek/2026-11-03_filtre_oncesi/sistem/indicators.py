"""Pine `ta.*` fonksiyonlarının birebir (akış / bar-bar) karşılıkları.

Her sınıf `update(...)` ile bir mum alır ve o mumdaki değeri döndürür.
Pine'daki `na` burada `None`'dır. Formüller TradingView Pine v6 belgelerindeki
referans uygulamalarla aynıdır:

  ta.sma, ta.stdev (yanlı / popülasyon), ta.ema (ilk değer = kaynak),
  ta.rma (ilk değer = SMA), ta.tr, ta.atr, ta.dmi, ta.vwma, ta.bb, ta.macd
"""
from __future__ import annotations

import math
from collections import deque

FLOAT_DIGITS = 10        # fiyat farklarını bu basamağa yuvarla (kayan nokta gürültüsü)


class SMA:
    def __init__(self, length: int):
        self.length = length
        self.buf: deque[float] = deque(maxlen=length)
        self.total = 0.0

    def update(self, x: float | None) -> float | None:
        if x is None:                       # Pine: pencerede na varsa sonuç na
            self.buf.clear()
            self.total = 0.0
            return None
        # TradingView sırası: toplam + yeni − eski (kayan nokta sonucu birebir aynı çıksın diye)
        old = self.buf[0] if len(self.buf) == self.length else None
        self.buf.append(x)
        self.total = self.total + x if old is None else (self.total + x) - old
        if len(self.buf) < self.length:
            return None
        return self.total / self.length


class StDev:
    """ta.stdev(src, length) — yanlı (popülasyon) standart sapma.

    TradingView gibi kayan toplamlarla hesaplanır: var = Σx²/n − (Σx/n)².
    Kapanış tam BB bandına eşit olduğunda (≈1e-15 fark) kırılım kararının
    TradingView ile aynı çıkması için bu yöntem gerekli (GDYN 1 dk ile doğrulandı).
    """

    def __init__(self, length: int):
        self.length = length
        self.buf: deque[float] = deque(maxlen=length)
        self.total = 0.0
        self.total_sq = 0.0

    def update(self, x: float) -> float | None:
        old = self.buf[0] if len(self.buf) == self.length else None
        self.buf.append(x)
        if old is None:
            self.total, self.total_sq = self.total + x, self.total_sq + x * x
        else:
            self.total = (self.total + x) - old
            self.total_sq = (self.total_sq + x * x) - old * old
        if len(self.buf) < self.length:
            return None
        mean = self.total / self.length
        return math.sqrt(max(self.total_sq / self.length - mean * mean, 0.0))


class EMA:
    """ta.ema — alpha = 2/(n+1), ilk değer kaynağın kendisi."""

    def __init__(self, length: int):
        self.alpha = 2.0 / (length + 1)
        self.value: float | None = None

    def update(self, x: float | None) -> float | None:
        if x is None:
            return self.value
        self.value = x if self.value is None else self.alpha * x + (1 - self.alpha) * self.value
        return self.value


class RMA:
    """ta.rma — alpha = 1/n, ilk değer ilk n geçerli değerin SMA'sı."""

    def __init__(self, length: int):
        self.length = length
        self.alpha = 1.0 / length
        self.seed: list[float] = []
        self.value: float | None = None

    def update(self, x: float | None) -> float | None:
        if x is None:
            return self.value
        if self.value is None:
            self.seed.append(x)
            if len(self.seed) == self.length:
                self.value = sum(self.seed) / self.length
            return self.value
        self.value = self.alpha * x + (1 - self.alpha) * self.value
        return self.value


def true_range(high: float, low: float, prev_close: float | None, handle_na: bool) -> float | None:
    if prev_close is None:
        return high - low if handle_na else None
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


class ATR:
    """ta.atr(n) = ta.rma(ta.tr(true), n)."""

    def __init__(self, length: int):
        self.rma = RMA(length)
        self.prev_close: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        tr = true_range(high, low, self.prev_close, handle_na=True)
        self.prev_close = close
        return self.rma.update(tr)


class DMI:
    """ta.dmi(diLength, adxSmoothing) → (+DI, −DI, ADX)."""

    def __init__(self, di_len: int, adx_len: int):
        self.tr_rma = RMA(di_len)
        self.plus_rma = RMA(di_len)
        self.minus_rma = RMA(di_len)
        self.adx_rma = RMA(adx_len)
        self.prev: tuple[float, float, float] | None = None   # high, low, close
        self.plus: float | None = None                          # fixnan için son geçerli
        self.minus: float | None = None

    def update(self, high: float, low: float, close: float):
        if self.prev is None:
            plus_dm = minus_dm = tr = None
        else:
            ph, pl, pc = self.prev
            # kayan nokta gürültüsünü at: 6.03-6.02 ile 6.02-6.01 eşit sayılmalı (TradingView öyle sayar)
            up, down = round(high - ph, FLOAT_DIGITS), round(-(low - pl), FLOAT_DIGITS)
            plus_dm = up if (up > down and up > 0) else 0.0
            minus_dm = down if (down > up and down > 0) else 0.0
            tr = true_range(high, low, pc, handle_na=False)
        self.prev = (high, low, close)

        trur = self.tr_rma.update(tr)
        p_rma = self.plus_rma.update(plus_dm)
        m_rma = self.minus_rma.update(minus_dm)
        if trur is not None and p_rma is not None and trur != 0:
            self.plus = 100 * p_rma / trur
        if trur is not None and m_rma is not None and trur != 0:
            self.minus = 100 * m_rma / trur
        if self.plus is None or self.minus is None:
            return None, None, None
        s = self.plus + self.minus
        adx = self.adx_rma.update(abs(self.plus - self.minus) / (1 if s == 0 else s))
        return self.plus, self.minus, None if adx is None else 100 * adx


class VWMA:
    """ta.vwma(src, n) = sma(src*volume, n) / sma(volume, n)."""

    def __init__(self, length: int):
        self.pv = SMA(length)
        self.v = SMA(length)

    def update(self, x: float, volume: float) -> float | None:
        a, b = self.pv.update(x * volume), self.v.update(volume)
        if a is None or b is None or b == 0:
            return None
        return a / b


class BB:
    """ta.bb(src, n, mult) → (orta, üst, alt)."""

    def __init__(self, length: int, mult: float):
        self.sma = SMA(length)
        self.sd = StDev(length)
        self.mult = mult

    def update(self, x: float):
        mid, dev = self.sma.update(x), self.sd.update(x)
        if mid is None or dev is None:
            return None, None, None
        return mid, mid + self.mult * dev, mid - self.mult * dev


class MACD:
    """ta.macd(src, fast, slow, signal) → (macd, sinyal, histogram)."""

    def __init__(self, fast: int, slow: int, signal: int):
        self.fast, self.slow, self.sig = EMA(fast), EMA(slow), EMA(signal)

    def update(self, x: float):
        line = self.fast.update(x) - self.slow.update(x)
        sig = self.sig.update(line)
        return line, sig, line - sig
