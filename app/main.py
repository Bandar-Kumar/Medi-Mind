"""MediMind's single entry point: load settings, set up logging, run a command, return an exit code.

Exit codes: 0 = success, 1 = health check failed or unexpected error, 2 = invalid configuration.
Run with: ``uv run python -m app health``
"""

import argparse
import logging
import sys
from collections.abc import Callable

from app import __version__
from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError, MediMindError
from app.core.health import format_report, overall_status, run_health_checks
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG_ERROR = 2


def run_health(settings: Settings) -> int:
    results = run_health_checks(settings)
    print(format_report(results, settings), flush=True)
    status = overall_status(results)
    logger.info("Health check finished: %s", status)
    return EXIT_FAILURE if status == "fail" else EXIT_OK


def run_migrate(settings: Settings) -> int:
    """Upgrade the configured database to the latest schema revision (upgrade only).

    Database errors propagate to ``main``, which logs them sanitized (no driver text).
    """
    from app.records.migrations import run_upgrade

    head = run_upgrade(settings)
    print(f"Database schema is at revision {head} (latest).", flush=True)
    return EXIT_OK


COMMANDS: dict[str, Callable[[Settings], int]] = {"health": run_health, "migrate": run_migrate}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app", description="MediMind command line.")
    parser.add_argument("--version", action="version", version=f"MediMind {__version__}")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("health", help="check that the environment is set up correctly")
    subcommands.add_parser(
        "migrate",
        help="upgrade the database schema to the latest migration (downgrades: alembic CLI only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK

    # Settings come first: logging cannot be configured until we know the log level.
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(f"Configuration error. {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    configure_logging(settings.log_level, settings.log_format)

    try:
        return COMMANDS[args.command](settings)
    except MediMindError as exc:  # expected failure: clear message, no traceback
        logger.error("%s", exc)
        return EXIT_FAILURE
    except Exception as exc:
        # Database errors: log only the sanitized category, class name and SQLSTATE. Their raw
        # messages and tracebacks can contain driver text, so they are never logged.
        from app.records.db import describe_db_error, is_database_error

        if is_database_error(exc):
            logger.error(
                "Database error while running %r: %s", args.command, describe_db_error(exc)
            )
            return EXIT_FAILURE
        # Anything else is a bug: log the full traceback so it can be fixed.
        logger.exception("Unexpected error while running %r", args.command)
        return EXIT_FAILURE
