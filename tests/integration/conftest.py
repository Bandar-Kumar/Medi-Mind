"""Shared fixtures for the destructive integration tests (Task 1.7).

Destructive tests (migrations, constraint checks) run ONLY in ``medimind_test``:
- the target database name is a hard-coded constant (``_guards.TEST_DATABASE``);
- every destructive step first checks ``current_database()`` and fails the run otherwise;
- Alembic receives an explicit connection, so it can never fall back to the app database;
- a read-only snapshot proves the application database ``medimind`` is unchanged afterwards.

Credentials come from the existing .env, read in memory only and never printed.
"""

import socket
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.records import db
from tests.integration._guards import (
    CREATE_TEST_DATABASE_HINT,
    TEST_DATABASE,
    assert_is_test_database,
    run_alembic,
)

REPO_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@pytest.fixture(scope="session")
def app_settings() -> Settings:
    """Application settings from the real .env (read-only, in memory). Skips if unusable."""
    settings = Settings(_env_file=REPO_ENV_FILE)
    password = settings.db_password
    if password is None or not password.get_secret_value():
        pytest.skip("MEDIMIND_DB_PASSWORD is not set in .env")
    try:
        socket.create_connection((settings.db_host, settings.db_port), timeout=1).close()
    except OSError:
        pytest.skip("database not reachable; start it with `docker compose start`")
    return settings


@pytest.fixture(scope="session")
def test_engine(app_settings: Settings) -> Iterator[Engine]:
    """A read-write NullPool engine for ``medimind_test`` only, checked by the name guard."""
    test_settings = app_settings.model_copy(update={"db_name": TEST_DATABASE})
    engine = db.create_db_engine(test_settings, pooled=False)
    try:
        with engine.connect() as connection:
            assert_is_test_database(connection, app_settings.db_name)
    except DBAPIError:
        engine.dispose()
        pytest.skip(f"{TEST_DATABASE} is not available; {CREATE_TEST_DATABASE_HINT}")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def schema_at_head(test_engine: Engine, app_settings: Settings) -> None:
    """Bring ``medimind_test`` to the latest revision (idempotent)."""
    run_alembic(test_engine, app_settings.db_name, "upgrade", "head")


_MAIN_SNAPSHOT_SQL = text(
    "SELECT"
    " (SELECT count(*) FROM information_schema.tables"
    "   WHERE table_schema NOT IN ('pg_catalog', 'information_schema')),"
    " (SELECT string_agg(extname, ',' ORDER BY extname) FROM pg_extension),"
    " (SELECT count(*) FROM pg_roles),"
    " (SELECT string_agg(nspname, ',' ORDER BY nspname) FROM pg_namespace),"
    " (SELECT to_regclass('public.alembic_version') IS NOT NULL)"
)


# Row counts of the tables Task 2.5 code writes, when they exist in the application database.
# Secondary safety net only: the primary boundary is the per-connection check in
# ``test_db_session_factory``.
_WATCHED_TABLES = ("patients", "documents", "pages", "pipeline_runs")


def _main_snapshot(app_settings: Settings) -> tuple:
    engine = db.create_db_engine(app_settings, pooled=False, read_only=True)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT current_database()")).scalar_one() == (
                app_settings.db_name
            )
            schema = tuple(connection.execute(_MAIN_SNAPSHOT_SQL).one())
            rows = []
            for table in _WATCHED_TABLES:  # fixed names, never user input
                if connection.execute(
                    text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}
                ).scalar_one():
                    count = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                    rows.append((table, count))
            return (*schema, tuple(rows))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def main_database_unchanged(app_settings: Settings) -> Iterator[None]:
    """The application database must look exactly the same after all destructive tests."""
    before = _main_snapshot(app_settings)
    yield
    after = _main_snapshot(app_settings)
    assert after == before, f"application database changed: {before} -> {after}"


# --- Task 2.5: repositories and services against medimind_test ---------------------------------


