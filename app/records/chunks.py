"""Patient-scoped chunk repository: indexing and search (Milestone R1, ADR-016, ADR-035).

The records layer is the only database access path. Every public function here takes
``patient_id`` first and filters every query by it; the composite foreign keys of ``chunks``
(``(document_id, patient_id)`` and ``(document_id, page_number)``) enforce the same ownership in the
database. Each function runs in one transaction and returns frozen dataclasses.

Indexing protocol (simpler than the parse protocol, because nothing is written outside the
transaction): the caller chunks and embeds with no transaction open; ``insert_document_chunks``
then locks the document row, checks that the document has pages and no chunks yet, inserts every
chunk and one ``pipeline_runs`` row (stage ``index``, ``succeeded``) - all or nothing. Concurrent
indexers serialize on the row lock; exactly one wins, the others get ``already_indexed``.
Indexing never changes ``documents.processing_status`` (ARCHITECTURE §4.1).

Indexing gate (Task 2.8, ADR-036 A1): a document may be indexed only with a succeeded
``classify`` run, a succeeded ``identity`` run, and ``identity_check <> 'mismatch'``. The columns
alone cannot tell "never checked" from "checked, unknown" (both default to ``unknown``), so the
runs decide. ``get_index_target`` reports the gate (a pre-check before chunking and embedding);
``insert_document_chunks`` re-checks it under the document row lock, in the transaction that
inserts the chunks, so a check recorded in between can never be bypassed. Refusals:
``identity_mismatch``, ``identity_not_checked``, ``not_classified``.

Chunk text is evidence: it is stored exactly as given, never logged, and hidden from ``repr()``.
Logs carry identifiers, counts and durations only.
"""

import datetime as dt
import logging
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import reduce

from sqlalchemy import exists, func, literal_column, select, tuple_
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import DocumentProcessingError, PatientError
from app.records.db import session_scope
from app.records.models import (
    EMBEDDING_DIMENSIONS,
    Chunk,
    ChunkType,
    Document,
    Page,
    Patient,
    PipelineRun,
    RunStatus,
)
from app.records.repositories import (
    _NUL,
    CLASSIFY_STAGE,
    DOCUMENT_NOT_FOUND,
    IDENTITY_STAGE,
    PATIENT_NOT_FOUND,
    ProcessingErrorKind,
    _encodes_as_utf8,
    _has_succeeded_run,
    _lock_document,
)

logger = logging.getLogger(__name__)

INDEX_STAGE = "index"
ALREADY_INDEXED = "already_indexed"  # the document already has chunks
NOT_PARSED = "not_parsed"  # the document has no page rows yet
NOT_CLASSIFIED = "not_classified"  # no succeeded classify run (ADR-036 A1)
IDENTITY_NOT_CHECKED = "identity_not_checked"  # no succeeded identity run
IDENTITY_MISMATCH = "identity_mismatch"  # the identity check found a different patient
GATE_REFUSALS = frozenset({NOT_CLASSIFIED, IDENTITY_NOT_CHECKED, IDENTITY_MISMATCH})
_MISMATCH = "mismatch"


def _gate_refusal(classified: bool, identity_checked: bool, identity_check: str) -> str | None:
    """Why a document may not be indexed (ADR-036 A1), or None. The single gate rule."""
    if identity_check == _MISMATCH:
        return IDENTITY_MISMATCH
    if not identity_checked:
        return IDENTITY_NOT_CHECKED
    if not classified:
        return NOT_CLASSIFIED
    return None


MAX_SEARCH_LIMIT = 500
MAX_TERMS = 24
MAX_TERM_CHARS = 64
_SIMPLE = literal_column("'simple'::regconfig")  # a constant, never user input


@dataclass(frozen=True)
class ChunkRecord:
    """One chunk to store. ``content`` and ``embedding`` never appear in ``repr()``."""

    page_number: int
    chunk_type: str  # a ChunkType value
    section: str | None
    content: str = field(repr=False)
    embedding: tuple[float, ...] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class IndexTarget:
    """What indexing needs to know about a document (no filename, no content)."""

    document_id: uuid.UUID
    sha256: str
    page_count: int | None
    page_rows: int
    chunk_count: int
    classified: bool  # a succeeded classify run exists
    identity_checked: bool  # a succeeded identity run exists
    identity_check: str  # match / mismatch / unknown (the column)

    @property
    def blocked_reason(self) -> str | None:
        """The indexing-gate refusal (ADR-036 A1), or None when the gate is open."""
        return _gate_refusal(self.classified, self.identity_checked, self.identity_check)


