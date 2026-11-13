"""Unit tests for app/records/models.py: the schema as declared (no database needed).

The expectations below are written independently of the models (a specification), so a model
change cannot silently redefine what is "correct".
"""

import re
from enum import StrEnum

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Table, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from app.records import models
from app.records.models import Base

TABLES = Base.metadata.tables

# --- Patient-ownership classification (each table exactly once) ------------------------------
ROOT = {"patients"}
DIRECTLY_OWNED = {
    "documents",
    "pages",
    "chunks",
    "extracted_facts",
    "review_decisions",
    "conflicts",
    "conflict_members",
    "pipeline_runs",
}
SUBTYPES = {
    "observations": "observation",
    "medication_mentions": "medication",
    "allergy_mentions": "allergy",
    "clinical_events": "event",
}
REFERENCE = {"analyte_catalog", "analyte_aliases", "unit_conversions"}

# How each directly owned table proves its patient: the referenced (table, columns) of a FK that
# includes patient_id.
OWNERSHIP_FKS: dict[str, set[tuple[str, tuple[str, ...]]]] = {
    # patients FK, plus the version link that can only point to the same patient's document.
    "documents": {("patients", ("id",)), ("documents", ("id", "patient_id"))},
    "pages": {("documents", ("id", "patient_id"))},
    "chunks": {("documents", ("id", "patient_id"))},
    "extracted_facts": {("documents", ("id", "patient_id"))},
    "review_decisions": {("extracted_facts", ("id", "patient_id"))},
    "conflicts": {("patients", ("id",))},
    "conflict_members": {
        ("conflicts", ("id", "patient_id")),
        ("extracted_facts", ("id", "patient_id")),
    },
    "pipeline_runs": {("patients", ("id",)), ("documents", ("id", "patient_id"))},
}

# (table, local columns) -> ON DELETE action
DELETE_RULES: dict[tuple[str, tuple[str, ...]], str] = {
    ("documents", ("patient_id",)): "RESTRICT",
    ("documents", ("supersedes_document_id", "patient_id")): "RESTRICT",
    ("pages", ("document_id", "patient_id")): "CASCADE",
    ("pipeline_runs", ("patient_id",)): "RESTRICT",
    ("pipeline_runs", ("document_id", "patient_id")): "CASCADE",
    ("analyte_aliases", ("analyte_id",)): "CASCADE",
    ("unit_conversions", ("analyte_id",)): "CASCADE",
    ("extracted_facts", ("document_id", "patient_id")): "CASCADE",
    ("extracted_facts", ("document_id", "page_number")): "CASCADE",
    ("observations", ("fact_id",)): "CASCADE",
    ("observations", ("analyte_id",)): "RESTRICT",
    ("medication_mentions", ("fact_id",)): "CASCADE",
    ("allergy_mentions", ("fact_id",)): "CASCADE",
    ("clinical_events", ("fact_id",)): "CASCADE",
    ("review_decisions", ("fact_id", "patient_id")): "CASCADE",
    ("conflicts", ("patient_id",)): "RESTRICT",
    ("conflict_members", ("conflict_id", "patient_id")): "CASCADE",
    ("conflict_members", ("fact_id", "patient_id")): "CASCADE",
    ("chunks", ("document_id", "patient_id")): "CASCADE",
    ("chunks", ("document_id", "page_number")): "CASCADE",
}

DATE_TRIPLES = {
    "documents": ("document_date", "document_date_precision", "document_date_raw"),
    "observations": ("effective_date", "date_precision", "date_raw"),
    "medication_mentions": ("mention_date", "date_precision", "date_raw"),
    "allergy_mentions": ("mention_date", "date_precision", "date_raw"),
    "clinical_events": ("event_date", "date_precision", "date_raw"),
}

ENUM_COLUMNS: dict[tuple[str, str], type[StrEnum]] = {
    ("documents", "doc_type"): models.DocType,
    ("documents", "processing_status"): models.ProcessingStatus,
    ("documents", "identity_check"): models.IdentityCheck,
    ("pages", "parse_method"): models.ParseMethod,
    ("pipeline_runs", "status"): models.RunStatus,
    ("extracted_facts", "fact_type"): models.FactType,
    ("extracted_facts", "assertion_type"): models.AssertionType,
    ("extracted_facts", "subject"): models.Subject,
    ("extracted_facts", "review_status"): models.ReviewStatus,
    ("observations", "comparator"): models.Comparator,
    ("medication_mentions", "mention_context"): models.MentionContext,
    ("clinical_events", "event_type"): models.EventType,
    ("review_decisions", "decision"): models.Decision,
    ("conflicts", "conflict_type"): models.ConflictType,
    ("conflicts", "status"): models.ConflictStatus,
    ("chunks", "chunk_type"): models.ChunkType,
}

