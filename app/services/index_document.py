"""Index one parsed document for evidence retrieval (Milestone R1, ADR-035).

Steps, each its own unit; no database transaction is open while files are read or models run:

    1. get_index_target          document hash, page count, page and chunk row counts (scoped),
                                 and the indexing gate (ADR-036 A1), checked before any work
    2. read_published_manifest   the parse outputs' manifest, pre-checked (layout, document, source)
       parse_run_belongs_to_document   its producing run is a parse run of this document
       load_verified_parse_outputs     IR and Docling JSON verified against the manifest hashes
    3. chunk_document            pure, deterministic; text spans and table rows (CHUNKER_VERSION)
    4. embedder.embed_passages   CPU, batched; 384-dimension normalized vectors
    5. insert_document_chunks    one transaction: lock, re-check (gate included), insert chunks +
                                 index run

Indexing gate (Task 2.8, ADR-036 A1): a succeeded ``classify`` run, a succeeded ``identity`` run,
and no identity ``mismatch``. Every indexing path (``python -m app index``, ``index_patient``,
the R1.8 runner) calls ``index_document``, and ``index_document`` is the only caller of
``insert_document_chunks``, which re-checks the gate under the row lock. ``index_patient``
returns one explicit result per document: indexed, or blocked with the gate's reason.

Nothing here writes or deletes a file. After a successful insert, the shared completion rule
(``complete_document_if_ready``, Phase 3 §9.4) sets ``processed`` once every stage, extraction
included, is complete; indexing itself never decides the status.
Logs carry identifiers, counts and durations only.
"""

import logging
import time
import uuid
from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import DocumentProcessingError
from app.ingestion.parse_outputs import load_verified_parse_outputs, read_published_manifest
from app.records.chunks import (
    ALREADY_INDEXED,
    GATE_REFUSALS,
    NOT_PARSED,
    ChunkRecord,
    get_index_target,
    insert_document_chunks,
    list_unindexed_documents,
)
from app.records.facts import complete_document_if_ready
from app.records.repositories import ProcessingErrorKind, parse_run_belongs_to_document
from app.retrieval.chunking import CHUNKER_VERSION, chunk_document
from app.retrieval.models import Embedder, check_embedding

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexResult:
    document_id: uuid.UUID
    chunks: int
    text_chunks: int
    table_chunks: int
    duration_ms: int


INDEXED = "indexed"
BLOCKED = "blocked"
ALREADY_DONE = "already_done"


@dataclass(frozen=True)
class IndexOutcome:
    """One document of a bulk run: ``indexed`` (with its result), ``blocked`` (with the gate's
    reason) or ``already_done`` (another indexer finished it first)."""

    document_id: uuid.UUID
    status: str
    reason: str | None = None
    result: IndexResult | None = None


def _mismatch(document_id: uuid.UUID, message: str) -> DocumentProcessingError:
    return DocumentProcessingError(
        f"Document {document_id}: {message}", kind=ProcessingErrorKind.INTEGRITY_MISMATCH
    )


