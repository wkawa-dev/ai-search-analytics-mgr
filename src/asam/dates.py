"""Human/agent friendly date ranges.

Accepted specs: last7d, last28d, last90d, last3m, last6m, last12m, last16m, ytd, mtd,
yesterday, today, YYYY-MM-DD..YYYY-MM-DD, YYYY-MM-DD (single day).
GSC data lags ~2-3 days, so GSC ranges end at today-`lag` by default.
"""

from __future__ import annotations

import datetime as dt
import re

from .errors import AsamError

GSC_LAG_DAYS = 3


def today() -> dt.date:
    return dt.date.today()


def parse_range(spec: str | None, lag: int = 0, default: str = "last28d") -> tuple[dt.date, dt.date]:
    spec = (spec or default).strip().lower().replace(" ", "")
    end = today() - dt.timedelta(days=lag)
    if ".." in spec:
        a, b = spec.split("..", 1)
        return _d(a), _d(b)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", spec):
        d = _d(spec)
        return d, d
    if spec == "today":
        return today(), today()
    if spec == "yesterday":
        y = today() - dt.timedelta(days=1)
        return y, y
    if spec == "ytd":
        return dt.date(end.year, 1, 1), end
    if spec == "mtd":
        return end.replace(day=1), end
    m = re.fullmatch(r"(?:last)?(\d+)([dwm])", spec)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * {"d": 1, "w": 7, "m": 30}[unit] if unit != "m" else None
        if unit == "m":
            start = _add_months(end, -n) + dt.timedelta(days=1)
        else:
            start = end - dt.timedelta(days=days - 1)
        return start, end
    raise AsamError("BAD_DATE_RANGE", f"Unrecognised date range '{spec}'.",
                    fix="Use last7d, last28d, last3m, last16m, ytd, mtd, or YYYY-MM-DD..YYYY-MM-DD.")


def previous_period(start: dt.date, end: dt.date, mode: str = "previous") -> tuple[dt.date, dt.date]:
    if mode == "yoy":
        return _add_months(start, -12), _add_months(end, -12)
    length = (end - start).days + 1
    pend = start - dt.timedelta(days=1)
    return pend - dt.timedelta(days=length - 1), pend


def _d(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError as e:
        raise AsamError("BAD_DATE", f"Bad date '{s}'", fix="Use YYYY-MM-DD.") from e


def _add_months(d: dt.date, months: int) -> dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    import calendar

    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def iso(d: dt.date) -> str:
    return d.isoformat()
