"""Shared test fixtures.

The autouse fixture isolates every test so it never reads or modifies the real .env, the real
data/ directory or the developer's environment, and so tests pass alone and in any order.
"""

import logging
import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import ENV_PREFIX, get_settings


def _remove_medimind_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_medimind_handler", False):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
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
    root_level = logging.getLogger().level

    yield tmp_path

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
