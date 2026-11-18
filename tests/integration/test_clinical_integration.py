"""Phase 6 clinical facts on medimind_test (PostgreSQL). Fictional content only.

Upload → all six pipeline stages (fake parser and embedder) → facts, review, comparison, records
Q&A; atomicity and rollback; idempotence; the gate; patient isolation; schema checks; the
migration 0005 downgrade refusal. docs/PHASE_6_PLAN.md §4–§9.
"""

import datetime as dt
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.errors import DocumentProcessingError
from app.ingestion.ir import ParsedDocument, ParsedPage, ParseMethod, ParserInfo
from app.ingestion.parsing import ParseResult
from app.ingestion.storage import sha256_hex
from app.ingestion.validation import ValidatedPdf
from app.records import clinical as repo
from app.services import clinical_records
from app.services.document_pipeline import run_document_pipeline
from app.services.extract_clinical import current_versions
from app.services.review_clinical import review_clinical_fact
from app.services.upload_document import upload_document
from tests.integration._guards import assert_is_test_database, run_alembic

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

PRESCRIPTION = (
    "Dr. Kiran Imaginary (Fictional), MBBS, MD\nSunbeam Family Clinic (Fictional)\n"
    "Date: 15/03/2024\nPatient Name: {name}   Age/Sex: 40 Y / F\nRx\n"
    "1. Tab. Fictomol 500 mg 1-0-1 x 5 days 2. Tab. Zorbaxin 10 mg OD\n"
    "Follow-up: review after 2 weeks."
)
DISCHARGE = (
    "Lotus Care Hospital (Fictional) DISCHARGE SUMMARY\n"
    "Patient Name: {name}   Age/Sex: 40 Y / F\nDate of Admission: 13/03/2024\n"
    "Date of Discharge: 15/03/2024\nFinal Diagnosis: Viral fever\n"
    "Allergies: No known drug allergies\nDischarge Medications:\n1. Tab. Fictomol 250 mg 1-0-1"
)
ALLERGY_RECORD = (
    "ALLERGY RECORD\nDate Recorded: 20/03/2024\nPatient Name: {name}   Age/Sex: 40 Y / F\n"
    "1. Penicillin - Reaction: rash - Severity: moderate - Status: confirmed"
)


class TextParser:
    """A parser stand-in that returns the given page texts for any PDF."""

    def __init__(self, *pages: str) -> None:
        self.pages = pages

    @property
    def parser_info(self) -> ParserInfo:
        return ParserInfo(name="fake", version="1", options={})

    def parse(self, pdf: ValidatedPdf) -> ParseResult:
        pages = [
            ParsedPage(page_number=n, text=t, parse_method=ParseMethod.NATIVE, native_char_count=50)
            for n, t in enumerate(self.pages, start=1)
        ]
        document = ParsedDocument(
            sha256=sha256_hex(pdf.data),
            page_count=pdf.page_count,
            parser=self.parser_info,
            pages=pages,
        )
        return ParseResult(document=document, docling_json=b"{}", duration_ms=1)


class Embedder:
    model_name = "fake@0"

    def embed_passages(self, texts):
        return [[1.0] + [0.0] * 383 for _ in texts]

    def embed_query(self, text):
        return [1.0] + [0.0] * 383


@pytest.fixture
def sql(test_engine: Engine, app_settings: Settings) -> Callable[..., list]:
    def run(statement: str, **params: object) -> list:
        with test_engine.begin() as connection:
            assert_is_test_database(connection, app_settings.db_name)
            result = connection.execute(text(statement), params)
            return list(result) if result.returns_rows else []

    return run


@pytest.fixture
def patient_name(sql, test_patient) -> str:
    return sql("SELECT display_name FROM patients WHERE id = :p", p=test_patient)[0][0]