NO_DEFAULT_COLUMNS = {
    ("patients", "is_synthetic"),
    ("extracted_facts", "assertion_type"),
    ("extracted_facts", "negated"),
    ("extracted_facts", "subject"),
}


def _fks(table: Table) -> list[ForeignKeyConstraint]:
    return [c for c in table.constraints if isinstance(c, ForeignKeyConstraint)]


def _fk_target(fk: ForeignKeyConstraint) -> tuple[str, tuple[str, ...]]:
    return fk.referred_table.name, tuple(e.column.name for e in fk.elements)


def _checks(table: Table) -> dict[str, str]:
    return {
        str(c.name): str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)
    }


# --- Tables and ownership ----------------------------------------------------------------------


def test_exactly_sixteen_tables_each_classified_once() -> None:
    classes = [ROOT, DIRECTLY_OWNED, set(SUBTYPES), REFERENCE]
    assert sum(len(c) for c in classes) == 16
    assert set().union(*classes) == set(TABLES)
    assert len(TABLES) == 16


@pytest.mark.parametrize("name", sorted(DIRECTLY_OWNED))
def test_directly_owned_tables_enforce_their_patient_by_foreign_key(name: str) -> None:
    table = TABLES[name]
    assert "patient_id" in table.c
    # Nullable only for pipeline_runs (runs that are not about a patient), guarded by a CHECK.
    assert table.c.patient_id.nullable is (name == "pipeline_runs")
    targets = {_fk_target(fk) for fk in _fks(table) if "patient_id" in fk.column_keys}
    assert targets == OWNERSHIP_FKS[name]


def test_pipeline_run_with_a_document_must_name_its_patient() -> None:
    checks = _checks(TABLES["pipeline_runs"])
    assert checks["ck_pipeline_runs_document_needs_patient"] == (
        "document_id IS NULL OR patient_id IS NOT NULL"
    )


@pytest.mark.parametrize(("name", "identity"), sorted(SUBTYPES.items()))
def test_subtypes_inherit_ownership_from_extracted_facts(name: str, identity: str) -> None:
    table = TABLES[name]
    assert "patient_id" not in table.c  # owned through the base row, not by a copy
    assert [c.name for c in table.primary_key.columns] == ["fact_id"]
    base_fks = [fk for fk in _fks(table) if fk.referred_table.name == "extracted_facts"]
    assert len(base_fks) == 1
    assert _fk_target(base_fks[0]) == ("extracted_facts", ("id",))
    assert base_fks[0].ondelete == "CASCADE"
    # And the ORM maps it as joined-table inheritance with the right discriminator value.
    configure_mappers()
    mapper = next(m for m in Base.registry.mappers if m.local_table is table)
    assert mapper.inherits is not None
    assert mapper.inherits.class_ is models.ExtractedFact
    assert mapper.polymorphic_identity == identity


def test_extracted_facts_base_owns_the_patient_for_all_subtypes() -> None:
    base = TABLES["extracted_facts"]
    assert base.c.patient_id.nullable is False
    assert ("documents", ("id", "patient_id")) in {_fk_target(fk) for fk in _fks(base)}
    # (id, patient_id) is unique, so review decisions and conflict members can reference it.
    uniques = {
        tuple(c.name for c in uq.columns)
        for uq in base.constraints
        if isinstance(uq, UniqueConstraint)
    }
    assert ("id", "patient_id") in uniques
    assert set(models.FactType) == set(SUBTYPES.values())


@pytest.mark.parametrize("name", sorted(REFERENCE))
def test_reference_tables_hold_no_patient_data(name: str) -> None:
    table = TABLES[name]
    assert "patient_id" not in table.c
    assert all(fk.referred_table.name in REFERENCE for fk in _fks(table))


# --- Integrity rules -----------------------------------------------------------------------------


def test_every_foreign_key_has_the_planned_delete_rule() -> None:
    actual = {
        (table.name, tuple(fk.column_keys)): fk.ondelete
        for table in TABLES.values()
        for fk in _fks(table)
    }
    assert actual == DELETE_RULES


def test_evidence_foreign_keys_point_to_existing_pages() -> None:
    for name in ("extracted_facts", "chunks"):
        targets = {_fk_target(fk) for fk in _fks(TABLES[name])}
        assert ("pages", ("document_id", "page_number")) in targets


