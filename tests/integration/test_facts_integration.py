"""Integration tests for extraction persistence, review and conflicts on medimind_test (Phase 3).

Only the guarded test database is used. All text is fictional. These tests need PostgreSQL
(Docker); without it they are skipped, never reported as passed. docs/PHASE_3_PLAN.md §5-§9.
"""

import datetime as dt
import threading
import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.errors import DocumentProcessingError
from app.records import facts as repo
from app.records import repositories as base
from app.records.repositories import PageRecord
from app.services.fact_conflicts import build_detector
from app.services.reference_data import seed_reference
from tests.integration._guards import assert_is_test_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

PAGE = "Report Date: 25/03/2024\nTest\tResult\tUnit\nHaemoglobin\t14.2\tgm/dL\nMCV\t88\tfL"
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
    seed_reference()  # idempotent: reference data only


@pytest.fixture(autouse=True)
def conflicts_cleanup(sql, test_patient, second_patient):
    yield
    for patient in (test_patient, second_patient):  # conflicts RESTRICT patient deletion
        sql("DELETE FROM conflicts WHERE patient_id = :p", p=patient)


def _ready_document(
    sql, patient: uuid.UUID, *, identity="match", doc_type="lab_report"
) -> uuid.UUID:
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    document = base.create_document(
        patient, original_filename="fictional.pdf", storage_path=f"uploads/{patient}/{sha}.pdf",
        sha256=sha, size_bytes=10, page_count=1,
    ).document  # fmt: skip
    claim = base.claim_document_for_parsing(patient, document.id)
    base.persist_parsed_pages(
        patient, document.id, claim.run_id,
        (PageRecord(page_number=1, text=PAGE, parse_method="native", has_tables=True),),
    )  # fmt: skip
    base.record_classification(
        patient, document.id, doc_type=doc_type, score=0.8, rules_version="2", duration_ms=1
    )
    base.record_identity_check(
        patient, document.id, outcome=identity, reason_codes=("name_match",), rules_version="2",
        duration_ms=1,
    )  # fmt: skip
    return document.id


def _obs(value="14.2", *, locator="p1:t0:r1", analyte="Haemoglobin", start=None, **kw):
    excerpt = f"{analyte}\t{value}\tgm/dL"
    begin = PAGE.index(excerpt) if start is None and excerpt in PAGE else (start or 0)
    base_kw = dict(
        page_number=1, source_locator=locator, source_excerpt=excerpt, char_start=begin,
        char_end=begin + len(excerpt), analyte_raw=analyte, analyte_name=analyte,
        analyte_match="alias_exact", value_raw=value, value_num=Decimal(value), comparator=None,
        value_qualitative=None, unit_raw="gm/dL", unit_norm="g/dL", value_num_norm=Decimal(value),
        ref_range_raw=None, ref_low=None, ref_high=None, flag_as_printed=None,
        ref_range_comparison=None, specimen=None, effective_date=dt.date(2024, 3, 25),
        date_precision="day", date_raw="25/03/2024", effective_date_source="report",
        date_page_number=1, date_char_start=13, date_char_end=23, effective_time=None,
        time_raw=None, review_status="validated", review_reasons=(), quality_notes=(),
    )  # fmt: skip
    base_kw.update(kw)
    return repo.ObservationRecord(**base_kw)


def _extract(patient, document, observations, **kw):
    return repo.record_extraction(
        patient, document, observations=observations, versions=kw.pop("versions", VERSIONS),
        reference_date=dt.date(2026, 11, 17), abstentions={"table_unrecognized": 1},
        report_date=repo.ReportDate(dt.date(2024, 3, 25), "day", "25/03/2024"), duration_ms=3,
        detector=DETECTOR, **kw,
    )  # fmt: skip


# --- extraction runs, gate, idempotency ------------------------------------------------------


def test_extraction_writes_one_run_and_all_facts_atomically(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient)
    record = _extract(
        test_patient, document, [_obs(), _obs("88", locator="p1:t0:r2", analyte="MCV")]
    )
    rows = sql(
        "SELECT f.extraction_run_id, f.review_status, f.source_locator, f.prompt_version "
        "FROM extracted_facts f WHERE f.document_id = :d ORDER BY f.source_locator", d=document
    )  # fmt: skip
    assert [r[2] for r in rows] == ["p1:t0:r1", "p1:t0:r2"]
    assert {r[0] for r in rows} == {record.run_id} and {r[3] for r in rows} == {"none"}
    run = sql("SELECT status, component_versions FROM pipeline_runs WHERE id = :r", r=record.run_id)
    assert run[0][0] == "succeeded" and run[0][1]["facts_written"] == 2
    assert run[0][1]["reference_date"] == "2026-11-17"
    date = sql("SELECT document_date FROM documents WHERE id = :d", d=document)[0][0]
    assert date == dt.date(2024, 3, 25)


