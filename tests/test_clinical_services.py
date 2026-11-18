"""Phase 6 services with the repository replaced by fakes (no database). Fictional content only.

``extract_clinical_document`` (stage outcomes, failure recording, logs), ``review_clinical_fact``
(correction normalization and refusals), ``ask_records`` (dispatch between the clinical path and
the unchanged Phase 5 copilot).
"""

import datetime as dt
import logging
import uuid
from types import SimpleNamespace

import pytest

from app.core.errors import DocumentProcessingError
from app.records.clinical import ClinicalExtractionRecord, ClinicalFactView
from app.services import clinical_records as records
from app.services import extract_clinical as stage
from app.services import review_clinical as review
from tests.fixtures.extraction_ir import document, page

PATIENT = uuid.uuid4()
DOC = uuid.uuid4()
SECRET = "Fictomol"  # document content: must never reach a log line
TEXT = (
    "Dr. Kiran Imaginary (Fictional), MD\nDate: 15/03/2024\nRx\n"
    f"1. Tab. {SECRET} 500 mg 1-0-1\nFollow-up: review after 5 days."
)


def _target(doc_type="other", blocked=None, extracted=False):
    return SimpleNamespace(
        document_id=DOC,
        sha256="a" * 64,
        page_count=1,
        doc_type=doc_type,
        reference_date=dt.date(2025, 1, 1),
        blocked_reason=blocked,
        extracted=extracted,
    )


@pytest.fixture
def wired(monkeypatch):
    calls: dict[str, list] = {"extraction": [], "skipped": [], "failure": []}
    ir = document([page(1, [TEXT])])
    monkeypatch.setattr(stage, "get_clinical_target", lambda p, d: _target())
    monkeypatch.setattr(stage, "_verified_ir", lambda *a: ir)
    monkeypatch.setattr(
        stage,
        "list_document_pages",
        lambda p, d: [SimpleNamespace(page_number=1, text=TEXT)],
    )

    def extraction(patient_id, document_id, **kw):
        calls["extraction"].append(kw)
        n = len(kw["facts"])
        return ClinicalExtractionRecord(
            document_id, uuid.uuid4(), "succeeded", kw["clinical_doc_type"], n, 0
        )

    def skipped(patient_id, document_id, **kw):
        calls["skipped"].append(kw)
        return ClinicalExtractionRecord(
            document_id, uuid.uuid4(), "skipped", None, 0, 0, kw["reason"]
        )

    monkeypatch.setattr(stage, "record_clinical_extraction", extraction)
    monkeypatch.setattr(stage, "record_clinical_skipped", skipped)
    monkeypatch.setattr(
        stage, "record_clinical_failure", lambda p, d, **kw: calls["failure"].append(kw)
    )
    return calls


