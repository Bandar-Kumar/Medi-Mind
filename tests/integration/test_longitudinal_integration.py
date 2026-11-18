"""PostgreSQL integration tests of the Phase 4 longitudinal reads and services (medimind_test).

All text is fictional. Facts are stored through the production write path (``record_extraction``,
``review_fact``); the longitudinal layer only reads. docs/PHASE_4_PLAN.md §7-§8.
"""

import datetime as dt
import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import Engine, event, text

from app.core.config import Settings
from app.core.errors import DocumentProcessingError
from app.longitudinal.duplicates import EXACT
from app.longitudinal.timeline import LAB_RESULT, TimelineFilter
from app.records import facts as repo
from app.records import longitudinal as reads
from app.records import repositories as base
from app.records.repositories import PageRecord
from app.services import longitudinal as service
from app.services import review_fact
from app.services.fact_conflicts import build_detector
from app.services.reference_data import seed_reference
from tests.integration._guards import assert_is_test_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

VERSIONS = {
    "extractor_version": "lab-rules-2",
    "normalizer_version": "norm-1",
    "reference_data_version": "ref-test",
    "date_policy_version": "dates-1",
}
DETECTOR = build_detector()


@pytest.fixture
def sql(test_engine: Engine, app_settings: Settings) -> Callable[..., list]:
    def run(statement: str, **params: object) -> list:
        with test_engine.begin() as connection:
            assert_is_test_database(connection, app_settings.db_name)
            result = connection.execute(text(statement), params)
            return list(result) if result.returns_rows else []

    return run


@pytest.fixture(autouse=True)
def seeded(test_db_session_factory) -> None:
    seed_reference()


@pytest.fixture(autouse=True)
def conflicts_cleanup(sql, test_patient, second_patient):
    yield
    for patient in (test_patient, second_patient):  # conflicts RESTRICT patient deletion
        sql("DELETE FROM conflicts WHERE patient_id = :p", p=patient)


def _document(
    patient: uuid.UUID,
    rows: list[tuple[str, str]],
    *,
    date: str | None = "25/03/2024",
    identity: str = "match",
    doc_type: str = "lab_report",
) -> tuple[uuid.UUID, str]:
    """A parsed, classified, identity-checked document whose page prints ``rows``."""
    header = f"Collected: {date}\n" if date else "Lab report\n"
    page = header + "".join(f"{name}\t{value}\tg/dL\t13.0 - 17.0\n" for name, value in rows)
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    document = base.create_document(
        patient, original_filename="fictional.pdf", storage_path=f"uploads/{patient}/{sha}.pdf",
        sha256=sha, size_bytes=10, page_count=1,
    ).document  # fmt: skip
    claim = base.claim_document_for_parsing(patient, document.id)
    base.persist_parsed_pages(
        patient, document.id, claim.run_id,
        (PageRecord(page_number=1, text=page, parse_method="native", has_tables=True),),
    )  # fmt: skip
    base.record_classification(
        patient, document.id, doc_type=doc_type, score=0.8, rules_version="2", duration_ms=1
    )
    base.record_identity_check(
        patient, document.id, outcome=identity, reason_codes=("name_match",), rules_version="2",
        duration_ms=1,
    )  # fmt: skip
    return document.id, page


def _extract(patient, document, page, rows, *, date=dt.date(2024, 3, 25), status="validated"):
    observations = []
    for i, (name, value) in enumerate(rows):
        excerpt = f"{name}\t{value}\tg/dL\t13.0 - 17.0"
        start = page.index(excerpt)
        number = Decimal(value)
        observations.append(
            repo.ObservationRecord(
                page_number=1,
                source_locator=f"p1:t0:r{i + 1}",
                source_excerpt=excerpt,
                char_start=start,
                char_end=start + len(excerpt),
                analyte_raw=name,
                analyte_name=name,
                analyte_match="alias_exact",
                value_raw=value,
                value_num=number,
                comparator=None,
                value_qualitative=None,
                unit_raw="g/dL",
                unit_norm="g/dL",
                value_num_norm=number,
                ref_range_raw="13.0 - 17.0",
                ref_low=Decimal("13.0"),
                ref_high=Decimal("17.0"),
                ref_range_comparison="within" if 13 <= number <= 17 else "below",
                flag_as_printed=None,
                specimen=None,
                effective_date=date,
                date_precision="day" if date else None,
                date_raw="x" if date else None,
                effective_date_source="collection" if date else None,
                date_page_number=1 if date else None,
                date_char_start=11 if date else None,
                date_char_end=21 if date else None,
                effective_time=None,
                time_raw=None,
                review_status=status,
                review_reasons=("ambiguous_date",) if status == "needs_review" else (),
                quality_notes=() if date else ("no_effective_date",),
            )  # fmt: skip
        )
    return repo.record_extraction(
        patient, document, observations=observations, versions=VERSIONS,
        reference_date=dt.date(2026, 11, 17), abstentions={}, report_date=None, duration_ms=1,
        detector=DETECTOR,
    )  # fmt: skip


