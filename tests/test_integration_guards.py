"""Regression tests for the integration-test transaction helper (no PostgreSQL needed).

Bug fixed: the constraint-test fixture ran the database-name guard (a SELECT, which autobegins a
transaction in SQLAlchemy 2.x) and only then called ``connection.begin()``, which raised
InvalidRequestError for every test. ``rolled_back_connection`` begins the outer transaction first.
A file-based SQLite database stands in for PostgreSQL; the guard is replaced by a stub that runs
SQL, exactly like the real guard does.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.integration._guards import rolled_back_connection


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'guard.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE t (x INTEGER PRIMARY KEY)"))
    yield engine
    engine.dispose()


def _count(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM t")).scalar_one()


def test_guard_runs_sql_inside_the_outer_transaction_before_the_test(engine: Engine) -> None:
    events: list[str] = []

    def sql_running_guard(connection: Connection, app_database: str) -> None:
        connection.execute(text("SELECT 1"))  # would autobegin if no transaction were open
        events.append(f"guard:{app_database}:in_transaction={connection.in_transaction()}")

    with rolled_back_connection(engine, "medimind", guard=sql_running_guard) as connection:
        events.append("test body")
        outer = connection.get_transaction()
        assert outer is not None and not connection.in_nested_transaction()
        connection.execute(text("INSERT INTO t (x) VALUES (1)"))
    assert events == ["guard:medimind:in_transaction=True", "test body"]
    assert _count(engine) == 0  # rolled back


def test_savepoint_failures_are_recoverable_and_still_rolled_back(engine: Engine) -> None:
    with rolled_back_connection(engine, "medimind", guard=lambda c, a: None) as connection:
        connection.execute(text("INSERT INTO t (x) VALUES (1)"))
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO t (x) VALUES (1)"))  # duplicate key
        savepoint.rollback()
        connection.execute(text("INSERT INTO t (x) VALUES (2)"))  # outer transaction usable
        assert connection.execute(text("SELECT count(*) FROM t")).scalar_one() == 2
    assert _count(engine) == 0


def test_guard_failure_yields_nothing_and_rolls_back(engine: Engine) -> None:
    def refusing_guard(connection: Connection, app_database: str) -> None:
        connection.execute(text("INSERT INTO t (x) VALUES (9)"))
        raise RuntimeError("wrong database")

    entered = False
    with pytest.raises(RuntimeError, match="wrong database"):
        with rolled_back_connection(engine, "medimind", guard=refusing_guard):
            entered = True
    assert entered is False
    assert _count(engine) == 0


def test_beginning_after_a_statement_is_the_bug_this_prevents(engine: Engine) -> None:
    from sqlalchemy.exc import InvalidRequestError

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))  # autobegin
        with pytest.raises(InvalidRequestError):
            connection.begin()
