"""Unit tests for the document pipeline and the library wrappers (Task 2.9a, ADR-036 A4).

The five stage services are replaced by stand-ins over an in-memory model of each document; the
stand-in index stage applies the real indexing-gate rule (``app.records.chunks._gate_refusal``)
and the stand-in extract stage the real extraction gate (``extraction_gate_refusal``, Phase 3).
No database, parser or model is used. All data is fictional.
"""

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy.exc import OperationalError

from app.core.errors import (
    DocumentParseError,
    DocumentProcessingError,
    DocumentValidationError,
    PatientError,
)
from app.records.chunks import _gate_refusal
from app.records.facts import extraction_applies, extraction_gate_refusal
from app.records.repositories import DocumentSummary
from app.services import document_pipeline as pipe
from app.services import library
from app.services.check_identity import IdentityCheckResult
from app.services.classify_document import ClassifyResult
from app.services.extract_clinical import ClinicalExtractResult
from app.services.extract_document import ExtractResult
from app.services.index_document import IndexResult
from app.services.process_document import ProcessResult

PATIENT = uuid.uuid4()
OTHER_PATIENT = uuid.uuid4()
SECRET = "Asha Fictional 25/03/1985"  # must never reach a result or a log


def _err(kind: str) -> DocumentProcessingError:
    return DocumentProcessingError(f"refused {SECRET}", kind=kind)