def _pdf(pages: int) -> bytes:
    """A distinct small PDF per call (identical bytes would be deduplicated at upload)."""
    import io

    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for _ in range(pages):
        pdf.drawString(72, 720, f"Fictional test page {uuid.uuid4()}")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _ingest(settings: Settings, patient: uuid.UUID, *pages: str, name: str = "doc.pdf"):
    uploaded = upload_document(settings, patient, _pdf(len(pages)), original_filename=name)
    result = run_document_pipeline(
        settings, TextParser(*pages), Embedder(), patient, uploaded.document.id
    )
    return uploaded.document.id, result


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


# --- end to end ---------------------------------------------------------------------------------


def test_upload_to_answer_end_to_end(settings, sql, test_patient, patient_name) -> None:
    rx, result = _ingest(settings, test_patient, PRESCRIPTION.format(name=patient_name))
    stage = result.stage("clinical_extract")
    assert stage.status == "succeeded"
    assert (stage.detail.outcome, stage.detail.clinical_doc_type) == ("extracted", "prescription")
    assert result.stage("extract").detail.outcome == "not_applicable"  # Phase 3 unchanged
    row = sql("SELECT clinical_doc_type, doc_type FROM documents WHERE id = :d", d=rx)
    assert row == [("prescription", "other")]  # the Phase 2 type is never changed
    facts = repo.list_clinical_facts(test_patient, document_id=rx)
    meds = {f.values["name_raw"]: f for f in facts if f.fact_type == "medication"}
    assert meds["Fictomol"].review_status == "validated"
    assert meds["Fictomol"].values["strength_value"] == 500
    assert meds["Zorbaxin"].review_reasons == ("unmapped_medication",)
    # provenance: every excerpt is the stored page text at its offsets, on its own page
    pages = dict(sql("SELECT page_number, text FROM pages WHERE document_id = :d", d=rx))
    for fact in facts:
        assert pages[fact.page_number][fact.char_start : fact.char_end] == fact.source_excerpt
        if fact.date_raw is not None:
            page = pages[fact.date_page_number]
            assert page[fact.date_char_start : fact.date_char_end] == fact.date_raw
    # a second run of the pipeline repeats nothing
    again = run_document_pipeline(
        settings, TextParser("unused"), Embedder(), test_patient, rx
    ).stage("clinical_extract")
    assert (again.status, again.reason) == ("already_done", "already_extracted")
    assert len(repo.list_clinical_facts(test_patient, document_id=rx)) == len(facts)

    ds, _ = _ingest(settings, test_patient, DISCHARGE.format(name=patient_name), name="ds.pdf")
    al, _ = _ingest(settings, test_patient, ALLERGY_RECORD.format(name=patient_name), name="al.pdf")
    conflicts = {c.conflict_type for c in clinical_records.clinical_conflicts(test_patient)}
    assert conflicts == {"medication_same_date_mismatch", "allergy_contradiction"}

    reply = clinical_records.ask_records(
        test_patient,
        "What allergies are recorded?",
        copilot=lambda p, q: pytest.fail("the copilot must not answer this"),
        as_of=dt.date(2025, 1, 1),
    )
    assert reply.clinical is not None
    cited = {c.document_id for i in reply.clinical.section("documented") for c in i.citations}
    assert cited == {str(ds), str(al)}
    assert reply.clinical.section("conflicting")


