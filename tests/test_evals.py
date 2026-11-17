"""Unit tests for the synthetic generator, dataset manifest, question set, metrics and reports."""

import json
import math
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from app.ingestion.validation import validate_pdf_bytes
from app.retrieval.query import QuestionGate, classify_question
from evals import metrics
from evals.dataset import DatasetError, load_dataset
from evals.questions import ABSTAIN, ANSWER, OUT_OF_SCOPE, generate_questions
from evals.report import RunConfig, RunReport, to_markdown, write_reports
from evals.run_retrieval import Outcome, ingestion_timing, score
from evals.synthetic.build import build_dataset
from evals.synthetic.generator import generate, split_patients, to_json
from evals.synthetic.render import DISCLAIMER, render_report


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic") / "lab_test"
    build_dataset(root, seed=11, patients=5)
    return load_dataset(root)


# --- generator and renderer ----------------------------------------------------------------------


def test_generation_is_deterministic_and_marks_nothing_real() -> None:
    assert to_json(generate(3, 4)) == to_json(generate(3, 4))
    assert to_json(generate(3, 4)) != to_json(generate(4, 4))
    from evals.synthetic.generator import _LAST_NAMES

    for patient in generate(3, 4):
        assert patient.display_name.split()[-1] in _LAST_NAMES  # visibly fictional surnames
        assert 3 <= len(patient.reports) <= 5


def test_split_is_by_patient_disjoint_and_seventy_thirty() -> None:
    split = split_patients([f"P{n:02d}" for n in range(1, 13)], seed=1)
    assert len(split["dev"]) == 8 and len(split["heldout"]) == 4
    assert not set(split["dev"]) & set(split["heldout"])


def test_every_ground_truth_row_is_printed_on_its_own_page() -> None:
    patient = generate(5, 2)[0]
    for report in patient.reports:
        data = render_report(patient, report)
        assert render_report(patient, report) == data  # byte-identical
        assert validate_pdf_bytes(data, max_bytes=10**7, max_pages=20).page_count == len(
            report.pages
        )
        pdf = pdfium.PdfDocument(data)
        for index, page in enumerate(report.pages):
            text = pdf[index].get_textpage().get_text_range()
            assert DISCLAIMER in text and report.date_printed in text
            for row in page.rows:
                assert row.printed_name in text and row.value in text
            panel_titles = {pt for p in report.pages for pt in p.panels if p is not page}
            for panel in panel_titles - set(page.panels):  # other pages' panels are not here
                from evals.synthetic.catalog import PANEL_BY_KEY

                assert PANEL_BY_KEY[panel].title not in text


# --- dataset manifest ----------------------------------------------------------------------------


def test_the_dataset_loads_and_freezes_the_heldout_split(dataset) -> None:
    manifest = dataset.manifest
    assert manifest["synthetic"] is True and manifest["patients"] == 5
    assert len(manifest["files"]) == manifest["reports"] + 1
    assert dataset.version.startswith("lab_v0@")
    assert {p["patient_key"] for p in dataset.patients("heldout")} == set(
        manifest["split"]["heldout"]
    )


def test_a_changed_file_or_split_is_refused(tmp_path) -> None:
    root = tmp_path / "ds"
    build_dataset(root, seed=2, patients=3)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    pdf = next(iter(p for p in manifest["files"] if p.startswith("pdfs/")))
    (root / pdf).write_bytes((root / pdf).read_bytes() + b"%")
    with pytest.raises(DatasetError):
        load_dataset(root)


def test_the_heldout_digest_detects_a_moved_patient(tmp_path) -> None:
    root = tmp_path / "ds"
    build_dataset(root, seed=2, patients=4)
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    moved = manifest["split"]["dev"].pop()
    manifest["split"]["heldout"].append(moved)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DatasetError):
        load_dataset(root)


def test_a_dataset_is_never_overwritten(dataset) -> None:
    with pytest.raises(FileExistsError):
        build_dataset(dataset.root, seed=11, patients=5)


# --- questions -----------------------------------------------------------------------------------


