"""Database connection layer: SQLAlchemy 2.x engine + psycopg 3 driver (Task 1.6).

Responsibilities:
- build the connection URL from settings without ever exposing the password;
- create engines: one pooled engine per process for the app, and throwaway NullPool engines for
  one-shot checks;
- provide ``session_scope()`` for the records layer only (ADR-016);
- probe the database with an authenticated, read-only query for the health check;
- classify driver errors into sanitized categories, using only exception types, SQLSTATE codes and
  a bounded TCP reachability check (never the driver's message text).

Security rules (CLAUDE.md, "Sensitive data in logs and errors"):
- The password is read with ``get_secret_value()`` in exactly one place: ``build_database_url``.
- SQLAlchemy masks the password in ``str(url)``/``repr(url)``/``repr(engine)``; we never call
  ``render_as_string(hide_password=False)``.
- ``hide_parameters=True`` keeps SQL parameter values (future patient data) out of errors and logs.
- Raw driver exceptions are never logged or put into messages. ``DatabaseError`` is raised outside
  the ``except`` block, so it carries no chained driver exception.

Not in this module (later tasks): tables, models, migrations, ``CREATE EXTENSION``, repositories.
"""

import logging
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import psycopg
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError, DatabaseError

logger = logging.getLogger(__name__)

DRIVER = "postgresql+psycopg"
APPLICATION_NAME = "medimind"
# libpq treats anything below 2 s as 2 s (measured in Task 1.6), so 2 is the effective minimum.
CONNECT_TIMEOUT_SECONDS = 2

# Bounded pool for a single-user laptop: at most POOL_SIZE + MAX_OVERFLOW = 5 connections, far
# below PostgreSQL's default max_connections (100).
POOL_SIZE = 2
MAX_OVERFLOW = 3
POOL_TIMEOUT_SECONDS = 5
POOL_RECYCLE_SECONDS = 1800

READ_ONLY_OPTION = "-c default_transaction_read_only=on"

# Bounded TCP check used only to tell "rejected by the server" from "never answered" (see
# classify_db_error). Loopback answers are instant; 1 s caps the cost when the port is closed.
REACHABILITY_TIMEOUT_SECONDS = 1.0


class DbErrorKind(StrEnum):
    UNREACHABLE = "unreachable"
    AUTHENTICATION_FAILED = "authentication_failed"
    DATABASE_MISSING = "database_missing"
    CONNECTION_REJECTED = "connection_rejected"
    UNEXPECTED = "unexpected"


_MESSAGES: dict[DbErrorKind, str] = {
    # On Windows a refused connection is reported only as a timeout (psycopg ConnectionTimeout,
    # measured in Task 1.6), so the two cannot be told apart honestly.
    DbErrorKind.UNREACHABLE: "Database unreachable or timed out",
    DbErrorKind.AUTHENTICATION_FAILED: (
        "Database authentication failed (check MEDIMIND_DB_USER and MEDIMIND_DB_PASSWORD)"
    ),
    DbErrorKind.DATABASE_MISSING: "Database does not exist (check MEDIMIND_DB_NAME)",
    # psycopg 3.3.9 / libpq 18.4 report a failed login (wrong password, missing database, pg_hba
    # refusal) as a plain OperationalError with NO SQLSTATE (measured in Task 1.6), so these
    # causes cannot be told apart without reading the message text, which we never do.
    DbErrorKind.CONNECTION_REJECTED: (
        "Database rejected the connection: authentication failed, database missing or login "
        "refused (check MEDIMIND_DB_USER, MEDIMIND_DB_PASSWORD and MEDIMIND_DB_NAME)"
    ),
    DbErrorKind.UNEXPECTED: "Unexpected database error",
}

_AUTH_SQLSTATES = frozenset({"28P01", "28000"})  # invalid_password, invalid_authorization_spec
_MISSING_DATABASE_SQLSTATES = frozenset({"3D000"})  # invalid_catalog_name


@dataclass(frozen=True)
class DbErrorInfo:
    """A sanitized description of a database failure: category, class name, SQLSTATE only."""

    kind: DbErrorKind
    error_class: str
    sqlstate: str | None

    def describe(self) -> str:
        details = (
            self.error_class
            if self.sqlstate is None
            else (f"{self.error_class}, sqlstate={self.sqlstate}")
        )
        return f"{_MESSAGES[self.kind]} ({details})"