def test_review_appends_decisions_and_never_changes_extracted_values(
    settings, sql, test_patient, patient_name
) -> None:
    rx, _ = _ingest(settings, test_patient, PRESCRIPTION.format(name=patient_name))
    ds, _ = _ingest(settings, test_patient, DISCHARGE.format(name=patient_name), name="ds.pdf")
    rx_med = next(
        f
        for f in repo.list_clinical_facts(test_patient, document_id=rx)
        if f.values.get("name_raw") == "Fictomol"
    )
    assert clinical_records.clinical_conflicts(test_patient)  # 500 mg vs 250 mg, same date
    review_clinical_fact(test_patient, rx_med.id, "correct", correction={"strength": "250 mg"})
    after = repo.get_clinical_fact(test_patient, rx_med.id)
    assert after.review_status == "corrected"
    assert after.values["strength_value"] == 500  # the extracted value is never overwritten
    assert after.corrected_values["normalized"]["strength_value"] == "250"
    medication_conflicts = [
        c for c in clinical_records.clinical_conflicts(test_patient)
        if c.conflict_type.startswith("medication")
    ]  # fmt: skip
    assert medication_conflicts == []  # derived from the corrected values
    review_clinical_fact(test_patient, rx_med.id, "reject", note="fictional note")
    assert repo.get_clinical_fact(test_patient, rx_med.id).review_status == "rejected"
    decisions = sql(
        "SELECT decision FROM review_decisions WHERE fact_id = :f ORDER BY decided_at", f=rx_med.id
    )
    assert [d[0] for d in decisions] == ["correct", "reject"]
    with pytest.raises(DocumentProcessingError) as error:
        review_clinical_fact(test_patient, rx_med.id, "correct", correction={"strength": "lots"})
    assert error.value.kind == "invalid_correction"
    assert len(sql("SELECT 1 FROM review_decisions WHERE fact_id = :f", f=rx_med.id)) == 2


# --- repository: atomicity, gate, idempotence --------------------------------------------------


def _ready(settings, patient, page: str, *, identity_text: str | None = None):
    """A parsed, classified and identity-checked document, before the clinical stage."""
    uploaded = upload_document(settings, patient, _pdf(1), original_filename="x.pdf")
    from app.services.check_identity import check_document_identity
    from app.services.classify_document import classify_document
    from app.services.process_document import process_document

    process_document(settings, TextParser(identity_text or page), patient, uploaded.document.id)
    classify_document(patient, uploaded.document.id)
    check_document_identity(patient, uploaded.document.id)
    return uploaded.document.id


def _record(fact_type="medication", page_number=1, **values) -> repo.ClinicalFactRecord:
    defaults = {
        "medication": {"name_raw": "Fictomol", "mention_context": "prescribed"},
        "allergy": {"substance_raw": "Penicillin", "substance_norm": "penicillin",
                    "allergy_status": "confirmed"},
        "event": {"event_type": "consultation", "event_context": "encounter",
                  "description_raw": "Dr. Kiran Imaginary"},
    }[fact_type]  # fmt: skip
    return repo.ClinicalFactRecord(
        fact_type=fact_type, page_number=page_number, source_locator="p1:l0:s1",
        source_excerpt="Dr. Kiran", char_start=0, char_end=9, subject="patient",
        negated=False, date=None, date_precision=None, date_raw=None, date_source=None,
        date_page_number=None, date_char_start=None, date_char_end=None,
        review_status="validated", review_reasons=(), quality_notes=(),
        values={**defaults, **values},
    )  # fmt: skip


def _write(patient, document, facts, **kw):
    return repo.record_clinical_extraction(
        patient, document, clinical_doc_type=kw.pop("clinical_doc_type", "prescription"),
        facts=facts, versions=current_versions(), reference_date=dt.date(2025, 1, 1),
        rule_ids=("rx.marker",), abstentions={}, duration_ms=1, **kw,
    )  # fmt: skip


def _counts(sql, document) -> tuple:
    runs = sql(
        "SELECT count(*) FROM pipeline_runs WHERE document_id = :d AND stage = 'clinical_extract'",
        d=document,
    )[0][0]
    facts = sql(
        "SELECT count(*) FROM extracted_facts WHERE document_id = :d "
        "AND fact_type <> 'observation'",
        d=document,
    )[0][0]
    kind = sql("SELECT clinical_doc_type FROM documents WHERE id = :d", d=document)[0][0]
    return runs, facts, kind


