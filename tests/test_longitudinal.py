"""Pure tests of app/longitudinal (Phase 4): hand-calculated fixtures, no database.

All values are fictional. docs/PHASE_4_PLAN.md §4-§6.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.extraction.reference import load_reference
from app.longitudinal import dates
from app.longitudinal.duplicates import (
    CORRECTED_VALUE,
    DIFFERENT_DATE_SOURCE,
    DIFFERENT_PRINTED_UNIT,
    EXACT,
    POSSIBLE,
    SAME_PRINTED_TEXT,
    find_duplicates,
)
from app.longitudinal.model import LongitudinalDocument, LongitudinalObservation
from app.longitudinal.timeline import (
    DOCUMENT,
    DOCUMENT_REPORT_DATE,
    LAB_RESULT,
    TimelineFilter,
    build_timeline,
)
from app.longitudinal.trends import (
    ABOVE_PRINTED_RANGE,
    BOUND_VALUE,
    CHANGED,
    DIFFERENT_UNITS,
    NO_PRINTED_RANGE,
    NOT_COMPARED,
    NOT_NUMERIC,
    ONLY_ON_FIRST,
    PARTIAL_DATE,
    SEVERAL_RESULTS,
    UNCHANGED,
    WITHIN_PRINTED_RANGE,
    build_trend,
    compare_dates,
)

REF = load_reference()
D = dt.date
_ids = iter(range(1, 10_000))


def obs(**kw) -> LongitudinalObservation:
    n = next(_ids)
    base = dict(
        fact_id=str(uuid.UUID(int=n)),  # deterministic, sortable UUID text
        document_id="docA",
        extraction_run_id="run",
        page_number=1,
        source_locator=f"p1:t0:r{n}",
        char_start=n * 10,
        char_end=n * 10 + 5,
        analyte_id=1,
        analyte_name="Haemoglobin",
        value=Decimal("14.2"),
        comparator=None,
        unit="g/dL",
        qualitative=None,
        ref_low=Decimal("13.0"),
        ref_high=Decimal("17.0"),
        range_comparison="within",
        effective_date=D(2024, 3, 25),
        date_precision="day",
        date_source="collection",
        effective_time=None,
        review_status="validated",
        value_source="machine",
        doc_type="lab_report",
        identity_check="match",
        analyte_raw="Haemoglobin",
        value_raw="14.2",
        unit_raw="gm/dL",
        ref_range_raw="13.0 - 17.0",
    )
    base.update(kw)
    return LongitudinalObservation(**base)


def doc(document_id: str, **kw) -> LongitudinalDocument:
    base = dict(
        document_id=document_id,
        doc_type="lab_report",
        identity_check="match",
        processing_status="processed",
        document_date=None,
        document_date_precision=None,
        uploaded_at=dt.datetime(2026, 11, 18, 10, tzinfo=dt.UTC),
        processed_at=None,
    )
    base.update(kw)
    return LongitudinalDocument(**base)


# --- dates ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("date", "precision", "label"),
    [
        (D(2024, 3, 7), "day", "7 Mar 2024"),
        (D(2024, 3, 1), "month", "March 2024"),
        (D(2024, 1, 1), "year", "2024"),
        (None, None, "No date"),
    ],
)
def test_partial_dates_are_never_shown_as_a_day(date, precision, label) -> None:
    assert dates.date_label(date, precision) == label


def test_periods_and_window_overlap() -> None:
    assert dates.period(D(2024, 2, 1), "month") == (D(2024, 2, 1), D(2024, 2, 29))
    assert dates.period(D(2023, 1, 1), "year") == (D(2023, 1, 1), D(2023, 12, 31))
    assert dates.overlaps(D(2024, 3, 1), "month", D(2024, 3, 20), None)  # month overlaps window
    assert not dates.overlaps(D(2024, 3, 1), "month", D(2024, 4, 1), None)
    assert dates.overlaps(D(2024, 1, 1), "year", None, D(2024, 1, 1))


def test_sort_rule_day_before_month_before_year_and_timed_first() -> None:
    keys = [
        dates.sort_key(D(2024, 1, 1), "year"),
        dates.sort_key(D(2024, 1, 1), "month"),
        dates.sort_key(D(2024, 1, 1), "day"),
        dates.sort_key(D(2024, 1, 1), "day", dt.time(8, 0)),
    ]
    assert sorted(keys) == list(reversed(keys))


# --- duplicates ----------------------------------------------------------------------------------


def test_identical_printed_results_in_two_documents_are_exact() -> None:
    a, b = obs(document_id="docA"), obs(document_id="docB")
    (group,) = find_duplicates([a, b], REF)
    assert group.kind == EXACT and set(group.members) == {a.fact_id, b.fact_id}
    assert SAME_PRINTED_TEXT in group.reasons


def test_equal_values_with_different_printing_are_possible_with_reasons() -> None:
    # 142 g/L converted to 14.2 g/dL at extraction: same value, different printed unit
    a = obs(document_id="docA")
    b = obs(document_id="docB", value_raw="142", unit_raw="g/L", ref_range_raw="130 - 170")
    c = obs(document_id="docC", date_source="report")
    groups = find_duplicates([a, b, c], REF)
    (group,) = groups
    assert group.kind == POSSIBLE
    assert DIFFERENT_PRINTED_UNIT in group.reasons and DIFFERENT_DATE_SOURCE in group.reasons
    assert len(group.pairs) == 3


@pytest.mark.parametrize(
    "other",
    [
        {"document_id": "docA"},  # same document: not cross-document
        {"effective_date": D(2024, 3, 26)},  # another day
        {"value": Decimal("13.1"), "value_raw": "13.1"},  # another value
        {"unit": "mmol/L"},  # incompatible unit: never compared
        {"effective_time": dt.time(9, 0)},  # timed vs untimed: equal, but see below
        {"date_precision": "month", "effective_date": D(2024, 3, 1)},  # not a day
        {"analyte_id": 2, "analyte_name": "MCV"},
    ],
)
def test_non_duplicates(other) -> None:
    a = obs(document_id="docA")
    b = obs(**{"document_id": "docB", **other})
    groups = find_duplicates([a, b], REF)
    if "effective_time" in other:  # equal by Phase 3 rules, but the time differs: only possible
        assert [g.kind for g in groups] == [POSSIBLE]
    else:
        assert groups == ()


def test_a_differently_printed_test_name_is_only_possible() -> None:
    a = obs(document_id="docA")
    b = obs(document_id="docB", analyte_raw="Hb")
    (group,) = find_duplicates([a, b], REF)
    assert group.kind == POSSIBLE and "different_printed_name" in group.reasons


def test_two_different_collection_times_are_not_duplicates() -> None:
    a = obs(document_id="docA", effective_time=dt.time(8, 0))
    b = obs(document_id="docB", effective_time=dt.time(18, 0))
    assert find_duplicates([a, b], REF) == ()


def test_a_corrected_value_is_at_most_a_possible_duplicate() -> None:
    a = obs(document_id="docA")
    b = obs(document_id="docB", value_source="human_correction", review_status="corrected")
    (group,) = find_duplicates([a, b], REF)
    assert group.kind == POSSIBLE and CORRECTED_VALUE in group.reasons
    assert group.representative == b.fact_id  # human-reviewed is shown first


def test_duplicates_are_deterministic_and_order_independent() -> None:
    facts = [obs(document_id=f"doc{i}") for i in range(4)]
    assert find_duplicates(facts, REF) == find_duplicates(list(reversed(facts)), REF)


# --- timeline ------------------------------------------------------------------------------------


def test_timeline_orders_by_clinical_date_never_by_upload() -> None:
    late = obs(document_id="d1", effective_date=D(2024, 6, 1))
    early = obs(document_id="d2", effective_date=D(2023, 1, 15))
    documents = [
        doc("d1", uploaded_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)),
        doc("d2", uploaded_at=dt.datetime(2026, 2, 1, tzinfo=dt.UTC)),  # uploaded later
    ]
    page = build_timeline([late, early], documents, [])
    assert [e.entry_id for e in page.entries] == [early.fact_id, late.fact_id]
    assert all(e.date_source == "collection" for e in page.entries)


def test_exact_duplicates_show_once_with_every_source_and_possible_ones_link() -> None:
    a, b = obs(document_id="d1"), obs(document_id="d2")
    c = obs(document_id="d3", value_raw="14.20", effective_date=D(2024, 3, 25))
    groups = find_duplicates([a, b], REF) + find_duplicates([obs(document_id="x")], REF)
    page = build_timeline([a, b], [doc("d1"), doc("d2")], groups)
    (entry,) = page.entries
    assert entry.duplicate_kind == EXACT and {s.fact_id for s in entry.sources} == {
        a.fact_id,
        b.fact_id,
    }
    groups2 = find_duplicates([a, c], REF)
    page2 = build_timeline([a, c], [doc("d1"), doc("d3")], groups2)
    assert [e.duplicate_kind for e in page2.entries] == ["possible", "possible"]
    assert page2.entries[0].related == (page2.entries[1].entry_id,)


def test_documents_are_dated_by_report_date_and_undated_items_are_separate() -> None:
    dated = doc("d1", document_date=D(2024, 3, 27), document_date_precision="day")
    undated = doc("d2", doc_type="other")
    o = obs(document_id="d1", effective_date=D(2024, 3, 25))
    loose = obs(document_id="d1", effective_date=None, date_precision=None)
    page = build_timeline([o, loose], [dated, undated], [])
    assert [(e.kind, e.date) for e in page.entries] == [
        (LAB_RESULT, D(2024, 3, 25)),
        (DOCUMENT, D(2024, 3, 27)),
    ]
    assert page.entries[1].date_source == DOCUMENT_REPORT_DATE
    assert page.undated_observations == (loose,)
    assert page.undated_documents == (undated,)


def test_mismatch_documents_are_excluded_and_counted_unknown_is_flagged() -> None:
    bad = doc("bad", identity_check="mismatch", document_date=D(2024, 1, 2))
    unsure = doc("u", identity_check="unknown")
    page = build_timeline(
        [obs(document_id="bad"), obs(document_id="u", identity_check="unknown")], [bad, unsure], []
    )
    assert page.excluded_mismatch_documents == 1
    (entry,) = page.entries
    assert entry.sources[0].document_id == "u" and entry.identity_uncertain


def test_filters_and_pagination() -> None:
    facts = [
        obs(document_id="d1", effective_date=D(2024, m, 15), analyte_name=name)
        for m in range(1, 7)
        for name in ("Haemoglobin", "MCV")
    ]
    month = obs(document_id="d2", effective_date=D(2024, 7, 1), date_precision="month")
    documents = [doc("d1", document_date=D(2024, 1, 16), document_date_precision="day"), doc("d2")]
    window = TimelineFilter(date_from=D(2024, 3, 1), date_to=D(2024, 7, 10))
    page = build_timeline([*facts, month], documents, [], filters=window, page_size=3)
    assert page.total == 9 and page.pages == 3 and len(page.entries) == 3
    last = build_timeline([*facts, month], documents, [], filters=window, page=3, page_size=3)
    assert last.entries[-1].entry_id == month.fact_id  # month overlaps the window, sorts last
    only = build_timeline(
        [*facts, month], documents, [], filters=TimelineFilter(analytes=frozenset({"MCV"}))
    )
    assert {e.kind for e in only.entries} == {LAB_RESULT} and only.total == 6
    docs_only = build_timeline(
        [*facts], documents, [], filters=TimelineFilter(kinds=frozenset({DOCUMENT}))
    )
    assert [e.kind for e in docs_only.entries] == [DOCUMENT]
    newest = build_timeline([*facts, month], documents, [], newest_first=True)
    assert newest.entries[0].entry_id == month.fact_id
    with pytest.raises(ValueError):
        build_timeline([], [], [], page=0)


# --- trends --------------------------------------------------------------------------------------


def test_trend_series_points_changes_and_range_states() -> None:
    first = obs(document_id="d1", effective_date=D(2024, 1, 10), value=Decimal("14.2"))
    second = obs(
        document_id="d2",
        effective_date=D(2024, 4, 10),
        value=Decimal("13.5"),
        range_comparison=None,
        ref_low=None,
        ref_high=None,
        ref_range_raw=None,
    )
    third = obs(
        document_id="d3",
        effective_date=D(2024, 7, 10),
        value=Decimal("17.6"),
        range_comparison="above",
        ref_high=Decimal("16.5"),
        ref_range_raw="12.0 - 16.5",
    )
    other_unit = obs(document_id="d4", effective_date=D(2024, 5, 1), unit="mmol/L")
    bound = obs(document_id="d5", effective_date=D(2024, 8, 1), comparator="<")
    partial = obs(document_id="d6", effective_date=D(2024, 9, 1), date_precision="month")
    unparsed_range = obs(document_id="d7", effective_date=D(2024, 10, 1), range_comparison=None)
    undated = obs(document_id="d8", effective_date=None, date_precision=None)
    t = build_trend(
        "Haemoglobin",
        [third, first, second, other_unit, bound, partial, unparsed_range, undated],
        [],
    )
    assert t.several_units and [s.unit for s in t.series] == ["g/dL", "mmol/L"]
    main = t.series[0]
    assert [p.observation.fact_id for p in main.points][:3] == [
        first.fact_id,
        second.fact_id,
        third.fact_id,
    ]
    states = [p.range_state for p in main.points]
    assert states[:3] == [WITHIN_PRINTED_RANGE, NO_PRINTED_RANGE, ABOVE_PRINTED_RANGE]
    assert {p.not_plotted_reason for p in main.points if not p.plotted} == {
        BOUND_VALUE,
        PARTIAL_DATE,
    }
    assert main.points[-1].range_state == NOT_COMPARED
    deltas = [(c.delta, c.days) for c in main.changes]
    assert deltas[:2] == [(Decimal("-0.7"), 91), (Decimal("4.1"), 91)]
    assert t.undated == (undated,)


def test_qualitative_values_are_not_numeric_series() -> None:
    q = obs(
        analyte_name="Urine Glucose", value=None, unit=None, qualitative="negative", value_raw="Nil"
    )
    (series,) = build_trend("Urine Glucose", [q], []).series
    assert series.kind == "qualitative" and series.points[0].not_plotted_reason == NOT_NUMERIC
    assert series.changes == ()


def test_a_day_with_several_untimed_results_gets_no_change() -> None:
    a = obs(document_id="d1", effective_date=D(2024, 1, 1), value=Decimal("12"))
    b = obs(document_id="d2", effective_date=D(2024, 2, 1), value=Decimal("13"))
    c = obs(document_id="d3", effective_date=D(2024, 2, 1), value=Decimal("15"))
    d = obs(document_id="d4", effective_date=D(2024, 3, 1), value=Decimal("14"))
    (series,) = build_trend("Haemoglobin", [a, b, c, d], []).series
    assert series.changes == () and series.skipped_change_dates == (D(2024, 2, 1),)
    b2 = obs(document_id="d2", effective_date=D(2024, 2, 1), value=Decimal("13"),
             effective_time=dt.time(8))  # fmt: skip
    c2 = obs(document_id="d3", effective_date=D(2024, 2, 1), value=Decimal("15"),
             effective_time=dt.time(20))  # fmt: skip
    (series2,) = build_trend("Haemoglobin", [a, b2, c2, d], []).series
    assert [c.delta for c in series2.changes] == [Decimal("1"), Decimal("2"), Decimal("-1")]


def test_exact_duplicates_are_one_trend_point() -> None:
    a, b = obs(document_id="d1"), obs(document_id="d2")
    (series,) = build_trend("Haemoglobin", [a, b], find_duplicates([a, b], REF)).series
    (point,) = series.points
    assert len(point.sources) == 2


def test_compare_two_dates() -> None:
    a1 = obs(document_id="d1", effective_date=D(2024, 1, 1), value=Decimal("14.2"))
    a2 = obs(document_id="d2", effective_date=D(2024, 6, 1), value=Decimal("13.0"))
    m1 = obs(analyte_name="MCV", analyte_id=2, document_id="d1", effective_date=D(2024, 1, 1),
             value=Decimal("88"), unit="fL")  # fmt: skip
    m2 = obs(analyte_name="MCV", analyte_id=2, document_id="d2", effective_date=D(2024, 6, 1),
             value=Decimal("88"), unit="fL")  # fmt: skip
    g1 = obs(analyte_name="Fasting Blood Sugar", analyte_id=3, document_id="d1",
             effective_date=D(2024, 1, 1), value=Decimal("99"), unit="mg/dL")  # fmt: skip
    g2 = obs(analyte_name="Fasting Blood Sugar", analyte_id=3, document_id="d2",
             effective_date=D(2024, 6, 1), value=Decimal("5.5"), unit="mmol/L")  # fmt: skip
    rows = compare_dates([a1, a2, m1, m2, g1, g2], [], D(2024, 1, 1), D(2024, 6, 1))
    by = {(r.analyte_name, r.unit): r for r in rows}
    assert by[("Haemoglobin", "g/dL")].status == CHANGED
    assert by[("Haemoglobin", "g/dL")].delta == Decimal("-1.2")
    assert by[("MCV", "fL")].status == UNCHANGED
    sugar = by[("Fasting Blood Sugar", "mg/dL")]
    assert sugar.status == ONLY_ON_FIRST and sugar.reason == DIFFERENT_UNITS


def test_compare_two_dates_with_several_results() -> None:
    a = obs(document_id="d1", effective_date=D(2024, 1, 1), value=Decimal("12"))
    b = obs(document_id="d2", effective_date=D(2024, 1, 1), value=Decimal("15"))
    c = obs(document_id="d3", effective_date=D(2024, 2, 1), value=Decimal("13"))
    (row,) = compare_dates([a, b, c], [], D(2024, 1, 1), D(2024, 2, 1))
    assert row.reason == SEVERAL_RESULTS and row.delta is None
