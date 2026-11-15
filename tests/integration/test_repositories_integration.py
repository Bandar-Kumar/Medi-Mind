"""Integration tests for the parse-claim repositories (Task 2.5a) against ``medimind_test``.

Every repository session goes through ``test_db_session_factory``: each connection's database is
checked to be ``medimind_test`` before any statement runs, and the application database must be
unchanged afterwards (``main_database_unchanged``). Rows are created per test for a fictional
patient and deleted afterwards. All text is fictional.
"""

import datetime as dt
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Engine, text

from app.core.config import Settings
from app.core.errors import DocumentProcessingError, PatientError
from app.records import repositories as repo
from app.records.repositories import PageRecord
from tests.integration._guards import TEST_DATABASE, assert_is_test_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

FFFD = chr(0xFFFD)
SUPPLEMENTARY = chr(0x1D707)
JOIN_TIMEOUT = 15


@pytest.fixture
def sql(test_engine: Engine, app_settings: Settings) -> Callable[..., list]:
    """Run one guarded statement in ``medimind_test`` (committed); returns all rows, if any."""

    def run(statement: str, **params: object) -> list:
        with test_engine.begin() as connection:
            assert_is_test_database(connection, app_settings.db_name)
            result = connection.execute(text(statement), params)
            return list(result) if result.returns_rows else []

    return run


def _sha() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def _create(
    patient: uuid.UUID, sha: str | None = None, pages: int = 2, **kw
) -> repo.CreatedDocument:
    sha = sha or _sha()
    arguments = {
        "original_filename": "fictional-report.pdf",
        "storage_path": f"uploads/{patient}/{sha}.pdf",
        "sha256": sha,
        "size_bytes": 1234,
        "page_count": pages,
    } | kw
    return repo.create_document(patient, **arguments)


def _pages(*texts: str) -> tuple[PageRecord, ...]:
    return tuple(
        PageRecord(page_number=n, text=t, parse_method="native", has_tables=False)
        for n, t in enumerate(texts, start=1)
    )


def _claim(patient: uuid.UUID, document: uuid.UUID, **kw) -> repo.ParseClaim:
    return repo.claim_document_for_parsing(patient, document, component_versions={"t": "1"}, **kw)


def _backdate(sql, run_id: uuid.UUID, seconds: float) -> None:
    sql(
        "UPDATE pipeline_runs SET started_at = now() - make_interval(secs => :s) WHERE id = :r",
        s=seconds,
        r=run_id,
    )


def _status(sql, document: uuid.UUID) -> str:
    return sql("SELECT processing_status FROM documents WHERE id = :d", d=document)[0][0]


def _run_row(sql, run_id: uuid.UUID) -> tuple:
    return sql(
        "SELECT status, error_kind, error_class, finished_at IS NOT NULL, duration_ms "
        "FROM pipeline_runs WHERE id = :r",
        r=run_id,
    )[0]


def _page_rows(sql, document: uuid.UUID) -> list:
    return sql(
        "SELECT page_number, text, parse_method, has_tables FROM pages "
        "WHERE document_id = :d ORDER BY page_number",
        d=document,
    )


def _expect(kind: str, call: Callable[[], object]) -> DocumentProcessingError:
    with pytest.raises(DocumentProcessingError) as caught:
        call()
    assert caught.value.kind == kind, f"expected {kind}, got {caught.value.kind}"
    return caught.value


# --- database identity ---------------------------------------------------------------------------


def test_every_repository_connection_is_to_the_test_database(
    test_db_session_factory, test_patient
) -> None:
    _, log = test_db_session_factory
    _create(test_patient)
    assert log.databases and set(log.databases) == {TEST_DATABASE}


# --- create_document -----------------------------------------------------------------------------


def test_create_then_duplicate_returns_the_same_row(test_patient, sql) -> None:
    first = _create(test_patient)
    again = _create(test_patient, first.document.sha256)
    assert first.created is True and again.created is False
    assert again.document == first.document
    assert first.document.processing_status == "uploaded" and first.document.page_count == 2
    assert sql("SELECT count(*) FROM documents WHERE patient_id = :p", p=test_patient)[0][0] == 1


