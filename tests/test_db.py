"""Unit tests for app/records/db.py. No Docker, no PostgreSQL, no real connections.

All credentials here are recognizable fakes; the real password is never used.
"""

import ast
import logging
import time
import traceback
import warnings
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.exc import ProgrammingError as SAProgrammingError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

from app.core.config import Settings
from app.core.errors import ConfigurationError, DatabaseError
from app.core.logging import configure_logging
from app.records import db

FAKE_PASSWORD = "FAKE-DB-PASSWORD-for-tests-only-a17f"
SPECIAL_PASSWORD = "p@ss:w/rd#%?&=FAKE"


def _settings(password: str | None = FAKE_PASSWORD, **overrides: Any) -> Settings:
    return Settings(db_password=password, **overrides)


# --- URL -------------------------------------------------------------------------------------


def test_url_uses_psycopg_driver_and_configured_values() -> None:
    url = db.build_database_url(_settings(db_host="127.0.0.1", db_port=5433, db_name="x"))
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username) == ("127.0.0.1", 5433, "x", "medimind")


def test_url_handles_special_characters_and_masks_the_password() -> None:
    url = db.build_database_url(_settings(SPECIAL_PASSWORD))
    assert url.password == SPECIAL_PASSWORD  # round-trips exactly, no manual escaping
    for rendered in (str(url), repr(url)):
        assert "***" in rendered
        assert SPECIAL_PASSWORD not in rendered


@pytest.mark.parametrize("password", [None, ""])
def test_missing_password_raises_configuration_error(password: str | None) -> None:
    with pytest.raises(ConfigurationError, match="MEDIMIND_DB_PASSWORD"):
        db.build_database_url(_settings(password))


# --- Engines ---------------------------------------------------------------------------------


@pytest.fixture
def captured_engine_kwargs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the keyword arguments create_db_engine passes to SQLAlchemy's create_engine."""
    calls: list[dict[str, Any]] = []
    real_create_engine = db.create_engine

    def recording_create_engine(url: Any, **kwargs: Any) -> Engine:
        calls.append(kwargs)
        return real_create_engine(url, **kwargs)

    monkeypatch.setattr(db, "create_engine", recording_create_engine)
    return calls


def test_pooled_engine_is_bounded_and_pre_pings(captured_engine_kwargs: list) -> None:
    engine = db.create_db_engine(_settings())
    kwargs = captured_engine_kwargs[-1]
    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.size() == 2
    assert kwargs["max_overflow"] == 3
    assert kwargs["pool_timeout"] == 5
    assert kwargs["pool_recycle"] == 1800
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["hide_parameters"] is True
    assert kwargs["echo"] is False
    assert kwargs["connect_args"]["connect_timeout"] == 2
    assert kwargs["connect_args"]["application_name"] == "medimind"
    assert "options" not in kwargs["connect_args"]  # app engine is read-write
    engine.dispose()


def test_nullpool_engine_gets_no_pool_sizing_arguments(captured_engine_kwargs: list) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning fails the test
        engine = db.create_db_engine(_settings(), pooled=False, read_only=True)
    kwargs = captured_engine_kwargs[-1]
    assert isinstance(engine.pool, NullPool)
    for forbidden in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle", "pool_pre_ping"):
        assert forbidden not in kwargs
    assert kwargs["connect_args"]["options"] == "-c default_transaction_read_only=on"
    assert kwargs["hide_parameters"] is True
    engine.dispose()


def test_engines_never_reveal_the_password() -> None:
    for engine in (
        db.create_db_engine(_settings()),
        db.create_db_engine(_settings(), pooled=False),
    ):
        assert FAKE_PASSWORD not in repr(engine)
        assert FAKE_PASSWORD not in str(engine.url)
        assert FAKE_PASSWORD not in repr(engine.url)
        engine.dispose()


def test_creating_an_engine_does_not_connect() -> None:
    # The conftest guard raises if psycopg.connect is called; creating engines must not trigger it.
    db.create_db_engine(_settings()).dispose()
    db.create_db_engine(_settings(), pooled=False).dispose()


# --- Engine lifecycle ------------------------------------------------------------------------