def _facts(patient, document) -> dict[str, uuid.UUID]:
    return {f.analyte_raw: f.id for f in repo.list_document_facts(patient, document)}


def test_usable_facts_exclude_needs_review_rejected_and_other_patients(
    test_patient, second_patient
) -> None:
    d1, p1 = _document(test_patient, [("Haemoglobin", "14.2"), ("MCV", "88")])
    _extract(test_patient, d1, p1, [("Haemoglobin", "14.2"), ("MCV", "88")])
    d2, p2 = _document(test_patient, [("Haemoglobin", "13.1")])
    _extract(test_patient, d2, p2, [("Haemoglobin", "13.1")], status="needs_review")
    other, po = _document(second_patient, [("Haemoglobin", "15.0")])
    _extract(second_patient, other, po, [("Haemoglobin", "15.0")])
    mcv = _facts(test_patient, d1)["MCV"]
    review_fact.review_fact(test_patient, mcv, "reject")

    usable = reads.list_usable_facts(test_patient)
    assert {f.analyte_raw for f in usable} == {"Haemoglobin"}
    assert {f.document_id for f in usable} == {d1}
    assert (
        reads.list_facts_by_ids(test_patient, [_facts(second_patient, other)["Haemoglobin"]]) == ()
    )
    view = service.timeline(test_patient)
    assert view.awaiting_review == 1
    assert [e.observation.value for e in view.page.entries if e.kind == LAB_RESULT] == [
        Decimal("14.2")
    ]
    assert service.timeline(second_patient).page.total == 1


def test_a_corrected_date_moves_the_entry_and_passes_the_date_filter(test_patient) -> None:
    d1, p1 = _document(test_patient, [("Haemoglobin", "14.2")])
    _extract(test_patient, d1, p1, [("Haemoglobin", "14.2")])
    fact_id = _facts(test_patient, d1)["Haemoglobin"]
    review_fact.review_fact(
        test_patient, fact_id, "correct",
        correction=review_fact.Correction(value="14.6", effective_date="27/08/2024"),
    )  # fmt: skip
    window = TimelineFilter(date_from=dt.date(2024, 8, 1), date_to=dt.date(2024, 8, 31))
    (entry,) = service.timeline(test_patient, filters=window).page.entries
    assert entry.date == dt.date(2024, 8, 27) and entry.observation.value == Decimal("14.6")
    assert entry.observation.value_source == "human_correction"
    assert entry.observation.value_raw == "14.2"  # the printed value is kept
    before = TimelineFilter(date_to=dt.date(2024, 4, 1))
    assert service.timeline(test_patient, filters=before).page.total == 0


def test_exact_duplicates_conflicts_and_their_presentation(test_patient) -> None:
    d1, p1 = _document(test_patient, [("Haemoglobin", "14.2")])
    _extract(test_patient, d1, p1, [("Haemoglobin", "14.2")])
    d2, p2 = _document(test_patient, [("Haemoglobin", "14.2")])  # same printed result again
    _extract(test_patient, d2, p2, [("Haemoglobin", "14.2")])
    (group,) = service.duplicates(test_patient)
    assert group.group.kind == EXACT and len(group.members) == 2
    (entry,) = [e for e in service.timeline(test_patient).page.entries if e.kind == LAB_RESULT]
    assert {s.document_id for s in entry.sources} == {str(d1), str(d2)}

    d3, p3 = _document(test_patient, [("Haemoglobin", "9.8")])  # disagrees the same day
    _extract(test_patient, d3, p3, [("Haemoglobin", "9.8")])
    view = service.trend(test_patient, "Haemoglobin")
    (conflict,) = view.conflicts
    assert len(conflict.members) == 3 and {m.role for m in conflict.members} == {"direct"}
    assert len(conflict.direct_pairs) == 2
    assert all(code == service.NON_OVERLAPPING_VALUES for _, _, code in conflict.direct_pairs)
    (series,) = view.trend.series
    assert len(series.points) == 2  # the exact pair is one point; the conflicting value another
    assert series.changes == ()  # several untimed results on one date: no change computed

    low = _facts(test_patient, d3)["Haemoglobin"]
    review_fact.review_fact(test_patient, low, "reject")
    assert service.trend(test_patient, "Haemoglobin").conflicts == ()
    (closed,) = [c for c in service.conflicts(test_patient, include_closed=True)]
    assert closed.record.closed_reason == "member_rejected"
    assert any(m.rejected_or_superseded for m in closed.members)  # history keeps the member
    assert all(
        e.observation.value == Decimal("14.2")
        for e in service.timeline(test_patient).page.entries
        if e.kind == LAB_RESULT
    )


