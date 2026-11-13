"""Offline tests for the Alembic setup and the frozen migrations (no database needed).

Alembic's offline mode (``--sql``) renders the DDL as text using a dialect-only URL, so these
tests prove the migrations' content, order and safety without connecting anywhere.
"""

import ast
import io
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.records import migrations
from app.records.models import Base
from tests.integration._guards import TEST_DATABASE, refusal_reason

REPO = Path(__file__).resolve().parent.parent
VERSIONS = REPO / "migrations" / "versions"
FAKE_PASSWORD = "FAKE-DB-PASSWORD-for-tests-only-91c3"

EXPECTED_ORDER = {
    "0001": ["patients", "documents", "pages", "pipeline_runs"],
    "0002": [
        "analyte_catalog",
        "analyte_aliases",
        "unit_conversions",
        "extracted_facts",
        "observations",
        "medication_mentions",
        "allergy_mentions",
        "clinical_events",
        "review_decisions",
        "conflicts",
        "conflict_members",
    ],
    "0003": ["chunks"],
}


def _render(*, upgrade: bool) -> str:
    buffer = io.StringIO()
    config = Config(str(migrations.ALEMBIC_INI), output_buffer=buffer)
    config.attributes["configure_logging"] = False
    if upgrade:
        command.upgrade(config, "base:head", sql=True)
    else:
        command.downgrade(config, "head:base", sql=True)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    return _render(upgrade=True)


@pytest.fixture(scope="module")
def downgrade_sql() -> str:
    return _render(upgrade=False)


# --- Revision chain ------------------------------------------------------------------------------


def test_linear_chain_with_a_single_head() -> None:
    script = ScriptDirectory.from_config(migrations.alembic_config())
    assert script.get_heads() == ["0003"]
    chain = {rev.revision: rev.down_revision for rev in script.walk_revisions()}
    assert chain == {"0001": None, "0002": "0001", "0003": "0002"}
    assert migrations.head_revision() == "0003"
    assert migrations.known_revisions() == {"0001", "0002", "0003"}


def test_migrations_are_frozen_and_import_no_application_code() -> None:
    allowed = {"collections", "sqlalchemy", "alembic", "pgvector"}
    files = sorted(VERSIONS.glob("*.py"))
    assert [f.name for f in files] == ["0001_core.py", "0002_facts.py", "0003_retrieval.py"]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] in allowed, f"{path.name} imports {module}"


# --- Rendered upgrade ----------------------------------------------------------------------------


def test_upgrade_is_one_transaction_and_creates_the_extension_first(upgrade_sql: str) -> None:
    assert upgrade_sql.count("BEGIN;") == 1
    assert upgrade_sql.count("COMMIT;") == 1
    assert upgrade_sql.index("CREATE EXTENSION IF NOT EXISTS vector") < upgrade_sql.index(
        "CREATE TABLE patients"
    )


def test_upgrade_creates_all_tables_in_dependency_order(upgrade_sql: str) -> None:
    created = re.findall(r"^CREATE TABLE (\w+) \(", upgrade_sql, flags=re.M)
    expected = [t for tables in EXPECTED_ORDER.values() for t in tables]
    assert created == ["alembic_version", *expected]
    assert set(expected) == set(Base.metadata.tables)


def test_every_model_constraint_and_index_name_is_in_the_migrations(upgrade_sql: str) -> None:
    # Name-level agreement between the frozen migrations and the models. The integration test
    # compares full definitions (pg_get_constraintdef / pg_get_indexdef) on a real database.
    model_names = {
        str(obj.name)
        for table in Base.metadata.tables.values()
        for obj in [*table.constraints, *table.indexes]
    }
    sql_names = set(re.findall(r"CONSTRAINT (\w+) ", upgrade_sql)) | set(
        re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+) ", upgrade_sql)
    )
    sql_names.discard("alembic_version_pkc")
    assert sql_names == model_names


def test_upgrade_contains_the_key_postgres_features(upgrade_sql: str) -> None:
    assert "DEFAULT uuidv7()" in upgrade_sql
    assert "embedding VECTOR(384)" in upgrade_sql
    assert "GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, content)) STORED" in upgrade_sql
    assert "CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)" in upgrade_sql
    assert "WHERE duplicate_group_id IS NOT NULL" in upgrade_sql
    assert "is_synthetic BOOLEAN NOT NULL," in upgrade_sql  # no default (C4)


