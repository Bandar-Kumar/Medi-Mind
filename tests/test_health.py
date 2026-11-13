from pathlib import Path

import pytest

from app.core.config import SAFE_TO_DISPLAY, Settings
from app.core.errors import DatabaseError
from app.core.health import (
    TCP_ONLY_NOTE,
    CheckResult,
    check_database_connection,
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
    data_dir: Path, env_file: Path, tcp_listener: int, fake_probe
) -> None:
    fake_probe()  # successful authenticated probe, no real connection
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
    data_dir: Path, env_file: Path, closed_port: int, fake_probe
) -> None:
    fake_probe(error=DatabaseError("Database unreachable or timed out", kind="unreachable"))
    results = run_health_checks(Settings(db_port=closed_port, db_password=FAKE_PASSWORD))
    assert overall_status(results) == "warn"


@pytest.mark.parametrize("password", [None, ""])
def test_missing_or_empty_database_password_is_a_warning(password: str | None) -> None:
    result = check_database_password(Settings(db_password=password))
    assert result.status == "warn"
    assert "MEDIMIND_DB_PASSWORD" in result.detail


def test_database_password_value_never_appears_in_the_health_report(
    data_dir: Path, env_file: Path, closed_port: int, fake_probe
) -> None:
    fake_probe(error=DatabaseError("Database unreachable or timed out", kind="unreachable"))
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


# --- database_connection (authenticated health, Task 1.6) -------------------------------------


@pytest.mark.parametrize("password", [None, ""])
def test_database_connection_is_skipped_with_a_warning_without_password(
    password: str | None,
) -> None:
    # No fake_probe: if this tried to connect, the conftest guard would raise.
    result = check_database_connection(Settings(db_password=password))
    assert result.status == "warn"
    assert "skipped" in result.detail


def test_database_connection_ok_reports_user_database_and_version(fake_probe) -> None:
    fake_probe()
    result = check_database_connection(Settings(db_password=FAKE_PASSWORD))
    assert result.status == "ok"
    assert "authenticated as medimind to medimind" in result.detail
    assert "18.7" in result.detail


def test_unreachable_database_is_a_warning(fake_probe) -> None:
    fake_probe(error=DatabaseError("Database unreachable or timed out", kind="unreachable"))
    result = check_database_connection(Settings(db_password=FAKE_PASSWORD))
    assert result.status == "warn"
    assert "Database unreachable or timed out" in result.detail
    assert "docker compose up -d" in result.detail


@pytest.mark.parametrize(
    "kind", ["connection_rejected", "authentication_failed", "database_missing", "unexpected"]
)
def test_reachable_but_misconfigured_database_fails(fake_probe, kind: str) -> None:
    fake_probe(error=DatabaseError("sanitized message", kind=kind))
    result = check_database_connection(Settings(db_password=FAKE_PASSWORD))
    assert result.status == "fail"
    assert FAKE_PASSWORD not in result.detail


def test_rejected_login_fails_end_to_end_without_leaking(
    failing_engine, tcp_listener: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Real probe + classification with the measured psycopg 3.3.9 shape of a rejected login."""
    import psycopg
    from sqlalchemy.exc import OperationalError

    caplog.set_level("DEBUG")
    failing_engine(
        OperationalError(
            "SELECT 1", None, psycopg.OperationalError(f"password failed {FAKE_PASSWORD}")
        )
    )
    settings = Settings(db_port=tcp_listener, db_password=FAKE_PASSWORD)
    result = check_database_connection(settings)
    assert result.status == "fail"
    assert result.detail.startswith("Database rejected the connection")
    assert "docker compose up -d" not in result.detail  # starting it again would not help
    for text in (result.detail, caplog.text, format_report([result], settings)):
        assert FAKE_PASSWORD not in text
        assert "password failed" not in text


def test_real_driver_against_closed_port_is_unreachable_and_leaks_nothing(
    allow_real_db_connections: None,
    closed_port: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Uses the real psycopg driver (no Docker): a closed local port. On Windows this surfaces as
    # ConnectionTimeout after the 2 s libpq minimum (measured in Task 1.6).
    caplog.set_level("DEBUG")
    settings = Settings(db_port=closed_port, db_password=FAKE_PASSWORD)
    result = check_database_connection(settings)
    assert result.status == "warn"
    assert result.detail.startswith("Database unreachable or timed out (")
    assert FAKE_PASSWORD not in result.detail
    assert FAKE_PASSWORD not in caplog.text
    assert FAKE_PASSWORD not in format_report([result], settings)