def test_a_failed_insert_writes_nothing(settings, sql, test_patient, patient_name) -> None:
    document = _ready(settings, test_patient, PRESCRIPTION.format(name=patient_name))
    with pytest.raises(ValueError):  # a page the document does not have: checked first
        _write(test_patient, document, [_record(page_number=9)])
    assert _counts(sql, document) == (0, 0, None)
    with pytest.raises(IntegrityError):  # a database CHECK fails mid-transaction: rolled back
        _write(test_patient, document, [_record(strength_value=-5, strength_unit="mg")])
    assert _counts(sql, document) == (0, 0, None)
    _write(test_patient, document, [_record()])
    assert _counts(sql, document) == (1, 1, "prescription")
    with pytest.raises(DocumentProcessingError) as error:
        _write(test_patient, document, [_record()])
    assert error.value.kind == "already_extracted"
    assert _counts(sql, document) == (1, 1, "prescription")


def test_skipped_runs_complete_the_stage_and_lab_reports_are_refused(
    settings, sql, test_patient, patient_name
) -> None:
    document = _ready(settings, test_patient, "A fictional page with no heading")
    with pytest.raises(DocumentProcessingError) as error:  # not a lab report
        repo.record_clinical_skipped(
            test_patient, document, reason="lab_report", versions=current_versions(),
            rule_ids=(), duration_ms=1,
        )  # fmt: skip
    assert error.value.kind == "lab_report"
    repo.record_clinical_skipped(
        test_patient, document, reason="unclassified", versions=current_versions(),
        rule_ids=(), duration_ms=1,
    )  # fmt: skip
    assert _counts(sql, document) == (1, 0, None)
    with pytest.raises(DocumentProcessingError) as error:
        _write(test_patient, document, [_record()])
    assert error.value.kind == "already_extracted"
    (view,) = [d for d in repo.list_clinical_documents(test_patient) if d.document_id == document]
    assert (view.run_status, view.skip_reason) == ("skipped", "unclassified")


def test_an_identity_mismatch_is_refused_and_nothing_is_written(
    settings, sql, test_patient
) -> None:
    other = PRESCRIPTION.format(name="Ravi Totallydifferent")
    document = _ready(settings, test_patient, other)
    assert sql("SELECT identity_check FROM documents WHERE id = :d", d=document) == [("mismatch",)]
    with pytest.raises(DocumentProcessingError) as error:
        _write(test_patient, document, [_record()])
    assert error.value.kind == "identity_mismatch"
    assert _counts(sql, document) == (0, 0, None)


# --- patient isolation --------------------------------------------------------------------------


def test_another_patient_sees_and_changes_nothing(
    settings, sql, test_patient, second_patient, patient_name
) -> None:
    rx, _ = _ingest(settings, test_patient, PRESCRIPTION.format(name=patient_name))
    fact = repo.list_clinical_facts(test_patient, document_id=rx)[0]
    assert repo.list_clinical_facts(second_patient) == ()
    with pytest.raises(DocumentProcessingError) as error:
        repo.list_clinical_facts(second_patient, document_id=rx)
    assert error.value.kind == "document_not_found"
    with pytest.raises(DocumentProcessingError) as error:
        repo.get_clinical_fact(second_patient, fact.id)
    assert error.value.kind == "fact_not_found"
    with pytest.raises(DocumentProcessingError) as error:
        repo.get_clinical_target(second_patient, rx)
    assert error.value.kind == "document_not_found"
    with pytest.raises(DocumentProcessingError) as error:
        review_clinical_fact(second_patient, fact.id, "reject")
    assert error.value.kind == "fact_not_found"
    assert clinical_records.document_comparison(second_patient, rx, rx).items == ()
    assert sql("SELECT count(*) FROM review_decisions WHERE fact_id = :f", f=fact.id) == [(0,)]


# --- schema --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE allergy_mentions SET substance_norm = NULL WHERE fact_id = :f",  # confirmed
        "UPDATE allergy_mentions SET allergy_status = 'maybe' WHERE fact_id = :f",
        "UPDATE allergy_mentions SET date_source = 'document' WHERE fact_id = :f",  # no date
    ],
)
def test_allergy_constraints(settings, sql, test_patient, patient_name, statement) -> None:
    document = _ready(settings, test_patient, ALLERGY_RECORD.format(name=patient_name))
    _write(test_patient, document, [_record("allergy")], clinical_doc_type="allergy_record")
    fact = sql("SELECT fact_id FROM allergy_mentions am JOIN extracted_facts f ON f.id = "
               "am.fact_id WHERE f.document_id = :d", d=document)[0][0]  # fmt: skip
    with pytest.raises(IntegrityError):
        sql(statement, f=fact)