def test_duplicate_with_a_different_storage_path_or_size_is_a_mismatch(test_patient) -> None:
    first = _create(test_patient)
    sha = first.document.sha256
    _expect("integrity_mismatch", lambda: _create(test_patient, sha, size_bytes=99))
    _expect(
        "integrity_mismatch",
        lambda: _create(test_patient, sha, storage_path=f"uploads/{test_patient}/other.pdf"),
    )


def test_duplicate_race_takes_the_savepoint_path(test_patient, monkeypatch) -> None:
    first = _create(test_patient)
    real_find = repo._find_by_sha256
    calls = {"n": 0}

    def miss_once(session, patient_id, sha256):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(session, patient_id, sha256)

    monkeypatch.setattr(repo, "_find_by_sha256", miss_once)
    again = _create(test_patient, first.document.sha256)  # the insert hits the unique key
    assert again.created is False and again.document.id == first.document.id
    assert calls["n"] == 2


def test_concurrent_identical_uploads_create_one_row(test_patient, sql) -> None:
    sha = _sha()
    barrier = threading.Barrier(2)
    results: list[repo.CreatedDocument] = []
    errors: list[BaseException] = []

    def upload() -> None:
        try:
            barrier.wait(timeout=JOIN_TIMEOUT)
            results.append(_create(test_patient, sha))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=upload) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT)
    assert not errors and len(results) == 2
    assert sorted(r.created for r in results) == [False, True]
    assert results[0].document.id == results[1].document.id
    assert sql("SELECT count(*) FROM documents WHERE sha256 = :s", s=sha)[0][0] == 1


# --- claims, locking and stale recovery ----------------------------------------------------------


def test_claim_sets_processing_and_inserts_a_running_parse_run(test_patient, sql) -> None:
    document = _create(test_patient).document
    claim = _claim(test_patient, document.id)
    assert claim.document.processing_status == "processing" and claim.recovered_run_id is None
    assert _status(sql, document.id) == "processing"
    row = sql(
        "SELECT stage, status, component_versions FROM pipeline_runs WHERE id = :r",
        r=claim.run_id,
    )[0]
    assert (row[0], row[1], row[2]) == ("parse", "running", {"t": "1"})


def test_concurrent_claims_have_exactly_one_winner(test_patient, sql) -> None:
    document = _create(test_patient).document
    barrier = threading.Barrier(2)
    claims: list[repo.ParseClaim] = []
    errors: list[BaseException] = []

    def claim() -> None:
        try:
            barrier.wait(timeout=JOIN_TIMEOUT)
            claims.append(_claim(test_patient, document.id))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT)
    assert len(claims) == 1 and len(errors) == 1
    assert isinstance(errors[0], DocumentProcessingError)
    assert errors[0].kind == "already_running"
    running = sql(
        "SELECT count(*) FROM pipeline_runs WHERE document_id = :d AND status = 'running'",
        d=document.id,
    )
    assert running[0][0] == 1


def test_claim_waits_for_the_document_row_lock(
    test_patient, test_engine: Engine, app_settings: Settings
) -> None:
    document = _create(test_patient).document
    outcome: list[object] = []
    with test_engine.connect() as holder:
        transaction = holder.begin()
        try:
            assert_is_test_database(holder, app_settings.db_name)
            holder.execute(
                text("SELECT id FROM documents WHERE id = :d FOR UPDATE"), {"d": document.id}
            )
            worker = threading.Thread(
                target=lambda: outcome.append(_claim(test_patient, document.id))
            )
            worker.start()
            time.sleep(1.0)
            assert worker.is_alive() and outcome == []  # blocked on SELECT ... FOR UPDATE
        finally:
            transaction.rollback()  # releases the lock in every case
    worker.join(JOIN_TIMEOUT)
    assert not worker.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], repo.ParseClaim)


def test_stale_claim_constant_is_1200_seconds() -> None:
    assert repo.STALE_CLAIM_SECONDS == 1200


def test_claim_just_inside_the_stale_boundary_is_still_active(test_patient, sql) -> None:
    document = _create(test_patient).document
    first = _claim(test_patient, document.id)
    _backdate(sql, first.run_id, 1198)  # < 1200 s old: active, even with recovery requested
    _expect("already_running", lambda: _claim(test_patient, document.id, recover_stale=True))
    assert _run_row(sql, first.run_id)[0] == "running"


