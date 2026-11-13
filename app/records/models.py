"""SQLAlchemy models: the physical schema of MediMind (Task 1.7).

The schema is created by the hand-written, frozen Alembic migrations in ``migrations/versions/``.
These models must describe exactly the same schema; an integration test compares the two catalog
by catalog (columns, defaults, constraints, indexes).

Conventions (see docs/DATABASE_DESIGN.md):
- Primary keys: ``uuid DEFAULT uuidv7()`` (PostgreSQL 18, time-ordered). Reference tables use
  integer identity keys.
- Enumerations: ``text`` + a named CHECK built from a Python ``StrEnum`` (easier to change in a
  migration than a native PostgreSQL enum).
- Clinical values: ``numeric``, never float. Raw values are kept as printed.
- Patient isolation: patient-owned rows carry ``patient_id`` and reference their parent through a
  composite foreign key ``(parent_id, patient_id)``, so a row cannot claim another patient's
  parent. Fact subtype tables inherit ownership from ``extracted_facts`` (joined inheritance).
- No defaults for semantic fields the application must state explicitly (``is_synthetic``,
  ``assertion_type``, ``negated``, ``subject``).
- Dates with precision: (date, precision, raw text) triples. Unknown dates stay NULL with the raw
  text kept; partial dates are normalized (month -> day 1, year -> 1 January).

Repositories (Task 1.8) are the only code that uses these models (ADR-016).
"""

import datetime as dt
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIMENSIONS = 384  # BGE-small-en-v1.5 (ADR-002, accepted for the schema in Task 1.7)

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --- Enumerations (the single source for the CHECK constraints in the models) -----------------