@pytest.mark.parametrize(("key", "enum"), sorted(ENUM_COLUMNS.items()))
def test_enum_checks_match_the_python_enums(key: tuple[str, str], enum: type[StrEnum]) -> None:
    table, column = key
    sql = _checks(TABLES[table])[f"ck_{table}_{column}_values"]
    values = re.findall(r"'([^']*)'", sql)
    assert values == [member.value for member in enum]
    assert sql.startswith(f"{column} IN (")


@pytest.mark.parametrize(("table", "triple"), sorted(DATE_TRIPLES.items()))
def test_every_date_has_precision_pairing_and_normalization(
    table: str, triple: tuple[str, str, str]
) -> None:
    date, precision, raw = triple
    columns = TABLES[table].c
    for column in triple:
        assert columns[column].nullable  # unknown dates stay NULL; raw text is kept
    assert str(columns[date].type) == "DATE"
    assert str(columns[raw].type) == "TEXT"
    checks = _checks(TABLES[table])
    prefix = date
    assert re.findall(r"'([^']*)'", checks[f"ck_{table}_{prefix}_precision_values"]) == [
        "day",
        "month",
        "year",
    ]
    assert checks[f"ck_{table}_{prefix}_pair"] == f"({date} IS NULL) = ({precision} IS NULL)"
    normalized = checks[f"ck_{table}_{prefix}_normalized"]
    assert f"{precision} = 'month' AND extract(day FROM {date}) = 1" in normalized
    assert f"{precision} = 'year' AND extract(month FROM {date}) = 1" in normalized


def test_date_precision_values_are_day_month_year() -> None:
    assert [p.value for p in models.DatePrecision] == ["day", "month", "year"]


@pytest.mark.parametrize("key", sorted(NO_DEFAULT_COLUMNS))
def test_semantic_fields_have_no_default(key: tuple[str, str]) -> None:
    table, column = key
    col = TABLES[table].c[column]
    assert col.nullable is False
    assert col.server_default is None and col.default is None


def test_review_decisions_have_no_database_level_append_only_guard() -> None:
    # Documented limitation (C5): append-only is enforced by the application only. If a trigger
    # or role-based guard is added later, update this test and the docs together.
    assert "append-only" in (models.ReviewDecision.__doc__ or "").lower()
    assert "does not" in (models.ReviewDecision.__doc__ or "")


def test_clinical_values_are_exact_numeric_never_float() -> None:
    for table in TABLES.values():
        for column in table.c:
            assert "FLOAT" not in str(column.type).upper()
            assert "REAL" not in str(column.type).upper()


# --- Names and DDL -------------------------------------------------------------------------------


def _all_names() -> list[str]:
    names = []
    for table in TABLES.values():
        names += [str(c.name) for c in table.constraints]
        names += [str(i.name) for i in table.indexes]
    return names


def test_constraint_and_index_names_follow_the_convention_and_fit_postgres() -> None:
    names = _all_names()
    assert len(names) == len(set(names))
    for name in names:
        assert re.match(r"^(pk|fk|uq|ck|ix)_[a-z0-9_]+$", name), name
        assert len(name) <= 63, name


def test_postgres_ddl_compiles_with_the_planned_types() -> None:
    dialect = postgresql.dialect()
    ddl = {name: str(CreateTable(t).compile(dialect=dialect)) for name, t in TABLES.items()}
    for name, sql in ddl.items():
        if "id" in TABLES[name].c and name not in REFERENCE:
            assert "id UUID DEFAULT uuidv7() NOT NULL" in sql, name
    assert "embedding VECTOR(384)" in ddl["chunks"]
    assert (
        "GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, content)) STORED" in (ddl["chunks"])
    )
    assert "GENERATED ALWAYS AS IDENTITY" in ddl["analyte_catalog"]
    assert "is_synthetic BOOLEAN NOT NULL" in ddl["patients"]
    assert "sha256 TEXT NOT NULL" in ddl["documents"]
    index_sql = {
        str(i.name): str(CreateIndex(i).compile(dialect=dialect))
        for t in TABLES.values()
        for i in t.indexes
    }
    assert "USING gin (tsv)" in index_sql["ix_chunks_tsv"]
    assert (
        "WHERE duplicate_group_id IS NOT NULL"
        in (index_sql["ix_extracted_facts_duplicate_group_id"])
    )
    assert models.EMBEDDING_DIMENSIONS == 384


def test_no_vector_index_yet() -> None:
    # Exact search filtered by patient at personal scale; HNSW only if Phase 5 measures a need.
    assert all("embedding" not in [c.name for c in i.columns] for i in TABLES["chunks"].indexes)
