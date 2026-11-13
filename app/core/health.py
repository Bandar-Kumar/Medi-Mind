"""Environment health checks: a quick answer to "is my setup broken?".

Each check is a plain function ``(Settings) -> CheckResult``. To add one, write the function and
append it to ``CHECKS``.
"""

import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.core.config import ENV_FILE, Settings
from app.core.errors import DatabaseError

Status = Literal["ok", "warn", "fail"]

SUPPORTED_PYTHON = (3, 12)  # keep in sync with requires-python in pyproject.toml (ADR-026)

DB_TCP_TIMEOUT_SECONDS = 1.0
TCP_ONLY_NOTE = "TCP reachability only; login is checked by database_connection"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


def check_python_version(
    settings: Settings, version: tuple[int, ...] = tuple(sys.version_info)
) -> CheckResult:
    current = ".".join(str(part) for part in version[:3])
    if tuple(version[:2]) == SUPPORTED_PYTHON:
        return CheckResult("python_version", "ok", current)
    supported = ".".join(map(str, SUPPORTED_PYTHON))
    return CheckResult("python_version", "fail", f"{current} (supported: {supported}.x)")


def check_configuration(settings: Settings) -> CheckResult:
    # Only allow-listed, non-secret values are shown.
    detail = ", ".join(f"{key}={value}" for key, value in settings.safe_summary().items())
    return CheckResult("configuration", "ok", detail)


def check_env_file(settings: Settings) -> CheckResult:
    if ENV_FILE.is_file():
        return CheckResult("env_file", "ok", f"{ENV_FILE} found")
    return CheckResult("env_file", "warn", f"{ENV_FILE} not found; using defaults")


def check_data_dir(settings: Settings) -> CheckResult:
    if settings.data_dir.is_dir():
        return CheckResult("data_dir", "ok", str(settings.data_dir))
    return CheckResult("data_dir", "fail", f"{settings.data_dir} is missing or not a directory")


def check_synthetic_data(settings: Settings) -> CheckResult:
    synthetic = settings.data_dir / "synthetic"
    if synthetic.is_dir():
        return CheckResult("synthetic_data", "ok", str(synthetic))
    return CheckResult("synthetic_data", "warn", f"{synthetic} not found (optional demo data)")


def check_database_tcp(settings: Settings) -> CheckResult:
    """Is anything accepting TCP connections on the database port?

    This proves only that the port is open. It does not log in or run a query; that is
    ``database_connection``. A closed port is a warning, not a failure: a stopped container is the
    normal state on this laptop.
    """
    address = f"{settings.db_host}:{settings.db_port}"
    try:
        with socket.create_connection(
            (settings.db_host, settings.db_port), timeout=DB_TCP_TIMEOUT_SECONDS
        ):
            pass
    except OSError as exc:  # refused, timed out, or host not resolvable
        return CheckResult(
            "database_tcp",
            "warn",
            f"{address} not reachable ({type(exc).__name__}); start it with "
            f"`docker compose up -d`. {TCP_ONLY_NOTE}",
        )
    return CheckResult("database_tcp", "ok", f"{address} accepts connections. {TCP_ONLY_NOTE}")


def check_database_password(settings: Settings) -> CheckResult:
    """Warn if the database password is missing or empty. Never reveals the value."""
    password = settings.db_password
    if password is None or not password.get_secret_value():
        return CheckResult(
            "database_password",
            "warn",
            "MEDIMIND_DB_PASSWORD is not set in .env (Docker Compose will refuse to start the "
            "database)",
        )
    return CheckResult("database_password", "ok", "MEDIMIND_DB_PASSWORD is set (value hidden)")


def check_database_connection(settings: Settings) -> CheckResult:
    """Authenticated database health: log in and run one read-only query (Task 1.6).

    Unlike ``database_tcp`` (is the port open?), this proves the configured user can log in to the
    configured database. Severity (approved decision D2):
    - WARN: no password configured (skipped), or database unreachable/timed out (a stopped
      container is the normal state on this laptop);
    - FAIL: the server answered but rejected the login (``connection_rejected``: wrong
      password/user, missing database), an SQLSTATE-identified authentication or missing-database
      error, or any other unexpected database error.
    """
    password = settings.db_password
    if password is None or not password.get_secret_value():
        return CheckResult(
            "database_connection", "warn", "skipped: MEDIMIND_DB_PASSWORD is not set"
        )

    # Imported here so that app.core does not depend on app.records at import time.
    from app.records.db import DbErrorKind, probe_database

    try:
        probe = probe_database(settings)
    except DatabaseError as exc:  # already sanitized: category + class name + SQLSTATE
        if exc.kind == DbErrorKind.UNREACHABLE:
            return CheckResult(
                "database_connection", "warn", f"{exc}; start it with `docker compose up -d`"
            )
        return CheckResult("database_connection", "fail", str(exc))
    return CheckResult(
        "database_connection",
        "ok",
        f"authenticated as {probe.user} to {probe.database} "
        f"(PostgreSQL {probe.server_version}); read-only query succeeded",
    )


CHECKS: list[Callable[[Settings], CheckResult]] = [
    check_python_version,
    check_configuration,
    check_env_file,
    check_data_dir,
    check_synthetic_data,
    check_database_tcp,
    check_database_password,
    check_database_connection,
]


def run_health_checks(settings: Settings) -> list[CheckResult]:
    return [check(settings) for check in CHECKS]


def overall_status(results: list[CheckResult]) -> Status:
    statuses = {result.status for result in results}
    if "fail" in statuses:
        return "fail"
    return "warn" if "warn" in statuses else "ok"


def format_report(results: list[CheckResult], settings: Settings) -> str:
    width = max(len(result.name) for result in results)
    lines = [f"MediMind health check (env={settings.app_env})"]
    lines += [f"  {r.status:<5} {r.name:<{width}}  {r.detail}" for r in results]
    warnings = sum(r.status == "warn" for r in results)
    failures = sum(r.status == "fail" for r in results)
    overall = "FAIL" if failures else "OK"
    plural = "" if warnings == 1 else "s"
    lines.append(f"Overall: {overall} ({failures} failed, {warnings} warning{plural})")
    return "\n".join(lines)
