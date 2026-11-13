import io
import json
import logging

from app.core.logging import configure_logging


def _medimind_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, "_medimind_handler", False)]


def test_configured_level_is_respected() -> None:
    stream = io.StringIO()
    configure_logging("WARNING", "text", stream=stream)
    log = logging.getLogger("app.test")
    log.info("hidden message")
    log.warning("visible message")
    output = stream.getvalue()
    assert "visible message" in output
    assert "hidden message" not in output


def test_json_format_produces_parseable_lines_with_extras() -> None:
    stream = io.StringIO()
    configure_logging("INFO", "json", stream=stream)
    logging.getLogger("app.test").info("hello %s", "world", extra={"stage": "parse"})
    record = json.loads(stream.getvalue().strip())
    assert record["message"] == "hello world"
    assert record["level"] == "INFO"
    assert record["logger"] == "app.test"
    assert record["stage"] == "parse"
    assert "timestamp" in record


def test_json_format_includes_exception_traceback() -> None:
    stream = io.StringIO()
    configure_logging("INFO", "json", stream=stream)
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("app.test").exception("failed")
    record = json.loads(stream.getvalue().strip())
    assert "ValueError: boom" in record["exception"]


def test_configuring_twice_keeps_a_single_medimind_handler() -> None:
    configure_logging("INFO", "text", stream=io.StringIO())
    configure_logging("DEBUG", "json", stream=io.StringIO())
    assert len(_medimind_handlers()) == 1
    assert logging.getLogger().level == logging.DEBUG


def test_handlers_owned_by_other_code_are_not_removed() -> None:
    root = logging.getLogger()
    foreign = logging.StreamHandler(io.StringIO())
    root.addHandler(foreign)
    try:
        configure_logging("INFO", "text", stream=io.StringIO())
        configure_logging("INFO", "text", stream=io.StringIO())
        assert foreign in root.handlers
    finally:
        root.removeHandler(foreign)


def test_alembic_plugin_startup_noise_is_quieted() -> None:
    import logging

    from app.core.logging import configure_logging

    configure_logging("INFO", "text")
    assert logging.getLogger("alembic.runtime.plugins").level == logging.WARNING
    assert logging.getLogger("alembic.runtime.migration").level == logging.NOTSET  # kept
