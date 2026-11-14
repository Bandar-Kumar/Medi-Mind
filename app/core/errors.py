"""MediMind's exception hierarchy.

Expected failures (bad configuration, and later invalid uploads or parse failures) raise a
subclass of ``MediMindError`` and get a clear message. Any other exception is treated as a bug:
the entry point logs its full traceback.

Add new subclasses in the phase that needs them.
"""


class MediMindError(Exception):
    """Base class for all expected, application-level errors."""


class ConfigurationError(MediMindError):
    """Settings are missing or invalid."""


class DocumentValidationError(MediMindError):
    """An uploaded document was rejected before parsing (Phase 2).

    ``reason`` is a stable code such as ``"too_large"``, ``"not_pdf"`` or ``"encrypted"``. The
    message never contains document content; it may name a limit (e.g. the page limit).
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class DocumentParseError(MediMindError):
    """Parsing a validated document failed or timed out (Phase 2).

    ``kind`` is a stable category (e.g. ``"timeout"``, ``"parser_failed"``). The message is fixed
    text plus the parser's exception class name; the parser's own message is never included,
    because it can contain document content.
    """

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


class DocumentStorageError(MediMindError):
    """Storing an original document or a derived file failed (Phase 2).

    ``kind`` is a stable category such as ``"integrity_mismatch"`` (a file already at the
    content-addressed path has different bytes; it is never overwritten), ``"outside_data_dir"``,
    ``"invalid_key"`` or ``"write_failed"``. The message never contains the original filename,
    document content or the operating system's error text.
    """

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


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
