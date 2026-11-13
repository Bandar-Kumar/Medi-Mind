"""Schema integrity tests against the real schema (Task 1.7). Run ONLY in ``medimind_test``.

Every test runs inside one transaction that is rolled back at the end, so no rows persist.
Expected violations run inside a SAVEPOINT and are identified by SQLSTATE:
23505 unique, 23503 foreign key, 23001 restrict (ON DELETE RESTRICT), 23514 check,
23502 not null, 22xxx data exception.
All data is fictional.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.records import db
from app.records.models import ExtractedFact, MedicationMention, Observation
from tests.integration._guards import assert_is_test_database, rolled_back_connection

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

SHA_A = "a" * 64
SHA_B = "b" * 64


@pytest.fixture
def conn(test_engine: Engine, app_settings: Settings, schema_at_head: None) -> Iterator[Connection]:
    # Outer transaction first, then the database-name guard, then the test; always rolled back.
    with rolled_back_connection(test_engine, app_settings.db_name) as connection:
        yield connection


def _sqlstate(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> str | None:
    """Run ``sql`` in a savepoint; return the SQLSTATE of the failure, or None on success."""
    savepoint = conn.begin_nested()
    try:
        conn.execute(text(sql), params or {})
    except DBAPIError as exc:
        savepoint.rollback()
        return getattr(exc.orig, "sqlstate", None)
    savepoint.commit()
    return None


def _one(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one()


def patient(conn: Connection, name: str = "Test Patient (fictional)") -> uuid.UUID:
    return _one(
        conn,
        "INSERT INTO patients (display_name, is_synthetic) VALUES (:n, true) RETURNING id",
        {"n": name},
    )


def document(conn: Connection, patient_id: uuid.UUID, sha: str = SHA_A, **extra: Any) -> uuid.UUID:
    columns = {
        "patient_id": patient_id,
        "original_filename": "report.pdf",
        "storage_path": f"uploads/{uuid.uuid4()}.pdf",
        "sha256": sha,
        "mime_type": "application/pdf",
        "size_bytes": 1024,
        **extra,
    }
    names = ", ".join(columns)
    values = ", ".join(f":{k}" for k in columns)
    return _one(conn, f"INSERT INTO documents ({names}) VALUES ({values}) RETURNING id", columns)


def page(conn: Connection, patient_id: uuid.UUID, document_id: uuid.UUID, number: int = 1) -> None:
    conn.execute(
        text(
            "INSERT INTO pages (patient_id, document_id, page_number, text, parse_method)"
            " VALUES (:p, :d, :n, 'Haemoglobin 13.5 g/dL', 'native')"
        ),
        {"p": patient_id, "d": document_id, "n": number},
    )


FACT_SQL = (
    "INSERT INTO extracted_facts (patient_id, document_id, fact_type, page_number,"
    " source_excerpt, assertion_type, negated, subject, extractor_version, prompt_version,"
    " model_id{extra_cols}) VALUES (:p, :d, :t, :n, 'Haemoglobin 13.5 g/dL', 'direct_fact',"
    " false, 'patient', 'x1', 'p1', 'm1'{extra_vals}) RETURNING id"
)


def fact(
    conn: Connection,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    fact_type: str = "observation",
    page_number: int = 1,
) -> uuid.UUID:
    return _one(
        conn,
        FACT_SQL.format(extra_cols="", extra_vals=""),
        {"p": patient_id, "d": document_id, "t": fact_type, "n": page_number},
    )


@pytest.fixture
def setup(conn: Connection) -> dict[str, uuid.UUID]:
    """Two fictional patients, each with one document and one page; one fact for patient A."""
    a, b = patient(conn, "Patient A (fictional)"), patient(conn, "Patient B (fictional)")
    doc_a, doc_b = document(conn, a, SHA_A), document(conn, b, SHA_B)
    page(conn, a, doc_a)
    page(conn, b, doc_b)
    fact_a = fact(conn, a, doc_a)
    return {"a": a, "b": b, "doc_a": doc_a, "doc_b": doc_b, "fact_a": fact_a}


# --- Uniqueness ----------------------------------------------------------------------------------


def test_duplicate_upload_for_the_same_patient_is_rejected(conn: Connection, setup) -> None:
    sql = (
        "INSERT INTO documents (patient_id, original_filename, storage_path, sha256, mime_type,"
        " size_bytes) VALUES (:p, 'copy.pdf', 'uploads/copy.pdf', :s, 'application/pdf', 1)"
    )
    assert _sqlstate(conn, sql, {"p": setup["a"], "s": SHA_A}) == "23505"
    assert _sqlstate(conn, sql, {"p": setup["b"], "s": SHA_A}) is None  # other patient: allowed


# --- Patient isolation through composite foreign keys -------------------------------------------


def test_page_cannot_claim_another_patients_document(conn: Connection, setup) -> None:
    sql = (
        "INSERT INTO pages (patient_id, document_id, page_number, text, parse_method)"
        " VALUES (:p, :d, 2, 'x', 'native')"
    )
    assert _sqlstate(conn, sql, {"p": setup["b"], "d": setup["doc_a"]}) == "23503"


def test_fact_must_cite_an_existing_page(conn: Connection, setup) -> None:
    sql = FACT_SQL.format(extra_cols="", extra_vals="")
    params = {"p": setup["a"], "d": setup["doc_a"], "t": "observation", "n": 99}
    assert _sqlstate(conn, sql, params) == "23503"


def test_fact_cannot_claim_another_patients_document(conn: Connection, setup) -> None:
    sql = FACT_SQL.format(extra_cols="", extra_vals="")
    params = {"p": setup["b"], "d": setup["doc_a"], "t": "observation", "n": 1}
    assert _sqlstate(conn, sql, params) == "23503"


def test_review_decision_must_belong_to_the_facts_patient(conn: Connection, setup) -> None:
    sql = "INSERT INTO review_decisions (patient_id, fact_id, decision) VALUES (:p, :f, 'verify')"
    assert _sqlstate(conn, sql, {"p": setup["b"], "f": setup["fact_a"]}) == "23503"
    assert _sqlstate(conn, sql, {"p": setup["a"], "f": setup["fact_a"]}) is None


def test_conflict_cannot_link_another_patients_fact(conn: Connection, setup) -> None:
    conflict_b = _one(
        conn,
        "INSERT INTO conflicts (patient_id, conflict_type, description, detected_by_rule)"
        " VALUES (:p, 'other', 'test conflict', 'rule-test') RETURNING id",
        {"p": setup["b"]},
    )
    sql = "INSERT INTO conflict_members (conflict_id, fact_id, patient_id) VALUES (:c, :f, :p)"
    assert _sqlstate(conn, sql, {"c": conflict_b, "f": setup["fact_a"], "p": setup["b"]}) == (
        "23503"
    )
    assert _sqlstate(conn, sql, {"c": conflict_b, "f": setup["fact_a"], "p": setup["a"]}) == (
        "23503"
    )


def test_subtype_row_needs_a_base_row(conn: Connection) -> None:
    sql = "INSERT INTO observations (fact_id, analyte_raw, value_raw) VALUES (:f, 'Hb', '13.5')"
    assert _sqlstate(conn, sql, {"f": uuid.uuid4()}) == "23503"


def test_pipeline_run_with_a_document_needs_its_patient(conn: Connection, setup) -> None:
    sql = (
        "INSERT INTO pipeline_runs (document_id, patient_id, stage, status)"
        " VALUES (:d, :p, 'parse', 'running')"
    )
    assert _sqlstate(conn, sql, {"d": setup["doc_a"], "p": None}) == "23514"
    assert _sqlstate(conn, sql, {"d": setup["doc_a"], "p": setup["b"]}) == "23503"
    assert _sqlstate(conn, sql, {"d": setup["doc_a"], "p": setup["a"]}) is None


# --- Deletion behaviour --------------------------------------------------------------------------


def test_deleting_a_patient_with_documents_is_blocked(conn: Connection, setup) -> None:
    delete = "DELETE FROM patients WHERE id = :p"
    assert _sqlstate(conn, delete, {"p": setup["a"]}) == "23001"  # RESTRICT -> restrict_violation
    # The failed delete changed nothing.
    assert _one(conn, "SELECT count(*) FROM patients WHERE id = :p", {"p": setup["a"]}) == 1
    assert _one(conn, "SELECT count(*) FROM documents WHERE id = :d", {"d": setup["doc_a"]}) == 1


def test_version_links_stay_within_a_patient_and_cannot_be_broken(conn: Connection, setup) -> None:
    newer = document(conn, setup["a"], "c" * 64, supersedes_document_id=setup["doc_a"])
    assert newer is not None
    delete = "DELETE FROM documents WHERE id = :d"
    assert _sqlstate(conn, delete, {"d": setup["doc_a"]}) == "23001"  # superseded: blocked
    # The failed delete changed nothing: both documents and the version link survive.
    assert (
        _one(conn, "SELECT supersedes_document_id FROM documents WHERE id = :n", {"n": newer})
        == setup["doc_a"]
    )
    assert _one(conn, "SELECT count(*) FROM documents WHERE id = :d", {"d": setup["doc_a"]}) == 1
    # Each check below breaks exactly ONE rule. (PostgreSQL checks UNIQUE while inserting the row
    # and foreign keys at the end of the statement, so a row breaking both reports 23505.)
    link = (
        "INSERT INTO documents (patient_id, original_filename, storage_path, sha256, mime_type,"
        " size_bytes, supersedes_document_id) VALUES (:p, :name, :path, :s, 'application/pdf', 1,"
        " :old)"
    )
    # 1. Linear chain: doc_a already has a successor, so a second one is rejected (unique).
    second = {
        "p": setup["a"],
        "name": "y.pdf",
        "path": "uploads/y.pdf",
        "s": "e" * 64,
        "old": setup["doc_a"],
    }
    assert _sqlstate(conn, link, second) == "23505"
    # 2. Cross-patient: B's document cannot supersede A's document that has no successor yet.
    spare_a = document(conn, setup["a"], "f" * 64)
    cross = {
        "p": setup["b"],
        "name": "x.pdf",
        "path": "uploads/x.pdf",
        "s": "d" * 64,
        "old": spare_a,
    }
    assert _sqlstate(conn, link, cross) == "23503"
    # Neither rejected insert left a row behind.
    assert (
        _one(
            conn,
            "SELECT count(*) FROM documents WHERE sha256 IN (:e, :d)",
            {"e": "e" * 64, "d": "d" * 64},
        )
        == 0
    )


def test_deleting_a_document_cascades_to_everything_derived(conn: Connection, setup) -> None:
    a, doc_a, fact_a = setup["a"], setup["doc_a"], setup["fact_a"]
    conn.execute(
        text(
            "INSERT INTO observations (fact_id, analyte_raw, value_raw) VALUES (:f, 'Hb', '13.5')"
        ),
        {"f": fact_a},
    )
    conn.execute(
        text(
            "INSERT INTO review_decisions (patient_id, fact_id, decision) VALUES (:p, :f, 'verify')"
        ),
        {"p": a, "f": fact_a},
    )
    conflict = _one(
        conn,
        "INSERT INTO conflicts (patient_id, conflict_type, description, detected_by_rule)"
        " VALUES (:p, 'other', 'test', 'rule-test') RETURNING id",
        {"p": a},
    )
    conn.execute(
        text("INSERT INTO conflict_members (conflict_id, fact_id, patient_id) VALUES (:c, :f, :p)"),
        {"c": conflict, "f": fact_a, "p": a},
    )
    conn.execute(
        text(
            "INSERT INTO chunks (patient_id, document_id, page_number, chunk_index, chunk_type,"
            " content) VALUES (:p, :d, 1, 0, 'text', 'Haemoglobin 13.5 g/dL')"
        ),
        {"p": a, "d": doc_a},
    )
    conn.execute(
        text(
            "INSERT INTO pipeline_runs (patient_id, document_id, stage, status, finished_at)"
            " VALUES (:p, :d, 'parse', 'succeeded', now())"
        ),
        {"p": a, "d": doc_a},
    )

    conn.execute(text("DELETE FROM documents WHERE id = :d"), {"d": doc_a})

    counts = {
        table: _one(conn, f"SELECT count(*) FROM {table} WHERE patient_id = :p", {"p": a})
        for table in (
            "pages",
            "chunks",
            "extracted_facts",
            "review_decisions",
            "conflict_members",
            "pipeline_runs",
        )
    }
    assert counts == dict.fromkeys(counts, 0)
    assert _one(conn, "SELECT count(*) FROM observations WHERE fact_id = :f", {"f": fact_a}) == 0
    # The conflict itself stays (closing it is Phase 6 application logic), now with no members.
    assert _one(conn, "SELECT count(*) FROM conflicts WHERE id = :c", {"c": conflict}) == 1
    # Patient B is untouched.
    assert _one(conn, "SELECT count(*) FROM pages WHERE patient_id = :p", {"p": setup["b"]}) == 1


# --- CHECK and NOT NULL rules -----------------------------------------------------------------


def test_semantic_fields_must_be_stated_explicitly(conn: Connection, setup) -> None:
    assert _sqlstate(conn, "INSERT INTO patients (display_name) VALUES ('x')") == "23502"
    sql = (
        "INSERT INTO extracted_facts (patient_id, document_id, fact_type, page_number,"
        " source_excerpt, negated, subject, extractor_version, prompt_version, model_id)"
        " VALUES (:p, :d, 'observation', 1, 'x', false, 'patient', 'x1', 'p1', 'm1')"
    )
    assert _sqlstate(conn, sql, {"p": setup["a"], "d": setup["doc_a"]}) == "23502"


@pytest.mark.parametrize(
    ("date", "precision", "expected"),
    [
        (None, None, None),  # unknown date: allowed, raw text kept elsewhere
        ("2024-03-15", "day", None),
        ("2024-03-01", "month", None),
        ("2024-01-01", "year", None),
        ("2024-03-15", None, "23514"),  # date without precision
        (None, "day", "23514"),  # precision without date
        ("2024-03-15", "month", "23514"),  # month precision must be day 1
        ("2024-03-01", "year", "23514"),  # year precision must be 1 January
        ("2024-03-15", "week", "23514"),  # not an allowed precision
    ],
)
def test_date_precision_rules(
    conn: Connection, setup, date: str | None, precision: str | None, expected: str | None
) -> None:
    sql = (
        "INSERT INTO observations (fact_id, analyte_raw, value_raw, effective_date,"
        " date_precision, date_raw) VALUES (:f, 'Hb', '13.5', CAST(:dt AS date), :pr, 'as printed')"
    )
    assert _sqlstate(conn, sql, {"f": setup["fact_a"], "dt": date, "pr": precision}) == expected


@pytest.mark.parametrize(
    ("table", "date_col"),
    [
        ("documents", "document_date"),
        ("medication_mentions", "mention_date"),
        ("allergy_mentions", "mention_date"),
        ("clinical_events", "event_date"),
    ],
)
def test_every_date_pair_rejects_unnormalized_month_dates(
    conn: Connection, setup, table: str, date_col: str
) -> None:
    precision = "document_date_precision" if table == "documents" else "date_precision"
    if table == "documents":
        sql = f"UPDATE documents SET {date_col} = '2024-03-15', {precision} = 'month' WHERE id = :x"
        params = {"x": setup["doc_a"]}
    else:
        required = {
            "medication_mentions": ("name_raw, mention_context", "'Drug X', 'prescribed'"),
            "allergy_mentions": ("substance_raw", "'Substance X'"),
            "clinical_events": ("event_type, description_raw", "'consultation', 'Visit'"),
        }[table]
        sql = (
            f"INSERT INTO {table} (fact_id, {required[0]}, {date_col}, {precision})"
            f" VALUES (:x, {required[1]}, '2024-03-15', 'month')"
        )
        params = {"x": setup["fact_a"]}
    assert _sqlstate(conn, sql, params) == "23514"


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("UPDATE documents SET doc_type = 'x-ray' WHERE id = :d", "23514"),
        ("UPDATE documents SET sha256 = 'NOT-HEX' WHERE id = :d", "23514"),
        ("UPDATE documents SET supersedes_document_id = id WHERE id = :d", "23514"),
        ("UPDATE extracted_facts SET review_status = 'needs_review' WHERE id = :f", "23514"),
        (
            "UPDATE extracted_facts SET review_status = 'needs_review',"
            " review_reasons = ARRAY['ambiguous_date'] WHERE id = :f",
            None,
        ),
        ("UPDATE extracted_facts SET source_excerpt = '   ' WHERE id = :f", "23514"),
    ],
)
def test_check_constraints(conn: Connection, setup, sql: str, expected: str | None) -> None:
    assert _sqlstate(conn, sql, {"d": setup["doc_a"], "f": setup["fact_a"]}) == expected


def test_observation_value_rules(conn: Connection, setup) -> None:
    sql = (
        "INSERT INTO observations (fact_id, analyte_raw, value_raw, value_num, comparator,"
        " unit_norm, value_num_norm, ref_low, ref_high)"
        " VALUES (:f, 'Hb', :raw, :num, :cmp, :unit, :norm, :lo, :hi)"
    )
    base = {"f": setup["fact_a"], "raw": "<5", "num": None, "cmp": None, "unit": None}
    base |= {"norm": None, "lo": None, "hi": None}
    assert _sqlstate(conn, sql, {**base, "cmp": "<"}) == "23514"  # comparator needs a number
    assert _sqlstate(conn, sql, {**base, "norm": 5}) == "23514"  # normalized value needs unit
    assert _sqlstate(conn, sql, {**base, "lo": 10, "hi": 5}) == "23514"  # reversed range
    assert _sqlstate(conn, sql, {**base, "num": 5, "cmp": "<"}) is None


def test_pipeline_run_status_and_time_consistency(conn: Connection, setup) -> None:
    sql = (
        "INSERT INTO pipeline_runs (patient_id, stage, status, finished_at)"
        " VALUES (:p, 'parse', :s, CAST(:f AS timestamptz))"
    )
    p = setup["a"]
    assert _sqlstate(conn, sql, {"p": p, "s": "running", "f": "2026-01-01"}) == "23514"
    assert _sqlstate(conn, sql, {"p": p, "s": "succeeded", "f": None}) == "23514"
    assert _sqlstate(conn, sql, {"p": p, "s": "succeeded", "f": "2000-01-01"}) == "23514"
    assert _sqlstate(conn, sql, {"p": p, "s": "running", "f": None}) is None


# --- Retrieval columns ------------------------------------------------------------------------


def test_embedding_rules_and_generated_full_text(conn: Connection, setup) -> None:
    insert = (
        "INSERT INTO chunks (patient_id, document_id, page_number, chunk_index, chunk_type,"
        " content, embedding, embedding_model)"
        " VALUES (:p, :d, 1, :i, 'text', 'Haemoglobin 13.5 g/dL', CAST(:e AS vector), :m)"
    )
    vector_384 = "[" + ",".join(["0.1"] * 384) + "]"
    params = {"p": setup["a"], "d": setup["doc_a"]}
    assert _sqlstate(conn, insert, {**params, "i": 0, "e": vector_384, "m": None}) == "23514"
    wrong_dimension = _sqlstate(conn, insert, {**params, "i": 1, "e": "[1,2,3]", "m": "bge"})
    assert wrong_dimension is not None and wrong_dimension.startswith("22")
    assert _sqlstate(conn, insert, {**params, "i": 2, "e": vector_384, "m": "bge-small"}) is None
    matches = _one(
        conn,
        "SELECT count(*) FROM chunks WHERE tsv @@ to_tsquery('simple', 'haemoglobin')"
        " AND document_id = :d",
        {"d": setup["doc_a"]},
    )
    assert matches == 1


def test_uuidv7_ids_are_time_ordered(conn: Connection) -> None:
    ids = [patient(conn, f"Order test {i} (fictional)") for i in range(5)]
    assert ids == sorted(ids)
    assert all(i.version == 7 for i in ids)


# --- Joined-table inheritance through the ORM ----------------------------------------------------


def test_orm_joined_inheritance_writes_base_and_subtype_rows(conn: Connection, setup) -> None:
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    common = {
        "patient_id": setup["a"],
        "document_id": setup["doc_a"],
        "page_number": 1,
        "source_excerpt": "Haemoglobin 13.5 g/dL",
        "assertion_type": "direct_fact",
        "negated": False,
        "subject": "patient",
        "extractor_version": "x1",
        "prompt_version": "p1",
        "model_id": "m1",
    }
    obs = Observation(**common, analyte_raw="Haemoglobin", value_raw="13.5")
    med = MedicationMention(**common, name_raw="Drug X (fictional)", mention_context="prescribed")
    session.add_all([obs, med])
    session.flush()

    assert _one(conn, "SELECT fact_type FROM extracted_facts WHERE id = :i", {"i": obs.id}) == (
        "observation"
    )
    assert _one(conn, "SELECT count(*) FROM observations WHERE fact_id = :i", {"i": obs.id}) == 1
    assert _one(conn, "SELECT fact_type FROM extracted_facts WHERE id = :i", {"i": med.id}) == (
        "medication"
    )
    # Querying the base, scoped by patient, returns the right subclasses only for that patient.
    session.expunge_all()
    found = session.scalars(
        select(ExtractedFact)
        .where(ExtractedFact.patient_id == setup["a"])
        .where(ExtractedFact.id.in_([obs.id, med.id]))
    ).all()
    assert {type(f) for f in found} == {Observation, MedicationMention}
    assert (
        session.scalars(select(ExtractedFact).where(ExtractedFact.patient_id == setup["b"])).all()
        == []
    )
    session.close()


# --- Documented database-level gaps (C5, C13): asserted so they stay visible ---------------------


def test_known_gap_subtype_table_is_not_checked_against_fact_type(conn: Connection, setup) -> None:
    # A 'medication' base row with an observations subtype row is NOT rejected by the database.
    # The ORM's joined inheritance always writes matching rows; repositories (Task 1.8) use it.
    medication_fact = fact(conn, setup["a"], setup["doc_a"], fact_type="medication")
    sql = "INSERT INTO observations (fact_id, analyte_raw, value_raw) VALUES (:f, 'Hb', '1')"
    assert _sqlstate(conn, sql, {"f": medication_fact}) is None


def test_known_gap_review_decisions_are_append_only_by_application_only(
    conn: Connection, setup
) -> None:
    decision = _one(
        conn,
        "INSERT INTO review_decisions (patient_id, fact_id, decision) VALUES (:p, :f, 'verify')"
        " RETURNING id",
        {"p": setup["a"], "f": setup["fact_a"]},
    )
    # The database does not (yet) prevent UPDATE or DELETE; documented in the model and README.
    assert (
        _sqlstate(conn, "UPDATE review_decisions SET note = 'x' WHERE id = :i", {"i": decision})
        is None
    )
    assert _sqlstate(conn, "DELETE FROM review_decisions WHERE id = :i", {"i": decision}) is None


# --- Transaction rollback through the app's session_scope ---------------------------------------


def test_session_scope_rolls_back_on_error(
    test_engine: Engine, app_settings: Settings, schema_at_head: None
) -> None:
    marker = f"Rollback test {uuid.uuid4()} (fictional)"
    factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    with test_engine.connect() as connection:
        assert_is_test_database(connection, app_settings.db_name)
    with pytest.raises(RuntimeError, match="simulated failure"):
        with db.session_scope(factory) as session:
            session.execute(
                text("INSERT INTO patients (display_name, is_synthetic) VALUES (:n, true)"),
                {"n": marker},
            )
            raise RuntimeError("simulated failure")
    with test_engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM patients WHERE display_name = :n"), {"n": marker}
        ).scalar_one()
    assert count == 0
