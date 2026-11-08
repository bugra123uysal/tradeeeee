from datetime import datetime

from backtest.edgar_yuksek_bt import sinyal_ani
from sistem import edgar as E


def test_sinyal_ani_ondalik_saati_ny_zamanina_cevirir():
    assert sinyal_ani("2026-11-06", 10.25) == datetime(2026, 11, 6, 10, 15, tzinfo=E.NY)


def test_sinyal_ani_premarket_saati():
    assert sinyal_ani("2026-01-02", 4.0) == datetime(2026, 1, 2, 4, 0, tzinfo=E.NY)
