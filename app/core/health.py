"""Environment health checks: a quick answer to "is my setup broken?".

Each check is a plain function ``(Settings) -> CheckResult``. To add one (e.g. a database check
in the PostgreSQL task), write the function and append it to ``CHECKS``.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.core.config import ENV_FILE, Settings

Status = Literal["ok", "warn", "fail"]

SUPPORTED_PYTHON = (3, 12)  # keep in sync with requires-python in pyproject.toml (ADR-026)


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


CHECKS: list[Callable[[Settings], CheckResult]] = [
    check_python_version,
    check_configuration,
    check_env_file,
    check_data_dir,
    check_synthetic_data,
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
