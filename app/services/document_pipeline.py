"""Run a document's processing stages in order and report every stage (Task 2.9a, ADR-036 A4).

    run_document_pipeline   one document of a patient
    run_patient_pipeline    every document of a patient, oldest first; one result per document

Upload is separate (``app.services.upload_document``). The stages run synchronously, in
dependency order, each through its existing service; nothing here decides a stage's outcome:

    1. parse     ``process_document``         (claim, parse or reuse outputs, store pages)
    2. classify  ``classify_document``        (reads the stored pages; independent of identity)
    3. identity  ``check_document_identity``  (reads the stored pages; independent of classify)
    4. index     ``index_document``           (the centralized gate, then chunk, embed, store)
    5. extract   ``extract_document``         (Phase 3: its own gate; independent of index)

**Every stage is attempted once per call, and each service decides for itself.** A stage whose
input is missing is refused by its own service, not skipped here: without pages, classify and
identity refuse with ``not_parsed``; the index stage is refused by the single indexing gate
(``not_parsed``, ``not_classified``, ``identity_not_checked``, ``identity_mismatch``), which also
re-checks under the document row lock in the insert transaction. So a classification failure
never stops identity (and vice versa), either failure blocks indexing, and an ``unknown`` type or
identity is indexed only when its succeeded run recorded it (ADR-036 A1). The pipeline adds no
gate, no SQL and no status change: ``processing_status`` changes only where the stage services
already change it (parse; identity ``mismatch`` → ``needs_review``), and a parsed or indexed
document stays ``processing`` until every stage is complete: the shared completion rule
(``complete_document_if_ready``, called by the index and extract services) sets ``processed``
(docs/PHASE_3_PLAN.md §9.4).

Extraction (Phase 3) has its own gate with the indexing gate's precedence; an index failure never
blocks it. A document that is not a lab report gets a ``skipped`` extract run and the stage
reports ``succeeded`` with ``detail.outcome == "not_applicable"`` (§9.2).

Each stage reports exactly one status (A4):
- ``succeeded``    the service ran and stored its result (``detail`` holds it);
- ``already_done`` a succeeded run already exists; the stage is not repeated (A2);
- ``blocked``      the service refused because an earlier stage is missing or the gate refused
  (``reason``: ``not_parsed``, a gate refusal, or ``already_running`` for a parse claimed by
  another worker);
- ``failed``       anything else (``reason``: the error's fixed kind; ``error_class``: its class
  name). The stage services have already recorded their failed runs; the error is reported here,
  not raised, so the remaining stages and documents still run.

No retries: a call attempts each stage at most once. A stage that failed earlier is attempted
again only when the caller runs the pipeline again (ADR-036 A2), never automatically. Stale parse
claims are not recovered here. ``KeyboardInterrupt`` and ``SystemExit`` are not caught.

Errors raised (not reported): ``document_not_found`` for a document that is not this patient's
(``run_document_pipeline``) and ``patient_not_found`` (``run_patient_pipeline``), before any stage.

Logs and results carry identifiers, stage names, statuses, fixed kinds, class names and
durations only: never page text, names, dates of birth, filenames or exception messages.
"""

import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.core.config import Settings
from app.core.errors import MediMindError
from app.records.chunks import ALREADY_INDEXED, GATE_REFUSALS, NOT_PARSED
from app.records.db import is_database_error
from app.records.facts import ALREADY_EXTRACTED, EXTRACT_GATE_REFUSALS
from app.records.repositories import ALREADY_CHECKED, ALREADY_CLASSIFIED, ProcessingErrorKind
from app.retrieval.models import Embedder
from app.services.check_identity import IdentityCheckResult, check_document_identity
from app.services.classify_document import ClassifyResult, classify_document
from app.services.extract_document import ExtractResult, extract_document
from app.services.index_document import IndexResult, index_document
from app.services.library import get_document, list_documents
from app.services.process_document import Parser, ProcessResult, process_document

logger = logging.getLogger(__name__)

PARSE: Final = "parse"
CLASSIFY: Final = "classify"
IDENTITY: Final = "identity"
INDEX: Final = "index"
EXTRACT: Final = "extract"
STAGES: Final = (PARSE, CLASSIFY, IDENTITY, INDEX, EXTRACT)

SUCCEEDED: Final = "succeeded"
ALREADY_DONE: Final = "already_done"
BLOCKED: Final = "blocked"
FAILED: Final = "failed"

DATABASE_ERROR: Final = "database_error"
UNEXPECTED: Final = "unexpected"

# Per stage: the service's "a succeeded run exists" kind, and the kinds that mean "refused".
_ALREADY_DONE_KIND: Final = {
    PARSE: ProcessingErrorKind.ALREADY_PARSED.value,
    CLASSIFY: ALREADY_CLASSIFIED,
    IDENTITY: ALREADY_CHECKED,
    INDEX: ALREADY_INDEXED,
    EXTRACT: ALREADY_EXTRACTED,
}
_BLOCKED_KINDS: Final = {
    PARSE: frozenset({ProcessingErrorKind.ALREADY_RUNNING.value}),
    CLASSIFY: frozenset({NOT_PARSED}),
    IDENTITY: frozenset({NOT_PARSED}),
    INDEX: frozenset({NOT_PARSED}) | GATE_REFUSALS,
    EXTRACT: frozenset({NOT_PARSED}) | EXTRACT_GATE_REFUSALS,
}
_SAFE_KIND = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SAFE_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")

StageDetail = ProcessResult | ClassifyResult | IdentityCheckResult | IndexResult | ExtractResult