@dataclass(frozen=True)
class ChunkHit:
    """A search result. ``content`` is evidence text: never logged."""

    chunk_id: uuid.UUID
    patient_id: uuid.UUID
    document_id: uuid.UUID
    page_number: int
    chunk_type: str
    section: str | None
    content: str = field(repr=False)
    score: float  # dense: cosine similarity; lexical: ts_rank_cd


@dataclass(frozen=True)
class PageContext:
    """Where a cited page comes from. The filename can hold personal data: never log it."""

    document_id: uuid.UUID
    page_number: int
    original_filename: str = field(repr=False)
    document_date: dt.date | None
    doc_type: str
    parse_method: str
    identity_check: str  # match / mismatch / unknown: shown with the evidence, never ranked


def _error(kind: str, message: str) -> DocumentProcessingError:
    return DocumentProcessingError(message, kind=kind)


def _require_patient(session: Session, patient_id: uuid.UUID) -> None:
    if not session.scalar(select(exists().where(Patient.id == patient_id))):
        raise PatientError(f"Patient {patient_id} does not exist", kind=PATIENT_NOT_FOUND)


def _check_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must be 1..{MAX_SEARCH_LIMIT}")


def _valid_vector(vector: Sequence[float]) -> bool:
    return len(vector) == EMBEDDING_DIMENSIONS and all(
        isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in vector
    )


def _check_vector(vector: Sequence[float]) -> list[float]:
    if not _valid_vector(vector):
        raise ValueError(f"an embedding must have {EMBEDDING_DIMENSIONS} finite values")
    return [float(v) for v in vector]


# --- reading what to index -----------------------------------------------------------------------


def get_index_target(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> IndexTarget:
    """The document's hash, page and chunk row counts, and its indexing gate. Read-only.

    Raises:
        DocumentProcessingError: ``document_not_found`` (also for another patient's document).
    """
    with session_scope(factory) as session:
        document = session.scalars(
            select(Document).where(Document.id == document_id, Document.patient_id == patient_id)
        ).one_or_none()
        if document is None:
            raise _error(DOCUMENT_NOT_FOUND, f"Document {document_id} not found for this patient")
        pages = session.scalar(
            select(func.count())
            .select_from(Page)
            .where(Page.patient_id == patient_id, Page.document_id == document_id)
        )
        chunks = session.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.patient_id == patient_id, Chunk.document_id == document_id)
        )
        return IndexTarget(
            document_id=document.id,
            sha256=document.sha256,
            page_count=document.page_count,
            page_rows=int(pages or 0),
            chunk_count=int(chunks or 0),
            classified=_has_succeeded_run(session, patient_id, document_id, CLASSIFY_STAGE),
            identity_checked=_has_succeeded_run(session, patient_id, document_id, IDENTITY_STAGE),
            identity_check=document.identity_check,
        )


def list_unindexed_documents(
    patient_id: uuid.UUID, *, factory: sessionmaker[Session] | None = None
) -> tuple[uuid.UUID, ...]:
    """The patient's documents that have page rows but no chunks, oldest first. Read-only.

    Documents the indexing gate refuses are listed too (ADR-036 A1): indexing reports them as
    blocked instead of silently leaving them out.

    Raises:
        PatientError: ``patient_not_found``.
    """
    has_pages = exists().where(Page.document_id == Document.id, Page.patient_id == patient_id)
    has_chunks = exists().where(Chunk.document_id == Document.id, Chunk.patient_id == patient_id)
    with session_scope(factory) as session:
        _require_patient(session, patient_id)
        ids = session.scalars(
            select(Document.id)
            .where(Document.patient_id == patient_id, has_pages, ~has_chunks)
            .order_by(Document.created_at, Document.id)
        ).all()
    return tuple(ids)


# --- indexing ------------------------------------------------------------------------------------


