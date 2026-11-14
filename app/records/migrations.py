"""Schema migrations for the app: apply them, and report the database's schema version (Task 1.7).

This is the only application module that imports Alembic (ADR-016: the records layer is the only
database access path). The migration scripts themselves live in ``migrations/`` at the repository
root; ``alembic.ini`` has no database URL.

- ``run_upgrade(settings)``: upgrade the configured database to the latest revision. Upgrade only:
  downgrading destroys data and is left to the explicit ``alembic downgrade`` command.
- ``schema_status(settings)``: read-only check of ``alembic_version`` for the health check.
- Downgrade guard (used by ``migrations/env.py``): every operation that moves the database
  backwards (a downgrade, a backward ``stamp``, or ``stamp --purge``) requires an exact
  confirmation of the CONNECTED database's name, e.g.
  ``uv run alembic -x confirm_downgrade=medimind downgrade -1``.

Database errors are re-raised (``run_upgrade``) or converted to a sanitized ``DatabaseError``
(``schema_status``); raw driver messages are never logged.
"""

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
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


# --- Downgrade guard (used by migrations/env.py) ---------------------------------------------

CONFIRM_DOWNGRADE_KEY = "confirm_downgrade"


class DowngradeNotConfirmed(CommandError):
    """A backward migration operation without an exact database-name confirmation.

    Subclasses Alembic's ``CommandError``, so the alembic command line prints a clean
    ``FAILED: ...`` line (no traceback). The message names the database (not secret) and nothing
    else.
    """


def confirmation_values(x_args: Sequence[str], attributes: Mapping[str, Any]) -> list[str]:
    """Every ``confirm_downgrade`` value supplied, from ``-x`` arguments and from code.

    ``-x confirm_downgrade`` without ``=`` is recorded as an empty (malformed) value. Code (the
    integration-test helper) supplies it as ``config.attributes["confirm_downgrade"]``.
    """
    values: list[str] = []
    for argument in x_args:
        key, separator, value = argument.partition("=")
        if key == CONFIRM_DOWNGRADE_KEY:
            values.append(value if separator else "")
    supplied = attributes.get(CONFIRM_DOWNGRADE_KEY)
    if supplied is not None:
        values.append(supplied if isinstance(supplied, str) else "")
    return values


def downgrade_refusal(values: Sequence[str], current_database: str) -> str | None:
    """Why a backward operation must be refused (None = confirmed). Exact match only."""
    usage = f"add `-x {CONFIRM_DOWNGRADE_KEY}={current_database}` before the alembic command"
    if not values:
        return f"no confirmation given; {usage}"
    if len(values) > 1:
        return f"confirmation given {len(values)} times; give it exactly once"
    value = values[0]
    if value == "":
        return f"malformed confirmation (expected {CONFIRM_DOWNGRADE_KEY}=<database name>)"
    if value != current_database:
        return f"confirmation {value!r} does not match the connected database"
    return None


def require_confirmation(action: str, values: Sequence[str], current_database: str) -> None:
    reason = downgrade_refusal(values, current_database)
    if reason is not None:
        raise DowngradeNotConfirmed(
            f"Refusing to {action} database {current_database!r}: {reason}. "
            "Back up the database first (see README, 'Migrations and backups')."
        )


def is_cli_downgrade(cmd_opts: Any) -> bool:
    """True when the alembic command line is running ``downgrade`` (any target)."""
    cmd = getattr(cmd_opts, "cmd", None)
    return bool(cmd) and getattr(cmd[0], "__name__", "") == "downgrade"


def backward_step_guard(values: Sequence[str], current_database: str) -> Callable[..., None]:
    """Alembic ``on_version_apply`` callback refusing any backward step without confirmation.

    The backstop for every path, including Python API calls: down migrations and backward stamps
    report ``is_upgrade=False``; forward steps and forward stamps pass. The callback runs after
    the step inside the single migration transaction, so raising rolls the step back.
    """

    def on_version_apply(*, ctx: Any, step: Any, heads: Any, run_args: Any) -> None:
        if not step.is_upgrade:
            action = "stamp backwards" if step.is_stamp else "downgrade"
            require_confirmation(action, values, current_database)

    return on_version_apply
