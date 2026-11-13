"""MediMind's exception hierarchy.

Expected failures (bad configuration, and later invalid uploads or parse failures) raise a
subclass of ``MediMindError`` and get a clear message. Any other exception is treated as a bug:
the entry point logs its full traceback.

Add new subclasses in the phase that needs them (e.g. ``DocumentValidationError`` in Phase 2).
"""


class MediMindError(Exception):
    """Base class for all expected, application-level errors."""


class ConfigurationError(MediMindError):
    """Settings are missing or invalid."""


class DatabaseError(MediMindError):
    """A database operation failed.

    The message is always sanitized: a fixed category plus the driver's exception class name and
    SQLSTATE code. It never contains the driver's message text, credentials, connection URLs, SQL
    parameters or record contents. It is raised outside the ``except`` block that caught the
    driver error, so it carries no chained ``__cause__``/``__context__`` either.

    ``kind`` is a stable category such as ``"unreachable"`` or ``"authentication_failed"``.
    """

    def __init__(self, message: str, *, kind: str = "unexpected") -> None:
        super().__init__(message)
        self.kind = kind
