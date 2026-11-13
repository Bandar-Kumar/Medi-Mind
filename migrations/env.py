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
"""

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy.engine import Connection

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
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
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
        with engine.connect() as connection:
            _run_with_connection(connection)
    finally:
        engine.dispose()


_configure_logging()
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