@dataclass(frozen=True)
class StageResult:
    """One stage of one document. Identifiers, fixed kinds and numbers only."""

    stage: str  # one of STAGES
    status: str  # succeeded / already_done / blocked / failed
    reason: str | None = None  # the fixed kind for already_done, blocked and failed
    error_class: str | None = None  # failed only: the exception class name, never its message
    detail: StageDetail | None = None  # succeeded only: the stage service's result
    duration_ms: int = 0


@dataclass(frozen=True)
class DocumentPipelineResult:
    """Every stage of one document, always all five, in ``STAGES`` order."""

    document_id: uuid.UUID
    stages: tuple[StageResult, ...]

    def stage(self, name: str) -> StageResult:
        for result in self.stages:
            if result.stage == name:
                return result
        raise KeyError(name)

    @property
    def indexed(self) -> bool:
        """The document is in the evidence index (indexed now or earlier)."""
        return self.stage(INDEX).status in (SUCCEEDED, ALREADY_DONE)

    @property
    def extracted(self) -> bool:
        """The extract stage is complete (facts stored, or not applicable to this type)."""
        return self.stage(EXTRACT).status in (SUCCEEDED, ALREADY_DONE)


StageCallback = Callable[[uuid.UUID, StageResult], None]


def run_document_pipeline(
    settings: Settings,
    parser: Parser,
    embedder: Embedder,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    on_stage: StageCallback | None = None,
) -> DocumentPipelineResult:
    """Run parse → classify → identity → index → extract for one document of this patient.

    ``on_stage`` (optional) is called after each stage, for progress display; an exception it
    raises propagates.

    Raises:
        DocumentProcessingError: ``document_not_found`` (also another patient's document), before
            any stage runs. Stage errors are reported in the result, not raised.
    """
    get_document(patient_id, document_id)  # scoping: never run stages on another's document
    return _run_stages(settings, parser, embedder, patient_id, document_id, on_stage)


def run_patient_pipeline(
    settings: Settings,
    parser: Parser,
    embedder: Embedder,
    patient_id: uuid.UUID,
    *,
    on_stage: StageCallback | None = None,
) -> tuple[DocumentPipelineResult, ...]:
    """Run every stage for each of this patient's documents, oldest first.

    One result per listed document, in order: a document whose stages fail or are blocked is
    reported and the next document still runs; none is left out. Completed documents report
    ``already_done`` stages.

    Raises:
        PatientError: ``patient_not_found``, before any stage runs.
    """
    documents = tuple(reversed(list_documents(patient_id)))  # listed newest first
    results = tuple(
        _run_stages(settings, parser, embedder, patient_id, document.id, on_stage)
        for document in documents
    )
    logger.info(
        "Patient pipeline: patient_id=%s documents=%d indexed=%d with_failed_stage=%d",
        patient_id,
        len(results),
        sum(result.indexed for result in results),
        sum(any(s.status == FAILED for s in result.stages) for result in results),
    )
    return results


def _run_stages(
    settings: Settings,
    parser: Parser,
    embedder: Embedder,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    on_stage: StageCallback | None,
) -> DocumentPipelineResult:
    calls: tuple[tuple[str, Callable[[], StageDetail]], ...] = (
        (PARSE, lambda: process_document(settings, parser, patient_id, document_id)),
        (CLASSIFY, lambda: classify_document(patient_id, document_id)),
        (IDENTITY, lambda: check_document_identity(patient_id, document_id)),
        (INDEX, lambda: index_document(settings, embedder, patient_id, document_id)),
        (EXTRACT, lambda: extract_document(settings, patient_id, document_id)),
    )
    results = []
    for stage, call in calls:
        result = _run_stage(stage, call)
        logger.info(
            "Pipeline stage: document_id=%s stage=%s status=%s reason=%s error_class=%s "
            "duration_ms=%d",
            document_id,
            stage,
            result.status,
            result.reason,
            result.error_class,
            result.duration_ms,
        )
        results.append(result)
        if on_stage is not None:
            on_stage(document_id, result)
    return DocumentPipelineResult(document_id, tuple(results))


def _run_stage(stage: str, call: Callable[[], StageDetail]) -> StageResult:
    started = time.perf_counter()
    try:
        detail = call()
    except Exception as exc:  # reported, not raised; BaseException-only signals propagate
        elapsed = _elapsed_ms(started)
        kind = error_kind(exc)
        if kind == _ALREADY_DONE_KIND[stage]:
            return StageResult(stage, ALREADY_DONE, reason=kind, duration_ms=elapsed)
        if kind in _BLOCKED_KINDS[stage]:
            return StageResult(stage, BLOCKED, reason=kind, duration_ms=elapsed)
        return StageResult(
            stage, FAILED, reason=kind, error_class=_class_name(exc), duration_ms=elapsed
        )
    return StageResult(stage, SUCCEEDED, detail=detail, duration_ms=_elapsed_ms(started))


def error_kind(exc: BaseException) -> str:
    """The error's fixed kind: an application error's own kind, else a fixed category.

    Public so the UI can show a failure's kind without its message (Task 2.9b).
    """
    if isinstance(exc, MediMindError):
        # ``kind`` on most application errors; ``reason`` on DocumentValidationError
        kind = str(getattr(exc, "kind", None) or getattr(exc, "reason", ""))
        if _SAFE_KIND.fullmatch(kind):
            return kind
    if is_database_error(exc):
        return DATABASE_ERROR
    return UNEXPECTED


def _class_name(exc: Exception) -> str | None:
    name = type(exc).__name__
    return name if _SAFE_CLASS.fullmatch(name) else None


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