def test_rendered_sql_contains_no_credentials_or_hosts(
    upgrade_sql: str, downgrade_sql: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEDIMIND_DB_PASSWORD", FAKE_PASSWORD)
    rendered = _render(upgrade=True) + upgrade_sql + downgrade_sql
    assert FAKE_PASSWORD not in rendered
    assert "127.0.0.1" not in rendered
    assert "postgresql+psycopg" not in rendered


def test_alembic_ini_has_no_database_url() -> None:
    ini = migrations.ALEMBIC_INI.read_text(encoding="utf-8")
    active = [line for line in ini.splitlines() if line.strip() and not line.startswith("#")]
    assert not any("sqlalchemy.url" in line for line in active)
    assert migrations.alembic_config().get_main_option("sqlalchemy.url") is None


# --- Rendered downgrade --------------------------------------------------------------------------


def test_downgrade_drops_everything_in_reverse_order(downgrade_sql: str) -> None:
    dropped = re.findall(r"^DROP TABLE (\w+);", downgrade_sql, flags=re.M)
    expected = [t for rev in ("0003", "0002", "0001") for t in reversed(EXPECTED_ORDER[rev])]
    assert dropped == expected
    # Alembic keeps its own version table and only removes the version row.
    assert "DELETE FROM alembic_version WHERE alembic_version.version_num = '0001'" in (
        downgrade_sql
    )
    assert downgrade_sql.index("DROP TABLE patients") < downgrade_sql.index(
        "DROP EXTENSION IF EXISTS vector"
    )


# --- Test-database guard (used by the destructive integration tests) -----------------------------


@pytest.mark.parametrize(
    ("current", "app", "safe"),
    [
        (TEST_DATABASE, "medimind", True),
        ("medimind", "medimind", False),  # the application database: never
        ("postgres", "medimind", False),
        ("MEDIMIND_TEST", "medimind", False),  # exact name only
        (TEST_DATABASE, TEST_DATABASE, False),  # app misconfigured to use the test database
    ],
)
def test_test_database_guard(current: str, app: str, safe: bool) -> None:
    assert (refusal_reason(current, app) is None) is safe


def test_test_database_name_is_a_constant() -> None:
    assert TEST_DATABASE == "medimind_test"


# --- schema_status without a database (fast path and sanitized failures) -------------------------


def _settings(port: int):
    from app.core.config import Settings

    return Settings(db_port=port, db_password=FAKE_PASSWORD)


def test_schema_status_closed_port_is_unreachable_without_connecting(closed_port: int) -> None:
    # The conftest guard would raise if a real psycopg connection were attempted.
    from app.core.errors import DatabaseError

    with pytest.raises(DatabaseError) as excinfo:
        migrations.schema_status(_settings(closed_port))
    assert excinfo.value.kind == "unreachable"
    assert str(excinfo.value) == "Database unreachable or timed out (PortClosed)"


def test_schema_status_failure_is_sanitized_and_unchained(
    monkeypatch: pytest.MonkeyPatch, tcp_listener: int
) -> None:
    import traceback

    import psycopg
    from sqlalchemy.exc import OperationalError

    from app.core.errors import DatabaseError

    driver_error = OperationalError(
        "SELECT 1", None, psycopg.OperationalError(f"auth failed {FAKE_PASSWORD}")
    )
    disposed: list[bool] = []

    class _Connection:
        def __enter__(self):
            raise driver_error

        def __exit__(self, *args: object) -> None:
            return None

    class _Engine:
        def connect(self) -> _Connection:
            return _Connection()

        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(migrations, "create_db_engine", lambda *a, **k: _Engine())
    with pytest.raises(DatabaseError) as excinfo:
        migrations.schema_status(_settings(tcp_listener))
    error = excinfo.value
    assert error.kind == "connection_rejected"  # port open + plain OperationalError
    assert error.__cause__ is None and error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    assert FAKE_PASSWORD not in rendered and "auth failed" not in rendered
    assert disposed == [True]


def test_run_upgrade_without_password_names_the_setting_only() -> None:
    from app.core.config import Settings
    from app.core.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="MEDIMIND_DB_PASSWORD"):
        migrations.run_upgrade(Settings(db_password=None))