class _DatabaseIdentityLog:
    """Every database name seen by connections of the redirected session factory."""

    def __init__(self) -> None:
        self.databases: list[str] = []


@pytest.fixture
def test_db_session_factory(
    test_engine: Engine,
    schema_at_head: None,
    app_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[sessionmaker[Session], _DatabaseIdentityLog]]:
    """Redirect the records layer's default sessions to ``medimind_test``.

    Services call repositories without a session factory, so they would use the process-wide
    engine built from the application settings. This fixture replaces that factory with one bound
    to the guarded ``test_engine`` and makes ``get_engine`` unusable, so nothing can fall back to
    the application database.

    Primary safety boundary: ``test_engine`` uses NullPool, so every session opens a NEW
    connection, and a ``connect`` listener checks ``current_database()`` on each one before any
    statement runs. A connection to any other database raises immediately (nothing is written)
    and fails the test. Every database name seen is recorded so tests can assert it.
    """
    log = _DatabaseIdentityLog()

    def check_database(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("SELECT current_database()")
            name = cursor.fetchone()[0]
        finally:
            cursor.close()
        dbapi_connection.rollback()  # type: ignore[attr-defined]
        log.databases.append(name)
        if name != TEST_DATABASE or name == app_settings.db_name:
            raise RuntimeError(f"Refusing a repository connection to {name!r}")

    event.listen(test_engine, "connect", check_database)

    def no_application_engine() -> Engine:
        raise RuntimeError("The application engine must not be used in integration tests")

    factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(db, "_get_session_factory", lambda: factory)
    monkeypatch.setattr(db, "get_engine", no_application_engine)
    try:
        yield factory, log
    finally:
        event.remove(test_engine, "connect", check_database)
    assert log.databases, "no repository connection was opened"
    assert set(log.databases) == {TEST_DATABASE}, f"unexpected databases: {set(log.databases)}"


@pytest.fixture
def test_patient(
    test_db_session_factory: tuple[sessionmaker[Session], _DatabaseIdentityLog],
    test_engine: Engine,
    app_settings: Settings,
) -> Iterator[uuid.UUID]:
    """A fictional patient in ``medimind_test``, removed with everything it owns afterwards.

    Repository functions commit, so tests cannot run inside one rolled-back transaction. Cleanup
    deletes only this patient's rows, after re-checking the connection is ``medimind_test``:
    documents first (pages and pipeline runs cascade), then the patient.
    """
    patient_id = _insert_patient(test_engine, app_settings)
    try:
        yield patient_id
    finally:
        _delete_patient(test_engine, app_settings, patient_id)


def _insert_patient(engine: Engine, app_settings: Settings) -> uuid.UUID:
    with engine.begin() as connection:
        assert_is_test_database(connection, app_settings.db_name)
        return connection.execute(
            text(
                "INSERT INTO patients (display_name, is_synthetic) "
                "VALUES ('Fictional Test Patient', true) RETURNING id"
            )
        ).scalar_one()


def _delete_patient(engine: Engine, app_settings: Settings, patient_id: uuid.UUID) -> None:
    with engine.begin() as connection:
        assert_is_test_database(connection, app_settings.db_name)
        connection.execute(text("DELETE FROM documents WHERE patient_id = :p"), {"p": patient_id})
        connection.execute(
            text("DELETE FROM pipeline_runs WHERE patient_id = :p"), {"p": patient_id}
        )
        connection.execute(text("DELETE FROM patients WHERE id = :p"), {"p": patient_id})


@pytest.fixture
def second_patient(
    test_db_session_factory: tuple[sessionmaker[Session], _DatabaseIdentityLog],
    test_engine: Engine,
    app_settings: Settings,
) -> Iterator[uuid.UUID]:
    """Another fictional patient, for isolation tests."""
    patient_id = _insert_patient(test_engine, app_settings)
    try:
        yield patient_id
    finally:
        _delete_patient(test_engine, app_settings, patient_id)