def test_claim_past_the_stale_boundary_needs_explicit_recovery(test_patient, sql) -> None:
    document = _create(test_patient).document
    first = _claim(test_patient, document.id)
    _backdate(sql, first.run_id, 1201)
    error = _expect("already_running", lambda: _claim(test_patient, document.id))
    assert "stale" in str(error)
    assert _run_row(sql, first.run_id)[0] == "running"  # never recovered automatically
    second = _claim(test_patient, document.id, recover_stale=True)
    assert second.recovered_run_id == first.run_id and second.run_id != first.run_id
    status, kind, error_class, finished, duration = _run_row(sql, first.run_id)
    assert (status, kind, error_class, finished) == ("failed", "abandoned", None, True)
    assert duration >= 1201 * 1000 - 5000
    assert _run_row(sql, second.run_id)[0] == "running"


def test_processing_without_a_run_needs_explicit_recovery(test_patient, sql) -> None:
    document = _create(test_patient).document
    sql("UPDATE documents SET processing_status = 'processing' WHERE id = :d", d=document.id)
    _expect("already_running", lambda: _claim(test_patient, document.id))
    claim = _claim(test_patient, document.id, recover_stale=True)
    assert claim.recovered_run_id is None


def test_an_old_worker_cannot_write_after_its_claim_was_recovered(test_patient, sql) -> None:
    document = _create(test_patient).document
    old = _claim(test_patient, document.id)
    _backdate(sql, old.run_id, 1201)
    new = _claim(test_patient, document.id, recover_stale=True)
    _expect(
        "claim_lost",
        lambda: repo.persist_parsed_pages(test_patient, document.id, old.run_id, _pages("a", "b")),
    )
    assert (
        repo.record_parse_failure(test_patient, document.id, old.run_id, error_kind="timeout")
        is False
    )
    assert _status(sql, document.id) == "processing" and _page_rows(sql, document.id) == []
    assert _run_row(sql, old.run_id)[:2] == ("failed", "abandoned")  # unchanged by the old worker
    assert repo.persist_parsed_pages(test_patient, document.id, new.run_id, _pages("a", "b")) == 2
    assert _run_row(sql, new.run_id)[0] == "succeeded"


# --- persist_parsed_pages ------------------------------------------------------------------------


def test_pages_are_stored_exactly_and_the_run_succeeds(test_patient, sql) -> None:
    document = _create(test_patient, pages=4).document
    claim = _claim(test_patient, document.id)
    texts = ("  Haemoglobin  13.5\n\n", f"TSH {FFFD} {SUPPLEMENTARY}", "", "Glucose 92 \t")
    records = (
        PageRecord(1, texts[0], "native", True),
        PageRecord(2, texts[1], "native", False),
        PageRecord(3, texts[2], "native", False),
        PageRecord(4, texts[3], "ocr", False),
    )
    assert repo.persist_parsed_pages(test_patient, document.id, claim.run_id, records) == 4
    assert _page_rows(sql, document.id) == [
        (1, texts[0], "native", True),
        (2, texts[1], "native", False),
        (3, texts[2], "native", False),
        (4, texts[3], "ocr", False),
    ]
    status, kind, _, finished, duration = _run_row(sql, claim.run_id)
    assert (status, kind, finished) == ("succeeded", None, True) and duration >= 0
    # Parsing is not the whole pipeline: the document stays `processing` (ARCHITECTURE §4.1).
    assert _status(sql, document.id) == "processing"


def test_a_parsed_document_cannot_be_claimed_or_written_again(test_patient, sql) -> None:
    document = _create(test_patient).document
    claim = _claim(test_patient, document.id)
    repo.persist_parsed_pages(test_patient, document.id, claim.run_id, _pages("a", "b"))
    before = _page_rows(sql, document.id)
    _expect("already_parsed", lambda: _claim(test_patient, document.id))
    _expect("already_parsed", lambda: _claim(test_patient, document.id, recover_stale=True))
    _expect(
        "claim_lost",
        lambda: repo.persist_parsed_pages(
            test_patient, document.id, claim.run_id, _pages("x", "y")
        ),
    )
    assert _page_rows(sql, document.id) == before