def test_new_event_types_are_accepted_and_unknown_ones_refused(
    settings, sql, test_patient, patient_name
) -> None:
    document = _ready(settings, test_patient, PRESCRIPTION.format(name=patient_name))
    _write(test_patient, document, [_record("event", event_type="imaging")])
    fact = repo.list_clinical_facts(test_patient, document_id=document)[0]
    assert fact.values["event_type"] == "imaging"
    with pytest.raises(IntegrityError):
        sql("UPDATE clinical_events SET event_type = 'diagnosis' WHERE fact_id = :f", f=fact.id)
    with pytest.raises(IntegrityError):
        sql("UPDATE clinical_events SET event_context = 'later' WHERE fact_id = :f", f=fact.id)


def test_downgrade_is_refused_while_clinical_data_exists(
    settings, test_engine, app_settings, sql, test_patient, patient_name
) -> None:
    document = _ready(settings, test_patient, PRESCRIPTION.format(name=patient_name))
    _write(test_patient, document, [_record()])
    with pytest.raises(Exception, match="0005 downgrade refused"):
        run_alembic(test_engine, app_settings.db_name, "downgrade", "0004")
    assert sql("SELECT version_num FROM alembic_version") == [("0005",)]
    assert _counts(sql, document) == (1, 1, "prescription")


# --- acceptance fixes: identity outcome, answer-time citations ----------------------------------

NO_NAME_PRESCRIPTION = (
    "Dr. Kiran Imaginary (Fictional), MBBS, MD\nDate: 16/03/2024\nRx\n1. Tab. Mockcillin 250 mg BD"
)


def test_identity_outcomes_reach_views_and_answers(
    settings, sql, test_patient, patient_name
) -> None:
    confirmed, _ = _ingest(settings, test_patient, PRESCRIPTION.format(name=patient_name))
    unknown, _ = _ingest(settings, test_patient, NO_NAME_PRESCRIPTION, name="no-name.pdf")
    # The identity service decided "unknown" for the page that prints no patient name.
    assert sql("SELECT identity_check FROM documents WHERE id = :d", d=unknown) == [("unknown",)]
    # Setup for the read path only: mark the other document as a confirmed match (the fixture
    # profile's name does not give "match" under identity rules v2, which are frozen).
    sql("UPDATE documents SET identity_check = 'match' WHERE id = :d", d=confirmed)
    views = repo.list_clinical_facts(test_patient)
    assert {v.document_id: v.identity_check for v in views} == {
        confirmed: "match",
        unknown: "unknown",
    }
    reply = clinical_records.ask_records(
        test_patient,
        "What medications are documented in my records?",
        copilot=lambda p, q: pytest.fail("the copilot must not answer this"),
        as_of=dt.date(2025, 1, 1),
    )
    result = reply.clinical
    assert result is not None and result.status == "answered"  # citations matched stored pages
    flags = {
        c.document_id: c.identity_confirmed
        for i in result.section("documented")
        for c in i.citations
    }
    assert flags == {str(confirmed): True, str(unknown): False}


def test_stored_page_texts_are_patient_scoped(
    settings, test_patient, second_patient, patient_name
) -> None:
    rx, _ = _ingest(settings, test_patient, PRESCRIPTION.format(name=patient_name))
    assert set(repo.stored_page_texts(test_patient, [(rx, 1)])) == {(str(rx), 1)}
    assert repo.stored_page_texts(second_patient, [(rx, 1)]) == {}
    assert repo.stored_page_texts(test_patient, [(rx, 9)]) == {}