def test_question_labels_are_exact_by_construction(dataset) -> None:
    patients = dataset.patients("dev")
    questions = generate_questions(patients, seed=7)
    assert questions == generate_questions(patients, seed=7)
    pages = {
        (r["report_id"], pg["page_number"]): {row["analyte_key"] for row in pg["rows"]}
        for p in dataset.ground_truth
        for r in p["reports"]
        for pg in r["pages"]
    }
    owners = {r["report_id"]: p["patient_key"] for p in dataset.ground_truth for r in p["reports"]}
    for question in questions:
        if question.expect == ANSWER:
            assert question.relevant
            assert all(owners[report] == question.patient_key for report, _ in question.relevant)
            assert all(key in pages for key in question.relevant)
            assert classify_question(question.text) is QuestionGate.ALLOWED
        elif question.expect == ABSTAIN:
            assert not question.relevant
            assert classify_question(question.text) is QuestionGate.ALLOWED
        else:
            assert question.expect == OUT_OF_SCOPE
            assert classify_question(question.text) is not QuestionGate.ALLOWED


def test_leak_probes_ask_for_analytes_only_other_patients_of_the_split_have(dataset) -> None:
    dev = dataset.patients("dev")
    measured = {
        p["patient_key"]: {
            row["analyte_key"] for r in p["reports"] for pg in r["pages"] for row in pg["rows"]
        }
        for p in dev  # dev only: another split's patients are not ingested with dev
    }
    from evals.synthetic.catalog import ANALYTES

    names = {a.name: a.key for a in ANALYTES}
    probes = [q for q in generate_questions(dev, seed=7) if q.kind == "leak"]
    assert probes  # the fixture's dev split has genuine cross-patient probes
    for question in probes:
        key = next(k for name, k in names.items() if name in question.text)
        assert key not in measured[question.patient_key]
        assert any(key in m for p, m in measured.items() if p != question.patient_key)


def test_dev_questions_do_not_depend_on_any_other_patient(dataset) -> None:
    """Questions for a split are a function of that split's ground truth alone."""
    dev = dataset.patients("dev")
    assert generate_questions(dev, seed=7) == generate_questions(list(dev), seed=7)
    with pytest.raises(TypeError):
        generate_questions(dev, dataset.ground_truth, seed=7)  # no second patient list any more


# --- metrics (hand-computed) ---------------------------------------------------------------------


def test_ranking_metrics_by_hand() -> None:
    ranked = ["x", "a", "y", "b"]
    relevant = {"a": 1, "b": 1}
    assert metrics.recall_at_k(ranked, relevant, 2) == 0.5
    assert metrics.recall_at_k(ranked, relevant, 4) == 1.0
    assert (
        metrics.hit_at_k(ranked, relevant, 1) == 0.0
        and metrics.hit_at_k(ranked, relevant, 2) == 1.0
    )
    assert metrics.reciprocal_rank(ranked, relevant) == 0.5
    assert metrics.reciprocal_rank(["x"], relevant) == 0.0
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    idcg = 1 + 1 / math.log2(3)
    assert metrics.ndcg_at_k(ranked, relevant, 10) == pytest.approx(dcg / idcg)
    assert metrics.ndcg_at_k(["b", "a"], {"a": 2, "b": 1}, 2) == pytest.approx(
        (1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3))
    )


def test_binary_scores_rates_and_percentiles_by_hand() -> None:
    scores = metrics.binary_scores([True, True, False, False], [True, False, True, False])
    assert (scores.precision, scores.recall, scores.f1) == (0.5, 0.5, 0.5)
    assert metrics.rate([True, False, False, False]) == 0.25
    assert metrics.percentile([5, 1, 4, 2, 3], 50) == 3
    assert metrics.percentile([5, 1, 4, 2, 3], 95) == 5
    with pytest.raises(ValueError):
        metrics.recall_at_k(["a"], {}, 1)


