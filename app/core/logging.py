"""Centralized logging setup.

Call ``configure_logging`` once at startup; every module then uses
``logger = logging.getLogger(__name__)``.

Rules:
- Logs go to stderr, so command output on stdout stays clean.
- Never log document text, patient values or secret settings (ARCHITECTURE.md section 12).
- ``configure_logging`` manages only the handler it created itself (tagged with
  ``_MEDIMIND_HANDLER``). Calling it again replaces that handler; handlers owned by other code,
  such as pytest or third-party libraries, are never touched.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Literal, TextIO

LogFormat = Literal["text", "json"]

_MEDIMIND_HANDLER = "_medimind_handler"
_TEXT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Attributes every LogRecord has; anything else on a record came from `extra=...`.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Format each record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    level: str = "INFO", fmt: LogFormat = "text", stream: TextIO | None = None
) -> logging.Handler:
    """Install (or replace) MediMind's handler on the root logger and return it.

    Args:
        level: Minimum level to output, e.g. "INFO".
        fmt: "text" for humans or "json" for machine-readable lines.
        stream: Where to write; defaults to the current ``sys.stderr``.
    """
    root = logging.getLogger()

    for handler in list(root.handlers):
        if getattr(handler, _MEDIMIND_HANDLER, False):
            root.removeHandler(handler)
            handler.close()

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(
        JsonFormatter() if fmt == "json" else logging.Formatter(_TEXT_FORMAT, _DATE_FORMAT)
    )
    setattr(handler, _MEDIMIND_HANDLER, True)

    root.addHandler(handler)
    root.setLevel(level)
    return handler