def index_document(
    settings: Settings, embedder: Embedder, patient_id: uuid.UUID, document_id: uuid.UUID
) -> IndexResult:
    """Chunk, embed and store one parsed document of this patient.

    Raises:
        DocumentProcessingError: ``document_not_found``, ``not_parsed``, ``already_indexed``,
            ``identity_mismatch``, ``identity_not_checked``, ``not_classified`` (the gate),
            ``integrity_mismatch`` (missing or unverifiable parse outputs), ``text_not_storable``.
        DocumentStorageError: unreadable or unsafe parse output files.
        RetrievalError: the embedding model is not available or returned invalid vectors.
    """
    started = time.perf_counter()
    target = get_index_target(patient_id, document_id)
    if target.page_rows == 0:
        raise DocumentProcessingError(
            f"Document {document_id} has no parsed pages", kind=NOT_PARSED
        )
    if target.chunk_count:
        raise DocumentProcessingError(
            f"Document {document_id} is already indexed", kind=ALREADY_INDEXED
        )
    if target.blocked_reason is not None:  # before any file read, chunking or embedding
        raise DocumentProcessingError(
            f"Document {document_id} may not be indexed ({target.blocked_reason})",
            kind=target.blocked_reason,
        )
    if target.page_count is None:
        raise _mismatch(document_id, "no recorded page count")
    manifest = read_published_manifest(
        settings, patient_id=patient_id, document_id=document_id, source_sha256=target.sha256
    )
    if manifest is None:
        raise _mismatch(document_id, "no published parse outputs")
    if not parse_run_belongs_to_document(patient_id, document_id, manifest.parse_run_id):
        raise _mismatch(document_id, "the parse outputs name a run of another document")
    outputs = load_verified_parse_outputs(
        settings,
        patient_id=patient_id,
        document_id=document_id,
        parse_run_id=manifest.parse_run_id,
        source_sha256=target.sha256,
        expected_page_count=target.page_count,
    )
    if outputs is None:
        raise _mismatch(document_id, "the published parse outputs are missing")

    drafts = chunk_document(outputs.document)
    text_count = sum(1 for d in drafts if d.chunk_type == "text")
    if not drafts:
        logger.info("Nothing to index: document_id=%s (no text or tables)", document_id)
        return IndexResult(document_id, 0, 0, 0, round((time.perf_counter() - started) * 1000))
    vectors = embedder.embed_passages([draft.content for draft in drafts])
    if len(vectors) != len(drafts):
        raise _mismatch(document_id, "the embedder returned a different number of vectors")
    records = [
        ChunkRecord(
            page_number=draft.page_number,
            chunk_type=draft.chunk_type,
            section=draft.section,
            content=draft.content,
            embedding=check_embedding(vector),
        )
        for draft, vector in zip(drafts, vectors, strict=True)
    ]
    duration_ms = round((time.perf_counter() - started) * 1000)
    insert_document_chunks(
        patient_id,
        document_id,
        records,
        embedding_model=embedder.model_name,
        component_versions={
            "chunker_version": CHUNKER_VERSION,
            "embedding_model": embedder.model_name,
            "producing_parse_run_id": str(manifest.parse_run_id),
        },
        duration_ms=duration_ms,
    )
    complete_document_if_ready(patient_id, document_id)
    return IndexResult(
        document_id=document_id,
        chunks=len(records),
        text_chunks=text_count,
        table_chunks=len(records) - text_count,
        duration_ms=duration_ms,
    )


def index_patient(
    settings: Settings, embedder: Embedder, patient_id: uuid.UUID
) -> tuple[IndexOutcome, ...]:
    """Index every parsed, not yet indexed document of this patient, oldest first.

    Returns one outcome per listed document, in order: a gate refusal is ``blocked`` with its
    reason and the run continues with the next document; nothing is left out. Any other error
    (a failure, not a block) propagates.
    """
    outcomes = []
    for document_id in list_unindexed_documents(patient_id):
        try:
            result = index_document(settings, embedder, patient_id, document_id)
        except DocumentProcessingError as exc:
            if exc.kind in GATE_REFUSALS:
                outcomes.append(IndexOutcome(document_id, BLOCKED, reason=str(exc.kind)))
                continue
            if exc.kind == ALREADY_INDEXED:
                outcomes.append(IndexOutcome(document_id, ALREADY_DONE, reason=ALREADY_INDEXED))
                continue
            raise
        outcomes.append(IndexOutcome(document_id, INDEXED, result=result))
    logger.info(
        "Patient indexing: patient_id=%s indexed=%d blocked=%d already_done=%d",
        patient_id,
        sum(1 for o in outcomes if o.status == INDEXED),
        sum(1 for o in outcomes if o.status == BLOCKED),
        sum(1 for o in outcomes if o.status == ALREADY_DONE),
    )
    return tuple(outcomes)
