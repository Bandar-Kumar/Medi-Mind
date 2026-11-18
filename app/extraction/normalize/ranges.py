"""Printed reference ranges and the derived comparison (docs/PHASE_3_PLAN.md §7.4).

Parsed forms: ``a - b`` (also ``a-b``, ``a to b``), ``< b`` / ``<= b`` / ``Up to b``, ``> a`` /
``>= a``, optionally followed by a unit equal to the row's unit. Everything else (a range in a
different unit, multi-tier ranges, sex- or age-specific text) is left unparsed: both bounds NULL,
note ``ref_range_unparsed``. A missing range stays missing; it is never taken from a catalogue.

The comparison is **derived** and uses only this row's printed range, never MediMind's own ranges
(ARCHITECTURE §8). A value whose interval straddles a bound, or equals an exclusive bound, gives
None (not comparable).
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.extraction.normalize.numbers import _NUMBER, parse_number, text_key

BELOW = "below"
WITHIN = "within"
ABOVE = "above"

_BETWEEN = re.compile(rf"^(?P<a>{_NUMBER})\s*(?:-|to)\s*(?P<b>{_NUMBER})(?:\s+(?P<unit>\S.*))?$")
_UPPER = re.compile(rf"^(?P<op><=|<|up to|upto)\s*(?P<b>{_NUMBER})(?:\s+(?P<unit>\S.*))?$")
_LOWER = re.compile(rf"^(?P<op>>=|>)\s*(?P<a>{_NUMBER})(?:\s+(?P<unit>\S.*))?$")


@dataclass(frozen=True)
class ParsedRange:
    low: Decimal | None = None
    high: Decimal | None = None
    low_inclusive: bool = True
    high_inclusive: bool = True
    parsed: bool = False


def parse_range(raw: str | None, *, unit_key: str | None) -> ParsedRange:
    """``unit_key``: ``text_key`` of the row's printed unit (a range unit must equal it)."""
    if raw is None:
        return ParsedRange()
    text = text_key(raw).replace("–", "-").replace("−", "-")
    if not text:
        return ParsedRange()

    def unit_ok(unit: str | None) -> bool:
        return unit is None or (unit_key is not None and text_key(unit) == unit_key)

    if match := _BETWEEN.match(text):
        low, high = parse_number(match["a"]), parse_number(match["b"])
        if low is not None and high is not None and low <= high and unit_ok(match["unit"]):
            return ParsedRange(low, high, parsed=True)
        return ParsedRange()
    if match := _UPPER.match(text):
        high = parse_number(match["b"])
        if high is not None and unit_ok(match["unit"]):
            return ParsedRange(None, high, high_inclusive=match["op"] != "<", parsed=True)
        return ParsedRange()
    if match := _LOWER.match(text):
        low = parse_number(match["a"])
        if low is not None and unit_ok(match["unit"]):
            return ParsedRange(low, None, low_inclusive=match["op"] != ">", parsed=True)
    return ParsedRange()


def value_interval(
    value: Decimal, comparator: str | None
) -> tuple[Decimal | None, bool, Decimal | None, bool]:
    """(low, low_inclusive, high, high_inclusive); None = unbounded."""
    if comparator is None:
        return value, True, value, True
    if comparator in ("<", "<="):
        return None, False, value, comparator == "<="
    return value, comparator == ">=", None, False


def compare_to_range(
    value: Decimal | None, comparator: str | None, printed: ParsedRange
) -> str | None:
    """below / within / above against this document's printed range; None if not comparable."""
    if value is None or not printed.parsed:
        return None
    v_low, v_low_inc, v_high, v_high_inc = value_interval(value, comparator)
    r_low, r_high = printed.low, printed.high
    # Entirely below the range's low bound.
    if r_low is not None and v_high is not None:
        if v_high < r_low or (v_high == r_low and (not v_high_inc or not printed.low_inclusive)):
            return BELOW
    # Entirely above the range's high bound.
    if r_high is not None and v_low is not None:
        if v_low > r_high or (v_low == r_high and (not v_low_inc or not printed.high_inclusive)):
            return ABOVE
    # Entirely within: the value interval must be bounded where the range is bounded.
    low_ok = r_low is None or (
        v_low is not None and (v_low > r_low or (v_low == r_low and printed.low_inclusive))
    )
    high_ok = r_high is None or (
        v_high is not None and (v_high < r_high or (v_high == r_high and printed.high_inclusive))
    )
    if low_ok and high_ok:
        return WITHIN
    return None
