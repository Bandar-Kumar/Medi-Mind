"""Shared test fixtures.

The autouse fixture isolates every test so it never reads or modifies the real .env, the real
data/ directory or the developer's environment, and so tests pass alone and in any order.
"""

import logging
import os
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
def env_file(tmp_path: Path) -> Path:
    """A temporary, empty .env in the test's working directory."""
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    return path