def test_undated_mismatch_and_unknown_documents(test_patient) -> None:
    d1, p1 = _document(test_patient, [("Haemoglobin", "14.2")], date=None, identity="unknown")
    _extract(test_patient, d1, p1, [("Haemoglobin", "14.2")], date=None)
    mismatch, page = _document(test_patient, [("Haemoglobin", "11.0")], identity="mismatch")
    stored = {d.id: d.identity_check for d in reads.list_timeline_documents(test_patient)}
    assert stored[mismatch] == "mismatch"
    with pytest.raises(DocumentProcessingError) as caught:  # extraction is blocked (Phase 3)
        _extract(test_patient, mismatch, page, [("Haemoglobin", "11.0")])
    assert caught.value.kind == "identity_mismatch"
    view = service.timeline(test_patient)
    assert view.page.total == 0
    (undated,) = view.page.undated_observations
    assert undated.identity_check == "unknown"
    assert view.page.excluded_mismatch_documents == 1
    overview = service.overview(test_patient)
    assert overview.identity_mismatch_documents == 1 and overview.undated_results == 1


def test_evidence_is_exact_and_patient_scoped(test_patient, second_patient, sql) -> None:
    d1, p1 = _document(test_patient, [("Haemoglobin", "14.2")])
    _extract(test_patient, d1, p1, [("Haemoglobin", "14.2")])
    fact_id = _facts(test_patient, d1)["Haemoglobin"]
    found = service.evidence(test_patient, fact_id)
    f = found.view.fact
    page_text = sql("SELECT text FROM pages WHERE document_id = :d", d=d1)[0][0]
    assert page_text[f.char_start : f.char_end] == f.source_excerpt
    assert found.filename == "fictional.pdf" and not found.is_correction
    with pytest.raises(DocumentProcessingError) as caught:
        service.evidence(second_patient, fact_id)
    assert caught.value.kind == "fact_not_found"


def test_compare_dates_and_analytes(test_patient) -> None:
    d1, p1 = _document(test_patient, [("Haemoglobin", "14.2")], date="15/01/2024")
    _extract(test_patient, d1, p1, [("Haemoglobin", "14.2")], date=dt.date(2024, 1, 15))
    d2, p2 = _document(test_patient, [("Haemoglobin", "13.5")], date="15/06/2024")
    _extract(test_patient, d2, p2, [("Haemoglobin", "13.5")], date=dt.date(2024, 6, 15))
    (summary,) = service.analytes(test_patient)
    assert (summary.name, summary.results, summary.units) == ("Haemoglobin", 2, ("g/dL",))
    assert service.result_dates(test_patient) == (dt.date(2024, 1, 15), dt.date(2024, 6, 15))
    (row,) = service.compare_dates(test_patient, dt.date(2024, 1, 15), dt.date(2024, 6, 15)).rows
    assert row.delta == Decimal("-0.7") and row.status == "changed"


def test_statement_count_does_not_grow_with_the_history(test_patient, test_engine) -> None:
    statements: list[str] = []

    def count(*_args: object) -> None:
        statements.append("x")

    def run() -> int:
        statements.clear()
        event.listen(test_engine, "before_cursor_execute", count)
        try:
            service.timeline(test_patient)
            service.trend(test_patient, "Haemoglobin")
        finally:
            event.remove(test_engine, "before_cursor_execute", count)
        return len(statements)

    d1, p1 = _document(test_patient, [("Haemoglobin", "14.2")])
    _extract(test_patient, d1, p1, [("Haemoglobin", "14.2")])
    small = run()
    for month in range(2, 10):
        rows = [("Haemoglobin", f"1{month}.1"), ("MCV", "88")]
        d, p = _document(test_patient, rows, date=f"15/{month:02d}/2024")
        _extract(test_patient, d, p, rows, date=dt.date(2024, month, 15))
    assert run() == small  # no per-fact or per-document queries