class DatePrecision(StrEnum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"


class DocType(StrEnum):
    LAB_REPORT = "lab_report"
    PRESCRIPTION = "prescription"
    DISCHARGE_SUMMARY = "discharge_summary"
    OTHER = "other"
    UNKNOWN = "unknown"


class ProcessingStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class IdentityCheck(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class ParseMethod(StrEnum):
    NATIVE = "native"
    OCR = "ocr"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class FactType(StrEnum):
    OBSERVATION = "observation"
    MEDICATION = "medication"
    ALLERGY = "allergy"
    EVENT = "event"


class AssertionType(StrEnum):
    DIRECT_FACT = "direct_fact"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


class Subject(StrEnum):
    PATIENT = "patient"
    FAMILY = "family"
    OTHER = "other"


class ReviewStatus(StrEnum):
    """ADR-023. verified/corrected/rejected are set only by the review service (Phase 3)."""

    EXTRACTED = "extracted"
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"
    VERIFIED = "verified"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class Comparator(StrEnum):
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="


class MentionContext(StrEnum):
    PRESCRIBED = "prescribed"
    DISCHARGE_LIST = "discharge_list"
    HISTORY = "history"
    DISCONTINUED = "discontinued"
    OTHER = "other"


class EventType(StrEnum):
    ADMISSION = "admission"
    DISCHARGE = "discharge"
    PROCEDURE = "procedure"
    CONSULTATION = "consultation"
    DIAGNOSIS_DOCUMENTED = "diagnosis_documented"  # a document states it; MediMind never infers
    FOLLOW_UP_INSTRUCTION = "follow_up_instruction"
    OTHER = "other"


class Decision(StrEnum):
    VERIFY = "verify"
    CORRECT = "correct"
    REJECT = "reject"


class ConflictType(StrEnum):
    MEDICATION_DOSE_MISMATCH = "medication_dose_mismatch"
    ALLERGY_CONTRADICTION = "allergy_contradiction"
    VALUE_MISMATCH_SAME_DATE = "value_mismatch_same_date"
    OTHER = "other"


class ConflictStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"


class ChunkType(StrEnum):
    TEXT = "text"
    TABLE_ROWS = "table_rows"


# --- Constraint helpers ----------------------------------------------------------------------


def enum_check(column: str, enum: type[StrEnum], name: str | None = None) -> CheckConstraint:
    """``column IN (...)`` with the enum's values, named ``ck_<table>_<name>``."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name or f"{column}_values")


def date_precision_checks(date: str, precision: str, prefix: str) -> tuple[CheckConstraint, ...]:
    """The three date-precision rules, applied identically to every (date, precision) pair.

    1. allowed values: day, month, year;
    2. pairing: both NULL (unknown date; the raw text is kept) or both set;
    3. normalized storage: a month-precision date is the 1st of the month, a year-precision date
       is 1 January, so a partial date can never be read as a day-precise one.
    """
    return (
        enum_check(precision, DatePrecision, f"{prefix}_precision_values"),
        CheckConstraint(f"({date} IS NULL) = ({precision} IS NULL)", name=f"{prefix}_pair"),
        CheckConstraint(
            f"{precision} IS NULL OR {precision} = 'day'"
            f" OR ({precision} = 'month' AND extract(day FROM {date}) = 1)"
            f" OR ({precision} = 'year' AND extract(month FROM {date}) = 1"
            f" AND extract(day FROM {date}) = 1)",
            name=f"{prefix}_normalized",
        ),
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, server_default=sql_text("uuidv7()"))


def _created_at() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# --- Migration 0001_core ---------------------------------------------------------------------


class Patient(Base):
    """One profile per person whose records are managed (ADR-013: no users table)."""

    __tablename__ = "patients"
    __table_args__ = (
        CheckConstraint(
            "length(btrim(display_name)) BETWEEN 1 AND 200", name="display_name_length"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    date_of_birth: Mapped[dt.date | None] = mapped_column(Date)
    # No default: synthetic demo records must be marked true explicitly.
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()


class Document(Base):
    """An uploaded file, its classification and processing state. Parent of all derived rows."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "patient_id"),  # target of the composite foreign keys
        UniqueConstraint("patient_id", "sha256"),  # duplicate upload detection per patient
        UniqueConstraint("supersedes_document_id"),  # a linear version chain
        ForeignKeyConstraint(
            ["supersedes_document_id", "patient_id"],
            ["documents.id", "documents.patient_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(original_filename) BETWEEN 1 AND 255", name="original_filename_length"
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("page_count IS NULL OR page_count >= 1", name="page_count_positive"),
        CheckConstraint(
            "doc_type_confidence IS NULL OR doc_type_confidence BETWEEN 0 AND 1",
            name="doc_type_confidence_range",
        ),
        CheckConstraint("supersedes_document_id <> id", name="not_self_superseding"),
        enum_check("doc_type", DocType),
        enum_check("processing_status", ProcessingStatus),
        enum_check("identity_check", IdentityCheck),
        *date_precision_checks("document_date", "document_date_precision", "document_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    doc_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'unknown'")
    )
    doc_type_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    document_date: Mapped[dt.date | None] = mapped_column(Date)
    document_date_precision: Mapped[str | None] = mapped_column(Text)
    document_date_raw: Mapped[str | None] = mapped_column(Text)
    processing_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'uploaded'")
    )
    identity_check: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'unknown'")
    )
    supersedes_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[dt.datetime] = _created_at()
    # Maintained by the ORM on update; direct SQL updates do not change it.
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Page(Base):
    """Parsed text of one page: the evidence that facts and chunks point to."""

    __tablename__ = "pages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "patient_id"],
            ["documents.id", "documents.patient_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("document_id", "page_number"),  # target of the evidence foreign keys
        CheckConstraint("page_number >= 1", name="page_number_positive"),
        enum_check("parse_method", ParseMethod),
        Index("ix_pages_document_id_patient_id", "document_id", "patient_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parse_method: Mapped[str] = mapped_column(Text, nullable=False)
    has_tables: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    created_at: Mapped[dt.datetime] = _created_at()


class PipelineRun(Base):
    """One execution of one pipeline stage. Never stores raw exception text."""

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        # With the default MATCH SIMPLE, a composite FK is not checked when any column is NULL,
        # so a run tied to a document must also name its patient.
        CheckConstraint(
            "document_id IS NULL OR patient_id IS NOT NULL", name="document_needs_patient"
        ),
        ForeignKeyConstraint(
            ["document_id", "patient_id"],
            ["documents.id", "documents.patient_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("length(stage) BETWEEN 1 AND 64", name="stage_length"),
        enum_check("status", RunStatus),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="finished_matches"),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="finished_after_started"
        ),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_ms_positive"),
        CheckConstraint("tokens_in IS NULL OR tokens_in >= 0", name="tokens_in_positive"),
        CheckConstraint("tokens_out IS NULL OR tokens_out >= 0", name="tokens_out_positive"),
        CheckConstraint("cost_usd IS NULL OR cost_usd >= 0", name="cost_usd_positive"),
        Index("ix_pipeline_runs_document_id_started_at", "document_id", "started_at"),
        Index("ix_pipeline_runs_patient_id", "patient_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    component_versions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    error_kind: Mapped[str | None] = mapped_column(Text)  # sanitized category only
    error_class: Mapped[str | None] = mapped_column(Text)  # exception class name only


# --- Migration 0002_facts: reference data ----------------------------------------------------


class AnalyteCatalog(Base):
    """Canonical lab analytes (seeded from version-controlled CSV in Phase 3)."""

    __tablename__ = "analyte_catalog"
    __table_args__ = (
        CheckConstraint(
            "plausible_min IS NULL OR plausible_max IS NULL OR plausible_min < plausible_max",
            name="plausible_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    loinc_code: Mapped[str | None] = mapped_column(Text, unique=True)
    default_unit: Mapped[str] = mapped_column(Text, nullable=False)
    plausible_min: Mapped[Decimal | None] = mapped_column(Numeric)
    plausible_max: Mapped[Decimal | None] = mapped_column(Numeric)


class AnalyteAlias(Base):
    """A printed analyte name, normalized by the application, mapped to a canonical analyte."""

    __tablename__ = "analyte_aliases"
    __table_args__ = (
        CheckConstraint("alias = lower(btrim(alias))", name="alias_normalized"),
        Index("ix_analyte_aliases_analyte_id", "analyte_id"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    alias: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    analyte_id: Mapped[int] = mapped_column(
        ForeignKey("analyte_catalog.id", ondelete="CASCADE"), nullable=False
    )


class UnitConversion(Base):
    """Deterministic unit conversion for one analyte: value * factor + addend (ADR-003)."""

    __tablename__ = "unit_conversions"
    __table_args__ = (
        PrimaryKeyConstraint("analyte_id", "from_unit", "to_unit"),
        CheckConstraint("from_unit <> to_unit", name="different_units"),
        CheckConstraint("factor <> 0", name="factor_nonzero"),
    )

    analyte_id: Mapped[int] = mapped_column(
        ForeignKey("analyte_catalog.id", ondelete="CASCADE"), nullable=False
    )
    from_unit: Mapped[str] = mapped_column(Text, nullable=False)
    to_unit: Mapped[str] = mapped_column(Text, nullable=False)
    factor: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    # "offset" is a reserved SQL word, hence "addend".
    addend: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=sql_text("0"))


# --- Migration 0002_facts: extracted facts (joined-table inheritance, ADR-021) ----------------


class ExtractedFact(Base):
    """Base row of every extracted fact: provenance, assertion, review status and versions.

    Subtype tables (observations, medication_mentions, allergy_mentions, clinical_events) share
    this row's id as their primary key, so their patient is this row's patient.
    """

    __tablename__ = "extracted_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "patient_id"],
            ["documents.id", "documents.patient_id"],
            ondelete="CASCADE",
        ),
        # Evidence: a fact can only cite a page that exists in its own document.
        ForeignKeyConstraint(
            ["document_id", "page_number"],
            ["pages.document_id", "pages.page_number"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "patient_id"),  # target for review decisions / conflict members
        enum_check("fact_type", FactType),
        enum_check("assertion_type", AssertionType),
        enum_check("subject", Subject),
        enum_check("review_status", ReviewStatus),
        CheckConstraint("length(btrim(source_excerpt)) > 0", name="source_excerpt_present"),
        CheckConstraint("(char_start IS NULL) = (char_end IS NULL)", name="char_span_pair"),
        CheckConstraint(
            "char_start IS NULL OR (char_start >= 0 AND char_start <= char_end)",
            name="char_span_order",
        ),
        CheckConstraint(
            "review_status <> 'needs_review' OR cardinality(review_reasons) > 0",
            name="needs_review_has_reasons",
        ),
        Index("ix_extracted_facts_document_id_patient_id", "document_id", "patient_id"),
        Index("ix_extracted_facts_document_id_page_number", "document_id", "page_number"),
        Index(
            "ix_extracted_facts_patient_id_fact_type_review_status",
            "patient_id",
            "fact_type",
            "review_status",
        ),
        Index(
            "ix_extracted_facts_duplicate_group_id",
            "duplicate_group_id",
            postgresql_where=sql_text("duplicate_group_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    fact_type: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # No defaults: the extractor must state these explicitly.
    assertion_type: Mapped[str] = mapped_column(Text, nullable=False)
    negated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'extracted'")
    )
    review_reasons: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'::text[]")
    )
    duplicate_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # a label, no FK
    extractor_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()

    __mapper_args__ = {"polymorphic_on": "fact_type"}


def _fact_pk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("extracted_facts.id", ondelete="CASCADE"), primary_key=True)


class Observation(ExtractedFact):
    """One lab result: raw and normalized values side by side."""

    __tablename__ = "observations"
    __table_args__ = (
        enum_check("comparator", Comparator),
        CheckConstraint("comparator IS NULL OR value_num IS NOT NULL", name="comparator_number"),
        CheckConstraint(
            "value_num_norm IS NULL OR unit_norm IS NOT NULL", name="normalized_value_unit"
        ),
        CheckConstraint(
            "ref_low IS NULL OR ref_high IS NULL OR ref_low <= ref_high", name="ref_range_order"
        ),
        *date_precision_checks("effective_date", "date_precision", "effective_date"),
        Index("ix_observations_analyte_id", "analyte_id"),
    )
    __mapper_args__ = {"polymorphic_identity": FactType.OBSERVATION.value}

    fact_id: Mapped[uuid.UUID] = _fact_pk()
    analyte_raw: Mapped[str] = mapped_column(Text, nullable=False)
    analyte_id: Mapped[int | None] = mapped_column(
        ForeignKey("analyte_catalog.id", ondelete="RESTRICT")
    )
    value_raw: Mapped[str] = mapped_column(Text, nullable=False)
    value_num: Mapped[Decimal | None] = mapped_column(Numeric)
    comparator: Mapped[str | None] = mapped_column(Text)
    value_qualitative: Mapped[str | None] = mapped_column(Text)
    unit_raw: Mapped[str | None] = mapped_column(Text)
    unit_norm: Mapped[str | None] = mapped_column(Text)
    value_num_norm: Mapped[Decimal | None] = mapped_column(Numeric)
    ref_range_raw: Mapped[str | None] = mapped_column(Text)
    ref_low: Mapped[Decimal | None] = mapped_column(Numeric)
    ref_high: Mapped[Decimal | None] = mapped_column(Numeric)
    flag_as_printed: Mapped[str | None] = mapped_column(Text)
    effective_date: Mapped[dt.date | None] = mapped_column(Date)
    date_precision: Mapped[str | None] = mapped_column(Text)
    date_raw: Mapped[str | None] = mapped_column(Text)
    specimen: Mapped[str | None] = mapped_column(Text)


class MedicationMention(ExtractedFact):
    """A medication as documented on a date. Not a current medication list."""

    __tablename__ = "medication_mentions"
    __table_args__ = (
        enum_check("mention_context", MentionContext),
        *date_precision_checks("mention_date", "date_precision", "mention_date"),
    )
    __mapper_args__ = {"polymorphic_identity": FactType.MEDICATION.value}

    fact_id: Mapped[uuid.UUID] = _fact_pk()
    name_raw: Mapped[str] = mapped_column(Text, nullable=False)
    name_norm: Mapped[str | None] = mapped_column(Text)
    strength_raw: Mapped[str | None] = mapped_column(Text)
    dose_raw: Mapped[str | None] = mapped_column(Text)
    route_raw: Mapped[str | None] = mapped_column(Text)
    frequency_raw: Mapped[str | None] = mapped_column(Text)
    duration_raw: Mapped[str | None] = mapped_column(Text)
    instructions_raw: Mapped[str | None] = mapped_column(Text)
    mention_context: Mapped[str] = mapped_column(Text, nullable=False)
    mention_date: Mapped[dt.date | None] = mapped_column(Date)
    date_precision: Mapped[str | None] = mapped_column(Text)
    date_raw: Mapped[str | None] = mapped_column(Text)


class AllergyMention(ExtractedFact):
    """An allergy as documented. "No known allergies" is ``negated = true`` on the base row."""

    __tablename__ = "allergy_mentions"
    __table_args__ = (*date_precision_checks("mention_date", "date_precision", "mention_date"),)
    __mapper_args__ = {"polymorphic_identity": FactType.ALLERGY.value}

    fact_id: Mapped[uuid.UUID] = _fact_pk()
    substance_raw: Mapped[str] = mapped_column(Text, nullable=False)
    reaction_raw: Mapped[str | None] = mapped_column(Text)
    severity_raw: Mapped[str | None] = mapped_column(Text)
    mention_date: Mapped[dt.date | None] = mapped_column(Date)
    date_precision: Mapped[str | None] = mapped_column(Text)
    date_raw: Mapped[str | None] = mapped_column(Text)


class ClinicalEvent(ExtractedFact):
    """An encounter or event as documented (admission, consultation, procedure, ...)."""

    __tablename__ = "clinical_events"
    __table_args__ = (
        enum_check("event_type", EventType),
        *date_precision_checks("event_date", "date_precision", "event_date"),
    )
    __mapper_args__ = {"polymorphic_identity": FactType.EVENT.value}

    fact_id: Mapped[uuid.UUID] = _fact_pk()
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    event_date: Mapped[dt.date | None] = mapped_column(Date)
    date_precision: Mapped[str | None] = mapped_column(Text)
    date_raw: Mapped[str | None] = mapped_column(Text)


# --- Migration 0002_facts: review and conflicts ----------------------------------------------


class ReviewDecision(Base):
    """A human review decision (ADR-023).

    Append-only at the APPLICATION level only: repositories and the review service offer no
    update or delete. The database does not (yet) prevent a direct UPDATE or DELETE, and deleting
    a document cascades to its decisions by design.
    """

    __tablename__ = "review_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["fact_id", "patient_id"],
            ["extracted_facts.id", "extracted_facts.patient_id"],
            ondelete="CASCADE",
        ),
        enum_check("decision", Decision),
        CheckConstraint(
            "(decision = 'correct') = (corrected_values IS NOT NULL)",
            name="corrected_values_match",
        ),
        Index("ix_review_decisions_fact_id_patient_id", "fact_id", "patient_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    fact_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Conflict(Base):
    """A rule-detected contradiction between facts. Stored and shown, never auto-resolved."""

    __tablename__ = "conflicts"
    __table_args__ = (
        UniqueConstraint("id", "patient_id"),
        enum_check("conflict_type", ConflictType),
        enum_check("status", ConflictStatus),
        Index("ix_conflicts_patient_id_status", "patient_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False
    )
    conflict_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'open'"))
    detected_by_rule: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()


class ConflictMember(Base):
    """A fact taking part in a conflict. Both FKs carry patient_id: same-patient links only."""

    __tablename__ = "conflict_members"
    __table_args__ = (
        PrimaryKeyConstraint("conflict_id", "fact_id"),
        ForeignKeyConstraint(
            ["conflict_id", "patient_id"],
            ["conflicts.id", "conflicts.patient_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["fact_id", "patient_id"],
            ["extracted_facts.id", "extracted_facts.patient_id"],
            ondelete="CASCADE",
        ),
        Index("ix_conflict_members_fact_id_patient_id", "fact_id", "patient_id"),
    )

    conflict_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    fact_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


# --- Migration 0003_retrieval ----------------------------------------------------------------


class Chunk(Base):
    """A retrieval unit with an optional dense embedding and a generated full-text vector.

    No vector index yet: an exact scan filtered by patient is exact and fast at personal scale.
    Document type and date are read by joining ``documents`` (no denormalized copies, U5).
    """

    __tablename__ = "chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "patient_id"],
            ["documents.id", "documents.patient_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "page_number"],
            ["pages.document_id", "pages.page_number"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("document_id", "chunk_index"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_positive"),
        enum_check("chunk_type", ChunkType),
        CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)", name="embedding_model_pair"
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_document_id_patient_id", "document_id", "patient_id"),
        Index("ix_chunks_document_id_page_number", "document_id", "page_number"),
        Index("ix_chunks_patient_id", "patient_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)  # the starting page
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(Text)
    chunk_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(Text)
    # Two-argument to_tsvector: a generated column needs an immutable expression.
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple'::regconfig, content)", persisted=True)
    )
    created_at: Mapped[dt.datetime] = _created_at()
