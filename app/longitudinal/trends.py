"""Lab trends and comparisons between dates (docs/PHASE_4_PLAN.md §6; USER_FLOWS F5; ADR-039).

- **Series:** one per (analyte, display unit). Values were converted to the analyte's default unit
  at extraction only where an exact conversion is defined (``norm-1``); a unit without one keeps
  its own series, so incompatible units are never compared or drawn together.
- **Points:** an exact duplicate group is one point with every source. Each point carries *its own
  document's* printed range and the derived comparison with it, never another document's range,
  and never a MediMind range. Range states avoid clinical words:
  ``within_printed_range`` / ``below_printed_range`` / ``above_printed_range`` (derived),
  ``not_compared`` (a range is printed but the value could not be compared with it),
  ``no_printed_range``.
- **Plotted** points are numeric, without a comparator (``< 5`` is a bound, not a value), with a
  day-precision date. Others stay in the table with the reason they are not drawn.
- **Changes** are exact ``Decimal`` differences between consecutive plotted points of one series.
  A date with several results that no time of day separates gets no change in or out of it.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from app.longitudinal.dates import date_label, sort_key
from app.longitudinal.duplicates import EXACT, DuplicateGroup
from app.longitudinal.model import DAY, LongitudinalObservation

WITHIN_PRINTED_RANGE: Final = "within_printed_range"
BELOW_PRINTED_RANGE: Final = "below_printed_range"
ABOVE_PRINTED_RANGE: Final = "above_printed_range"
NOT_COMPARED: Final = "not_compared"
NO_PRINTED_RANGE: Final = "no_printed_range"

NUMERIC: Final = "numeric"
QUALITATIVE: Final = "qualitative"
NO_UNIT: Final = "numeric_without_unit"

PARTIAL_DATE: Final = "partial_date"  # reasons a point is not drawn
BOUND_VALUE: Final = "bound_value"
NOT_NUMERIC: Final = "not_numeric"

SEVERAL_RESULTS: Final = "several_results_on_date"  # reasons a change is not computed
DIFFERENT_UNITS: Final = "different_units"

_RANGE_STATES = {"within": WITHIN_PRINTED_RANGE, "below": BELOW_PRINTED_RANGE}
_RANGE_STATES["above"] = ABOVE_PRINTED_RANGE


def range_state(o: LongitudinalObservation) -> str:
    if o.range_comparison in _RANGE_STATES:
        return _RANGE_STATES[o.range_comparison]
    if o.ref_range_raw or o.ref_low is not None or o.ref_high is not None:
        return NOT_COMPARED
    return NO_PRINTED_RANGE


@dataclass(frozen=True)
class TrendPoint:
    observation: LongitudinalObservation  # the representative
    sources: tuple[LongitudinalObservation, ...]  # every source (exact duplicates collapsed)
    date: dt.date
    precision: str
    label: str
    range_state: str
    plotted: bool
    not_plotted_reason: str | None
    possible_duplicates: tuple[str, ...]  # fact ids of possible duplicates in this series


@dataclass(frozen=True)
class TrendChange:
    from_fact: str
    to_fact: str
    from_date: dt.date
    to_date: dt.date
    delta: Decimal  # to - from, exact, in the series unit
    days: int


@dataclass(frozen=True)
class TrendSeries:
    kind: str  # numeric / qualitative / numeric_without_unit
    unit: str | None
    points: tuple[TrendPoint, ...]  # chronological
    changes: tuple[TrendChange, ...]
    skipped_change_dates: tuple[dt.date, ...]  # dates with several results and no time


@dataclass(frozen=True)
class Trend:
    analyte_name: str
    series: tuple[TrendSeries, ...]  # numeric series first, then by unit
    undated: tuple[LongitudinalObservation, ...]
    several_units: bool  # more than one numeric series: shown separately, never combined


def _series_key(o: LongitudinalObservation) -> tuple[str, str | None]:
    if o.value is None:
        return (QUALITATIVE, None)
    if o.unit is None:
        return (NO_UNIT, None)
    return (NUMERIC, o.unit)


def _collapse(
    observations: Sequence[LongitudinalObservation], groups: Sequence[DuplicateGroup]
) -> tuple[list[tuple[LongitudinalObservation, tuple[LongitudinalObservation, ...]]], dict]:
    by_id = {o.fact_id: o for o in observations}
    hidden: set[str] = set()
    sources: dict[str, tuple[LongitudinalObservation, ...]] = {}
    possible: dict[str, tuple[str, ...]] = {}
    for g in groups:
        if not set(g.members) <= by_id.keys():
            continue
        if g.kind == EXACT:
            hidden.update(m for m in g.members if m != g.representative)
            sources[g.representative] = tuple(
                sorted((by_id[m] for m in g.members), key=lambda s: s.document_id)
            )
        else:
            for m in g.members:
                possible[m] = tuple(x for x in g.members if x != m)
    kept = [(o, sources.get(o.fact_id, (o,))) for o in observations if o.fact_id not in hidden]
    return kept, possible


def _point(o: LongitudinalObservation, sources: tuple, kind: str, possible: dict) -> TrendPoint:
    assert o.effective_date is not None and o.date_precision is not None
    reason = None
    if kind != NUMERIC:
        reason = NOT_NUMERIC
    elif o.comparator is not None:
        reason = BOUND_VALUE
    elif o.date_precision != DAY:
        reason = PARTIAL_DATE
    return TrendPoint(
        observation=o,
        sources=sources,
        date=o.effective_date,
        precision=o.date_precision,
        label=date_label(o.effective_date, o.date_precision),
        range_state=range_state(o),
        plotted=reason is None,
        not_plotted_reason=reason,
        possible_duplicates=possible.get(o.fact_id, ()),
    )


def _changes(points: Sequence[TrendPoint]) -> tuple[tuple[TrendChange, ...], tuple[dt.date, ...]]:
    plotted = [p for p in points if p.plotted]
    per_date: dict[dt.date, list[TrendPoint]] = {}
    for p in plotted:
        per_date.setdefault(p.date, []).append(p)
    unclear = {
        date
        for date, same_day in per_date.items()
        if len(same_day) > 1
        and (
            any(p.observation.effective_time is None for p in same_day)
            or len({p.observation.effective_time for p in same_day}) < len(same_day)
        )
    }
    changes = []
    for a, b in zip(plotted, plotted[1:], strict=False):
        if a.date in unclear or b.date in unclear:
            continue
        assert a.observation.value is not None and b.observation.value is not None
        changes.append(
            TrendChange(
                from_fact=a.observation.fact_id,
                to_fact=b.observation.fact_id,
                from_date=a.date,
                to_date=b.date,
                delta=b.observation.value - a.observation.value,
                days=(b.date - a.date).days,
            )
        )
    return tuple(changes), tuple(sorted(unclear))


def _point_order(p: TrendPoint) -> tuple:
    o = p.observation
    return (
        sort_key(p.date, p.precision, o.effective_time),
        o.document_id,
        o.page_number,
        o.char_start,
        o.fact_id,
    )


def build_trend(
    analyte_name: str,
    observations: Sequence[LongitudinalObservation],
    groups: Sequence[DuplicateGroup],
) -> Trend:
    """The trend of one display analyte from the patient's usable observations."""
    mine = [o for o in observations if o.analyte_name == analyte_name]
    kept, possible = _collapse(mine, groups)
    buckets: dict[tuple[str, str | None], list[TrendPoint]] = {}
    for o, sources in kept:
        if o.dated:
            key = _series_key(o)
            buckets.setdefault(key, []).append(_point(o, sources, key[0], possible))
    series = []
    for (kind, unit), points in sorted(
        buckets.items(), key=lambda kv: (kv[0][0] != NUMERIC, kv[0][0], kv[0][1] or "")
    ):
        ordered = tuple(sorted(points, key=_point_order))
        changes, skipped = _changes(ordered) if kind == NUMERIC else ((), ())
        series.append(TrendSeries(kind, unit, ordered, changes, skipped))
    undated = tuple(
        sorted(
            (o for o, _ in kept if not o.dated),
            key=lambda o: (o.document_id, o.page_number, o.char_start),
        )
    )
    return Trend(
        analyte_name=analyte_name,
        series=tuple(series),
        undated=undated,
        several_units=sum(s.kind == NUMERIC for s in series) > 1,
    )