def test_a_repeat_is_already_extracted(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient)
    _extract(test_patient, document, [_obs()])
    with pytest.raises(DocumentProcessingError) as caught:
        _extract(test_patient, document, [_obs()])
    assert caught.value.kind == "already_extracted"


def test_a_mismatch_document_gets_no_facts_and_no_run(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient, identity="mismatch")
    with pytest.raises(DocumentProcessingError) as caught:
        _extract(test_patient, document, [_obs()])
    assert caught.value.kind == "identity_mismatch"
    assert sql("SELECT count(*) FROM extracted_facts WHERE document_id = :d", d=document)[0][0] == 0
    assert sql(
        "SELECT count(*) FROM pipeline_runs WHERE document_id = :d AND stage = 'extract'",
        d=document,
    )[0][0] == 0  # fmt: skip


def test_other_types_get_a_skipped_run_that_completes_the_stage(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient, doc_type="other")
    record = repo.record_extraction_skipped(
        test_patient, document, versions=VERSIONS, duration_ms=1
    )
    assert record.status == "skipped"
    assert repo.get_extraction_target(test_patient, document).extracted
    with pytest.raises(DocumentProcessingError) as caught:
        repo.record_extraction_skipped(test_patient, document, versions=VERSIONS, duration_ms=1)
    assert caught.value.kind == "already_extracted"


def test_a_failed_insert_leaves_no_facts(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient)
    with pytest.raises(DocumentProcessingError) as caught:  # an unknown analyte name
        _extract(test_patient, document, [_obs(), _obs(locator="p1:t0:r9", analyte_name="Nope")])
    assert caught.value.kind == "reference_data_not_seeded"
    assert sql("SELECT count(*) FROM extracted_facts WHERE document_id = :d", d=document)[0][0] == 0
    assert not repo.get_extraction_target(test_patient, document).extracted


def test_concurrent_extractions_one_wins(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient)
    outcomes: list[object] = []

    def attempt() -> None:
        try:
            outcomes.append(_extract(test_patient, document, [_obs()]))
        except DocumentProcessingError as exc:
            outcomes.append(exc.kind)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert sorted(o if isinstance(o, str) else "won" for o in outcomes) == [
        "already_extracted",
        "won",
    ]


# --- database constraints ----------------------------------------------------------------------


def _fact_id(sql, document) -> uuid.UUID:
    return sql("SELECT id FROM extracted_facts WHERE document_id = :d LIMIT 1", d=document)[0][0]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE extracted_facts SET review_status = 'extracted' WHERE id = :f",
        "UPDATE extracted_facts SET review_reasons = '{ambiguous_date}' WHERE id = :f",
        "UPDATE extracted_facts SET source_locator = 'row 1' WHERE id = :f",
        "UPDATE extracted_facts SET char_start = NULL, char_end = NULL WHERE id = :f",
        "UPDATE extracted_facts SET superseded_at = now() WHERE id = :f",
        "UPDATE observations SET ref_range_comparison = 'high' WHERE fact_id = :f",
        "UPDATE observations SET effective_date_source = NULL WHERE fact_id = :f",
    ],
)
def test_fact_constraints_refuse_invalid_states(sql, test_patient, statement) -> None:
    document = _ready_document(sql, test_patient)
    _extract(test_patient, document, [_obs()])
    with pytest.raises(IntegrityError):
        sql(statement, f=_fact_id(sql, document))


def test_supersession_must_name_a_run_of_the_same_document(sql, test_patient, second_patient):
    mine = _ready_document(sql, test_patient)
    other_doc = _ready_document(sql, test_patient)
    foreign = _ready_document(sql, second_patient)
    _extract(test_patient, mine, [_obs()])
    other_run = _extract(test_patient, other_doc, [_obs()]).run_id
    foreign_run = _extract(second_patient, foreign, [_obs()]).run_id
    fact = _fact_id(sql, mine)
    for run in (other_run, foreign_run):
        with pytest.raises(IntegrityError):
            sql(
                "UPDATE extracted_facts SET superseded_at = now(), superseded_by_run_id = :r "
                "WHERE id = :f",
                r=run, f=fact,
            )  # fmt: skip