def test_score_combines_answerable_abstention_safety_and_leaks() -> None:
    from evals.questions import Question

    questions = [
        Question("q1", "P01", "value", "t", ANSWER, {("R1", 1): 1}),
        Question("q2", "P01", "unanswerable", "t", ABSTAIN),
        Question("q3", "P01", "advice", "t", OUT_OF_SCOPE),
        Question("q4", "P01", "leak", "t", ABSTAIN),
        Question("q5", "P01", "leak", "t", ABSTAIN),
        Question("q6", "P01", "injection", "t", OUT_OF_SCOPE),
    ]
    outcomes = [
        Outcome("answered", (("R2", 1), ("R1", 1)), 3.0, 0, 100),
        Outcome("answered", (("?", 1),), 1.0, 1, 50),  # a foreign page on a NON-leak question
        Outcome("out_of_scope", (), None, 0, 1),
        Outcome("answered", (("?", 2),), 2.0, 1, 70),  # a leak probe that leaked
        Outcome("insufficient_evidence", (), None, 0, 60),  # a leak probe that abstained
        Outcome("answered", (), None, 0, 1),  # an injection that was NOT refused
    ]
    values, counts = score(questions, outcomes)
    assert values["recall@1"] == 0.0 and values["recall@3"] == 1.0 and values["mrr"] == 0.5
    # Abstention uses answerable + ordinary unanswerable only (q1, q2), not the leak probes.
    assert values["abstain_recall"] == 0.0 and values["abstain_precision"] == 0.0
    assert values["advice_refusal_rate"] == 1.0 and values["injection_refusal_rate"] == 0.0
    # Leakage: denominator = the two leak probes only.
    assert values["leak_rate"] == 0.5 and values["leak_probe_abstain_rate"] == 0.5
    assert values["questions_with_foreign_pages"] == 2  # q2 and q4, across all kinds
    assert "refusal_rate" not in values
    assert counts == {
        "advice": 1,
        "injection": 1,
        "leak": 2,
        "unanswerable": 1,
        "value": 1,
        "questions": 6,
    }


def test_score_reports_missing_probe_kinds_as_not_measured() -> None:
    from evals.questions import Question

    values, _ = score(
        [Question("q1", "P01", "value", "t", ANSWER, {("R1", 1): 1})],
        [Outcome("answered", (("R1", 1),), 1.0, 0, 10)],
    )
    assert values["leak_rate"] is None and values["leak_probe_abstain_rate"] is None
    assert values["advice_refusal_rate"] is None and values["injection_refusal_rate"] is None
    assert values["questions_with_foreign_pages"] == 0


def test_ingestion_timing_uses_recorded_wall_clock_per_page() -> None:
    timings = {
        "P01-R01": {"pages": 2, "index_wall_ms": 300, "index_ms": 250, "parse_ms": 7000},
        "P01-R02": {"pages": 1, "index_wall_ms": 50, "index_ms": 40, "parse_ms": 3000},
        "P04-R01": {"pages": 2, "index_wall_ms": None, "index_ms": None, "parse_ms": None},
    }
    assert ingestion_timing(timings) == {
        "reports_timed": 2,
        "reports_not_timed": 1,  # indexed before a crash: counted, never estimated
        "pages_timed": 3,
        "index_ms_per_page": pytest.approx(350 / 3),
        "index_ms_per_page_p50": 50.0,
        "index_ms_per_page_p95": 150.0,
    }
    empty = ingestion_timing({})
    assert empty["index_ms_per_page"] is None and empty["reports_timed"] == 0


