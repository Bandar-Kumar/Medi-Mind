"""Alembic environment for MediMind (Task 1.7).

Where migrations connect, in priority order:
1. ``config.attributes["connection"]``: a connection supplied by the caller. The integration tests
   use this to target the ``medimind_test`` database explicitly (it can never fall back to the
   application database).
2. Online (default): an engine built from the application settings by the existing, sanitized
   helper ``create_db_engine`` (NullPool, read-write, ``hide_parameters``). The URL and password are
   never written into Alembic's config.
3. Offline (``--sql``): a dialect-only URL with no host and no credentials, to render the DDL for
   review without a database.

Logging: ``config.attributes["configure_logging"]`` (default True) lets the app and the tests keep
their own logging; the Alembic command line uses MediMind's logging setup, not ``fileConfig``.

Downgrade guard (online paths 1 and 2; offline ``--sql`` only prints SQL and is not guarded):
any operation that moves the database backwards needs an exact confirmation of the CONNECTED
database's name, e.g. ``uv run alembic -x confirm_downgrade=medimind downgrade -1``.
- ``alembic downgrade`` (any target, including ``base``) is refused before any migration runs;
- ``stamp --purge`` is refused before it deletes the recorded revision;
- every other backward step (Python API downgrades, backward stamps) is refused by a per-step
  backstop; the single transaction then rolls the step back.
"""

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.records.migrations import (
    backward_step_guard,
    confirmation_values,
    is_cli_downgrade,
    require_confirmation,
)
from app.records.models import Base

config = context.config
target_metadata = Base.metadata

OFFLINE_URL = "postgresql+psycopg://"  # dialect only: no host, user or password


def _configure_logging() -> None:
    if not config.attributes.get("configure_logging", True):
        return
    from app.core.config import get_settings
    from app.core.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


def _run_with_connection(connection: Connection) -> None:
    # Let reflection recognize pgvector's type, so comparisons do not report false differences.
    connection.dialect.ischema_names["vector"] = Vector

    # Downgrade guard: compare confirmations with the database actually connected to.
    current_database = connection.execute(text("SELECT current_database()")).scalar_one()
    confirmations = confirmation_values(context.get_x_argument(), config.attributes)
    if is_cli_downgrade(config.cmd_opts):
        require_confirmation("downgrade", confirmations, current_database)

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        on_version_apply=backward_step_guard(confirmations, current_database),
    )
    if context.get_context().purge:  # `stamp --purge` deletes the recorded revision first
        require_confirmation("purge the recorded revision of", confirmations, current_database)
    # One transaction for the whole command: PostgreSQL DDL (including CREATE EXTENSION) is
    # transactional, so a failure leaves nothing half-applied.
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=OFFLINE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied: Connection | None = config.attributes.get("connection")
    if supplied is not None:
        _run_with_connection(supplied)
        return

    from app.core.config import get_settings
    from app.records.db import create_db_engine

    engine = create_db_engine(get_settings(), pooled=False)
    try:
        # begin(): the caller owns the single transaction (commit on success, rollback on any
        # error, including a refused downgrade), exactly like run_upgrade.
        with engine.begin() as connection:
            _run_with_connection(connection)
    finally:
        engine.dispose()


_configure_logging()
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