def test_existing_pages_are_never_overwritten(test_patient, sql) -> None:
    document = _create(test_patient).document
    claim = _claim(test_patient, document.id)
    sql(
        "INSERT INTO pages (patient_id, document_id, page_number, text, parse_method) "
        "VALUES (:p, :d, 1, 'original', 'native')",
        p=test_patient,
        d=document.id,
    )
    _expect(
        "already_parsed",
        lambda: repo.persist_parsed_pages(
            test_patient, document.id, claim.run_id, _pages("a", "b")
        ),
    )
    assert [row[1] for row in _page_rows(sql, document.id)] == ["original"]


@pytest.mark.parametrize(
    ("records", "kind"),
    [
        (_pages("a" + chr(0), "b"), "text_not_storable"),
        (_pages("a", "b" + chr(0xD800)), "text_not_storable"),
        (_pages("a", "b" + chr(0xDFFF)), "text_not_storable"),
        (_pages("a", "b", "c"), "integrity_mismatch"),  # the document has 2 pages
        ((), "integrity_mismatch"),
        (
            (PageRecord(2, "a", "native", False), PageRecord(1, "b", "native", False)),
            "integrity_mismatch",
        ),
        (
            (PageRecord(1, "a", "scanned", False), PageRecord(2, "b", "native", False)),
            "integrity_mismatch",
        ),
    ],
)
def test_invalid_pages_are_refused_without_writing(test_patient, sql, records, kind) -> None:
    document = _create(test_patient).document
    claim = _claim(test_patient, document.id)
    error = _expect(
        kind, lambda: repo.persist_parsed_pages(test_patient, document.id, claim.run_id, records)
    )
    assert "Test Person" not in str(error)
    assert _page_rows(sql, document.id) == []
    assert _run_row(sql, claim.run_id)[0] == "running"


def test_a_failure_after_inserting_pages_rolls_everything_back(
    test_patient, sql, monkeypatch
) -> None:
    document = _create(test_patient).document
    claim = _claim(test_patient, document.id)

    def fail(*args, **kwargs):
        raise RuntimeError("simulated failure after the page INSERTs")

    monkeypatch.setattr(repo, "_finish_run", fail)
    with pytest.raises(RuntimeError):
        repo.persist_parsed_pages(test_patient, document.id, claim.run_id, _pages("a", "b"))
    assert _page_rows(sql, document.id) == []
    assert _run_row(sql, claim.run_id)[0] == "running"
    assert _status(sql, document.id) == "processing"


# --- record_parse_failure ------------------------------------------------------------------------


def test_failure_marks_run_and_document_failed_and_allows_a_retry(test_patient, sql) -> None:
    document = _create(test_patient).document
    claim = _claim(test_patient, document.id)
    assert repo.record_parse_failure(
        test_patient,
        document.id,
        claim.run_id,
        error_kind="timeout",
        error_class="DocumentParseError",
    )
    status, kind, error_class, finished, duration = _run_row(sql, claim.run_id)
    assert (status, kind, error_class, finished) == (
        "failed",
        "timeout",
        "DocumentParseError",
        True,
    )
    assert duration >= 0 and _status(sql, document.id) == "failed"
    retry = _claim(test_patient, document.id)  # failed → claimable again, no recovery flag
    assert retry.run_id != claim.run_id and _status(sql, document.id) == "processing"


def test_recording_twice_does_nothing_the_second_time(test_patient, sql) -> None:
    document = _create(test_patient).document
    claim = _claim(test_patient, document.id)
    assert repo.record_parse_failure(test_patient, document.id, claim.run_id, error_kind="a")
    assert not repo.record_parse_failure(test_patient, document.id, claim.run_id, error_kind="b")
    assert _run_row(sql, claim.run_id)[1] == "a"


