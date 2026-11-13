"""Architecture guard for ADR-016: the records layer is the only database access path.

Outside ``app/records/``, code may not:
- import SQLAlchemy or psycopg directly;
- import anything from ``app.records.db`` except the sanitizing helpers and the health probe.

So no service, UI or core module can open an engine or a session and run unscoped queries.
Patient-scoped repositories (Task 1.8) will live inside ``app/records/``.
"""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
RECORDS_DIR = APP_DIR / "records"

FORBIDDEN_TOP_LEVEL_PACKAGES = {"sqlalchemy", "psycopg"}
# The only names from app.records.db that code outside app/records/ may import.
ALLOWED_DB_NAMES_OUTSIDE_RECORDS = {
    "probe_database",  # authenticated health check (app/core/health.py)
    "DbErrorKind",  # health check severity
    "describe_db_error",  # entry-point sanitization (app/main.py)
    "is_database_error",
}


def _python_files_outside_records() -> list[Path]:
    return [p for p in APP_DIR.rglob("*.py") if RECORDS_DIR not in p.parents]


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_TOP_LEVEL_PACKAGES:
                    found.append(f"import {alias.name}")
                if alias.name == "app.records.db":
                    found.append("import app.records.db (use the allowed names only)")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN_TOP_LEVEL_PACKAGES:
                found.append(f"from {node.module} import ...")
            if node.module == "app.records.db":
                for alias in node.names:
                    if alias.name not in ALLOWED_DB_NAMES_OUTSIDE_RECORDS:
                        found.append(f"from app.records.db import {alias.name}")
            if node.module == "app.records":
                found.append("from app.records import ... (import the allowed db names only)")
    return found


def test_only_the_records_layer_touches_the_database() -> None:
    problems = {
        str(path.relative_to(APP_DIR.parent)): issues
        for path in _python_files_outside_records()
        if (issues := _violations(path))
    }
    assert problems == {}, f"Database access outside app/records/ (ADR-016): {problems}"


def test_session_scope_is_not_on_the_allow_list() -> None:
    for name in ("session_scope", "get_engine", "create_db_engine", "build_database_url"):
        assert name not in ALLOWED_DB_NAMES_OUTSIDE_RECORDS


def test_the_guard_detects_a_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from app.records.db import session_scope\nfrom sqlalchemy.orm import Session\n",
        encoding="utf-8",
    )
    assert _violations(bad) == [
        "from app.records.db import session_scope",
        "from sqlalchemy.orm import ...",
    ]
