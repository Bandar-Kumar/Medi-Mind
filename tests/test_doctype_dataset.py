"""Tests for the ``doctype_v0`` dataset and the offline classification evaluation (Task 2.7).

No database and no Docling: the evaluation tests use a stand-in page parser. The held-out split of
the committed dataset is only hashed here, never parsed or classified.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from app.ingestion.validation import validate_pdf_bytes
from evals import run_classification as rc
from evals.dataset import DatasetError, load_dataset
from evals.synthetic import doctype
from evals.synthetic.render import DISCLAIMER

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED = REPO_ROOT / "data" / "synthetic" / "doctype_v0"
FROZEN_HELDOUT_SHA256 = "8d71ffb5f01fd7bcbd1cd10a1047482ade0d2607ef1a7891e0bd602522d7a01d"


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("doctype") / "doctype_v0"
    doctype.build_dataset(out)
    return out


# --- dataset ----------------------------------------------------------------------------------


def test_generation_is_deterministic_and_labels_every_kind() -> None:
    first = doctype.generate_documents(doctype.DEFAULT_SEED, 4)
    assert first == doctype.generate_documents(doctype.DEFAULT_SEED, 4)
    for patient, documents in first:
        assert [d.kind for d in documents] == [kind for kind, _, _ in doctype.KINDS]
        for document, (_, suffix, true_type) in zip(documents, doctype.KINDS, strict=True):
            assert document.document_id == f"{patient.patient_key}-{suffix}"
            assert document.true_type == true_type in (doctype.LAB_REPORT, doctype.OTHER)


def test_every_document_is_a_valid_pdf_with_the_synthetic_disclaimer() -> None:
    for patient, documents in doctype.generate_documents(doctype.DEFAULT_SEED, 2):
        for document in documents:
            data = doctype.render_document(patient, document)
            assert data == doctype.render_document(patient, document)  # byte-reproducible
            validate_pdf_bytes(data, max_bytes=10_000_000, max_pages=10)
            pdf = pdfium.PdfDocument(data)
            try:
                for page in pdf:
                    assert DISCLAIMER in page.get_textpage().get_text_range()
            finally:
                pdf.close()


def test_the_split_is_by_patient_and_disjoint(built) -> None:
    dataset = load_dataset(built)
    dev, heldout = dataset.manifest["split"]["dev"], dataset.manifest["split"]["heldout"]
    assert set(dev).isdisjoint(heldout) and len(dev) + len(heldout) == doctype.DEFAULT_PATIENTS
    for split, keys in (("dev", dev), ("heldout", heldout)):
        for document in rc.documents(dataset, split):
            assert document["document_id"].split("-")[0] in keys


def test_the_committed_dataset_is_reproducible_and_frozen(built) -> None:
    committed = load_dataset(COMMITTED)  # verifies every file hash and the held-out digest
    assert committed.manifest["heldout_sha256"] == FROZEN_HELDOUT_SHA256
    rebuilt = json.loads((built / "manifest.json").read_text(encoding="utf-8"))
    assert rebuilt == committed.manifest  # same seed, same bytes: provenance is reproducible
    assert committed.manifest["synthetic"] is True


def test_a_changed_heldout_file_or_split_is_refused(built, tmp_path) -> None:
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(built, copy)
    manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    heldout_pdf = f"pdfs/{manifest['split']['heldout'][0]}-LS.pdf"
    (copy / heldout_pdf).write_bytes(b"%PDF-1.4 tampered")
    with pytest.raises(DatasetError):
        load_dataset(copy)

    moved = tmp_path / "moved"
    shutil.copytree(built, moved)
    manifest["split"]["dev"].append(manifest["split"]["heldout"].pop())
    (moved / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DatasetError):
        load_dataset(moved)


def test_a_dataset_is_never_overwritten(built) -> None:
    with pytest.raises(FileExistsError):
        doctype.build_dataset(built)


# --- evaluation ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    page_number: int
    text: str
    has_tables: bool


def _parser(text: str, seen: list[bytes] | None = None):
    def parse(data: bytes):
        if seen is not None:
            seen.append(data)
        return [Page(1, text, False)]

    return parse


def _outcome(true: str, predicted: str, kind: str = "k") -> rc.Outcome:
    return rc.Outcome("P01-XX", kind, "v", true, predicted, 0.5, ())


def test_scores_by_hand() -> None:
    outcomes = [
        _outcome("lab_report", "lab_report"),
        _outcome("lab_report", "unknown"),
        _outcome("other", "other"),
        _outcome("other", "lab_report"),
    ]
    scores = rc.score(outcomes)
    assert scores["accuracy"] == 0.5 and scores["unknown_rate"] == 0.25
    assert scores["decided_accuracy"] == pytest.approx(2 / 3)
    assert scores["lab_report_precision"] == 0.5 and scores["lab_report_recall"] == 0.5
    assert scores["confusion"] == {
        "lab_report": {"lab_report": 1, "other": 0, "unknown": 1},
        "other": {"lab_report": 1, "other": 1, "unknown": 0},
    }


def test_precision_without_lab_predictions_is_not_measured() -> None:
    scores = rc.score([_outcome("lab_report", "unknown"), _outcome("other", "other")])
    assert scores["lab_report_precision"] is None and scores["lab_report_recall"] == 0.0
    with pytest.raises(ValueError):
        rc.score([])
    with pytest.raises(ValueError):
        rc.score([_outcome("unknown", "unknown")])


def test_dev_evaluation_reads_dev_documents_only(built) -> None:
    dataset = load_dataset(built)
    seen: list[bytes] = []
    outcomes = rc.evaluate(dataset, "dev", _parser("Rx\nTab. Fictomol 500 mg 1-0-1", seen))
    heldout_bytes = {
        dataset.pdf_path(d["document_id"]).read_bytes() for d in rc.documents(dataset, "heldout")
    }
    assert len(seen) == len(outcomes) == len(rc.documents(dataset, "dev"))
    assert not heldout_bytes & set(seen)
    assert {o.predicted for o in outcomes} == {"other"}


def test_reports_hold_no_page_text_and_are_never_overwritten(built, tmp_path) -> None:
    dataset = load_dataset(built)
    outcomes = rc.evaluate(dataset, "dev", _parser("Asha Fictional\nRx\nTab. X 10 mg 1-0-1"))
    json_path, md_path = rc.write_report(dataset, "dev", outcomes, tmp_path)
    for path in (json_path, md_path):
        assert "Asha" not in path.read_text(encoding="utf-8")
    assert "not a probability" in md_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        rc.write_report(dataset, "dev", outcomes, tmp_path)


def test_heldout_needs_a_matching_freeze_and_runs_once(built, tmp_path, monkeypatch) -> None:
    dataset = load_dataset(built)
    parse = _parser("Dear Dr. Madeup,\nYours sincerely,")
    with pytest.raises(rc.EvaluationError, match="no frozen configuration"):
        rc.run_heldout(dataset, tmp_path, parse)
    with pytest.raises(rc.EvaluationError, match="no dev report"):
        rc.freeze(dataset, tmp_path)

    rc.write_report(dataset, "dev", rc.evaluate(dataset, "dev", parse), tmp_path)
    selection = json.loads(rc.freeze(dataset, tmp_path).read_text(encoding="utf-8"))
    assert selection["rules_version"] == rc.CLASSIFIER_RULES_VERSION
    assert selection["heldout_sha256"] == dataset.manifest["heldout_sha256"]
    with pytest.raises(rc.EvaluationError, match="already frozen"):
        rc.freeze(dataset, tmp_path)

    monkeypatch.setattr(rc, "CLASSIFIER_RULES_VERSION", "999")  # rules changed after freeze
    with pytest.raises(rc.EvaluationError, match="rules version changed"):
        rc.check_heldout_allowed(dataset, tmp_path)
    monkeypatch.undo()

    seen: list[bytes] = []
    rc.run_heldout(dataset, tmp_path, _parser("Dear Dr. Madeup,\nYours sincerely,", seen))
    assert len(seen) == len(rc.documents(dataset, "heldout"))
    with pytest.raises(rc.EvaluationError, match="never re-run"):
        rc.run_heldout(dataset, tmp_path, parse)


def test_a_changed_dev_report_blocks_the_heldout_run(built, tmp_path) -> None:
    dataset = load_dataset(built)
    parse = _parser("Rx\nTab. X 10 mg 1-0-1")
    json_path, _ = rc.write_report(dataset, "dev", rc.evaluate(dataset, "dev", parse), tmp_path)
    rc.freeze(dataset, tmp_path)
    json_path.write_text("{}", encoding="utf-8")
    with pytest.raises(rc.EvaluationError, match="dev report is missing or changed"):
        rc.check_heldout_allowed(dataset, tmp_path)


def test_the_committed_dev_reports_match_the_rules_versions_they_name() -> None:
    reports = REPO_ROOT / "evals" / "reports" / "doctype_v0"
    current = json.loads(
        (reports / f"classification_dev_rules_v{rc.CLASSIFIER_RULES_VERSION}.json").read_text(
            encoding="utf-8"
        )
    )
    assert current["split"] == "dev" and current["heldout_sha256"] == FROZEN_HELDOUT_SHA256
    # Frozen by the owner on 2026-11-17 (rules v2); the held-out split was evaluated once.
    selection = json.loads((reports / rc.SELECTION).read_text(encoding="utf-8"))
    assert selection["rules_version"] == rc.CLASSIFIER_RULES_VERSION
    assert selection["heldout_sha256"] == FROZEN_HELDOUT_SHA256
    assert (reports / rc.HELDOUT_DONE).is_file()
    heldout = json.loads(
        (reports / f"classification_heldout_rules_v{rc.CLASSIFIER_RULES_VERSION}.json").read_text(
            encoding="utf-8"
        )
    )
    assert heldout["split"] == "heldout" and heldout["rules_version"] == selection["rules_version"]
