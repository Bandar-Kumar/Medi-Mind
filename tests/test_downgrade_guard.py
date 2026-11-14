"""Unit tests for the downgrade guard (no database; no downgrade or stamp is executed).

The guard lives in app/records/migrations.py and is wired into migrations/env.py:
- ``alembic downgrade`` (any target) needs ``-x confirm_downgrade=<connected database>``;
- backward ``stamp`` steps and ``stamp --purge`` need the same confirmation;
- forward migrations and forward stamps are never restricted.

Steps below come from Alembic's own revision planner (``ScriptDirectory``), which only reads the
migration scripts; nothing connects to a database.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import CommandLine
from alembic.script import ScriptDirectory
from alembic.util import CommandError

from app.records import migrations
from app.records.migrations import (
    DowngradeNotConfirmed,
    backward_step_guard,
    confirmation_values,
    downgrade_refusal,
    is_cli_downgrade,
    require_confirmation,
)

ENV_PY = Path(__file__).resolve().parent.parent / "migrations" / "env.py"
DB = "medimind"


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(migrations.alembic_config())


def _apply(guard, steps) -> None:
    for step in steps:
        guard(ctx=None, step=step.info, heads=set(), run_args={})


# --- Confirmation values -------------------------------------------------------------------------


def test_confirmation_values_from_x_arguments_and_code() -> None:
    assert confirmation_values(["confirm_downgrade=medimind"], {}) == ["medimind"]
    assert confirmation_values([], {"confirm_downgrade": "medimind_test"}) == ["medimind_test"]
    assert confirmation_values(["other=1", "dbname=x"], {}) == []  # unrelated -x arguments
    assert confirmation_values(["confirm_downgrade"], {}) == [""]  # no "=": malformed
    assert confirmation_values(["confirm_downgrade=a", "confirm_downgrade=b"], {}) == ["a", "b"]
    assert confirmation_values([], {"confirm_downgrade": 123}) == [""]  # not a string


def test_exact_confirmation_is_accepted() -> None:
    assert downgrade_refusal(["medimind"], DB) is None
    require_confirmation("downgrade", ["medimind"], DB)  # does not raise


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ([], "no confirmation given"),
        (["medimind_test"], "does not match the connected database"),
        (["MEDIMIND"], "does not match"),  # case-sensitive
        ([" medimind"], "does not match"),  # no trimming
        (["medimind "], "does not match"),
        (["medimind=x"], "does not match"),
        ([""], "malformed confirmation"),  # `-x confirm_downgrade` or `=` with no value
        (["medimind", "medimind"], "given 2 times"),  # duplicates are refused
    ],
)
def test_missing_incorrect_or_malformed_confirmation_is_refused(
    values: list[str], reason: str
) -> None:
    refusal = downgrade_refusal(values, DB)
    assert refusal is not None and reason in refusal
    with pytest.raises(DowngradeNotConfirmed, match=f"Refusing to downgrade database '{DB}'"):
        require_confirmation("downgrade", values, DB)


def test_refusal_is_a_clean_alembic_command_error() -> None:
    # The alembic CLI prints CommandError as "FAILED: <message>" without a traceback.
    assert issubclass(DowngradeNotConfirmed, CommandError)
    with pytest.raises(DowngradeNotConfirmed) as excinfo:
        require_confirmation("downgrade", [], DB)
    assert "-x confirm_downgrade=medimind" in str(excinfo.value)  # tells the user how


# --- Command-line detection: every downgrade target --------------------------------------------


@pytest.mark.parametrize("target", ["-1", "base", "0001", "0002", "head-1"])
def test_cli_downgrade_is_detected_for_every_target(target: str) -> None:
    options = CommandLine().parser.parse_args(["downgrade", target])
    assert is_cli_downgrade(options)


@pytest.mark.parametrize(
    "argv",
    [["upgrade", "head"], ["stamp", "base"], ["current"], ["check"], ["history"]],
)
def test_other_commands_are_not_cli_downgrades(argv: list[str]) -> None:
    assert not is_cli_downgrade(CommandLine().parser.parse_args(argv))


def test_programmatic_calls_have_no_cli_options() -> None:
    assert not is_cli_downgrade(None)  # Python API: covered by the per-step backstop instead


def test_confirmation_syntax_parses_before_the_subcommand() -> None:
    options = CommandLine().parser.parse_args(
        ["-x", "confirm_downgrade=medimind", "downgrade", "-1"]
    )
    assert is_cli_downgrade(options)
    assert confirmation_values(options.x, {}) == ["medimind"]


# --- Per-step backstop: downgrades (real steps from Alembic's planner) --------------------------


@pytest.mark.parametrize(("destination", "steps"), [("base", 3), ("0002", 1), ("0001", 2)])
def test_backstop_refuses_unconfirmed_downgrade_steps(destination: str, steps: int) -> None:
    planned = _script()._downgrade_revs(destination, "0003")
    assert len(planned) == steps and all(not s.info.is_upgrade for s in planned)
    with pytest.raises(DowngradeNotConfirmed, match="Refusing to downgrade"):
        _apply(backward_step_guard([], DB), planned)
    with pytest.raises(DowngradeNotConfirmed):
        _apply(backward_step_guard(["medimind_test"], DB), planned)


def test_backstop_allows_confirmed_downgrade_to_base() -> None:
    _apply(backward_step_guard(["medimind"], DB), _script()._downgrade_revs("base", "0003"))


def test_backstop_never_restricts_upgrades() -> None:
    _apply(backward_step_guard([], DB), _script()._upgrade_revs("head", None))


# --- Per-step backstop: stamps ------------------------------------------------------------------


@pytest.mark.parametrize("destination", ["0001", "0002", "base"])
def test_backward_stamp_without_confirmation_is_refused(destination: str) -> None:
    planned = _script()._stamp_revs(destination, "0003")
    assert planned and all(s.info.is_stamp and not s.info.is_upgrade for s in planned)
    with pytest.raises(DowngradeNotConfirmed, match="Refusing to stamp backwards"):
        _apply(backward_step_guard([], DB), planned)


def test_backward_stamp_with_exact_confirmation_is_allowed() -> None:
    _apply(backward_step_guard(["medimind"], DB), _script()._stamp_revs("0001", "0003"))


@pytest.mark.parametrize(("destination", "current"), [("0003", "0001"), ("head", None)])
def test_forward_stamp_is_not_restricted(destination: str, current: str | None) -> None:
    planned = _script()._stamp_revs(destination, current)
    assert planned and all(s.info.is_stamp and s.info.is_upgrade for s in planned)
    _apply(backward_step_guard([], DB), planned)  # no confirmation needed


def test_stamp_to_the_current_revision_plans_nothing() -> None:
    assert _script()._stamp_revs("0003", "0003") == []


def test_stamp_and_migration_steps_are_distinguished() -> None:
    down = backward_step_guard([], DB)
    migration_step = SimpleNamespace(is_upgrade=False, is_stamp=False)
    stamp_step = SimpleNamespace(is_upgrade=False, is_stamp=True)
    with pytest.raises(DowngradeNotConfirmed, match="Refusing to downgrade"):
        down(ctx=None, step=migration_step, heads=set(), run_args={})
    with pytest.raises(DowngradeNotConfirmed, match="Refusing to stamp backwards"):
        down(ctx=None, step=stamp_step, heads=set(), run_args={})


# --- Wiring in migrations/env.py (static: env.py cannot run without a database) ---------------


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(ENV_PY.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(node: ast.AST) -> set[str]:
    names = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            func = call.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def _configure_keywords(node: ast.AST) -> set[str]:
    for call in ast.walk(node):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "configure"
        ):
            return {k.arg for k in call.keywords if k.arg}
    raise AssertionError("context.configure not found")


def test_online_path_enforces_the_guard() -> None:
    online = _function("_run_with_connection")
    calls = _calls(online)
    assert {"confirmation_values", "is_cli_downgrade", "require_confirmation"} <= calls
    assert "backward_step_guard" in calls
    keywords = _configure_keywords(online)
    assert "on_version_apply" in keywords
    # The backstop's rollback relies on ONE transaction for the whole command.
    assert "transaction_per_migration" not in keywords
    source = ast.unparse(online)
    assert "SELECT current_database()" in source  # the actual connected database
    assert ".purge" in source  # stamp --purge is guarded before it runs


def test_settings_path_owns_one_transaction() -> None:
    # engine.begin(): commit on success, rollback on any error (including a refusal). With
    # engine.connect(), the guard's SELECT would leave a transaction Alembic never commits.
    assert "engine.begin()" in ast.unparse(_function("run_migrations_online"))


def test_offline_path_is_not_guarded() -> None:
    # --sql connects to nothing and only prints SQL, so there is nothing to protect.
    offline = _function("run_migrations_offline")
    assert "on_version_apply" not in _configure_keywords(offline)
