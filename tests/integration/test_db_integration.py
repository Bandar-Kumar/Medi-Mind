"""Integration tests against the real PostgreSQL container (Task 1.6).

Opt-in: deselected by default; run with `uv run pytest -m integration` after the owner has started
the database (`docker compose start`). Skipped automatically if the password is not configured or
the database is not reachable.

Safety rules (owner decision D3 + clarification 3):
- Credentials come from the existing .env, read-only and in memory; they are never printed.
- Every connection is read-only (`default_transaction_read_only=on`), and the tests run ONLY
  SELECT/SHOW statements. No write is attempted, not even one expected to fail.
- Each test checks that tables, extensions, roles and schemas are unchanged before and after.
"""

import socket
import traceback
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.errors import DatabaseError
from app.core.health import check_database_connection
from app.records import db

pytestmark = pytest.mark.integration

REPO_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
FAKE_WRONG_PASSWORD = "FAKE-WRONG-PASSWORD-integration-only-6c41"
MISSING_DATABASE = "medimind_missing_db_probe"


@pytest.fixture(scope="session")
def real_settings() -> Settings:
    """Settings from the real .env (read-only, in memory). Skips if unusable."""
    settings = Settings(_env_file=REPO_ENV_FILE)
    password = settings.db_password
    if password is None or not password.get_secret_value():
        pytest.skip("MEDIMIND_DB_PASSWORD is not set in .env")
    try:
        socket.create_connection((settings.db_host, settings.db_port), timeout=1).close()
    except OSError:
        pytest.skip("database not reachable; start it with `docker compose start`")
    return settings


@pytest.fixture
def read_only_engine(real_settings: Settings) -> Iterator[Engine]:
    engine = db.create_db_engine(real_settings, pooled=False, read_only=True)
    yield engine
    engine.dispose()


_SNAPSHOT_SQL = text(
    "SELECT"
    " (SELECT count(*) FROM information_schema.tables"
    "   WHERE table_schema NOT IN ('pg_catalog', 'information_schema')),"
    " (SELECT string_agg(extname, ',' ORDER BY extname) FROM pg_extension),"
    " (SELECT count(*) FROM pg_roles),"
    " (SELECT string_agg(nspname, ',' ORDER BY nspname) FROM pg_namespace)"
)


@pytest.fixture(autouse=True)
def database_is_unchanged(read_only_engine: Engine) -> Iterator[None]:
    """Prove each test created or changed nothing (read-only snapshot before and after)."""
    with read_only_engine.connect() as connection:
        before = tuple(connection.execute(_SNAPSHOT_SQL).one())
    yield
    with read_only_engine.connect() as connection:
        after = tuple(connection.execute(_SNAPSHOT_SQL).one())
    assert after == before


def test_connection_is_authenticated_and_read_only(
    read_only_engine: Engine, real_settings: Settings
) -> None:
    with read_only_engine.connect() as connection:
        user, database, version_num = connection.execute(
            text(
                "SELECT current_user, current_database(), "
                "current_setting('server_version_num')::int"
            )
        ).one()
        read_only = connection.execute(text("SHOW transaction_read_only")).scalar_one()
    assert user == real_settings.db_user
    assert database == real_settings.db_name
    assert version_num >= 180000
    assert read_only == "on"


def test_health_probe_succeeds(real_settings: Settings) -> None:
    probe = db.probe_database(real_settings)
    assert probe.user == real_settings.db_user
    assert probe.database == real_settings.db_name
    assert probe.server_version_num >= 180000

    result = check_database_connection(real_settings)
    assert result.status == "ok"
    assert "read-only query succeeded" in result.detail


def test_wrong_password_is_an_authentication_failure(real_settings: Settings) -> None:
    wrong = real_settings.model_copy(update={"db_password": SecretStr(FAKE_WRONG_PASSWORD)})
    with pytest.raises(DatabaseError) as excinfo:
        db.probe_database(wrong)
    error = excinfo.value
    # libpq reports a rejected login without an SQLSTATE (measured in Task 1.6), so a wrong
    # password is `connection_rejected`, confirmed by the open port; never "unreachable".
    assert error.kind == "connection_rejected"
    assert error.__cause__ is None and error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    assert FAKE_WRONG_PASSWORD not in rendered

    result = check_database_connection(wrong)
    assert result.status == "fail"
    assert result.detail.startswith("Database rejected the connection")
    assert FAKE_WRONG_PASSWORD not in result.detail


def test_missing_database_is_reported(real_settings: Settings) -> None:
    missing = real_settings.model_copy(update={"db_name": MISSING_DATABASE})
    with pytest.raises(DatabaseError) as excinfo:
        db.probe_database(missing)
    assert excinfo.value.kind == "connection_rejected"
    assert excinfo.value.__cause__ is None and excinfo.value.__context__ is None
    result = check_database_connection(missing)
    assert result.status == "fail"
    assert result.detail.startswith("Database rejected the connection")
    assert MISSING_DATABASE not in result.detail  # no configured values echoed


def test_pool_is_bounded_and_connections_are_returned(real_settings: Settings) -> None:
    engine = db.create_db_engine(real_settings, pooled=True, read_only=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    count_sql = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE application_name = :app AND pid <> pg_backend_pid()"
    )
    try:
        for _ in range(10):
            with db.session_scope(factory) as session:
                assert session.execute(text("SELECT 1")).scalar_one() == 1
        assert engine.pool.checkedout() == 0
        with db.session_scope(factory) as session:
            others = session.execute(count_sql, {"app": db.APPLICATION_NAME}).scalar_one()
        assert others <= db.POOL_SIZE
    finally:
        engine.dispose()

    with db.create_db_engine(real_settings, pooled=False, read_only=True).connect() as connection:
        remaining = connection.execute(count_sql, {"app": db.APPLICATION_NAME}).scalar_one()
    assert remaining == 0
