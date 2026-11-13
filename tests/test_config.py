import traceback
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.core.config import ENV_PREFIX, SAFE_TO_DISPLAY, Settings, get_settings
from app.core.errors import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_defaults_load_without_env_file() -> None:
    settings = Settings()
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.log_format == "text"
    assert settings.data_dir == Path("data")


def test_env_file_values_are_applied(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("MEDIMIND_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    assert Settings().log_level == "DEBUG"


def test_environment_variable_overrides_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("MEDIMIND_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.setenv("MEDIMIND_LOG_LEVEL", "ERROR")
    assert Settings().log_level == "ERROR"


def test_invalid_value_raises_configuration_error_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIMIND_LOG_LEVEL", "LOUD-SECRET-VALUE")
    with pytest.raises(ConfigurationError) as excinfo:
        get_settings()
    message = str(excinfo.value)
    assert "MEDIMIND_LOG_LEVEL" in message
    assert "LOUD-SECRET-VALUE" not in message


def test_configuration_error_traceback_never_contains_the_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression test for audit finding #1. The value is fake and only needs to be recognizable.
    fake_secret = "FAKE-SECRET-do-not-use-7f3a9c"
    monkeypatch.setenv("MEDIMIND_LOG_LEVEL", fake_secret)

    with pytest.raises(ConfigurationError) as excinfo:
        get_settings()

    error = excinfo.value
    full_traceback = "".join(traceback.format_exception(error))
    assert fake_secret not in full_traceback
    assert "MEDIMIND_LOG_LEVEL" in full_traceback
    # No chained exception at all, so nothing else can expose the original pydantic error.
    assert error.__cause__ is None
    assert error.__context__ is None


def test_unknown_variables_are_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDIMIND_SOMETHING_ELSE", "x")
    (tmp_path / ".env").write_text("UNRELATED=1\n", encoding="utf-8")
    Settings()  # must not raise


def test_get_settings_returns_cached_instance() -> None:
    assert get_settings() is get_settings()


def test_safe_summary_contains_only_allow_listed_fields() -> None:
    assert tuple(Settings().safe_summary()) == SAFE_TO_DISPLAY


def test_every_field_is_classified_as_displayable_or_secret() -> None:
    # Forces a conscious decision for each new setting: allow-list it, or make it a SecretStr.
    for name, field in Settings.model_fields.items():
        assert name in SAFE_TO_DISPLAY or field.annotation is SecretStr, name


def test_env_example_documents_every_setting() -> None:
    lines = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    documented = {
        line.split("=", 1)[0].strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }
    expected = {f"{ENV_PREFIX}{name.upper()}" for name in Settings.model_fields}
    assert documented == expected
