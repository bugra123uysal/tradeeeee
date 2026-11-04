from datetime import datetime, time


from sistem.liste_yenile import NY, PIYASA, PREMARKET, fmt_entry, new_entries, session_for


def ny(h, m):
    return datetime(2026, 11, 2, h, m, tzinfo=NY)


def test_session_by_time():
    close = time(16, 0)
    assert session_for(ny(3, 59), close) is None
    assert session_for(ny(4, 0), close) is PREMARKET
    assert session_for(ny(9, 29), close) is PREMARKET
    assert session_for(ny(9, 30), close) is PIYASA
    assert session_for(ny(15, 59), close) is PIYASA
    assert session_for(ny(16, 0), close) is None


def test_no_session_on_holiday_and_early_close():
    assert session_for(ny(10, 0), None) is None
    assert session_for(ny(13, 30), time(13, 0)) is None


def test_new_entries_keeps_order_and_ignores_existing():
    assert new_entries(["A", "B"], ["C", "A", "D"]) == ["C", "D"]
    assert new_entries([], ["A"]) == ["A"]


def test_fmt_entry():
    assert fmt_entry({"s": "NASDAQ:GDYN", "change": 32.6, "relative_volume_10d_calc": 3.74}, PIYASA) \
        == "GDYN +32.6% · hacim 3.7x"
    assert fmt_entry({"s": "AMEX:BURU", "premarket_change": 12.0, "premarket_volume": 45_000}, PREMARKET) \
        == "BURU +12.0% · pm hacim 45K"


from sistem.liste_yenile import HEADER_CALM, HEADER_RISKY, grouped, is_calm


def pm_row(sym, gain, close, high, vol, avg):
    return {"s": sym, "premarket_change": gain, "premarket_close": close, "premarket_high": high,
            "premarket_volume": vol, "average_volume_10d_calc": avg}


def test_premarket_calm_rules():
    assert is_calm(pm_row("A", 12, 5.0, 5.1, 10_000, 1e6), PREMARKET)
    assert not is_calm(pm_row("B", 35, 5.0, 5.1, 10_000, 1e6), PREMARKET)     # %20+
    assert not is_calm(pm_row("C", 12, 5.0, 6.0, 10_000, 1e6), PREMARKET)     # tepeden %17 aşağı
    assert not is_calm(pm_row("D", 12, 5.0, 5.1, 2e6, 1e6), PREMARKET)        # hacim > ort.
    assert not is_calm(pm_row("E", 12, 5.0, None, 10_000, 1e6), PREMARKET)    # veri eksik


def test_market_calm_rules():
    assert is_calm({"s": "A", "premarket_change": 1.0, "float_shares_outstanding": 80e6}, PIYASA)
    assert not is_calm({"s": "B", "premarket_change": 30.0, "float_shares_outstanding": 80e6}, PIYASA)
    assert not is_calm({"s": "C", "premarket_change": 1.0, "float_shares_outstanding": 5e6}, PIYASA)


def test_grouped_adds_headers_and_skips_empty_sections():
    calm = pm_row("X:A", 12, 5.0, 5.1, 10_000, 1e6)
    risky = pm_row("X:B", 50, 5.0, 5.1, 10_000, 1e6)
    assert grouped([risky, calm], PREMARKET) == [HEADER_CALM, "X:A", HEADER_RISKY, "X:B"]
    assert grouped([risky], PREMARKET) == [HEADER_RISKY, "X:B"]