def test_a_missing_status_is_refused_without_a_default(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient)
    run = _extract(test_patient, document, [_obs()]).run_id
    with pytest.raises(IntegrityError):
        sql(
            "INSERT INTO extracted_facts (patient_id, document_id, fact_type, page_number, "
            "source_excerpt, char_start, char_end, assertion_type, negated, subject, "
            "extractor_version, prompt_version, model_id, extraction_run_id, source_locator, "
            "normalizer_version, reference_data_version) VALUES (:p, :d, 'observation', 1, 'x', "
            "0, 1, 'direct_fact', false, 'patient', 'v', 'none', 'none', :r, 'p1:l9', 'n', 'r')",
            p=test_patient, d=document, r=run,
        )  # fmt: skip


# --- isolation ---------------------------------------------------------------------------------


def test_another_patients_document_and_fact_are_not_found(sql, test_patient, second_patient):
    document = _ready_document(sql, test_patient)
    _extract(test_patient, document, [_obs()])
    fact = _fact_id(sql, document)
    for call in (
        lambda: repo.get_extraction_target(second_patient, document),
        lambda: repo.list_document_facts(second_patient, document),
        lambda: repo.list_extraction_runs(second_patient, document),
        lambda: _extract(second_patient, document, [_obs()]),
        lambda: repo.complete_document_if_ready(second_patient, document),
    ):
        with pytest.raises(DocumentProcessingError) as caught:
            call()
        assert caught.value.kind == "document_not_found"
    for call in (
        lambda: repo.get_fact(second_patient, fact),
        lambda: repo.record_review_decision(
            second_patient,
            fact,
            decision="verify",
            corrected_values=None,
            note=None,
            detector=DETECTOR,
        ),  # fmt: skip
    ):
        with pytest.raises(DocumentProcessingError) as caught:
            call()
        assert caught.value.kind == "fact_not_found"
    assert repo.list_conflicts(second_patient) == ()


# --- review decisions --------------------------------------------------------------------------


def test_decisions_append_and_the_latest_decides(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient)
    _extract(test_patient, document, [_obs()])
    fact = _fact_id(sql, document)
    before = sql("SELECT value_raw, value_num FROM observations WHERE fact_id = :f", f=fact)
    correction = {"schema": "observation-correction-1", "entered": {"value": "13.9"},
                  "normalized": {"value_num_norm": "13.9", "unit_norm": "g/dL"}}  # fmt: skip
    for decision, values, status in (
        ("reject", None, "rejected"),
        ("correct", correction, "corrected"),
        ("verify", None, "verified"),
    ):
        record = repo.record_review_decision(
            test_patient, fact, decision=decision, corrected_values=values, note=None,
            detector=DETECTOR,
        )  # fmt: skip
        assert record.review_status == status
    assert [d.decision for d in repo.get_fact(test_patient, fact).decisions] == [
        "reject",
        "correct",
        "verify",
    ]
    after = sql("SELECT value_raw, value_num FROM observations WHERE fact_id = :f", f=fact)
    assert before == after  # values are never updated


def test_no_human_status_without_a_decision_row(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient)
    _extract(test_patient, document, [_obs()])
    orphans = sql(
        "SELECT count(*) FROM extracted_facts f WHERE f.review_status IN "
        "('verified', 'corrected', 'rejected') AND NOT EXISTS "
        "(SELECT 1 FROM review_decisions d WHERE d.fact_id = f.id)"
    )
    assert orphans[0][0] == 0


# --- conflicts -----------------------------------------------------------------------------------


def _two_reports(sql, patient, first="14.2", second="11.0"):
    a = _ready_document(sql, patient)
    b = _ready_document(sql, patient)
    _extract(patient, a, [_obs(first, start=0)])
    _extract(patient, b, [_obs(second, start=0)])
    return a, b


def test_a_same_date_disagreement_opens_one_conflict_and_redetection_is_idempotent(
    sql, test_patient
) -> None:
    _two_reports(sql, test_patient)
    current = repo.list_conflicts(test_patient)
    assert len(current) == 1 and len(current[0].member_fact_ids) == 2
    repo.acknowledge_conflict(test_patient, current[0].id)
    fact = current[0].member_fact_ids[0]
    repo.record_review_decision(  # a plain verify changes nothing: same key, still acknowledged
        test_patient, fact, decision="verify", corrected_values=None, note=None, detector=DETECTOR
    )
    again = repo.list_conflicts(test_patient)
    assert [c.id for c in again] == [current[0].id] and again[0].status == "acknowledged"