@dataclass
class Doc:
    """One document's stored state, as the stage services would see it."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    patient_id: uuid.UUID = PATIENT
    created: int = 0
    pages: bool = False
    classified: bool = False  # a succeeded classify run exists
    identity_checked: bool = False  # a succeeded identity run exists
    identity_check: str = "unknown"  # the column (default "unknown")
    doc_type: str = "unknown"
    indexed: bool = False
    extracted: bool = False
    clinical_extracted: bool = False  # Phase 6: a completed clinical_extract run exists
    # what the next attempt of each stage does
    parse_error: BaseException | None = None
    classify_error: BaseException | None = None
    identity_error: BaseException | None = None
    index_error: BaseException | None = None
    extract_error: BaseException | None = None
    clinical_error: BaseException | None = None
    next_type: str = "lab_report"
    next_identity: str = "match"


class World:
    def __init__(self, *docs: Doc) -> None:
        self.docs = {doc.id: doc for doc in docs}
        self.calls: list[tuple[str, uuid.UUID]] = []

    def _doc(self, patient_id: uuid.UUID, document_id: uuid.UUID) -> Doc:
        doc = self.docs.get(document_id)
        if doc is None or doc.patient_id != patient_id:
            raise _err("document_not_found")
        return doc

    # stage services ---------------------------------------------------------------------------
    def process(self, _settings, _parser, patient_id, document_id, **_kw) -> ProcessResult:
        self.calls.append((pipe.PARSE, document_id))
        doc = self._doc(patient_id, document_id)
        if doc.pages:
            raise _err("already_parsed")
        if doc.parse_error is not None:
            raise doc.parse_error
        doc.pages = True
        return ProcessResult(document_id, uuid.uuid4(), uuid.uuid4(), False, 2)

    def classify(self, patient_id, document_id) -> ClassifyResult:
        self.calls.append((pipe.CLASSIFY, document_id))
        doc = self._doc(patient_id, document_id)
        if not doc.pages:
            raise _err("not_parsed")
        if doc.classified:
            raise _err("already_classified")
        if doc.classify_error is not None:
            raise doc.classify_error
        doc.classified, doc.doc_type = True, doc.next_type
        return ClassifyResult(document_id, uuid.uuid4(), doc.doc_type, 0.8, ("lab.header",), "2", 3)

    def identity(self, patient_id, document_id) -> IdentityCheckResult:
        self.calls.append((pipe.IDENTITY, document_id))
        doc = self._doc(patient_id, document_id)
        if not doc.pages:
            raise _err("not_parsed")
        if doc.identity_checked:
            raise _err("already_checked")
        if doc.identity_error is not None:
            raise doc.identity_error
        doc.identity_checked, doc.identity_check = True, doc.next_identity
        status = "needs_review" if doc.identity_check == "mismatch" else "processing"
        return IdentityCheckResult(
            document_id, uuid.uuid4(), doc.identity_check, ("name_match",), "2", status, 4
        )

    def index(self, _settings, _embedder, patient_id, document_id) -> IndexResult:
        self.calls.append((pipe.INDEX, document_id))
        doc = self._doc(patient_id, document_id)
        if not doc.pages:
            raise _err("not_parsed")
        if doc.indexed:
            raise _err("already_indexed")
        refusal = _gate_refusal(doc.classified, doc.identity_checked, doc.identity_check)
        if refusal is not None:
            raise _err(refusal)
        if doc.index_error is not None:
            raise doc.index_error
        doc.indexed = True
        return IndexResult(document_id, 5, 4, 1, 6)

    def extract(self, _settings, patient_id, document_id) -> ExtractResult:
        self.calls.append((pipe.EXTRACT, document_id))
        doc = self._doc(patient_id, document_id)
        refusal = extraction_gate_refusal(
            has_pages=doc.pages,
            classified=doc.classified,
            identity_checked=doc.identity_checked,
            identity_check=doc.identity_check,
        )
        if refusal is not None:
            raise _err(refusal)
        if doc.extracted:
            raise _err("already_extracted")
        if doc.extract_error is not None:
            raise doc.extract_error
        doc.extracted = True
        outcome = "extracted" if extraction_applies(doc.doc_type) else "not_applicable"
        facts = 3 if outcome == "extracted" else 0
        return ExtractResult(
            document_id, outcome, uuid.uuid4(), facts, 0, {}, 0, 0, 0, doc.indexed, 7
        )

    def clinical(self, _settings, patient_id, document_id) -> ClinicalExtractResult:
        self.calls.append((pipe.CLINICAL_EXTRACT, document_id))
        doc = self._doc(patient_id, document_id)
        refusal = extraction_gate_refusal(
            has_pages=doc.pages,
            classified=doc.classified,
            identity_checked=doc.identity_checked,
            identity_check=doc.identity_check,
        )
        if refusal is not None:
            raise _err(refusal)
        if doc.clinical_extracted:
            raise _err("already_extracted")
        if doc.clinical_error is not None:
            raise doc.clinical_error
        doc.clinical_extracted = True
        lab = doc.doc_type == "lab_report"
        return ClinicalExtractResult(
            document_id,
            "skipped" if lab else "extracted",
            uuid.uuid4(),
            None if lab else "prescription",
            "lab_report" if lab else None,
            0 if lab else 2,
            0,
            {},
            5,
        )

    # library ----------------------------------------------------------------------------------
    def get_document(self, patient_id, document_id) -> DocumentSummary:
        return _summary(self._doc(patient_id, document_id))

    def list_documents(self, patient_id) -> tuple[DocumentSummary, ...]:
        if patient_id != PATIENT:
            raise PatientError(f"Patient {patient_id} does not exist", kind="patient_not_found")
        mine = [d for d in self.docs.values() if d.patient_id == patient_id]
        return tuple(_summary(d) for d in sorted(mine, key=lambda d: -d.created))  # newest first


def _summary(doc: Doc) -> DocumentSummary:
    when = dt.datetime(2026, 11, 17, tzinfo=dt.UTC) + dt.timedelta(minutes=doc.created)
    return DocumentSummary(
        doc.id, doc.patient_id, "report.pdf", 2, "processing", doc.doc_type, None, None,
        when, when, None, doc.identity_check,
    )  # fmt: skip


@pytest.fixture
def world(monkeypatch):
    def install(*docs: Doc) -> World:
        w = World(*docs)
        monkeypatch.setattr(pipe, "process_document", w.process)
        monkeypatch.setattr(pipe, "classify_document", w.classify)
        monkeypatch.setattr(pipe, "check_document_identity", w.identity)
        monkeypatch.setattr(pipe, "index_document", w.index)
        monkeypatch.setattr(pipe, "extract_document", w.extract)
        monkeypatch.setattr(pipe, "extract_clinical_document", w.clinical)
        monkeypatch.setattr(pipe, "get_document", w.get_document)
        monkeypatch.setattr(pipe, "list_documents", w.list_documents)
        return w

    return install


def _run(doc: Doc) -> pipe.DocumentPipelineResult:
    return pipe.run_document_pipeline(None, None, None, doc.patient_id, doc.id)  # type: ignore[arg-type]


def _statuses(result: pipe.DocumentPipelineResult) -> list[tuple[str, str, str | None]]:
    return [(s.stage, s.status, s.reason) for s in result.stages]


# --- progression and idempotency -----------------------------------------------------------------


def test_a_new_document_goes_through_every_stage_in_order(world) -> None:
    doc = Doc()
    w = world(doc)
    result = _run(doc)
    assert _statuses(result) == [
        ("parse", "succeeded", None),
        ("classify", "succeeded", None),
        ("identity", "succeeded", None),
        ("index", "succeeded", None),
        ("extract", "succeeded", None),
        ("clinical_extract", "succeeded", None),
    ]
    assert [stage for stage, _ in w.calls] == list(pipe.STAGES)
    assert isinstance(result.stage("classify").detail, ClassifyResult)
    assert result.stage("identity").detail.outcome == "match"  # type: ignore[union-attr]
    assert result.stage("index").detail.chunks == 5  # type: ignore[union-attr]
    assert result.indexed and result.document_id == doc.id
    assert result.extracted and result.stage("extract").detail.outcome == "extracted"  # type: ignore[union-attr]


def test_completed_stages_are_reported_already_done_and_not_repeated(world) -> None:
    doc = Doc()
    world(doc)
    _run(doc)
    again = _run(doc)
    assert _statuses(again) == [
        ("parse", "already_done", "already_parsed"),
        ("classify", "already_done", "already_classified"),
        ("identity", "already_done", "already_checked"),
        ("index", "already_done", "already_indexed"),
        ("extract", "already_done", "already_extracted"),
        ("clinical_extract", "already_done", "already_extracted"),
    ]
    assert again.indexed and all(s.detail is None for s in again.stages)


# --- independence of classification and identity ------------------------------------------------


def test_a_classification_failure_does_not_stop_identity_and_blocks_indexing(world) -> None:
    doc = Doc(classify_error=RuntimeError(SECRET))
    w = world(doc)
    result = _run(doc)
    assert _statuses(result) == [
        ("parse", "succeeded", None),
        ("classify", "failed", "unexpected"),
        ("identity", "succeeded", None),
        ("index", "blocked", "not_classified"),
        ("extract", "blocked", "not_classified"),
        ("clinical_extract", "blocked", "not_classified"),
    ]
    assert result.stage("classify").error_class == "RuntimeError"
    assert (pipe.IDENTITY, doc.id) in w.calls and not result.indexed


def test_an_identity_failure_does_not_stop_classification_and_blocks_indexing(world) -> None:
    doc = Doc(identity_error=OperationalError("SELECT", {}, Exception(SECRET)))
    world(doc)
    result = _run(doc)
    assert _statuses(result) == [
        ("parse", "succeeded", None),
        ("classify", "succeeded", None),
        ("identity", "failed", "database_error"),
        ("index", "blocked", "identity_not_checked"),
        ("extract", "blocked", "identity_not_checked"),
        ("clinical_extract", "blocked", "identity_not_checked"),
    ]


def test_classification_still_runs_after_an_earlier_identity_failure(world) -> None:
    """Rerun order does not matter: a failed identity stage never gates classification."""
    doc = Doc(pages=True, identity_error=RuntimeError("x"), classify_error=RuntimeError("y"))
    w = world(doc)
    _run(doc)  # both fail
    doc.classify_error = None  # only classification can succeed on the explicit rerun
    result = _run(doc)
    assert result.stage("classify").status == "succeeded"
    assert result.stage("identity").status == "failed"
    assert result.stage("index").reason == "identity_not_checked"
    assert [s for s, d in w.calls].count(pipe.CLASSIFY) == 2


def test_both_failures_block_indexing_with_the_gate_precedence(world) -> None:
    doc = Doc(classify_error=RuntimeError("x"), identity_error=RuntimeError("y"))
    world(doc)
    result = _run(doc)
    assert result.stage("classify").status == result.stage("identity").status == "failed"
    assert result.stage("index").status == "blocked"
    assert result.stage("index").reason == "identity_not_checked"  # the gate's own precedence


# --- the indexing gate ---------------------------------------------------------------------------


def test_an_identity_mismatch_blocks_indexing(world) -> None:
    doc = Doc(next_identity="mismatch")
    world(doc)
    result = _run(doc)
    identity = result.stage("identity")
    assert identity.status == "succeeded" and identity.detail.outcome == "mismatch"  # type: ignore[union-attr]
    assert identity.detail.processing_status == "needs_review"  # type: ignore[union-attr]
    assert (result.stage("index").status, result.stage("index").reason) == (
        "blocked",
        "identity_mismatch",
    )
    assert not result.indexed


def test_recorded_unknown_type_and_unknown_identity_are_indexed(world) -> None:
    doc = Doc(next_type="unknown", next_identity="unknown")
    world(doc)
    result = _run(doc)
    assert result.stage("classify").detail.doc_type == "unknown"  # type: ignore[union-attr]
    assert result.stage("identity").detail.outcome == "unknown"  # type: ignore[union-attr]
    assert result.indexed


@pytest.mark.parametrize(
    ("doc", "reason"),
    [
        # the column defaults ("unknown") without a succeeded run are never accepted
        (Doc(pages=True, classified=True, identity_check="unknown"), "identity_not_checked"),
        (Doc(pages=True, identity_checked=True, doc_type="unknown"), "not_classified"),
    ],
)
def test_unknown_column_defaults_without_a_succeeded_run_are_blocked(world, doc, reason) -> None:
    doc.classify_error = RuntimeError("x") if not doc.classified else None
    doc.identity_error = RuntimeError("y") if not doc.identity_checked else None
    world(doc)
    result = _run(doc)
    assert (result.stage("index").status, result.stage("index").reason) == ("blocked", reason)


def test_an_index_failure_after_the_gate_is_reported_as_failed(world) -> None:
    doc = Doc(index_error=DocumentProcessingError("x", kind="integrity_mismatch"))
    world(doc)
    index = _run(doc).stage("index")
    assert (index.status, index.reason, index.error_class) == (
        "failed",
        "integrity_mismatch",
        "DocumentProcessingError",
    )


# --- parse failures ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "reason"),
    [
        (DocumentParseError(f"timeout {SECRET}", kind="timeout"), "failed", "timeout"),
        (DocumentValidationError(f"bad {SECRET}", reason="encrypted"), "failed", "encrypted"),
        (_err("already_running"), "blocked", "already_running"),
    ],
)
def test_without_pages_every_later_stage_is_blocked_not_skipped(
    world, error, status, reason
) -> None:
    doc = Doc(parse_error=error)
    w = world(doc)
    result = _run(doc)
    assert (result.stage("parse").status, result.stage("parse").reason) == (status, reason)
    for stage in (pipe.CLASSIFY, pipe.IDENTITY, pipe.INDEX, pipe.EXTRACT):
        assert (result.stage(stage).status, result.stage(stage).reason) == ("blocked", "not_parsed")
    assert [stage for stage, _ in w.calls] == list(pipe.STAGES)  # every service was asked


def test_a_failed_parse_is_attempted_again_only_when_the_caller_reruns(world) -> None:
    doc = Doc(parse_error=DocumentParseError("x", kind="timeout"))
    w = world(doc)
    _run(doc)
    assert [stage for stage, _ in w.calls].count(pipe.PARSE) == 1  # no automatic retry
    doc.parse_error = None
    assert _run(doc).indexed


# --- patient runs --------------------------------------------------------------------------------


def test_a_patient_run_reports_every_document_in_order_and_continues(world) -> None:
    first, broken, last = (
        Doc(created=1),
        Doc(created=2, parse_error=RuntimeError(SECRET)),
        Doc(created=3, next_identity="mismatch"),
    )
    other = Doc(patient_id=OTHER_PATIENT)
    w = world(first, broken, last, other)
    results = pipe.run_patient_pipeline(None, None, None, PATIENT)  # type: ignore[arg-type]
    assert [r.document_id for r in results] == [first.id, broken.id, last.id]  # oldest first
    assert all(len(r.stages) == len(pipe.STAGES) == 6 for r in results)
    assert results[0].indexed
    assert results[1].stage("parse").status == "failed" and not results[1].indexed
    assert results[2].stage("index").reason == "identity_mismatch"
    assert results[2].stage("extract").reason == "identity_mismatch"  # no facts for a mismatch
    assert all(document_id != other.id for _, document_id in w.calls)  # never another patient


def test_a_patient_without_documents_gets_an_empty_result(world) -> None:
    world()
    assert pipe.run_patient_pipeline(None, None, None, PATIENT) == ()  # type: ignore[arg-type]


# --- scoping and errors --------------------------------------------------------------------------


def test_another_patients_document_is_refused_before_any_stage(world) -> None:
    doc = Doc(patient_id=OTHER_PATIENT)
    w = world(doc)
    with pytest.raises(DocumentProcessingError) as caught:
        pipe.run_document_pipeline(None, None, None, PATIENT, doc.id)  # type: ignore[arg-type]
    assert caught.value.kind == "document_not_found" and w.calls == []


def test_an_unknown_patient_is_refused_before_any_stage(world) -> None:
    w = world(Doc())
    with pytest.raises(PatientError) as caught:
        pipe.run_patient_pipeline(None, None, None, uuid.uuid4())  # type: ignore[arg-type]
    assert caught.value.kind == "patient_not_found" and w.calls == []


def test_an_interrupt_is_never_turned_into_a_result(world) -> None:
    doc = Doc(classify_error=KeyboardInterrupt())
    world(doc)
    with pytest.raises(KeyboardInterrupt):
        _run(doc)


def test_results_and_logs_hold_no_messages_or_personal_data(world, caplog) -> None:
    doc = Doc(parse_error=DocumentParseError(f"failed {SECRET}", kind="parser_failed"))
    world(doc)
    with caplog.at_level(logging.DEBUG, logger="app.services.document_pipeline"):
        result = _run(doc)
    assert SECRET not in repr(result)
    assert caplog.records and all(SECRET not in r.getMessage() for r in caplog.records)
    assert "stage=parse status=failed reason=parser_failed" in caplog.text


def test_progress_is_reported_after_each_stage(world) -> None:
    doc = Doc()
    world(doc)
    seen: list[tuple[uuid.UUID, str, str]] = []
    pipe.run_document_pipeline(
        None, None, None, PATIENT, doc.id,  # type: ignore[arg-type]
        on_stage=lambda document_id, stage: seen.append((document_id, stage.stage, stage.status)),
    )  # fmt: skip
    assert seen == [(doc.id, stage, "succeeded") for stage in pipe.STAGES]


# --- library wrappers ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wrapper", "target", "args"),
    [
        ("list_patients", "_list_patients", ()),
        ("get_patient", "_get_patient", (PATIENT,)),
        ("delete_patient", "_delete_patient", (PATIENT,)),
        ("list_documents", "_list_documents", (PATIENT,)),
        ("get_document", "_get_document", (PATIENT, uuid.UUID(int=1))),
        ("rename_document", "_rename_document", (PATIENT, uuid.UUID(int=1), "new.pdf")),
        ("delete_document", "_delete_document", (PATIENT, uuid.UUID(int=1))),
    ],
)
def test_library_wrappers_pass_the_patient_scope_through_unchanged(
    monkeypatch, wrapper, target, args
) -> None:
    calls = []
    monkeypatch.setattr(library, target, lambda *a, **kw: calls.append((a, kw)) or "result")
    returned = getattr(library, wrapper)(*args)
    assert [c[0] for c in calls] == [args]
    # delete_document also hands the conflict detector to the repository (Phase 3, §6.7)
    assert set(calls[0][1]) == ({"conflict_detector"} if wrapper == "delete_document" else set())
    assert returned == ("result" if wrapper != "delete_patient" else None)


def test_library_create_and_update_patient(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(library, "_create_patient", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(library, "_update_patient", lambda *a, **kw: calls.append((a, kw)))
    library.create_patient("Asha Fictional", is_synthetic=True)
    library.update_patient(PATIENT, date_of_birth=None)  # clear the DOB, keep the name
    library.update_patient(PATIENT)  # nothing to change: the repository decides
    assert calls == [
        (("Asha Fictional",), {"is_synthetic": True, "date_of_birth": None}),
        ((PATIENT,), {"date_of_birth": None}),
        ((PATIENT,), {}),
    ]


def test_library_errors_propagate_unchanged(monkeypatch) -> None:
    error = _err("document_not_found")

    def refuse(*_a, **_kw):
        raise error

    monkeypatch.setattr(library, "_get_document", refuse)
    with pytest.raises(DocumentProcessingError) as caught:
        library.get_document(PATIENT, uuid.uuid4())
    assert caught.value is error


# --- extraction stage (Phase 3, docs/PHASE_3_PLAN.md §9) ---------------------------------------


def test_extraction_is_independent_of_an_index_failure(world) -> None:
    doc = Doc(index_error=DocumentProcessingError("x", kind="integrity_mismatch"))
    world(doc)
    result = _run(doc)
    assert result.stage("index").status == "failed"
    assert result.stage("extract").status == "succeeded" and result.extracted


def test_identity_mismatch_blocks_extraction(world) -> None:
    doc = Doc(next_identity="mismatch")
    world(doc)
    extract = _run(doc).stage("extract")
    assert (extract.status, extract.reason) == ("blocked", "identity_mismatch")


def test_unknown_identity_is_extracted(world) -> None:
    doc = Doc(next_identity="unknown")
    world(doc)
    assert _run(doc).stage("extract").status == "succeeded"


@pytest.mark.parametrize("doc_type", ["other", "unknown"])
def test_other_document_types_are_not_applicable_but_complete(world, doc_type) -> None:
    doc = Doc(next_type=doc_type)
    world(doc)
    first = _run(doc).stage("extract")
    assert first.status == "succeeded" and first.reason is None
    assert first.detail.outcome == "not_applicable" and first.detail.facts == 0  # type: ignore[union-attr]
    again = _run(doc).stage("extract")
    assert (again.status, again.reason) == ("already_done", "already_extracted")


def test_an_extraction_failure_is_reported_and_retried_only_on_rerun(world) -> None:
    doc = Doc(extract_error=DocumentProcessingError(SECRET, kind="reference_data_not_seeded"))
    w = world(doc)
    extract = _run(doc).stage("extract")
    assert (extract.status, extract.reason) == ("failed", "reference_data_not_seeded")
    assert SECRET not in repr(extract)
    doc.extract_error = None
    assert _run(doc).extracted
    assert [s for s, _ in w.calls].count(pipe.EXTRACT) == 2
