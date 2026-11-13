from pathlib import Path

import pytest

from app.core.config import SAFE_TO_DISPLAY, Settings
from app.core.health import (
    TCP_ONLY_NOTE,
    CheckResult,
    check_database_password,
    check_database_tcp,
    check_python_version,
    format_report,
    overall_status,
    run_health_checks,
)

FAKE_PASSWORD = "FAKE-DB-PASSWORD-for-tests-only-5d8e"


def _by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {result.name: result for result in results}


def test_all_checks_pass_in_a_complete_setup(
    data_dir: Path, env_file: Path, tcp_listener: int
) -> None:
    settings = Settings(db_port=tcp_listener, db_password=FAKE_PASSWORD)
    results = run_health_checks(settings)
    assert {r.status for r in results} == {"ok"}
    assert overall_status(results) == "ok"


def test_database_tcp_ok_when_port_accepts_connections(tcp_listener: int) -> None:
    result = check_database_tcp(Settings(db_port=tcp_listener))
    assert result.status == "ok"
    assert TCP_ONLY_NOTE in result.detail


def test_database_tcp_is_a_warning_when_port_is_closed(closed_port: int) -> None:
    result = check_database_tcp(Settings(db_port=closed_port))
    assert result.status == "warn"
    assert "docker compose up -d" in result.detail
    assert TCP_ONLY_NOTE in result.detail


def test_closed_database_port_does_not_fail_the_overall_health(
    data_dir: Path, env_file: Path, closed_port: int
) -> None:
    results = run_health_checks(Settings(db_port=closed_port, db_password=FAKE_PASSWORD))
    assert overall_status(results) == "warn"


@pytest.mark.parametrize("password", [None, ""])
def test_missing_or_empty_database_password_is_a_warning(password: str | None) -> None:
    result = check_database_password(Settings(db_password=password))
    assert result.status == "warn"
    assert "MEDIMIND_DB_PASSWORD" in result.detail


def test_database_password_value_never_appears_in_the_health_report(
    data_dir: Path, env_file: Path, closed_port: int
) -> None:
    settings = Settings(db_port=closed_port, db_password=FAKE_PASSWORD)
    results = run_health_checks(settings)
    assert _by_name(results)["database_password"].status == "ok"
    assert FAKE_PASSWORD not in format_report(results, settings)


def test_missing_env_file_is_a_warning_not_a_failure(data_dir: Path) -> None:
    settings = Settings()
    results = run_health_checks(settings)
    assert _by_name(results)["env_file"].status == "warn"
    assert overall_status(results) == "warn"
    assert "Overall: OK" in format_report(results, settings)


def test_missing_required_data_dir_fails(env_file: Path) -> None:
    results = run_health_checks(Settings())
    assert _by_name(results)["data_dir"].status == "fail"
    assert overall_status(results) == "fail"


def test_missing_optional_synthetic_data_is_a_warning(tmp_path: Path, env_file: Path) -> None:
    (tmp_path / "data").mkdir()
    results = _by_name(run_health_checks(Settings()))
    assert results["data_dir"].status == "ok"
    assert results["synthetic_data"].status == "warn"


@pytest.mark.parametrize(
    ("version", "expected"),
    [((3, 12, 14), "ok"), ((3, 12, 0), "ok"), ((3, 11, 9), "fail"), ((3, 13, 0), "fail")],
)
def test_python_version_check_matches_supported_range(
    version: tuple[int, ...], expected: str
) -> None:
    assert check_python_version(Settings(), version).status == expected


def test_configuration_check_shows_only_allow_listed_fields() -> None:
    detail = _by_name(run_health_checks(Settings()))["configuration"].detail
    shown = {part.split("=", 1)[0] for part in detail.split(", ")}
    assert shown == set(SAFE_TO_DISPLAY)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [(["ok", "ok"], "ok"), (["ok", "warn"], "warn"), (["warn", "fail", "ok"], "fail")],
)
def test_overall_status(statuses: list[str], expected: str) -> None:
    results = [CheckResult(f"c{i}", status, "") for i, status in enumerate(statuses)]  # type: ignore[arg-type]
    assert overall_status(results) == expected
