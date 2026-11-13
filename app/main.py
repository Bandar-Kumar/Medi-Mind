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


COMMANDS: dict[str, Callable[[Settings], int]] = {"health": run_health}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app", description="MediMind command line.")
    parser.add_argument("--version", action="version", version=f"MediMind {__version__}")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("health", help="check that the environment is set up correctly")
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
    except Exception:  # a bug: log the full traceback so it can be fixed
        logger.exception("Unexpected error while running %r", args.command)
        return EXIT_FAILURE