@pytest.mark.parametrize(
    ("kind", "error_class"),
    [
        ("Bad Kind", None),
        ("contains text: Test Person", None),
        ("", None),
        ("timeout", "Not a class name!"),
    ],
)
def test_unsafe_error_fields_are_refused_before_the_database(
    test_db_session_factory, test_patient, kind, error_class
) -> None:
    _, log = test_db_session_factory
    document = _create(test_patient).document
    claim = _claim(test_patient, document.id)
    connections_before = len(log.databases)
    with pytest.raises(ValueError):
        repo.record_parse_failure(
            test_patient, document.id, claim.run_id, error_kind=kind, error_class=error_class
        )
    assert len(log.databases) == connections_before  # no connection was opened


# --- provenance and isolation --------------------------------------------------------------------


def test_parse_run_belongs_to_its_document_in_any_status(test_patient, sql) -> None:
    document = _create(test_patient).document
    first = _claim(test_patient, document.id)
    assert repo.parse_run_belongs_to_document(test_patient, document.id, first.run_id)
    repo.record_parse_failure(test_patient, document.id, first.run_id, error_kind="timeout")
    second = _claim(test_patient, document.id)
    repo.persist_parsed_pages(test_patient, document.id, second.run_id, _pages("a", "b"))
    for run in (first.run_id, second.run_id):  # failed, succeeded
        assert repo.parse_run_belongs_to_document(test_patient, document.id, run)
    other = _create(test_patient).document
    assert not repo.parse_run_belongs_to_document(test_patient, other.id, first.run_id)
    assert not repo.parse_run_belongs_to_document(test_patient, document.id, uuid.uuid4())
    other_stage = sql(
        "INSERT INTO pipeline_runs (patient_id, document_id, stage, status, finished_at) "
        "VALUES (:p, :d, 'classify', 'succeeded', now()) RETURNING id",
        p=test_patient,
        d=document.id,
    )[0][0]
    assert not repo.parse_run_belongs_to_document(test_patient, document.id, other_stage)


def test_another_patient_can_neither_see_nor_change_the_document(
    test_patient, second_patient, sql
) -> None:
    document = _create(test_patient).document
    claim = _claim(test_patient, document.id)
    _expect("document_not_found", lambda: _claim(second_patient, document.id))
    _expect(
        "document_not_found",
        lambda: repo.persist_parsed_pages(
            second_patient, document.id, claim.run_id, _pages("a", "b")
        ),
    )
    _expect(
        "document_not_found",
        lambda: repo.record_parse_failure(
            second_patient, document.id, claim.run_id, error_kind="x"
        ),
    )
    assert not repo.parse_run_belongs_to_document(second_patient, document.id, claim.run_id)
    assert _run_row(sql, claim.run_id)[0] == "running" and _page_rows(sql, document.id) == []
    same_bytes = _create(second_patient, document.sha256)  # same file, other patient: own row
    assert same_bytes.created is True and same_bytes.document.id != document.id


