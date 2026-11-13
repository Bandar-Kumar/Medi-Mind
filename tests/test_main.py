import logging
from pathlib import Path

import pytest

from app import main as main_module
from app.core.errors import MediMindError
from app.main import EXIT_CONFIG_ERROR, EXIT_FAILURE, EXIT_OK, main


def test_health_returns_ok_in_a_complete_setup(
    data_dir: Path, env_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["health"]) == EXIT_OK
    assert "Overall: OK" in capsys.readouterr().out


def test_health_returns_failure_when_data_dir_missing(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["health"]) == EXIT_FAILURE
    assert "Overall: FAIL" in capsys.readouterr().out


def test_invalid_configuration_returns_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MEDIMIND_LOG_FORMAT", "xml-secret")
    assert main(["health"]) == EXIT_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "MEDIMIND_LOG_FORMAT" in err
    assert "xml-secret" not in err


def test_unexpected_exception_is_logged_with_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(settings: object) -> int:
        raise RuntimeError("unexpected bug")

    monkeypatch.setitem(main_module.COMMANDS, "health", boom)
    assert main(["health"]) == EXIT_FAILURE
    record = next(r for r in caplog.records if r.levelno == logging.ERROR)
    assert record.exc_info is not None
    assert "unexpected bug" in str(record.exc_info[1])


def test_expected_error_is_logged_without_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def expected_failure(settings: object) -> int:
        raise MediMindError("something expected went wrong")

    monkeypatch.setitem(main_module.COMMANDS, "health", expected_failure)
    assert main(["health"]) == EXIT_FAILURE
    record = next(r for r in caplog.records if r.levelno == logging.ERROR)
    assert record.getMessage() == "something expected went wrong"
    assert record.exc_info is None


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_OK
    assert "health" in capsys.readouterr().out
