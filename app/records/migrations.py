"""Schema migrations for the app: apply them, and report the database's schema version (Task 1.7).

This is the only application module that imports Alembic (ADR-016: the records layer is the only
database access path). The migration scripts themselves live in ``migrations/`` at the repository
root; ``alembic.ini`` has no database URL.

- ``run_upgrade(settings)``: upgrade the configured database to the latest revision. Upgrade only:
  downgrading destroys data and is left to the explicit ``alembic downgrade`` command.
- ``schema_status(settings)``: read-only check of ``alembic_version`` for the health check.

Database errors are re-raised (``run_upgrade``) or converted to a sanitized ``DatabaseError``
(``schema_status``); raw driver messages are never logged.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
from app.core.errors import DatabaseError
from app.records.db import (
    DbErrorInfo,
    DbErrorKind,
    classify_with_reachability,
    create_db_engine,
    database_port_is_open,
)

logger = logging.getLogger(__name__)

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def alembic_config(connection: Connection | None = None) -> Config:
    """Alembic config for this project. No URL is ever placed in it.

    Args:
        connection: if given, migrations run on exactly this connection (used by the tests to
            target the ``medimind_test`` database). Logging is left to the caller.
    """
    config = Config(str(ALEMBIC_INI))
    config.attributes["configure_logging"] = False
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:  # pragma: no cover - the repository always has migrations
        raise RuntimeError("no migrations found")
    return head


def known_revisions() -> set[str]:
    script = ScriptDirectory.from_config(alembic_config())
    return {revision.revision for revision in script.walk_revisions()}


@contextmanager
def _transaction(settings: Settings) -> Iterator[Connection]:
    engine = create_db_engine(settings, pooled=False)
    try:
        with engine.begin() as connection:  # commits on success, rolls back on any error
            yield connection
    finally:
        engine.dispose()


def run_upgrade(settings: Settings, target: str = "head") -> str:
    """Upgrade the configured database to ``target`` in one transaction; return the new head."""
    with _transaction(settings) as connection:
        command.upgrade(alembic_config(connection), target)
    head = head_revision()
    logger.info("Database schema upgraded to revision %s", head)
    return head


class SchemaState(StrEnum):
    AT_HEAD = "at_head"
    NOT_MIGRATED = "not_migrated"
    BEHIND = "behind"
    UNKNOWN_REVISION = "unknown_revision"


@dataclass(frozen=True)
class SchemaStatus:
    state: SchemaState
    current: str | None
    head: str


def schema_status(settings: Settings) -> SchemaStatus:
    """Read-only: compare the database's ``alembic_version`` with the code's head revision.

    Raises:
        DatabaseError: sanitized, with ``kind`` (e.g. unreachable) for the health check.
    """
    head = head_revision()
    # Fast path when the database is stopped: one bounded TCP check (<= 1 s) instead of a full
    # connection attempt (2 s libpq minimum), so `health` does not pay the timeout twice.
    if not database_port_is_open(settings.db_host, settings.db_port):
        unreachable = DbErrorInfo(DbErrorKind.UNREACHABLE, "PortClosed", None)
        raise DatabaseError(unreachable.describe(), kind=unreachable.kind.value)

    engine = create_db_engine(settings, pooled=False, read_only=True)
    failure: DbErrorInfo | None = None
    current: str | None = None
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            ).scalar_one()
            if exists:
                current = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
    except DBAPIError as exc:
        failure = classify_with_reachability(exc, settings)
    finally:
        engine.dispose()

    if failure is not None:
        # Raised outside the except block: no chained driver exception.
        raise DatabaseError(failure.describe(), kind=failure.kind.value)
    if current is None:
        state = SchemaState.NOT_MIGRATED
    elif current == head:
        state = SchemaState.AT_HEAD
    elif current in known_revisions():
        state = SchemaState.BEHIND
    else:
        state = SchemaState.UNKNOWN_REVISION
    return SchemaStatus(state=state, current=current, head=head)
