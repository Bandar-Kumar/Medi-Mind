"""The longitudinal evaluation runner end to end on medimind_test (Phase 4).

Uses a scenario patient generated with a seed that is NOT the frozen dataset's, so neither dev
nor held-out data is touched. Loading goes through the production write path; scoring through
the services. A correct system scores every target. docs/PHASE_4_PLAN.md §10.
"""

import random
import uuid

import pytest
from sqlalchemy import Engine, text

from app.core.config import Settings
from app.extraction.reference import load_reference
from app.services.reference_data import seed_reference
from evals import run_longitudinal as runner
from evals.synthetic import longitudinal as gen
from tests.integration._guards import assert_is_test_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]


@pytest.fixture
def created(test_db_session_factory, test_engine: Engine, app_settings: Settings):
    patients: list[uuid.UUID] = []
    yield patients
    with test_engine.begin() as connection:
        assert_is_test_database(connection, app_settings.db_name)
        for patient in patients:
            for table in ("conflicts", "documents", "pipeline_runs", "patients"):
                column = "id" if table == "patients" else "patient_id"
                connection.execute(text(f"DELETE FROM {table} WHERE {column} = :p"), {"p": patient})


def test_a_scenario_patient_scores_every_target(created) -> None:
    seed_reference()
    rng = random.Random(4242)  # not the frozen dataset's seed
    first = gen.scenario_patient(1, rng)
    second = gen.scenario_patient(2, rng)
    reference = load_reference()
    loaded = {"patients": {}}
    for patient in (first, second):
        item = runner.load_patient(patient, reference)
        created.append(uuid.UUID(item["patient_id"]))
        loaded["patients"][patient["patient_key"]] = item
    assert [r.split(":")[1] for r in loaded["patients"]["L01"]["refusals"]] == ["identity_mismatch"]
    report = runner.evaluate("test", [first, second], loaded)
    metrics = report["metrics"]
    failed = {k: metrics[k] for k, ok in report["targets_met"].items() if ok is False}
    assert failed == {}, failed
    assert metrics["conflict_recall"] == 1.0 and metrics["duplicate_recall"] == 1.0
    assert metrics["patient_isolation_leaks"] == 0


def test_the_scoring_detects_a_broken_system(created, monkeypatch) -> None:
    """Negative control: with duplicate detection disabled, the metrics must drop."""
    from app.services import longitudinal as service

    seed_reference()
    patient = gen.scenario_patient(3, random.Random(777))
    item = runner.load_patient(patient, load_reference())
    created.append(uuid.UUID(item["patient_id"]))
    loaded = {"patients": {patient["patient_key"]: item}}
    monkeypatch.setattr(service, "find_duplicates", lambda observations, reference: ())
    metrics = runner.evaluate("test", [patient], loaded)["metrics"]
    assert metrics["duplicate_recall"] == 0.0
    assert metrics["timeline_entry_precision"] < 1.0  # exact copies are no longer collapsed
    assert metrics["trend_point_accuracy"] < 1.0