def test_reject_then_verify_closes_and_reopens_as_a_new_conflict(sql, test_patient) -> None:
    _two_reports(sql, test_patient)
    first = repo.list_conflicts(test_patient)[0]
    fact = first.member_fact_ids[0]
    repo.record_review_decision(
        test_patient, fact, decision="reject", corrected_values=None, note=None, detector=DETECTOR
    )
    assert repo.list_conflicts(test_patient) == ()
    closed = repo.list_conflicts(test_patient, current=False)
    assert closed[0].id == first.id and closed[0].closed_reason == "member_rejected"
    repo.record_review_decision(
        test_patient, fact, decision="verify", corrected_values=None, note=None, detector=DETECTOR
    )
    reopened = repo.list_conflicts(test_patient)
    assert len(reopened) == 1 and reopened[0].id != first.id
    assert repo.list_conflicts(test_patient, current=False)[0].id == first.id  # history kept


def test_a_correction_that_agrees_closes_the_conflict(sql, test_patient) -> None:
    _two_reports(sql, test_patient)
    conflict = repo.list_conflicts(test_patient)[0]
    facts = [repo.get_fact(test_patient, f) for f in conflict.member_fact_ids]
    target = next(f for f in facts if f.value_raw == "11.0")
    normalized = {"value_num_norm": "14.2", "unit_norm": "g/dL"}
    normalized |= {"analyte_id": target.analyte_id, "analyte_name": "Haemoglobin"}
    agree = {"normalized": normalized}
    repo.record_review_decision(
        test_patient, target.id, decision="correct", corrected_values=agree, note=None,
        detector=DETECTOR,
    )  # fmt: skip
    assert repo.list_conflicts(test_patient) == ()
    assert repo.list_conflicts(test_patient, current=False)[0].closed_reason == "member_corrected"


def test_document_deletion_closes_conflicts_and_keeps_history(sql, test_patient) -> None:
    first, _second = _two_reports(sql, test_patient)
    conflict = repo.list_conflicts(test_patient)[0]
    base.delete_document(test_patient, first, conflict_detector=DETECTOR)
    assert repo.list_conflicts(test_patient) == ()
    history = repo.list_conflicts(test_patient, current=False)
    assert history[0].id == conflict.id and history[0].closed_reason == "member_deleted"
    assert history[0].member_count_at_detection == 2 and len(history[0].member_fact_ids) == 1


# --- re-extraction and completion ------------------------------------------------------------


def test_reextraction_supersedes_and_is_refused_after_review(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient)
    _extract(test_patient, document, [_obs()])
    with pytest.raises(DocumentProcessingError) as caught:
        repo.reextract_and_supersede(
            test_patient, document, observations=[_obs()], versions=VERSIONS,
            reference_date=dt.date(2026, 11, 17), abstentions={}, report_date=None,
            duration_ms=1, detector=DETECTOR,
        )  # fmt: skip
    assert caught.value.kind == "not_reextractable"
    newer = {**VERSIONS, "extractor_version": "lab-rules-3"}
    record = repo.reextract_and_supersede(
        test_patient, document, observations=[_obs()], versions=newer,
        reference_date=dt.date(2026, 11, 17), abstentions={}, report_date=None, duration_ms=1,
        detector=DETECTOR,
    )  # fmt: skip
    assert record.superseded == 1
    assert len(repo.list_document_facts(test_patient, document)) == 1
    assert len(repo.list_document_facts(test_patient, document, include_superseded=True)) == 2
    fact = repo.list_document_facts(test_patient, document)[0].id
    repo.record_review_decision(
        test_patient, fact, decision="verify", corrected_values=None, note=None, detector=DETECTOR
    )
    with pytest.raises(DocumentProcessingError) as caught:
        repo.reextract_and_supersede(
            test_patient, document, observations=[_obs()],
            versions={**VERSIONS, "extractor_version": "lab-rules-4"},
            reference_date=dt.date(2026, 11, 17), abstentions={}, report_date=None,
            duration_ms=1, detector=DETECTOR,
        )  # fmt: skip
    assert caught.value.kind == "has_review_decisions"


def test_processed_needs_index_and_extract(sql, test_patient) -> None:
    document = _ready_document(sql, test_patient)
    _extract(test_patient, document, [_obs()])
    assert not repo.complete_document_if_ready(test_patient, document)  # not indexed yet
    status = sql("SELECT processing_status FROM documents WHERE id = :d", d=document)[0][0]
    assert status == "processing"
