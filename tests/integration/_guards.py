"""Safety guards for destructive integration tests (Task 1.7, owner decision C1).

Migration and constraint tests create, change and drop schema objects. They may run ONLY in the
dedicated ``medimind_test`` database, never in the application database ``medimind``.

The test database name is a hard-coded constant, deliberately not read from the environment or
.env, so a misconfiguration cannot redirect destructive tests to real data.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from alembic import command
from sqlalchemy import Connection, Engine, text

from app.records.migrations import alembic_config

TEST_DATABASE = "medimind_test"
CREATE_TEST_DATABASE_HINT = (
    "create it once with: docker compose exec db sh -c "
    "'createdb -U \"$POSTGRES_USER\" medimind_test'"
)


def refusal_reason(current_database: str, app_database: str) -> str | None:
    """Why a destructive test must not run against ``current_database`` (None = safe)."""
    if current_database != TEST_DATABASE:
        return f"connected to {current_database!r}, expected {TEST_DATABASE!r}"
    if current_database == app_database:
        return f"{current_database!r} is also the configured application database"
    return None


def assert_is_test_database(connection: Connection, app_database: str) -> None:
    """Stop the whole run (pytest.fail, not skip) unless connected to the test database."""
    current = connection.execute(text("SELECT current_database()")).scalar_one()
    reason = refusal_reason(current, app_database)
    if reason is not None:
        pytest.fail(f"Refusing a destructive database test: {reason}", pytrace=False)


def run_alembic(engine: Engine, app_database: str, action: str, target: str) -> None:
    """Upgrade or downgrade ``medimind_test`` on an explicit, guarded connection."""
    with engine.begin() as connection:
        assert_is_test_database(connection, app_database)
        config = alembic_config(connection)
        if action == "upgrade":
            command.upgrade(config, target)
        elif action == "downgrade":
            command.downgrade(config, target)
        else:  # pragma: no cover
            raise ValueError(action)


@contextmanager
def rolled_back_connection(
    engine: Engine,
    app_database: str,
    guard: Callable[[Connection, str], None] = assert_is_test_database,
) -> Iterator[Connection]:
    """A guarded connection inside ONE outer transaction that is always rolled back.

    The outer transaction is begun BEFORE the guard runs: in SQLAlchemy 2.x any statement
    autobegins a transaction, so beginning after the guard's SELECT would raise
    InvalidRequestError. Tests use savepoints (``begin_nested``) for expected failures; nothing is
    ever committed, and the rollback also runs when the guard or the test fails.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            guard(connection, app_database)
            yield connection
        finally:
            transaction.rollback()