def _check_chunks(chunks: Sequence[ChunkRecord], embedding_model: str | None) -> None:
    """Input checks before any database work; messages name positions, never content."""
    if not chunks:
        raise _error(ProcessingErrorKind.INTEGRITY_MISMATCH, "No chunks were given")
    types = {member.value for member in ChunkType}
    with_embedding = sum(1 for chunk in chunks if chunk.embedding is not None)
    if with_embedding not in (0, len(chunks)):
        raise _error(
            ProcessingErrorKind.INTEGRITY_MISMATCH, "Either every chunk has an embedding or none"
        )
    if (with_embedding > 0) != bool(embedding_model):
        raise _error(
            ProcessingErrorKind.INTEGRITY_MISMATCH,
            "embedding_model must be given exactly when chunks have embeddings",
        )
    for position, chunk in enumerate(chunks):
        if chunk.chunk_type not in types:
            raise _error(
                ProcessingErrorKind.INTEGRITY_MISMATCH, f"Chunk {position} has an unknown type"
            )
        if isinstance(chunk.page_number, bool) or chunk.page_number < 1:
            raise _error(
                ProcessingErrorKind.INTEGRITY_MISMATCH, f"Chunk {position} has no valid page"
            )
        if not chunk.content.strip():
            raise _error(ProcessingErrorKind.INTEGRITY_MISMATCH, f"Chunk {position} is empty")
        if _NUL in chunk.content or not _encodes_as_utf8(chunk.content):
            raise _error(
                ProcessingErrorKind.TEXT_NOT_STORABLE,
                f"Chunk {position} contains text that cannot be stored",
            )
        if chunk.embedding is not None and not _valid_vector(chunk.embedding):
            raise _error(
                ProcessingErrorKind.INTEGRITY_MISMATCH,
                f"Chunk {position} has an invalid embedding",
            )


def insert_document_chunks(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    chunks: Sequence[ChunkRecord],
    *,
    embedding_model: str | None,
    component_versions: Mapping[str, str],
    duration_ms: int,
    factory: sessionmaker[Session] | None = None,
) -> int:
    """Store every chunk of one document and record the index run; returns the chunk count.

    ``chunk_index`` is the position in ``chunks``. All chunks are inserted, or none.

    The indexing gate (ADR-036 A1) is re-checked here, under the row lock and in this
    transaction, immediately before the chunks are inserted.

    Raises:
        DocumentProcessingError: ``document_not_found``, ``not_parsed`` (no page rows),
            ``already_indexed``, ``identity_mismatch``, ``identity_not_checked``,
            ``not_classified``, ``integrity_mismatch`` (bad input, or a page that does not exist),
            ``text_not_storable``.
    """
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
        raise ValueError("duration_ms must be a non-negative integer")
    _check_chunks(chunks, embedding_model)
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)  # serializes indexers
        pages = set(
            session.scalars(
                select(Page.page_number).where(
                    Page.patient_id == patient_id, Page.document_id == document_id
                )
            ).all()
        )
        if not pages:
            raise _error(NOT_PARSED, f"Document {document_id} has no parsed pages")
        if session.scalar(
            select(exists().where(Chunk.patient_id == patient_id, Chunk.document_id == document_id))
        ):
            raise _error(ALREADY_INDEXED, f"Document {document_id} is already indexed")
        refusal = _gate_refusal(
            _has_succeeded_run(session, patient_id, document_id, CLASSIFY_STAGE),
            _has_succeeded_run(session, patient_id, document_id, IDENTITY_STAGE),
            document.identity_check,
        )
        if refusal is not None:
            raise _error(refusal, f"Document {document_id} may not be indexed ({refusal})")
        missing = sorted({chunk.page_number for chunk in chunks} - pages)
        if missing:
            raise _error(
                ProcessingErrorKind.INTEGRITY_MISMATCH,
                f"Document {document_id} has no page {missing[0]}",
            )
        session.add_all(
            Chunk(
                patient_id=patient_id,
                document_id=document_id,
                page_number=chunk.page_number,
                chunk_index=index,
                section=chunk.section,
                chunk_type=chunk.chunk_type,
                content=chunk.content,
                embedding=list(chunk.embedding) if chunk.embedding is not None else None,
                embedding_model=embedding_model if chunk.embedding is not None else None,
            )
            for index, chunk in enumerate(chunks)
        )
        session.add(
            PipelineRun(
                patient_id=patient_id,
                document_id=document_id,
                stage=INDEX_STAGE,
                status=RunStatus.SUCCEEDED.value,
                started_at=func.now() - dt.timedelta(milliseconds=duration_ms),
                finished_at=func.now(),
                duration_ms=duration_ms,
                component_versions=dict(component_versions),
            )
        )
        session.flush()
    logger.info(
        "Document indexed: document_id=%s chunks=%d duration_ms=%d",
        document_id,
        len(chunks),
        duration_ms,
    )
    return len(chunks)


# --- search --------------------------------------------------------------------------------------


def _hits(rows: Sequence) -> tuple[ChunkHit, ...]:
    return tuple(
        ChunkHit(
            chunk_id=row.id,
            patient_id=row.patient_id,
            document_id=row.document_id,
            page_number=row.page_number,
            chunk_type=row.chunk_type,
            section=row.section,
            content=row.content,
            score=float(row.score),
        )
        for row in rows
    )


