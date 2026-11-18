"""Numbers, comparators, qualitative results and printed flags (docs/PHASE_3_PLAN.md §7.2).

Pure; the raw cell text is never changed. ``Decimal`` keeps the printed precision (``14.20``).
Digit grouping follows the India region profile (``1,50,000``) and the international form
(``150,000``); a decimal comma (``1,5``) is refused, never guessed.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.extraction.reasons import ReviewReason

_NUMBER = r"\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+"
_COMPARATORS = {"<": "<", "<=": "<=", ">": ">", ">=": ">=", "≤": "<=", "≥": ">="}
_VALUE = re.compile(rf"^(?P<cmp><=|>=|<|>|≤|≥)?\s*(?P<num>{_NUMBER})(?:\s+(?P<flag>[A-Za-z*]+))?$")

# Printed abnormality flags (case-insensitive key -> stored canonical text).
FLAGS: dict[str, str] = {
    "h": "H",
    "l": "L",
    "hh": "HH",
    "ll": "LL",
    "high": "High",
    "low": "Low",
    "*": "*",
    "critical": "Critical",
}
HIGH_FLAGS = frozenset({"H", "HH", "High"})
LOW_FLAGS = frozenset({"L", "LL", "Low"})

# Qualitative results: exact vocabulary only. Anything else is ``unparsed_value``. The sign of a
# result is never inferred or flipped; comparison between tokens needs an analyte-specific scale.
QUALITATIVE: dict[str, str] = {
    "positive": "Positive",
    "negative": "Negative",
    "nil": "Nil",
    "trace": "Trace",
    "reactive": "Reactive",
    "non-reactive": "Non-reactive",
    "non reactive": "Non-reactive",
    "nonreactive": "Non-reactive",
    "absent": "Absent",
    "present": "Present",
    "detected": "Detected",
    "not detected": "Not detected",
}

PLACEHOLDERS = frozenset(
    {"", "--", "-", "na", "n/a", "pending", "see note", "awaited", "nd", "not done"}
)


@dataclass(frozen=True)
class ParsedValue:
    value_num: Decimal | None = None
    comparator: str | None = None
    qualitative: str | None = None
    flag: str | None = None  # a flag printed inside the value cell (``14.2 H``)
    reason: ReviewReason | None = None  # set when the value could not be parsed


def text_key(text: str) -> str:
    """NFKC, case-folded, whitespace collapsed: a lookup key, never stored as a value."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def is_placeholder(raw: str) -> bool:
    return text_key(raw) in PLACEHOLDERS


def parse_number(text: str) -> Decimal | None:
    """A number in Indian or international grouping; None when it is not exactly one."""
    if not re.fullmatch(_NUMBER, text):
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def parse_flag(raw: str) -> str | None:
    return FLAGS.get(text_key(raw))


def parse_value(raw: str) -> ParsedValue:
    """Parse one printed result cell. Never raises; unknown text gives ``unparsed_value``."""
    text = " ".join(unicodedata.normalize("NFKC", raw).split())
    if not text:
        return ParsedValue(reason=ReviewReason.UNPARSED_VALUE)
    match = _VALUE.match(text)
    if match:
        flag_text = match.group("flag")
        flag = parse_flag(flag_text) if flag_text else None
        number = parse_number(match.group("num"))
        if number is not None and (flag_text is None or flag is not None):
            cmp_text = match.group("cmp")
            comparator = _COMPARATORS[cmp_text] if cmp_text else None
            return ParsedValue(value_num=number, comparator=comparator, flag=flag)
    qualitative = QUALITATIVE.get(text_key(text))
    if qualitative is not None:
        return ParsedValue(qualitative=qualitative)
    words = text.rsplit(" ", 1)  # "Positive H": a qualitative word followed by a known flag
    if len(words) == 2:
        word_flag = parse_flag(words[1])
        word_value = QUALITATIVE.get(text_key(words[0]))
        if word_flag and word_value:
            return ParsedValue(qualitative=word_value, flag=word_flag)
    return ParsedValue(reason=ReviewReason.UNPARSED_VALUE)
