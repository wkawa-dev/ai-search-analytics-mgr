import datetime as dt

import pytest

from asam import dates
from asam.errors import AsamError


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(dates, "today", lambda: dt.date(2026, 9, 29))


def test_lastnd_with_lag():
    assert dates.parse_range("last28d", lag=3) == (dt.date(2026, 8, 30), dt.date(2026, 9, 26))


def test_explicit_and_single():
    assert dates.parse_range("2026-01-01..2026-01-31") == (dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    assert dates.parse_range("2026-02-03") == (dt.date(2026, 2, 3), dt.date(2026, 2, 3))


def test_months_and_ytd():
    s, e = dates.parse_range("last3m")
    assert e == dt.date(2026, 9, 29) and s == dt.date(2026, 6, 30)
    assert dates.parse_range("ytd")[0] == dt.date(2026, 1, 1)


def test_previous_and_yoy():
    s, e = dt.date(2026, 9, 1), dt.date(2026, 9, 28)
    assert dates.previous_period(s, e) == (dt.date(2026, 8, 4), dt.date(2026, 8, 31))
    assert dates.previous_period(s, e, "yoy") == (dt.date(2025, 9, 1), dt.date(2025, 9, 28))


def test_bad_range_has_fix():
    with pytest.raises(AsamError) as ei:
        dates.parse_range("lastweekish")
    assert ei.value.fix