ONLY_ON_FIRST: Final = "only_on_first_date"
ONLY_ON_SECOND: Final = "only_on_second_date"
CHANGED: Final = "changed"
UNCHANGED: Final = "unchanged"
NOT_COMPUTED: Final = "not_computed"


@dataclass(frozen=True)
class DateComparisonRow:
    analyte_name: str
    kind: str
    unit: str | None
    first: tuple[TrendPoint, ...]  # day-precision points on the first date
    second: tuple[TrendPoint, ...]
    status: str
    delta: Decimal | None  # second - first, only when both sides are one plotted value
    reason: str | None  # why no difference was computed


def compare_dates(
    observations: Sequence[LongitudinalObservation],
    groups: Sequence[DuplicateGroup],
    first: dt.date,
    second: dt.date,
) -> tuple[DateComparisonRow, ...]:
    """Results of two dates side by side, per analyte and series (day-precision dates only)."""
    names = sorted({o.analyte_name for o in observations if o.analyte_name})
    rows = []
    for name in names:
        trend = build_trend(name, observations, groups)
        sides = []
        for s in trend.series:
            a = tuple(p for p in s.points if p.precision == DAY and p.date == first)
            b = tuple(p for p in s.points if p.precision == DAY and p.date == second)
            if a or b:
                sides.append((s, a, b))
        units_first = {s.unit for s, a, _ in sides if a}
        units_second = {s.unit for s, _, b in sides if b}
        for s, a, b in sides:
            delta, reason = None, None
            if a and b:
                if len(a) > 1 or len(b) > 1:
                    status, reason = NOT_COMPUTED, SEVERAL_RESULTS
                elif not (a[0].plotted and b[0].plotted):
                    status = NOT_COMPUTED
                    reason = a[0].not_plotted_reason or b[0].not_plotted_reason
                else:
                    assert a[0].observation.value is not None
                    assert b[0].observation.value is not None
                    delta = b[0].observation.value - a[0].observation.value
                    status = CHANGED if delta != 0 else UNCHANGED
            elif a:
                status = ONLY_ON_FIRST
                reason = DIFFERENT_UNITS if units_second - {s.unit} else None
            else:
                status = ONLY_ON_SECOND
                reason = DIFFERENT_UNITS if units_first - {s.unit} else None
            rows.append(DateComparisonRow(name, s.kind, s.unit, a, b, status, delta, reason))
    return tuple(rows)
