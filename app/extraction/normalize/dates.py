"""Dates and times as printed on Indian lab reports (ADR-025; docs/PHASE_3_PLAN.md §7.1).

Day-month-year order (region profile). Rules:
- ``25/03/2024``, ``25-03-2024``, ``25.03.2024`` give day precision.
- Day and month both <= 12 and different (``03/04/2024``): read as DD/MM and **always** flagged
  ``ambiguous_date`` (open question Q12 keeps its default).
- ``12-Mar-2024``, ``12 March 2024``, ``12 Mar, 2024`` give day precision.
- ISO ``2024-03-12`` only with a four-digit year first.
- ``Mar 2024``, ``March 2024``, ``03/2024`` give month precision, stored as the 1st.
- A two-digit year is never expanded (``two_digit_year``); a missing year is never inferred.
- Impossible calendar dates give ``unparsed_date``. A date outside the date policy is kept and
  flagged ``implausible_date``.
"""

import datetime as dt
import re
from dataclasses import dataclass

from app.extraction.date_policy import is_plausible
from app.extraction.reasons import ReviewReason

DAY = "day"
MONTH = "month"
YEAR = "year"

_MONTH_NAMES: tuple[tuple[str, ...], ...] = (
    ("jan", "january"),
    ("feb", "february"),
    ("mar", "march"),
    ("apr", "april"),
    ("may",),
    ("jun", "june"),
    ("jul", "july"),
    ("aug", "august"),
    ("sep", "sept", "september"),
    ("oct", "october"),
    ("nov", "november"),
    ("dec", "december"),
)
_MONTHS = {name: number for number, names in enumerate(_MONTH_NAMES, start=1) for name in names}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))

_NUMERIC = re.compile(r"(?P<d>\d{1,2})(?P<sep>[/.\-])(?P<m>\d{1,2})(?P=sep)(?P<y>\d{4}|\d{2})")
_NAMED = re.compile(
    rf"(?P<d>\d{{1,2}})[ \-]*(?P<mon>{_MONTH})\.?[ \-,]*(?P<y>\d{{4}}|\d{{2}})", re.IGNORECASE
)
_ISO = re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})")
_MONTH_YEAR_NAMED = re.compile(rf"(?P<mon>{_MONTH})\.?[ \-,]*(?P<y>\d{{4}})", re.IGNORECASE)
_MONTH_YEAR_NUMERIC = re.compile(r"(?P<m>\d{1,2})/(?P<y>\d{4})")

# A date token inside a line (any of the forms above), not glued to other word characters.
DATE_TOKEN = re.compile(
    r"(?<![\w/.\-])(?:"
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}([/.\-])\d{1,2}\1(?:\d{4}|\d{2})"
    rf"|\d{{1,2}}[ \-]*(?:{_MONTH})\.?[ \-,]*(?:\d{{4}}|\d{{2}})"
    rf"|(?:{_MONTH})\.?[ \-,]*\d{{4}}"
    r"|\d{1,2}/\d{4}"
    r")(?![\w/\-])",
    re.IGNORECASE,
)
TIME_TOKEN = re.compile(
    r"(?<![\d:.])(?P<h>\d{1,2})[:.](?P<m>\d{2})(?:[:.](?P<s>\d{2}))?"
    r"(?:\s*(?P<ampm>[AaPp]\.?[Mm]\.?))?(?![\d:])"
)


@dataclass(frozen=True)
class ParsedDate:
    value: dt.date | None
    precision: str | None
    reasons: tuple[ReviewReason, ...] = ()


def _build(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _checked(
    value: dt.date | None, precision: str, reference_date: dt.date, *extra: ReviewReason
) -> ParsedDate:
    if value is None:
        return ParsedDate(None, None, (ReviewReason.UNPARSED_DATE,))
    reasons = list(extra)
    if not is_plausible(value, reference_date):
        reasons.append(ReviewReason.IMPLAUSIBLE_DATE)
    return ParsedDate(value, precision, tuple(reasons))


def parse_date(raw: str, reference_date: dt.date) -> ParsedDate:
    """Parse one printed date; ``reference_date`` bounds plausibility (the clock is never read)."""
    text = raw.strip()
    if match := _ISO.fullmatch(text):
        return _checked(
            _build(int(match["y"]), int(match["m"]), int(match["d"])), DAY, reference_date
        )
    if match := _NUMERIC.fullmatch(text):
        if len(match["y"]) == 2:
            return ParsedDate(None, None, (ReviewReason.TWO_DIGIT_YEAR,))
        day, month = int(match["d"]), int(match["m"])
        ambiguous = day <= 12 and month <= 12 and day != month
        extra = (ReviewReason.AMBIGUOUS_DATE,) if ambiguous else ()
        return _checked(_build(int(match["y"]), month, day), DAY, reference_date, *extra)
    if match := _NAMED.fullmatch(text):
        if len(match["y"]) == 2:
            return ParsedDate(None, None, (ReviewReason.TWO_DIGIT_YEAR,))
        value = _build(int(match["y"]), _MONTHS[match["mon"].lower()], int(match["d"]))
        return _checked(value, DAY, reference_date)
    if match := _MONTH_YEAR_NAMED.fullmatch(text):
        value = _build(int(match["y"]), _MONTHS[match["mon"].lower()], 1)
        return _checked(value, MONTH, reference_date)
    if match := _MONTH_YEAR_NUMERIC.fullmatch(text):
        return _checked(_build(int(match["y"]), int(match["m"]), 1), MONTH, reference_date)
    return ParsedDate(None, None, (ReviewReason.UNPARSED_DATE,))


def parse_time(raw: str) -> dt.time | None:
    """``10:30 AM``, ``22:15``, ``10.30 am``; None when it is not exactly one valid time."""
    match = TIME_TOKEN.fullmatch(raw.strip())
    if not match:
        return None
    hour, minute = int(match["h"]), int(match["m"])
    second = int(match["s"]) if match["s"] else 0
    if match["ampm"]:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if match["ampm"].lower().startswith("p") else 0)
    try:
        return dt.time(hour, minute, second)
    except ValueError:
        return None