def test_get_engine_is_lazy_cached_and_dispose_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIMIND_DB_PASSWORD", FAKE_PASSWORD)
    first = db.get_engine()
    assert db.get_engine() is first
    db.dispose_engine()
    db.dispose_engine()  # second call is a no-op
    assert db.get_engine() is not first


# --- session_scope (in-memory SQLite: ships with Python, no new dependency) -------------------


@pytest.fixture
def sqlite_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE t (x INTEGER)"))  # SQLite memory only, not Postgres
    return sessionmaker(bind=engine, expire_on_commit=False)


def _count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return session.execute(text("SELECT count(*) FROM t")).scalar_one()


def test_session_scope_commits_on_success(sqlite_factory: sessionmaker[Session]) -> None:
    with db.session_scope(sqlite_factory) as session:
        session.execute(text("INSERT INTO t (x) VALUES (1)"))
    assert _count(sqlite_factory) == 1


def test_session_scope_rolls_back_and_reraises(sqlite_factory: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="boom"):
        with db.session_scope(sqlite_factory) as session:
            session.execute(text("INSERT INTO t (x) VALUES (1)"))
            raise ValueError("boom")
    assert _count(sqlite_factory) == 0


def test_session_scope_always_closes(
    sqlite_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[bool] = []
    real_close = Session.close
    monkeypatch.setattr(Session, "close", lambda self: (closed.append(True), real_close(self)))
    with db.session_scope(sqlite_factory):
        pass
    with pytest.raises(RuntimeError):
        with db.session_scope(sqlite_factory):
            raise RuntimeError("x")
    assert closed == [True, True]


# --- Error classification: type + SQLSTATE + bounded TCP check, never message text ----------
#
# Shapes measured against the live container in Task 1.6 (psycopg 3.3.9, libpq 18.4, Windows):
# - wrong password / missing database: plain psycopg.OperationalError, sqlstate None;
# - closed port: psycopg.errors.ConnectionTimeout, sqlstate None.
# Driver messages here contain a fake secret to prove they are never echoed.


def _wrapped(orig: BaseException) -> SAOperationalError:
    return SAOperationalError("SELECT 1", None, orig)


def _login_rejected() -> psycopg.OperationalError:
    """The measured shape of a rejected login (wrong password or missing database)."""
    exc = psycopg.OperationalError(f"password authentication failed {FAKE_PASSWORD}")
    assert exc.sqlstate is None  # as observed live
    return exc


def _timed_out() -> psycopg.errors.ConnectionTimeout:
    """The measured shape of a closed port / unanswered connection on Windows."""
    exc = psycopg.errors.ConnectionTimeout(f"connection timeout expired {FAKE_PASSWORD}")
    assert exc.sqlstate is None  # as observed live
    return exc


@pytest.mark.parametrize("wrap", [False, True])
@pytest.mark.parametrize(
    ("port_open", "kind"),
    [(True, "connection_rejected"), (False, "unreachable"), (None, "unreachable")],
)
def test_plain_operational_error_depends_on_reachability(
    wrap: bool, port_open: bool | None, kind: str
) -> None:
    exc = _wrapped(_login_rejected()) if wrap else _login_rejected()
    info = db.classify_db_error(exc, port_open=port_open)
    assert info.kind.value == kind
    assert (info.error_class, info.sqlstate) == ("OperationalError", None)
    assert FAKE_PASSWORD not in info.describe()


@pytest.mark.parametrize("port_open", [True, False, None])
def test_connection_timeout_is_always_unreachable(port_open: bool | None) -> None:
    # A timeout means the server never answered; an open port must not turn it into "rejected".
    for exc in (_timed_out(), _wrapped(_timed_out())):
        assert db.classify_db_error(exc, port_open=port_open).kind.value == "unreachable"


@pytest.mark.parametrize(
    ("orig", "kind"),
    [
        (psycopg.errors.InvalidPassword(f"password {FAKE_PASSWORD}"), "authentication_failed"),
        (psycopg.errors.InvalidAuthorizationSpecification("x"), "authentication_failed"),
        (psycopg.errors.InvalidCatalogName("x"), "database_missing"),
        (psycopg.errors.UndefinedTable("x"), "unexpected"),
    ],
)
@pytest.mark.parametrize("port_open", [True, False, None])
def test_sqlstate_classification_is_preserved(
    orig: BaseException, kind: str, port_open: bool | None
) -> None:
    # When an error does carry an SQLSTATE, it wins; reachability is irrelevant.
    for exc in (orig, _wrapped(orig)):
        info = db.classify_db_error(exc, port_open=port_open)
        assert info.kind.value == kind
        assert info.error_class == type(orig).__qualname__
        assert FAKE_PASSWORD not in info.describe()


def test_non_database_errors_are_unexpected() -> None:
    assert db.classify_db_error(ValueError("x"), port_open=True).kind.value == "unexpected"


@pytest.mark.parametrize(
    ("exc", "needed"),
    [
        (_login_rejected(), True),
        (_wrapped(_login_rejected()), True),
        (_timed_out(), False),
        (_wrapped(_timed_out()), False),
        (psycopg.errors.InvalidPassword("x"), False),
        (psycopg.errors.UndefinedTable("x"), False),
        (psycopg.ProgrammingError("x"), False),
        (ValueError("x"), False),
    ],
)
def test_needs_reachability_check_only_for_ambiguous_errors(
    exc: BaseException, needed: bool
) -> None:
    assert db.needs_reachability_check(exc) is needed


def test_classification_ignores_message_text() -> None:
    # Messages that *look* like another category must not change the result.
    looks_like_auth = _wrapped(psycopg.errors.UndefinedTable("password authentication failed"))
    assert db.classify_db_error(looks_like_auth, port_open=True).kind.value == "unexpected"
    looks_like_refused = psycopg.OperationalError("could not connect: connection refused")
    rejected = db.classify_db_error(looks_like_refused, port_open=True)
    assert rejected.kind.value == "connection_rejected"
    looks_like_auth_but_closed = psycopg.OperationalError("password authentication failed")
    unreachable = db.classify_db_error(looks_like_auth_but_closed, port_open=False)
    assert unreachable.kind.value == "unreachable"


def test_unreachable_wording_is_honest() -> None:
    info = db.classify_db_error(_wrapped(_timed_out()))
    assert info.describe() == "Database unreachable or timed out (ConnectionTimeout)"


def test_rejected_wording_names_the_settings_but_no_values() -> None:
    info = db.classify_db_error(_wrapped(_login_rejected()), port_open=True)
    assert info.describe() == (
        "Database rejected the connection: authentication failed, database missing or login "
        "refused (check MEDIMIND_DB_USER, MEDIMIND_DB_PASSWORD and MEDIMIND_DB_NAME) "
        "(OperationalError)"
    )


def test_describe_includes_sqlstate_when_present() -> None:
    described = db.describe_db_error(_wrapped(psycopg.errors.InvalidPassword("x")))
    assert described.endswith("(InvalidPassword, sqlstate=28P01)")


def test_is_database_error() -> None:
    assert db.is_database_error(psycopg.OperationalError("x"))
    assert db.is_database_error(SAProgrammingError("SELECT 1", None, Exception("x")))
    assert not db.is_database_error(ValueError("x"))


# --- Bounded TCP reachability check ----------------------------------------------------------


def test_port_check_detects_an_open_port(tcp_listener: int) -> None:
    assert db.database_port_is_open("127.0.0.1", tcp_listener) is True


def test_port_check_is_bounded_for_a_closed_port(closed_port: int) -> None:
    # conftest sets REACHABILITY_TIMEOUT_SECONDS to 0.1 s for speed; production uses 1 s.
    started = time.perf_counter()
    assert db.database_port_is_open("127.0.0.1", closed_port) is False
    assert time.perf_counter() - started < db.REACHABILITY_TIMEOUT_SECONDS + 0.5


def test_production_reachability_timeout_is_bounded() -> None:
    # Read the constant from source: conftest overrides the runtime value in every test.
    tree = ast.parse(Path(db.__file__).read_text(encoding="utf-8"))
    values = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "REACHABILITY_TIMEOUT_SECONDS" for t in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    assert values == [1.0]


@pytest.fixture
def port_checks(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """Record every reachability check (and report the port as open)."""
    calls: list[tuple[str, int]] = []

    def fake(host: str, port: int) -> bool:
        calls.append((host, port))
        return True

    monkeypatch.setattr(db, "database_port_is_open", fake)
    return calls


@pytest.mark.parametrize(
    "exc",
    [
        _timed_out(),
        _wrapped(_timed_out()),
        _wrapped(psycopg.errors.InvalidPassword("x")),
        _wrapped(psycopg.errors.UndefinedTable("x")),
    ],
)
def test_no_tcp_probe_when_the_classification_does_not_need_it(
    exc: BaseException, port_checks: list[tuple[str, int]]
) -> None:
    db.classify_with_reachability(exc, _settings())
    assert port_checks == []


def test_exactly_one_tcp_probe_for_an_ambiguous_error(
    port_checks: list[tuple[str, int]],
) -> None:
    info = db.classify_with_reachability(_wrapped(_login_rejected()), _settings(db_port=6543))
    assert info.kind.value == "connection_rejected"
    assert port_checks == [("127.0.0.1", 6543)]


def test_describe_db_error_uses_configured_port(
    monkeypatch: pytest.MonkeyPatch, tcp_listener: int, closed_port: int
) -> None:
    monkeypatch.setenv("MEDIMIND_DB_PORT", str(tcp_listener))
    assert db.describe_db_error(_wrapped(_login_rejected())).startswith(
        "Database rejected the connection"
    )
    monkeypatch.setenv("MEDIMIND_DB_PORT", str(closed_port))
    db.get_settings.cache_clear()
    assert db.describe_db_error(_wrapped(_login_rejected())).startswith(
        "Database unreachable or timed out"
    )


# --- probe_database: sanitized DatabaseError with no chained driver exception ---------------


def test_rejected_login_is_sanitized_and_unchained(failing_engine, tcp_listener: int) -> None:
    disposed = failing_engine(_wrapped(_login_rejected()))
    with pytest.raises(DatabaseError) as excinfo:
        db.probe_database(_settings(db_port=tcp_listener))
    error = excinfo.value
    assert error.kind == "connection_rejected"
    assert error.__cause__ is None
    assert error.__context__ is None
    full_traceback = "".join(traceback.format_exception(error))
    assert FAKE_PASSWORD not in full_traceback
    assert "password authentication failed" not in full_traceback  # driver text never echoed
    assert "Database rejected the connection" in full_traceback
    assert disposed == [True]  # the throwaway engine is always disposed


def test_plain_operational_error_with_closed_port_is_unreachable(
    failing_engine, closed_port: int
) -> None:
    failing_engine(_wrapped(_login_rejected()))
    with pytest.raises(DatabaseError) as excinfo:
        db.probe_database(_settings(db_port=closed_port))
    assert excinfo.value.kind == "unreachable"


def test_probe_timeout_is_unreachable_without_a_tcp_probe(
    failing_engine, port_checks: list[tuple[str, int]]
) -> None:
    failing_engine(_wrapped(_timed_out()))
    with pytest.raises(DatabaseError) as excinfo:
        db.probe_database(_settings())
    assert excinfo.value.kind == "unreachable"
    assert port_checks == []


def test_probe_preserves_sqlstate_classification(failing_engine) -> None:
    failing_engine(_wrapped(psycopg.errors.InvalidCatalogName(f"db {FAKE_PASSWORD}")))
    with pytest.raises(DatabaseError) as excinfo:
        db.probe_database(_settings())
    assert excinfo.value.kind == "database_missing"
    assert FAKE_PASSWORD not in "".join(traceback.format_exception(excinfo.value))


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_probe_logs_never_contain_the_password(
    failing_engine, fmt: str, tcp_listener: int, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("DEBUG", fmt)  # writes to stderr, captured by capsys
    failing_engine(_wrapped(_login_rejected()))
    with pytest.raises(DatabaseError):
        db.probe_database(_settings(db_port=tcp_listener))
    logging.getLogger("app.records.db").info("after probe")
    err = capsys.readouterr().err
    assert "Database probe failed" in err
    assert "Database rejected the connection" in err
    assert FAKE_PASSWORD not in err
    assert "password authentication failed" not in err


def test_configuration_error_traceback_has_no_password() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        db.build_database_url(_settings(None))
    assert "MEDIMIND_DB_PASSWORD" in "".join(traceback.format_exception(excinfo.value))