def test_reports_say_not_measured_and_are_never_overwritten(tmp_path: Path) -> None:
    config = RunConfig("lab_v0@x", "dev", "lexical", 50, 60, 20, 10, None, "1", None, None)
    report = RunReport(config, {"mrr": 0.5, "latency_p50_ms": None}, {"questions": 2})
    assert "not measured" in to_markdown([report], "t")
    json_path, md_path = write_reports(
        [report], tmp_path, "run", "t", ingestion={"index_ms_per_page": None, "pages_timed": 0}
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["runs"][0]["metrics"]["mrr"] == 0.5
    assert payload["ingestion"] == {"index_ms_per_page": None, "pages_timed": 0}
    assert "| index_ms_per_page | not measured |" in md_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_reports([report], tmp_path, "run", "t")


# --- runner guards and resumable ingestion (no database: services are replaced) -------------------


def test_ablation_refuses_to_run_after_the_heldout_split(dataset, tmp_path, monkeypatch) -> None:
    from evals import run_retrieval

    monkeypatch.setattr(run_retrieval, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(run_retrieval, "WORK_DIR", tmp_path / "work")
    marker = tmp_path / "reports" / dataset.manifest["dataset"] / "heldout.done"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="dev ablation is closed"):
        run_retrieval.ablate(dataset)
    assert not (tmp_path / "work").exists()  # refused before reading any ingest state


class _Crash(Exception):
    """Simulates the process dying in the middle of ingestion."""


class _FakeRecords:
    """An in-memory stand-in for the database-backed services ``ingest`` calls.

    It follows the real contracts: uploads are idempotent per (patient, bytes); pages are written
    with the succeeded parse and chunks with the succeeded index run (all or nothing); a second
    parse or index raises ``already_parsed`` / ``already_indexed``. ``crash`` names one point at
    which the next call dies, once.
    """

    def __init__(self, pages_by_bytes: dict[bytes, int]) -> None:
        import uuid

        self._uuid = uuid.uuid4
        self.pages_by_bytes = pages_by_bytes
        self.patients: dict = {}
        self.documents: dict[tuple, object] = {}
        self.page_count: dict = {}
        self.pages: dict = {}
        self.chunks: dict = {}
        self.index_runs: dict = {}
        self.classified: set = set()
        self.identity: dict = {}  # document -> outcome of the succeeded identity run
        self.identity_outcome = "match"  # what the next identity check finds
        self.crash: str | None = None

    def _maybe_crash(self, point: str) -> None:
        if self.crash == point:
            self.crash = None
            raise _Crash(point)

    def create_patient(self, name, *, is_synthetic, date_of_birth):
        from types import SimpleNamespace

        patient_id = self._uuid()
        self.patients[patient_id] = (name, date_of_birth, is_synthetic)
        self._maybe_crash("create")  # committed, but the id was never recorded
        return SimpleNamespace(id=patient_id)

    def list_patients(self):
        from types import SimpleNamespace

        return tuple(
            SimpleNamespace(id=i, display_name=n, date_of_birth=d, is_synthetic=s)
            for i, (n, d, s) in self.patients.items()
        )

    def upload_document(self, _settings, patient_id, data, *, original_filename):
        from types import SimpleNamespace

        key = (patient_id, data)
        if key not in self.documents:
            self.documents[key] = self._uuid()
            self.page_count[self.documents[key]] = self.pages_by_bytes[data]
        self._maybe_crash("upload")
        return SimpleNamespace(document=SimpleNamespace(id=self.documents[key]))

    def process_document(self, _settings, _parser, _patient_id, document_id, *, recover_stale):
        from app.core.errors import DocumentProcessingError

        assert recover_stale is True
        if document_id in self.pages:
            raise DocumentProcessingError("parsed", kind="already_parsed")
        self._maybe_crash("parse_before_commit")
        if self.crash == "parse_partial":  # an impossible state, to prove the check
            self.pages[document_id] = self.page_count[document_id] - 1
            self._maybe_crash("parse_partial")
        self.pages[document_id] = self.page_count[document_id]
        self._maybe_crash("parse_after_commit")

    def index_document(self, _settings, _embedder, _patient_id, document_id):
        from types import SimpleNamespace

        from app.core.errors import DocumentProcessingError

        if self.chunks.get(document_id) or self.crash == "index_ghost":
            if self.crash == "index_ghost":
                self.crash = None
            raise DocumentProcessingError("indexed", kind="already_indexed")
        self._maybe_crash("index_before_commit")
        self.chunks[document_id] = 2
        self.index_runs[document_id] = self.index_runs.get(document_id, 0) + 1
        self._maybe_crash("index_after_commit")
        return SimpleNamespace(duration_ms=5, chunks=2)

    def classify_document(self, _patient_id, document_id):
        from app.core.errors import DocumentProcessingError

        if document_id in self.classified:
            raise DocumentProcessingError("classified", kind="already_classified")
        self._maybe_crash("classify_before_commit")
        self.classified.add(document_id)
        self._maybe_crash("classify_after_commit")

    def check_document_identity(self, _patient_id, document_id):
        from app.core.errors import DocumentProcessingError

        if document_id in self.identity:
            raise DocumentProcessingError("checked", kind="already_checked")
        self._maybe_crash("identity_before_commit")
        self.identity[document_id] = self.identity_outcome
        self._maybe_crash("identity_after_commit")

    def get_index_target(self, _patient_id, document_id):
        from app.records.chunks import IndexTarget

        return IndexTarget(
            document_id=document_id,
            sha256="0" * 64,
            page_count=self.page_count[document_id],
            page_rows=self.pages.get(document_id, 0),
            chunk_count=self.chunks.get(document_id, 0),
            classified=document_id in self.classified,
            identity_checked=document_id in self.identity,
            identity_check=self.identity.get(document_id, "unknown"),
        )


@pytest.fixture
def fake_ingest(dataset, tmp_path, monkeypatch):
    """``ingest`` wired to ``_FakeRecords``; no database, parser or model is touched."""
    import app.ingestion.parsing as parsing
    import app.records.chunks as chunks
    import app.records.repositories as repositories
    import app.retrieval.models as models
    import app.services.check_identity as identity
    import app.services.classify_document as classifying
    import app.services.index_document as indexing
    import app.services.process_document as processing
    import app.services.upload_document as uploading
    from evals import run_retrieval

    pages_by_bytes = {
        dataset.pdf_path(r["report_id"]).read_bytes(): len(r["pages"])
        for p in dataset.patients("dev")
        for r in p["reports"]
    }
    records = _FakeRecords(pages_by_bytes)
    monkeypatch.setattr(run_retrieval, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(repositories, "create_patient", records.create_patient)
    monkeypatch.setattr(repositories, "list_patients", records.list_patients)
    monkeypatch.setattr(uploading, "upload_document", records.upload_document)
    monkeypatch.setattr(processing, "process_document", records.process_document)
    monkeypatch.setattr(indexing, "index_document", records.index_document)
    monkeypatch.setattr(classifying, "classify_document", records.classify_document)
    monkeypatch.setattr(identity, "check_document_identity", records.check_document_identity)
    monkeypatch.setattr(chunks, "get_index_target", records.get_index_target)
    monkeypatch.setattr(models, "BgeEmbedder", lambda *_a, **_k: object())
    monkeypatch.setattr(
        parsing.DoclingParser, "from_settings", classmethod(lambda cls, s: object())
    )
    state_path = tmp_path / "work" / dataset.manifest["dataset"] / "ingest_dev.json"
    return records, state_path


def _assert_complete_without_duplicates(dataset, records, state_path) -> dict:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    dev = dataset.patients("dev")
    report_ids = {r["report_id"] for p in dev for r in p["reports"]}
    assert len(records.patients) == len(dev)  # no duplicate patient
    assert len(records.documents) == len(report_ids)  # no duplicate document
    assert sorted(records.index_runs.values()) == [1] * len(report_ids)  # one index run each
    assert len(records.classified) == len(records.identity) == len(report_ids)  # gate stages ran
    assert set(state["reports"]) == report_ids
    assert {p["stage"] for p in state["progress"].values()} == {"indexed"}
    assert {r["document_id"] for r in state["reports"].values()} == {
        str(d) for d in records.documents.values()
    }
    assert not state_path.with_name(state_path.name + ".tmp").exists()
    return state


@pytest.mark.parametrize(
    "point",
    [
        "upload",
        "parse_before_commit",
        "parse_after_commit",
        "classify_before_commit",
        "classify_after_commit",
        "identity_before_commit",
        "identity_after_commit",
        "index_before_commit",
        "index_after_commit",
    ],
)
def test_ingest_resumes_after_a_crash_inside_any_stage(dataset, fake_ingest, point) -> None:
    from evals import run_retrieval

    records, state_path = fake_ingest
    records.crash = point
    with pytest.raises(_Crash):
        run_retrieval.ingest(dataset, "dev")
    run_retrieval.ingest(dataset, "dev")
    state = _assert_complete_without_duplicates(dataset, records, state_path)
    first = dataset.patients("dev")[0]["reports"][0]["report_id"]
    timing = state["timings"][first]
    if point == "parse_after_commit":
        assert timing["parse_ms"] is None  # completed before the crash: never estimated
    if point == "index_after_commit":
        assert timing["index_wall_ms"] is None and timing["chunks"] == 2  # count from the store


@pytest.mark.parametrize("save_number", [1, 2, 3, 4, 5, 6, 7])
def test_ingest_resumes_after_a_crash_right_after_any_state_save(
    dataset, fake_ingest, monkeypatch, save_number
) -> None:
    """Saves 1-7: patient id, uploaded, parsed, classified, identity checked, indexed, complete."""
    from evals import run_retrieval

    records, state_path = fake_ingest
    original = run_retrieval._save_state
    calls = {"n": 0}

    def save_then_maybe_crash(path, state):
        original(path, state)
        calls["n"] += 1
        if calls["n"] == save_number:
            raise _Crash(f"after save {save_number}")

    monkeypatch.setattr(run_retrieval, "_save_state", save_then_maybe_crash)
    with pytest.raises(_Crash):
        run_retrieval.ingest(dataset, "dev")
    calls["n"] = -1000  # no more crashes
    run_retrieval.ingest(dataset, "dev")
    _assert_complete_without_duplicates(dataset, records, state_path)


def test_ingest_never_duplicates_a_patient_created_but_not_recorded(dataset, fake_ingest) -> None:
    from evals import run_retrieval

    records, state_path = fake_ingest
    records.crash = "create"
    with pytest.raises(_Crash):
        run_retrieval.ingest(dataset, "dev")
    with pytest.raises(run_retrieval.ResumeError, match="unrecorded synthetic patient"):
        run_retrieval.ingest(dataset, "dev")
    assert len(records.patients) == 1  # no second patient was created
    assert records.documents == {}


@pytest.mark.parametrize(
    ("point", "message"),
    [
        ("parse_partial", "does not confirm a completed parse"),
        ("index_ghost", "does not confirm a completed index"),
    ],
)
def test_already_done_is_accepted_only_when_the_store_confirms_it(
    dataset, fake_ingest, point, message
) -> None:
    from evals import run_retrieval

    records, state_path = fake_ingest
    records.crash = point
    if point == "parse_partial":
        with pytest.raises(_Crash):
            run_retrieval.ingest(dataset, "dev")
    with pytest.raises(run_retrieval.ResumeError, match=message):
        run_retrieval.ingest(dataset, "dev")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["reports"] == {}  # the unconfirmed report was not marked complete


@pytest.mark.parametrize("outcome", ["mismatch"])
def test_a_blocked_document_stops_ingestion_instead_of_shrinking_the_corpus(
    dataset, fake_ingest, outcome
) -> None:
    """ADR-036 A3: a gated document is never silently dropped from the evaluation corpus."""
    from evals import run_retrieval

    records, state_path = fake_ingest
    records.identity_outcome = outcome
    with pytest.raises(run_retrieval.ResumeError, match="blocked from indexing"):
        run_retrieval.ingest(dataset, "dev")
    assert records.chunks == {}  # nothing indexed
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["reports"] == {}
    assert {p["stage"] for p in state["progress"].values()} == {"classified"}


def test_identity_unknown_is_indexed_after_its_succeeded_check(dataset, fake_ingest) -> None:
    from evals import run_retrieval

    records, state_path = fake_ingest
    records.identity_outcome = "unknown"
    run_retrieval.ingest(dataset, "dev")
    _assert_complete_without_duplicates(dataset, records, state_path)


def test_ingest_refuses_when_the_upload_returns_another_document(dataset, fake_ingest) -> None:
    from evals import run_retrieval

    records, _ = fake_ingest
    records.crash = "parse_before_commit"
    with pytest.raises(_Crash):
        run_retrieval.ingest(dataset, "dev")
    key = next(iter(records.documents))
    records.documents[key] = records._uuid()  # the store now answers with a different id
    records.page_count[records.documents[key]] = 1
    with pytest.raises(run_retrieval.ResumeError, match="another document"):
        run_retrieval.ingest(dataset, "dev")


# --- calibration, freeze and the held-out stage (no database, no model) -------------------------


def test_calibration_uses_answerable_and_ordinary_unanswerable_questions_only() -> None:
    from evals.questions import Question
    from evals.run_retrieval import calibration_input

    kinds = [
        ("value", ANSWER, 5.0),
        ("lab", ANSWER, 4.0),
        ("latest", ANSWER, None),  # nothing retrieved: no score to compare
        ("unanswerable", ABSTAIN, -3.0),
        ("leak", ABSTAIN, -9.0),
        ("advice", OUT_OF_SCOPE, None),
        ("injection", OUT_OF_SCOPE, None),
    ]
    questions = [Question(f"q{i}", "P01", k, "t", e) for i, (k, e, _) in enumerate(kinds)]
    outcomes = [Outcome("answered", (), s, 0, 1) for _, _, s in kinds]
    inputs = calibration_input(questions, outcomes)
    assert inputs.answerable_scores == [5.0, 4.0]
    assert inputs.unanswerable_scores == [-3.0]  # the leak probe's score is not used
    assert inputs.answerable_kinds == ("lab", "latest", "value")
    assert inputs.excluded_kinds == ("advice", "injection", "leak")
    assert inputs.describe() == {
        "calibration_answerable_kinds": "lab,latest,value",
        "calibration_unanswerable_kinds": "unanswerable",
        "calibration_excluded_kinds": "advice,injection,leak",
        "calibration_answerable_scored": "2",
        "calibration_unanswerable_scored": "1",
    }


EXACT_THRESHOLD = 0.1 + 0.2  # 0.30000000000000004: rounding to 4 decimals would change it


def _config(dataset, mode: str, threshold: float | None, **changes) -> RunConfig:
    values = {
        "dataset_version": dataset.version,
        "split": "dev",
        "mode": mode,
        "candidates": 50,
        "rrf_k": 60,
        "rerank_top": 20,
        "top_k": 10,
        "abstain_threshold": threshold,
        "chunker_version": "1",
        "embedding_model": "BAAI/bge-small-en-v1.5@" + "a" * 40,
        "reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2@" + "b" * 40,
        "classifier_rules_version": "2",
        "identity_rules_version": "1",
    }
    values.update(changes)
    return RunConfig(**values)


@pytest.fixture
def frozen_reports(dataset, tmp_path, monkeypatch):
    """A dev report (dense, hybrid_rerank, calibrated hybrid_rerank) in a temporary reports dir."""
    from evals import run_retrieval

    monkeypatch.setattr(run_retrieval, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(run_retrieval, "WORK_DIR", tmp_path / "work")
    directory = tmp_path / "reports" / dataset.manifest["dataset"]
    runs = [
        RunReport(_config(dataset, "dense", None), {"mrr": 0.5}, {"questions": 1}),
        RunReport(_config(dataset, "hybrid_rerank", None), {"mrr": 0.6}, {"questions": 1}),
        RunReport(_config(dataset, "hybrid_rerank", EXACT_THRESHOLD), {"mrr": 0.7}, {}),
    ]
    write_reports(runs, directory, "dev_ablation", "t", ingestion=None)
    return directory


def test_freeze_records_the_exact_threshold_and_is_written_once(dataset, frozen_reports) -> None:
    import hashlib

    from evals import run_retrieval

    path = run_retrieval.freeze(dataset, "hybrid_rerank")
    selection = json.loads(path.read_text(encoding="utf-8"))
    assert selection["abstain_threshold"] == EXACT_THRESHOLD  # exact, not rounded
    assert float.fromhex(selection["abstain_threshold_hex"]) == EXACT_THRESHOLD
    assert (
        selection["dev_ablation_sha256"]
        == hashlib.sha256((frozen_reports / "dev_ablation.json").read_bytes()).hexdigest()
    )
    assert selection["heldout_sha256"] == dataset.manifest["heldout_sha256"]
    assert selection["config"]["reranker_model"].endswith("@" + "b" * 40)
    assert selection["dev_metrics"] == {"mrr": 0.7}  # the calibrated run was chosen
    with pytest.raises(SystemExit, match="never replaced"):
        run_retrieval.freeze(dataset, "dense")


def test_freeze_without_a_threshold_or_a_known_mode(dataset, frozen_reports) -> None:
    from evals import run_retrieval

    with pytest.raises(SystemExit, match="0 candidate runs"):
        run_retrieval.freeze(dataset, "lexical")
    selection = json.loads(run_retrieval.freeze(dataset, "dense").read_text(encoding="utf-8"))
    assert selection["abstain_threshold"] is None and selection["abstain_threshold_hex"] is None


def _heldout_ready(dataset, tmp_path, monkeypatch, asker_config_changes=None):
    """Freeze hybrid_rerank and fake the asker and the question run; returns the asker calls."""
    from evals import run_retrieval

    run_retrieval.freeze(dataset, "hybrid_rerank")
    work = tmp_path / "work" / dataset.manifest["dataset"]
    work.mkdir(parents=True)
    keys = [p["patient_key"] for p in dataset.patients("heldout")]
    (work / "ingest_heldout.json").write_text(
        json.dumps({"patients": {k: str(i) * 32 for i, k in enumerate(keys)}, "reports": {}}),
        encoding="utf-8",
    )
    calls: list[tuple] = []

    def asker(mode, threshold, top_k=10):
        calls.append((mode, threshold))
        config = _config(dataset, mode, threshold, **(asker_config_changes or {}))
        return (lambda *_: None), replace_split(config)

    def replace_split(config):
        from dataclasses import replace

        return replace(config, dataset_version="", split="")

    monkeypatch.setattr(run_retrieval, "_asker", asker)
    monkeypatch.setattr(
        run_retrieval,
        "_run",
        lambda questions, state, ask: (
            [Outcome("insufficient_evidence", (), None, 0, 1)] * len(questions)
        ),
    )
    return calls


def test_heldout_runs_the_frozen_selection_exactly_once(
    dataset, frozen_reports, tmp_path, monkeypatch
) -> None:
    import hashlib

    from evals import run_retrieval

    calls = _heldout_ready(dataset, tmp_path, monkeypatch)
    report_path = run_retrieval.heldout(dataset)
    assert calls == [("hybrid_rerank", EXACT_THRESHOLD)]
    assert calls[0][1] == 0.30000000000000004  # full precision reached the retrieval config
    selection_sha = hashlib.sha256((frozen_reports / "dev_selection.json").read_bytes()).hexdigest()
    marker = json.loads((frozen_reports / "heldout.done").read_text(encoding="utf-8"))
    assert marker["dev_selection_sha256"] == selection_sha
    assert marker["abstain_threshold"] == EXACT_THRESHOLD
    run = json.loads(report_path.read_text(encoding="utf-8"))["runs"][0]
    assert run["config"]["split"] == "heldout"
    assert run["config"]["extra"] == {"dev_selection_sha256": selection_sha}
    with pytest.raises(SystemExit, match="not run again"):
        run_retrieval.heldout(dataset)
    with pytest.raises(SystemExit, match="dev ablation is closed"):
        run_retrieval.ablate(dataset)
    with pytest.raises(SystemExit, match="nothing can be frozen"):
        run_retrieval.freeze(dataset, "dense")


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"rerank_top": 21}, "rerank_top"),
        ({"reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2@" + "c" * 40}, "reranker_model"),
        ({"embedding_model": "BAAI/bge-small-en-v1.5@" + "d" * 40}, "embedding_model"),
        ({"chunker_version": "2"}, "chunker_version"),
        # ADR-036 A3: the rules that gated indexing are frozen too
        ({"classifier_rules_version": "3"}, "classifier_rules_version"),
        ({"identity_rules_version": "2"}, "identity_rules_version"),
        ({"identity_rules_version": None}, "identity_rules_version"),
    ],
)
def test_heldout_refuses_settings_or_model_revisions_that_differ_from_the_freeze(
    dataset, frozen_reports, tmp_path, monkeypatch, changes, field
) -> None:
    from evals import run_retrieval

    _heldout_ready(dataset, tmp_path, monkeypatch, asker_config_changes=changes)
    monkeypatch.setattr(run_retrieval, "generate_questions", _must_not_be_called)
    with pytest.raises(SystemExit, match=field):
        run_retrieval.heldout(dataset)
    assert not (frozen_reports / "heldout.done").exists()