def test_a_prescription_is_classified_extracted_and_stored_in_one_call(wired, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    result = stage.extract_clinical_document(None, PATIENT, DOC)  # type: ignore[arg-type]
    assert (result.outcome, result.clinical_doc_type) == ("extracted", "prescription")
    (call,) = wired["extraction"]
    assert {f.fact_type for f in call["facts"]} == {"medication", "event"}
    assert set(call["versions"]) == {
        "classifier_version",
        "extractor_version",
        "normalizer_version",
        "reference_data_version",
        "date_policy_version",
    }
    assert SECRET not in caplog.text


def test_a_lab_report_gets_a_skipped_run(wired, monkeypatch) -> None:
    monkeypatch.setattr(stage, "get_clinical_target", lambda p, d: _target("lab_report"))
    result = stage.extract_clinical_document(None, PATIENT, DOC)  # type: ignore[arg-type]
    assert (result.outcome, result.skip_reason) == ("skipped", "lab_report")
    assert wired["extraction"] == []


def test_an_untypeable_document_is_skipped_never_guessed(wired, monkeypatch) -> None:
    ir = document([page(1, ["Some fictional page without any recognisable heading"])])
    monkeypatch.setattr(stage, "_verified_ir", lambda *a: ir)
    result = stage.extract_clinical_document(None, PATIENT, DOC)  # type: ignore[arg-type]
    assert (result.outcome, result.skip_reason) == ("skipped", "unclassified")
    assert wired["extraction"] == []


@pytest.mark.parametrize(
    ("target", "kind"),
    [
        (_target(blocked="identity_mismatch"), "identity_mismatch"),
        (_target(blocked="not_classified"), "not_classified"),
        (_target(extracted=True), "already_extracted"),
    ],
)
def test_refusals_write_no_run(wired, monkeypatch, target, kind) -> None:
    monkeypatch.setattr(stage, "get_clinical_target", lambda p, d: target)
    with pytest.raises(DocumentProcessingError) as error:
        stage.extract_clinical_document(None, PATIENT, DOC)  # type: ignore[arg-type]
    assert error.value.kind == kind
    assert wired == {"extraction": [], "skipped": [], "failure": []}


def test_a_storage_failure_is_recorded_and_the_original_error_re_raised(wired, monkeypatch) -> None:
    def boom(*a, **kw):
        raise RuntimeError(f"boom {SECRET}")

    monkeypatch.setattr(stage, "record_clinical_extraction", boom)
    with pytest.raises(RuntimeError):
        stage.extract_clinical_document(None, PATIENT, DOC)  # type: ignore[arg-type]
    (failure,) = wired["failure"]
    assert (failure["error_kind"], failure["error_class"]) == ("extraction_failed", "RuntimeError")


def test_a_failure_that_cannot_be_recorded_still_raises_the_original(wired, monkeypatch) -> None:
    monkeypatch.setattr(stage, "_verified_ir", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    monkeypatch.setattr(
        stage, "record_clinical_failure", lambda *a, **kw: (_ for _ in ()).throw(OSError("db"))
    )
    with pytest.raises(KeyError):
        stage.extract_clinical_document(None, PATIENT, DOC)  # type: ignore[arg-type]


# --- review ------------------------------------------------------------------------------------


def _view(fact_type: str) -> ClinicalFactView:
    return ClinicalFactView(
        id=uuid.uuid4(), fact_type=fact_type, document_id=DOC, document_name="x.pdf",
        clinical_doc_type="prescription", document_date=None, page_number=1,
        source_locator="p1:l0:s1", source_excerpt="Tab. Fictomol 500 mg", char_start=0,
        char_end=5, subject="patient", negated=False, date=None, date_precision=None,
        date_raw=None, date_source=None, date_page_number=None, date_char_start=None,
        date_char_end=None, review_status="validated", review_reasons=(), quality_notes=(),
        values={},
    )  # fmt: skip


@pytest.fixture
def reviewing(monkeypatch):
    decisions: list[dict] = []
    view = {"fact": _view("medication")}
    monkeypatch.setattr(review, "get_clinical_fact", lambda p, f: view["fact"])
    monkeypatch.setattr(review, "get_clinical_target", lambda p, d: _target())
    monkeypatch.setattr(
        review, "record_review_decision", lambda p, f, **kw: decisions.append(kw) or "recorded"
    )
    return decisions, view


def test_a_medication_correction_is_normalized_like_extraction(reviewing) -> None:
    decisions, _ = reviewing
    review.review_clinical_fact(
        PATIENT,
        uuid.uuid4(),
        "correct",
        correction={"name": "Feverex", "strength": "250 mg", "frequency": "BD"},
    )
    (decision,) = decisions
    values = decision["corrected_values"]
    assert values["schema"] == "clinical-correction-1"
    assert values["normalized"] == {
        "name_norm": "fictomol",
        "name_match": "brand_alias",
        "strength_value": "250",
        "strength_unit": "mg",
        "frequency_per_day": 2,
        "as_needed": False,
    }


@pytest.mark.parametrize(
    ("fact_type", "correction"),
    [
        ("medication", {}),
        ("medication", {"name": "Combiflex"}),  # ambiguous: no generic chosen
        ("medication", {"strength": "a lot"}),
        ("medication", {"severity": "mild"}),  # not a medication field
        ("allergy", {"status": "maybe"}),
        ("allergy", {"severity": "terrible"}),
        ("event", {"date": "03/04/2024"}),  # ambiguous date
        ("event", {"date": "31/02/2024"}),
    ],
)
def test_corrections_that_cannot_be_normalized_are_refused(reviewing, fact_type, correction):
    decisions, view = reviewing
    view["fact"] = _view(fact_type)
    with pytest.raises(DocumentProcessingError) as error:
        review.review_clinical_fact(PATIENT, uuid.uuid4(), "correct", correction=correction)
    assert error.value.kind == "invalid_correction"
    assert decisions == []


def test_verify_and_reject_need_no_correction(reviewing) -> None:
    decisions, _ = reviewing
    review.review_clinical_fact(PATIENT, uuid.uuid4(), "verify")
    review.review_clinical_fact(PATIENT, uuid.uuid4(), "reject", note="  ")
    assert [d["decision"] for d in decisions] == ["verify", "reject"]
    assert decisions[1]["note"] is None
    with pytest.raises(ValueError):
        review.review_clinical_fact(PATIENT, uuid.uuid4(), "approve")
    with pytest.raises(ValueError):
        review.review_clinical_fact(PATIENT, uuid.uuid4(), "verify", correction={"name": "x"})


# --- dispatch ----------------------------------------------------------------------------------


def test_clinical_questions_never_reach_the_copilot(monkeypatch) -> None:
    monkeypatch.setattr(records, "list_clinical_facts", lambda p: ())
    asked: list[str] = []
    reply = records.ask_records(
        PATIENT,
        "What medications are documented in my records?",
        copilot=lambda p, q: asked.append(q),
        as_of=dt.date(2025, 1, 1),
    )
    assert reply.clinical is not None and reply.copilot is None and asked == []
    assert reply.clinical.status == "no_records"


@pytest.mark.parametrize(
    "question",
    ["Show my Hb results over time.", "Should I stop taking my tablets?", "What is HbA1c?"],
)
def test_everything_else_goes_to_the_unchanged_copilot(monkeypatch, question) -> None:
    monkeypatch.setattr(records, "list_clinical_facts", lambda p: pytest.fail("not read"))
    reply = records.ask_records(PATIENT, question, copilot=lambda p, q: ("copilot", q))
    assert reply.copilot == ("copilot", question) and reply.clinical is None


def test_corrected_values_are_applied_when_facts_are_read() -> None:
    view = _view("medication")
    from dataclasses import replace

    from app.records.clinical import ClinicalDecision

    corrected = replace(
        view,
        review_status="corrected",
        values={"name_norm": "fictomol", "strength_value": None},
        decisions=(
            ClinicalDecision(
                uuid.uuid4(),
                "correct",
                {"normalized": {"strength_value": "250", "strength_unit": "mg"}},
                dt.datetime(2026, 11, 18, tzinfo=dt.UTC),
            ),
        ),
    )
    fact = records.to_fact(corrected)
    assert fact.corrected and str(fact.values["strength_value"]) == "250"