def _original_error(exc: BaseException) -> BaseException:
    """SQLAlchemy wraps driver errors; the original psycopg exception is on ``exc.orig``."""
    if isinstance(exc, DBAPIError) and exc.orig is not None:
        return exc.orig
    return exc


def needs_reachability_check(exc: BaseException) -> bool:
    """True when only a TCP check can tell "rejected by the server" from "never answered".

    That is a connection-level ``psycopg.OperationalError`` with no SQLSTATE that is not a
    ``ConnectionTimeout``. Measured in Task 1.6 (psycopg 3.3.9, libpq 18.4, Windows):
    - wrong password / missing database -> plain OperationalError, sqlstate None, ~0.03 s;
    - closed port -> ConnectionTimeout, sqlstate None, after the 2 s connect timeout.
    On Linux/macOS a refused port raises a plain OperationalError immediately, hence the check.
    """
    original = _original_error(exc)
    return (
        isinstance(original, psycopg.OperationalError)
        and not isinstance(original, psycopg.errors.ConnectionTimeout)
        and getattr(original, "sqlstate", None) is None
    )


def database_port_is_open(host: str, port: int) -> bool:
    """Bounded TCP reachability check (REACHABILITY_TIMEOUT_SECONDS). Sends no data."""
    try:
        with socket.create_connection((host, port), timeout=REACHABILITY_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def classify_db_error(exc: BaseException, *, port_open: bool | None = None) -> DbErrorInfo:
    """Classify a database exception by type, SQLSTATE and reachability; never by message text.

    Args:
        port_open: result of a TCP reachability check, needed only when
            ``needs_reachability_check(exc)`` is True. When it is None for such an error, the
            failure is reported as unreachable (the conservative, warning-level category).

    Order: SQLSTATE codes first (authoritative when present), then ``ConnectionTimeout``
    (never answered), then a plain no-SQLSTATE ``OperationalError``: rejected if the port is
    open, otherwise unreachable. Anything else is unexpected.
    """
    original = _original_error(exc)
    sqlstate = getattr(original, "sqlstate", None)
    error_class = type(original).__qualname__

    if sqlstate in _AUTH_SQLSTATES:
        kind = DbErrorKind.AUTHENTICATION_FAILED
    elif sqlstate in _MISSING_DATABASE_SQLSTATES:
        kind = DbErrorKind.DATABASE_MISSING
    elif isinstance(original, psycopg.errors.ConnectionTimeout):
        kind = DbErrorKind.UNREACHABLE
    elif needs_reachability_check(original):
        kind = DbErrorKind.CONNECTION_REJECTED if port_open else DbErrorKind.UNREACHABLE
    else:
        kind = DbErrorKind.UNEXPECTED
    return DbErrorInfo(kind=kind, error_class=error_class, sqlstate=sqlstate)


def classify_with_reachability(exc: BaseException, settings: Settings) -> DbErrorInfo:
    """Classify ``exc``, running the TCP check only if the classification depends on it."""
    port_open = None
    if needs_reachability_check(exc):
        port_open = database_port_is_open(settings.db_host, settings.db_port)
    return classify_db_error(exc, port_open=port_open)


def is_database_error(exc: BaseException) -> bool:
    """True for SQLAlchemy and psycopg exceptions (used by entry points to sanitize them)."""
    return isinstance(exc, SQLAlchemyError | psycopg.Error)


def describe_db_error(exc: BaseException) -> str:
    """A sanitized, loggable one-line description of a database exception (entry points).

    Uses the configured host/port for the reachability check, and only when it is needed.
    """
    try:
        settings = get_settings()
    except ConfigurationError:
        return classify_db_error(exc).describe()
    return classify_with_reachability(exc, settings).describe()


# --- URL and engines -------------------------------------------------------------------------


def build_database_url(settings: Settings) -> URL:
    """Build the connection URL. ``str()``/``repr()`` of the result mask the password as ***.

    Raises:
        ConfigurationError: if MEDIMIND_DB_PASSWORD is missing or empty (the value is never shown).
    """
    password = settings.db_password
    if password is None or not password.get_secret_value():
        raise ConfigurationError(
            "MEDIMIND_DB_PASSWORD is not set; add it to .env before connecting to the database"
        )
    # URL.create escapes special characters safely; no string concatenation of credentials.
    return URL.create(
        drivername=DRIVER,
        username=settings.db_user,
        password=password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )


def _connect_args(*, read_only: bool) -> dict[str, Any]:
    args: dict[str, Any] = {
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "application_name": APPLICATION_NAME,
    }
    if read_only:
        # Every transaction on these connections is read-only, enforced by PostgreSQL itself.
        args["options"] = READ_ONLY_OPTION
    return args


def create_db_engine(settings: Settings, *, pooled: bool = True, read_only: bool = False) -> Engine:
    """Create an engine. Creating it does not connect; the first query does.

    Args:
        pooled: True for the app's long-lived engine (bounded QueuePool). False for a throwaway
            NullPool engine: no connections are kept, and pool sizing arguments are NOT passed,
            because NullPool rejects them (TypeError, verified in Task 1.6).
        read_only: make every transaction read-only (health probe, integration tests).
    """
    kwargs: dict[str, Any] = {
        "hide_parameters": True,
        "echo": False,
        "connect_args": _connect_args(read_only=read_only),
    }
    if pooled:
        kwargs.update(
            pool_size=POOL_SIZE,
            max_overflow=MAX_OVERFLOW,
            pool_timeout=POOL_TIMEOUT_SECONDS,
            pool_recycle=POOL_RECYCLE_SECONDS,
            pool_pre_ping=True,  # detects connections broken by a container stop/start
        )
    else:
        kwargs["poolclass"] = NullPool
    return create_engine(build_database_url(settings), **kwargs)


# --- Process-wide engine lifecycle -----------------------------------------------------------

_lock = threading.Lock()
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide pooled engine, creating it on first use (lazy)."""
    global _engine
    with _lock:
        if _engine is None:
            _engine = create_db_engine(get_settings())
        return _engine


def _get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    engine = get_engine()
    with _lock:
        if _session_factory is None:
            _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        return _session_factory


def dispose_engine() -> None:
    """Close all pooled connections and forget the engine. Safe to call any number of times."""
    global _engine, _session_factory
    with _lock:
        engine, _engine, _session_factory = _engine, None, None
    if engine is not None:
        engine.dispose()


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """A transactional session: commit on success, roll back on any error, always close.

    ADR-016: only the records layer (patient-scoped repositories, Task 1.8) may use this. Errors
    are re-raised unchanged; entry points sanitize them with ``describe_db_error``.

    Args:
        factory: override the session factory (tests only). Defaults to the process-wide engine.
    """
    session = (factory or _get_session_factory())()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


# --- Authenticated health probe --------------------------------------------------------------


@dataclass(frozen=True)
class DatabaseProbe:
    """Non-secret facts returned by a successful authenticated probe."""

    user: str
    database: str
    server_version: str
    server_version_num: int


_PROBE_SQL = text(
    "SELECT current_user, current_database(), current_setting('server_version'), "
    "current_setting('server_version_num')::int"
)


def probe_database(settings: Settings) -> DatabaseProbe:
    """Log in and run one read-only query on a throwaway NullPool engine.

    Raises:
        ConfigurationError: if the password is missing.
        DatabaseError: sanitized; ``kind`` tells unreachable from authentication/database errors.
    """
    engine = create_db_engine(settings, pooled=False, read_only=True)
    failure: DbErrorInfo | None = None
    row: Any = None
    try:
        with engine.connect() as connection:
            row = connection.execute(_PROBE_SQL).one()
    except DBAPIError as exc:
        # Type + SQLSTATE (+ one bounded TCP check if ambiguous); the message is never read.
        failure = classify_with_reachability(exc, settings)
    finally:
        engine.dispose()

    if failure is not None:
        # Raised here, outside the `except` block: no chained driver exception (__cause__ or
        # __context__) can leak the driver's message into a traceback.
        logger.debug("Database probe failed: %s", failure.describe())
        raise DatabaseError(failure.describe(), kind=failure.kind.value)
    return DatabaseProbe(
        user=row[0], database=row[1], server_version=row[2], server_version_num=row[3]
    )