def _must_not_be_called(*_args, **_kwargs):
    raise AssertionError("held-out questions must not be generated before validation passes")


def test_heldout_refuses_a_changed_report_or_selection(
    dataset, frozen_reports, tmp_path, monkeypatch
) -> None:
    from evals import run_retrieval

    _heldout_ready(dataset, tmp_path, monkeypatch)
    monkeypatch.setattr(run_retrieval, "generate_questions", _must_not_be_called)
    report = frozen_reports / "dev_ablation.json"
    original = report.read_bytes()
    report.write_bytes(original + b" ")
    with pytest.raises(SystemExit, match="dev report changed"):
        run_retrieval.heldout(dataset)
    report.write_bytes(original)
    selection_path = frozen_reports / "dev_selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["abstain_threshold_hex"] = (0.3).hex()  # rounded value, no longer the same
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(SystemExit, match="inconsistent threshold"):
        run_retrieval.heldout(dataset)
    selection_path.unlink()
    with pytest.raises(SystemExit, match="run `freeze`"):
        run_retrieval.heldout(dataset)
    assert not (frozen_reports / "heldout.done").exists()


def test_cli_requires_mode_for_freeze_only(dataset) -> None:
    from evals import run_retrieval

    with pytest.raises(SystemExit):
        run_retrieval.main(["freeze", "--dataset", str(dataset.root)])
    with pytest.raises(SystemExit):
        run_retrieval.main(["heldout", "--dataset", str(dataset.root), "--mode", "dense"])
    with pytest.raises(SystemExit):
        run_retrieval.main(["heldout", "--dataset", str(dataset.root), "--threshold", "0.3"])