def dense_search(
    patient_id: uuid.UUID,
    query_embedding: Sequence[float],
    *,
    limit: int,
    factory: sessionmaker[Session] | None = None,
) -> tuple[ChunkHit, ...]:
    """The patient's chunks nearest to the query by cosine distance (exact scan, no index).

    ``score`` is the cosine similarity (1 - distance). Ties are broken by chunk id.
    """
    _check_limit(limit)
    vector = _check_vector(query_embedding)
    distance = Chunk.embedding.cosine_distance(vector)
    statement = (
        select(
            Chunk.id,
            Chunk.patient_id,
            Chunk.document_id,
            Chunk.page_number,
            Chunk.chunk_type,
            Chunk.section,
            Chunk.content,
            (1 - distance).label("score"),
        )
        .where(Chunk.patient_id == patient_id, Chunk.embedding.is_not(None))
        .order_by(distance, Chunk.id)
        .limit(limit)
    )
    with session_scope(factory) as session:
        hits = _hits(session.execute(statement).all())
    logger.debug("Dense search: patient_id=%s hits=%d", patient_id, len(hits))
    return hits


def _check_terms(terms: Sequence[str]) -> list[str]:
    if isinstance(terms, str):
        raise TypeError("terms must be a sequence of strings, not one string")
    checked = list(terms)
    if not 1 <= len(checked) <= MAX_TERMS:
        raise ValueError(f"1..{MAX_TERMS} search terms are required")
    for term in checked:
        if not isinstance(term, str) or not term.strip() or len(term) > MAX_TERM_CHARS:
            raise ValueError(f"each term must be 1..{MAX_TERM_CHARS} characters")
        if _NUL in term or not _encodes_as_utf8(term):
            raise ValueError("a term contains a character that cannot be searched")
    return checked


def lexical_search(
    patient_id: uuid.UUID,
    terms: Sequence[str],
    *,
    limit: int,
    factory: sessionmaker[Session] | None = None,
) -> tuple[ChunkHit, ...]:
    """The patient's chunks matching ANY term (Postgres full text, 'simple' config), best first.

    Each term becomes a bound ``plainto_tsquery('simple', :term)``; they are OR-ed (``||``), so no
    term is ever parsed as query syntax. Ranked by ``ts_rank_cd``; ties broken by chunk id.
    """
    _check_limit(limit)
    checked = _check_terms(terms)
    query = reduce(
        lambda left, right: left.op("||")(right),
        [func.plainto_tsquery(_SIMPLE, term) for term in checked],
    )
    rank = func.ts_rank_cd(Chunk.tsv, query)
    statement = (
        select(
            Chunk.id,
            Chunk.patient_id,
            Chunk.document_id,
            Chunk.page_number,
            Chunk.chunk_type,
            Chunk.section,
            Chunk.content,
            rank.label("score"),
        )
        .where(Chunk.patient_id == patient_id, Chunk.tsv.op("@@")(query))
        .order_by(rank.desc(), Chunk.id)
        .limit(limit)
    )
    with session_scope(factory) as session:
        hits = _hits(session.execute(statement).all())
    logger.debug(
        "Lexical search: patient_id=%s terms=%d hits=%d", patient_id, len(checked), len(hits)
    )
    return hits


def page_contexts(
    patient_id: uuid.UUID,
    pages: Sequence[tuple[uuid.UUID, int]],
    *,
    factory: sessionmaker[Session] | None = None,
) -> dict[tuple[uuid.UUID, int], PageContext]:
    """Source details of each requested (document, page) of this patient. Read-only.

    Pages of another patient, or that do not exist, are simply absent from the result.
    """
    wanted = list(dict.fromkeys(pages))
    if not wanted:
        return {}
    statement = (
        select(
            Document.id,
            Page.page_number,
            Document.original_filename,
            Document.document_date,
            Document.doc_type,
            Page.parse_method,
            Document.identity_check,
        )
        .join(Page, (Page.document_id == Document.id) & (Page.patient_id == Document.patient_id))
        .where(
            Document.patient_id == patient_id,
            tuple_(Page.document_id, Page.page_number).in_(wanted),
        )
    )
    with session_scope(factory) as session:
        rows = session.execute(statement).all()
    return {
        (row[0], row[1]): PageContext(
            document_id=row[0],
            page_number=row[1],
            original_filename=row[2],
            document_date=row[3],
            doc_type=row[4],
            parse_method=row[5],
            identity_check=row[6],
        )
        for row in rows
    }
