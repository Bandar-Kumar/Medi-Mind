"""Partial dates: labels, periods and the chronological sort rule (docs/PHASE_4_PLAN.md §4).

Stored dates are normalized (ADR-025): a month-precision date is the 1st of its month, a
year-precision date is 1 January. A partial date is never shown as an exact day (USER_FLOWS F4):
``2024-03-01`` with ``month`` precision is "March 2024".

Sort rule: by the period's first day; on the same first day a day-precision date comes before a
month-precision one, which comes before a year-precision one; then by time of day (entries
without a time after those with one). Remaining ties are broken by the caller with stable ids,
never by upload order.
"""

import calendar
import datetime as dt

from app.longitudinal.model import DAY, MONTH, YEAR

_PRECISION_RANK = {DAY: 0, MONTH: 1, YEAR: 2}


def date_label(date: dt.date | None, precision: str | None) -> str:
    """'17 Mar 2024', 'March 2024', '2024', or 'No date'."""
    if date is None or precision is None:
        return "No date"
    if precision == DAY:
        return f"{date.day} {calendar.month_abbr[date.month]} {date.year}"
    if precision == MONTH:
        return f"{calendar.month_name[date.month]} {date.year}"
    if precision == YEAR:
        return str(date.year)
    raise ValueError("unknown date precision")


def period(date: dt.date, precision: str) -> tuple[dt.date, dt.date]:
    """First and last day covered by a (possibly partial) date."""
    if precision == DAY:
        return date, date
    if precision == MONTH:
        return date.replace(day=1), date.replace(day=calendar.monthrange(date.year, date.month)[1])
    if precision == YEAR:
        return date.replace(month=1, day=1), date.replace(month=12, day=31)
    raise ValueError("unknown date precision")


def overlaps(
    date: dt.date, precision: str, date_from: dt.date | None, date_to: dt.date | None
) -> bool:
    """True when the date's period overlaps the inclusive window (open ends allowed)."""
    first, last = period(date, precision)
    return (date_from is None or last >= date_from) and (date_to is None or first <= date_to)


def sort_key(
    date: dt.date, precision: str, time: dt.time | None = None
) -> tuple[dt.date, int, int, dt.time]:
    first, _ = period(date, precision)
    return (first, _PRECISION_RANK[precision], 0 if time is not None else 1, time or dt.time())
