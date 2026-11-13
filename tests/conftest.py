"""Shared test fixtures.

The autouse fixture isolates every test so it never reads or modifies the real .env, the real
data/ directory or the developer's environment, and so tests pass alone and in any order.

Unit tests can never open a real database connection: the autouse fixture replaces psycopg's
connect functions with a guard. Tests that need a probe result use ``fake_probe``; the one test
that deliberately exercises the real driver against a closed local port uses
``allow_real_db_connections``. Integration tests (``-m integration``) are exempt.
"""

import logging
import os
import socket
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest

from app.core.config import ENV_PREFIX, get_settings
from app.core.errors import DatabaseError
from app.records import db
from app.records.db import DatabaseProbe

_REAL_PSYCOPG_CONNECT = psycopg.connect
_REAL_CONNECTION_CONNECT = psycopg.Connection.connect


def _blocked_connect(*args: object, **kwargs: object) -> None:
    raise RuntimeError(
        "Unit tests must not open real database connections. Use the fake_probe fixture, or put "
        "the test in tests/integration/ and mark it with @pytest.mark.integration."
    )


def _remove_medimind_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_medimind_handler", False):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def isolated_environment(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
    # 1. No MEDIMIND_* variables leak in (e.g. from the VS Code terminal's .env injection).
    for key in list(os.environ):
        if key.startswith(ENV_PREFIX):
            monkeypatch.delenv(key)
    # 2. Relative paths (.env, data/) now resolve inside an empty temporary folder.
    monkeypatch.chdir(tmp_path)
    # 3. No cached settings from a previous test.
    get_settings.cache_clear()
    # 4. Fast database TCP checks. On Windows a closed loopback port is refused only after ~2 s
    #    (connection retries), so the real 1 s timeout would add a second to every health test.
    #    Loopback connections to a live listener are effectively instant, so 0.1 s is enough.
    monkeypatch.setattr("app.core.health.DB_TCP_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(db, "REACHABILITY_TIMEOUT_SECONDS", 0.1)
    # 5. No real database connections in unit tests (integration tests are exempt).
    if request.node.get_closest_marker("integration") is None:
        monkeypatch.setattr(psycopg, "connect", _blocked_connect)
        monkeypatch.setattr(psycopg.Connection, "connect", _blocked_connect)
    db.dispose_engine()
    root_level = logging.getLogger().level

    yield tmp_path

    db.dispose_engine()
    get_settings.cache_clear()
    _remove_medimind_handlers()
    logging.getLogger().setLevel(root_level)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A temporary data directory containing the optional synthetic/ folder."""
    synthetic = tmp_path / "data" / "synthetic"
    synthetic.mkdir(parents=True)
    return tmp_path / "data"


@pytest.fixture
def tcp_listener() -> Iterator[int]:
    """A temporary TCP listener on 127.0.0.1 and a random free port; yields that port.

    The OS completes TCP handshakes for a listening socket, so no accept loop is needed. This lets
    the database TCP check be tested without Docker or PostgreSQL.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        yield server.getsockname()[1]


@pytest.fixture
def closed_port() -> int:
    """A port on 127.0.0.1 that nothing is listening on (bound briefly, then released)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    """A temporary, empty .env in the test's working directory."""
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def allow_real_db_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore psycopg's real connect functions for one test (closed local port only)."""
    monkeypatch.setattr(psycopg, "connect", _REAL_PSYCOPG_CONNECT)
    monkeypatch.setattr(psycopg.Connection, "connect", _REAL_CONNECTION_CONNECT)


FakeProbe = Callable[..., None]


@pytest.fixture
def fake_probe(monkeypatch: pytest.MonkeyPatch) -> FakeProbe:
    """Replace ``probe_database`` with a fake that returns a probe or raises a DatabaseError."""

    def install(*, error: DatabaseError | None = None) -> None:
        def fake(settings: object) -> DatabaseProbe:
            if error is not None:
                raise error
            return DatabaseProbe(
                user="medimind",
                database="medimind",
                server_version="18.7 (fake)",
                server_version_num=180007,
            )

        monkeypatch.setattr(db, "probe_database", fake)

    return install


class _FailingConnection:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __enter__(self) -> "_FailingConnection":
        raise self._exc

    def __exit__(self, *args: object) -> None:
        return None


@pytest.fixture
def failing_engine(monkeypatch: pytest.MonkeyPatch) -> Callable[[BaseException], list[bool]]:
    """Make ``create_db_engine`` return an engine whose connect() raises a chosen driver error.

    Unlike ``fake_probe``, the real ``probe_database`` and classification code run. Returns a list
    that records each ``dispose()`` call.
    """
    disposed: list[bool] = []

    def install(exc: BaseException) -> list[bool]:
        class _Engine:
            def connect(self) -> _FailingConnection:
                return _FailingConnection(exc)

            def dispose(self) -> None:
                disposed.append(True)

        monkeypatch.setattr(db, "create_db_engine", lambda *a, **k: _Engine())
        return disposed

    return install


@pytest.fixture
def fake_schema_status(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Replace ``schema_status`` with a fake returning a chosen state, or raising a DatabaseError.

    Defaults to "at head", so health tests need no database.
    """
    from app.records import migrations

    def install(
        state: str = "at_head", current: str | None = "HEAD", *, error: DatabaseError | None = None
    ) -> None:
        head = migrations.head_revision()

        def fake(settings: object) -> migrations.SchemaStatus:
            if error is not None:
                raise error
            return migrations.SchemaStatus(
                state=migrations.SchemaState(state),
                current=head if current == "HEAD" else current,
                head=head,
            )

        monkeypatch.setattr(migrations, "schema_status", fake)

    return install
