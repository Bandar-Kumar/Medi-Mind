"""Patient-scoped reads for the longitudinal views (Phase 4; docs/PHASE_4_PLAN.md §7; ADR-039).

Read-only: nothing here writes. Every function takes ``patient_id`` first and filters by it in SQL
(ADR-016); another patient's rows are never returned.

The SQL narrows conservatively and the pure layer filters exactly: a *corrected* fact can display
another analyte or date than its stored columns, so corrected facts always pass the analyte and
date pre-filters and are filtered on their display values by the service. Undated facts always pass
the date pre-filter (they are listed separately, never placed in a window).

Each call is a fixed number of queries (no per-fact queries): facts with their analyte names, their
decisions, documents with their latest finished run.
"""

import datetime as dt
import uuid
from collections.abc import Collection
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.records.db import session_scope
from app.records.facts import USABLE_STATUSES, FactRecord, _fact_rows
from app.records.models import (
    AnalyteCatalog,
    Document,
    IdentityCheck,
    Observation,
    PipelineRun,
    ReviewStatus,
)

# A year-precision date is stored as 1 January; widening the lower bound by a year keeps every
# partial date whose period can overlap the window. The exact overlap test is pure.
_PARTIAL_DATE_SLACK = dt.timedelta(days=366)


def _not_mismatch_documents(patient_id: uuid.UUID):  # noqa: ANN202 (SQLAlchemy subquery)
    return select(Document.id).where(
        Document.patient_id == patient_id,
        Document.identity_check != IdentityCheck.MISMATCH.value,
    )


def list_usable_facts(
    patient_id: uuid.UUID,
    *,
    analyte_names: Collection[str] | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    factory: sessionmaker[Session] | None = None,
) -> tuple[FactRecord, ...]:
    """Current (not superseded) validated, verified and corrected observations, with decisions.

    Facts of identity-``mismatch`` documents are never returned (none should exist: extraction is
    blocked for them; this is a second guard).
    """
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from is after date_to")
    corrected = Observation.review_status == ReviewStatus.CORRECTED.value
    conditions = [
        Observation.superseded_at.is_(None),
        Observation.review_status.in_(sorted(USABLE_STATUSES)),
        Observation.document_id.in_(_not_mismatch_documents(patient_id)),
    ]
    if analyte_names is not None:
        ids = select(AnalyteCatalog.id).where(
            AnalyteCatalog.canonical_name.in_(sorted(set(analyte_names)))
        )
        conditions.append(or_(corrected, Observation.analyte_id.in_(ids)))
    if date_from is not None:
        conditions.append(
            or_(
                corrected,
                Observation.effective_date.is_(None),
                Observation.effective_date >= date_from - _PARTIAL_DATE_SLACK,
            )
        )
    if date_to is not None:
        conditions.append(
            or_(
                corrected,
                Observation.effective_date.is_(None),
                Observation.effective_date <= date_to,
            )
        )
    with session_scope(factory) as session:
        return tuple(_fact_rows(session, patient_id, *conditions))


def list_facts_by_ids(
    patient_id: uuid.UUID,
    fact_ids: Collection[uuid.UUID],
    *,
    factory: sessionmaker[Session] | None = None,
) -> tuple[FactRecord, ...]:
    """The patient's observations among ``fact_ids`` (any status, superseded included), in one
    query. Ids of other patients' facts are silently absent, exactly like unknown ids."""
    if not fact_ids:
        return ()
    with session_scope(factory) as session:
        return tuple(_fact_rows(session, patient_id, Observation.id.in_(sorted(set(fact_ids)))))


@dataclass(frozen=True)
class TimelineDocumentRecord:
    id: uuid.UUID
    doc_type: str
    identity_check: str
    processing_status: str
    document_date: dt.date | None
    document_date_precision: str | None
    created_at: dt.datetime  # upload time: metadata, never a clinical date
    processed_at: dt.datetime | None  # latest finished pipeline run: metadata only
    original_filename: str = field(repr=False)


def list_timeline_documents(
    patient_id: uuid.UUID, *, factory: sessionmaker[Session] | None = None
) -> tuple[TimelineDocumentRecord, ...]:
    """Every document of the patient with its report date and processing metadata (one query)."""
    last_run = (
        select(PipelineRun.document_id, func.max(PipelineRun.finished_at).label("finished"))
        .where(PipelineRun.patient_id == patient_id, PipelineRun.finished_at.is_not(None))
        .group_by(PipelineRun.document_id)
        .subquery()
    )
    with session_scope(factory) as session:
        rows = session.execute(
            select(Document, last_run.c.finished)
            .outerjoin(last_run, last_run.c.document_id == Document.id)
            .where(Document.patient_id == patient_id)
            .order_by(Document.created_at, Document.id)
        ).all()
        return tuple(
            TimelineDocumentRecord(
                id=d.id,
                doc_type=d.doc_type,
                identity_check=d.identity_check,
                processing_status=d.processing_status,
                document_date=d.document_date,
                document_date_precision=d.document_date_precision,
                created_at=d.created_at,
                processed_at=finished,
                original_filename=d.original_filename,
            )
            for d, finished in rows
        )


@dataclass(frozen=True)
class StatusCount:
    analyte_name: str | None  # the stored (machine) analyte; None when unmapped
    review_status: str
    count: int


def count_facts_by_status(
    patient_id: uuid.UUID, *, factory: sessionmaker[Session] | None = None
) -> tuple[StatusCount, ...]:
    """Counts of current observations per stored analyte and review status (identity-mismatch
    documents excluded). Used to say how many results await review without showing them."""
    with session_scope(factory) as session:
        rows = session.execute(
            select(AnalyteCatalog.canonical_name, Observation.review_status, func.count())
            .outerjoin(AnalyteCatalog, AnalyteCatalog.id == Observation.analyte_id)
            .where(
                Observation.patient_id == patient_id,
                Observation.superseded_at.is_(None),
                Observation.document_id.in_(_not_mismatch_documents(patient_id)),
            )
            .group_by(AnalyteCatalog.canonical_name, Observation.review_status)
            .order_by(AnalyteCatalog.canonical_name, Observation.review_status)
        ).all()
    return tuple(StatusCount(name, status, int(count)) for name, status, count in rows)
