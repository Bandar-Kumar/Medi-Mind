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