def test_repository_logs_hold_identifiers_only(test_patient, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    document = _create(test_patient).document
    claim = _claim(test_patient, document.id)
    repo.persist_parsed_pages(
        test_patient, document.id, claim.run_id, _pages("Test Person 13.5", "Haemoglobin")
    )
    assert "Test Person" not in caplog.text and "Haemoglobin" not in caplog.text
    assert "fictional-report.pdf" not in caplog.text


# --- patients (Task 2.6a) ------------------------------------------------------------------------


@pytest.fixture
def created_patients(test_db_session_factory, sql) -> Iterator[list[uuid.UUID]]:
    """Patients created through ``create_patient`` in a test; deleted (guarded) afterwards."""
    ids: list[uuid.UUID] = []
    yield ids
    for patient_id in ids:
        sql("DELETE FROM documents WHERE patient_id = :p", p=patient_id)
        sql("DELETE FROM pipeline_runs WHERE patient_id = :p", p=patient_id)
        sql("DELETE FROM patients WHERE id = :p", p=patient_id)


def _patient_count(sql) -> int:
    return sql("SELECT count(*) FROM patients")[0][0]


def _expect_patient_error(kind: str, call: Callable[[], object]) -> PatientError:
    with pytest.raises(PatientError) as caught:
        call()
    assert caught.value.kind == kind, f"expected {kind}, got {caught.value.kind}"
    return caught.value


def test_create_patient_round_trip(test_db_session_factory, created_patients, sql) -> None:
    _, log = test_db_session_factory
    name = f"  Ra{chr(0x200D)}m Fictional{chr(0x200C)}  "
    ref = repo.create_patient(name, is_synthetic=True, date_of_birth=dt.date(1980, 5, 17))
    created_patients.append(ref.id)
    row = sql(
        "SELECT display_name, date_of_birth, is_synthetic FROM patients WHERE id = :p", p=ref.id
    )
    assert row == [(name.strip(), dt.date(1980, 5, 17), True)]  # exact code points, no NFC
    assert ref.is_synthetic is True and repo.patient_exists(ref.id)
    assert set(log.databases) == {TEST_DATABASE}


def test_create_patient_stores_is_synthetic_false_and_no_date(
    test_db_session_factory, created_patients, sql
) -> None:
    ref = repo.create_patient("Real-Looking Fictional", is_synthetic=False)
    created_patients.append(ref.id)
    row = sql("SELECT date_of_birth, is_synthetic FROM patients WHERE id = :p", p=ref.id)
    assert row == [(None, False)]


@pytest.mark.parametrize(
    ("name", "birth", "kind"),
    [
        ("", None, "invalid_display_name"),
        ("a" * 201, None, "invalid_display_name"),
        ("Asha" + chr(0) + "Fictional", None, "invalid_display_name"),
        ("Asha" + chr(0xD800), None, "invalid_display_name"),
        ("Asha" + chr(0x202E) + "Fictional", None, "invalid_display_name"),
        ("Asha Fictional", dt.date.today() + dt.timedelta(days=1), "invalid_date_of_birth"),
    ],
)
def test_invalid_patients_write_nothing(test_db_session_factory, sql, name, birth, kind) -> None:
    _, log = test_db_session_factory
    before = _patient_count(sql)
    _expect_patient_error(
        kind, lambda: repo.create_patient(name, is_synthetic=True, date_of_birth=birth)
    )
    assert _patient_count(sql) == before  # nothing written; "no session at all" is unit-tested
    assert set(log.databases) == {TEST_DATABASE}


def test_patient_exists_is_false_for_unknown_and_deleted_patients(
    test_db_session_factory, sql
) -> None:
    assert repo.patient_exists(uuid.uuid4()) is False
    ref = repo.create_patient("Short-Lived Fictional", is_synthetic=True)
    assert repo.patient_exists(ref.id) is True
    sql("DELETE FROM patients WHERE id = :p", p=ref.id)
    assert repo.patient_exists(ref.id) is False


def test_patient_exists_sees_each_patient_independently(test_patient, second_patient) -> None:
    assert repo.patient_exists(test_patient) and repo.patient_exists(second_patient)
    assert test_patient != second_patient


def test_create_document_for_an_unknown_patient_is_patient_not_found(
    test_db_session_factory, sql
) -> None:
    missing = uuid.uuid4()
    sha = _sha()
    error = _expect_patient_error("patient_not_found", lambda: _create(missing, sha))
    assert str(missing) in str(error)
    assert "fictional-report.pdf" not in str(error) and sha not in str(error)
    assert error.__cause__ is None and error.__context__ is None  # no chained driver error
    assert sql("SELECT count(*) FROM documents WHERE sha256 = :s", s=sha) == [(0,)]


def test_other_integrity_errors_are_not_mapped_to_patient_not_found(test_patient) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError) as caught:  # CHECK sha256_hex violated (23514)
        _create(test_patient, "not-a-hash")
    assert getattr(caught.value.orig, "sqlstate", None) == "23514"


def test_the_duplicate_path_still_works_after_the_fk_mapping(test_patient) -> None:
    first = _create(test_patient)
    again = _create(test_patient, first.document.sha256)
    assert again.created is False and again.document.id == first.document.id


def test_patient_creation_log_holds_the_identifier_only(
    test_db_session_factory, created_patients, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    ref = repo.create_patient(
        "Logged Fictional Person", is_synthetic=True, date_of_birth=dt.date(1980, 5, 17)
    )
    created_patients.append(ref.id)
    assert str(ref.id) in caplog.text
    assert "Logged Fictional" not in caplog.text and "1980" not in caplog.text
