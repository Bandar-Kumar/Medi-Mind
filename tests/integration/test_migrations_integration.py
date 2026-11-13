"""Migration integration tests (Task 1.7). Destructive: run ONLY in ``medimind_test``.

Opt-in: ``uv run pytest -m integration`` with the database started. Every step is guarded by
``assert_is_test_database``; the application database is snapshotted before and after.
"""

import pytest
from alembic import command
from sqlalchemy import Connection, Engine, text

from app.core.config import Settings
from app.records.migrations import alembic_config
from app.records.models import Base
from tests.integration._guards import assert_is_test_database, run_alembic

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

MEDIMIND_TABLES = set(Base.metadata.tables)
SCRATCH_SCHEMA = "models_check"


def _tables(connection: Connection, schema: str = "public") -> set[str]:
    rows = connection.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
        {"s": schema},
    )
    return {row[0] for row in rows} - {"alembic_version"}


def _state(engine: Engine) -> tuple[set[str], str | None, str | None]:
    with engine.connect() as connection:
        tables = _tables(connection)
        vector = connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
        has_version_table = connection.execute(
            text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        ).scalar_one()
        version = (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            if has_version_table
            else None
        )
    return tables, vector, version


def _catalog(connection: Connection, schema: str) -> dict[str, set[tuple]]:
    """Columns, constraints and indexes of one schema, with the schema name normalized away."""

    def norm(value: object) -> object:
        if isinstance(value, str):
            return value.replace(f"{schema}.", "")
        return value

    params = {"s": schema}
    columns = connection.execute(
        text(
            "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,"
            " pg_get_expr(d.adbin, d.adrelid), a.attgenerated, a.attidentity"
            " FROM pg_attribute a"
            " JOIN pg_class c ON c.oid = a.attrelid"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum"
            " WHERE n.nspname = :s AND c.relkind = 'r' AND a.attnum > 0"
            " AND NOT a.attisdropped AND c.relname <> 'alembic_version'"
        ),
        params,
    )
    constraints = connection.execute(
        text(
            "SELECT c.relname, con.conname, con.contype, pg_get_constraintdef(con.oid)"
            " FROM pg_constraint con"
            " JOIN pg_class c ON c.oid = con.conrelid"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = :s AND c.relname <> 'alembic_version'"
        ),
        params,
    )
    indexes = connection.execute(
        text(
            "SELECT tablename, indexname, indexdef FROM pg_indexes"
            " WHERE schemaname = :s AND tablename <> 'alembic_version'"
        ),
        params,
    )
    return {
        "columns": {tuple(norm(v) for v in row) for row in columns},
        "constraints": {tuple(norm(v) for v in row) for row in constraints},
        "indexes": {tuple(norm(v) for v in row) for row in indexes},
    }


def test_fresh_initialization_from_base(test_engine: Engine, app_settings: Settings) -> None:
    run_alembic(test_engine, app_settings.db_name, "downgrade", "base")
    tables, vector, version = _state(test_engine)
    assert (tables, vector, version) == (set(), None, None)

    run_alembic(test_engine, app_settings.db_name, "upgrade", "head")
    tables, vector, version = _state(test_engine)
    assert tables == MEDIMIND_TABLES
    assert len(tables) == 16
    assert vector == "0.8.7"
    assert version == "0003"


def test_stepwise_downgrade_and_upgrade_round_trip(
    test_engine: Engine, app_settings: Settings, schema_at_head: None
) -> None:
    with test_engine.connect() as connection:
        before = _catalog(connection, "public")

    expected_counts = {"0002": 15, "0001": 4, "base": 0}
    for target, count in expected_counts.items():
        run_alembic(test_engine, app_settings.db_name, "downgrade", "-1")
        tables, _, version = _state(test_engine)
        assert len(tables) == count, target
        assert version == (None if target == "base" else target)

    for target in ("0001", "0002", "0003"):
        run_alembic(test_engine, app_settings.db_name, "upgrade", "+1")
        assert _state(test_engine)[2] == target

    with test_engine.connect() as connection:
        after = _catalog(connection, "public")
    assert after == before


def test_alembic_check_finds_no_differences(
    test_engine: Engine, app_settings: Settings, schema_at_head: None
) -> None:
    with test_engine.connect() as connection:
        assert_is_test_database(connection, app_settings.db_name)
        command.check(alembic_config(connection))  # raises if models and database differ


def test_models_and_migrations_build_identical_catalogs(
    test_engine: Engine, app_settings: Settings, schema_at_head: None
) -> None:
    """Migrations build ``public``; the models build a scratch schema; every definition matches.

    ``alembic check`` does not compare CHECK constraints, so this compares the full catalog:
    column types, nullability, defaults, generated expressions, identity, every constraint
    definition and every index definition.
    """
    with test_engine.connect() as connection:
        assert_is_test_database(connection, app_settings.db_name)
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCRATCH_SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {SCRATCH_SCHEMA}"))
        try:
            Base.metadata.create_all(
                connection.execution_options(schema_translate_map={None: SCRATCH_SCHEMA})
            )
            from_migrations = _catalog(connection, "public")
            from_models = _catalog(connection, SCRATCH_SCHEMA)
        finally:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCRATCH_SCHEMA} CASCADE"))
            connection.commit()

    for part in ("columns", "constraints", "indexes"):
        only_migrations = from_migrations[part] - from_models[part]
        only_models = from_models[part] - from_migrations[part]
        assert not only_migrations and not only_models, (
            f"{part} differ: only in migrations {sorted(only_migrations)}; "
            f"only in models {sorted(only_models)}"
        )
    assert len(from_migrations["columns"]) > 100  # sanity: the snapshot is not empty


def test_pgvector_extension_version(test_engine: Engine, schema_at_head: None) -> None:
    with test_engine.connect() as connection:
        version = connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
    assert version == "0.8.7"
