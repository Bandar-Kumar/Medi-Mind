"""Shared fixtures for the destructive integration tests (Task 1.7).

Destructive tests (migrations, constraint checks) run ONLY in ``medimind_test``:
- the target database name is a hard-coded constant (``_guards.TEST_DATABASE``);
- every destructive step first checks ``current_database()`` and fails the run otherwise;
- Alembic receives an explicit connection, so it can never fall back to the app database;
- a read-only snapshot proves the application database ``medimind`` is unchanged afterwards.

Credentials come from the existing .env, read in memory only and never printed.
"""

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

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


def _main_snapshot(app_settings: Settings) -> tuple:
    engine = db.create_db_engine(app_settings, pooled=False, read_only=True)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT current_database()")).scalar_one() == (
                app_settings.db_name
            )
            return tuple(connection.execute(_MAIN_SNAPSHOT_SQL).one())
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def main_database_unchanged(app_settings: Settings) -> Iterator[None]:
    """The application database must look exactly the same after all destructive tests."""
    before = _main_snapshot(app_settings)
    yield
    after = _main_snapshot(app_settings)
    assert after == before, f"application database changed: {before} -> {after}"
