"""Evidence-retrieval evaluation (Milestone R1.8): ingest, ablate on dev, calibrate, held-out once.

Stages (each a separate command, run in order):

    ingest    create the split's fictional patients (is_synthetic=True), upload each PDF, parse it
              (Docling), classify it and check its identity (Task 2.8, ADR-036 A3: the indexing
              gate needs both; a blocked document stops ingestion), then index it (BGE). The
              classifier and identity rule versions are part of the run configuration and are
              frozen and validated like the model revisions. Writes <work>/ingest_<split>.json
              mapping patient keys and report ids to database ids, plus per-report stage progress
              and timings. Needs the
              database and the approved model revisions. Resumable after a crash at any stage:
              patient ids, document ids and each completed stage are saved as they happen; a parse
              or index that already completed is skipped only after the database confirms it
              (see ``ingest``). A completed step that a crash prevented from being timed is left
              untimed, never estimated.
    ablate    dev only: dense, lexical, hybrid (RRF) and hybrid_rerank without abstention; then
              calibrates the abstention threshold of hybrid_rerank on dev (answerable vs ordinary
              unanswerable questions only) and reports abstention with it. Writes
              evals/reports/<dataset>/dev_ablation.{json,md}, including the recorded indexing time
              per page. Refuses to run once the held-out split was evaluated.
    freeze    records the chosen dev configuration in evals/reports/<dataset>/dev_selection.json:
              the mode, the exact threshold (JSON number and float.hex), the full run
              configuration with model revisions, and the SHA-256 of the dev report. Written once.
    heldout   runs the frozen configuration ONCE on the held-out split. Takes no mode or threshold:
              it reads dev_selection.json and refuses if the dev report, the dataset or the current
              settings and model revisions differ from what was frozen, or if it already ran.

    uv run python -m evals.run_retrieval ingest  --dataset data/synthetic/lab_v0 --split dev
    uv run python -m evals.run_retrieval ablate  --dataset data/synthetic/lab_v0
    uv run python -m evals.run_retrieval freeze  --dataset data/synthetic/lab_v0 --mode <mode>
    uv run python -m evals.run_retrieval ingest  --dataset data/synthetic/lab_v0 --split heldout
    uv run python -m evals.run_retrieval heldout --dataset data/synthetic/lab_v0

The database is the one configured in ``.env`` (``MEDIMIND_DB_NAME``); ingestion only adds
synthetic patients and documents. Scoring (``score``) is pure and unit-tested.
"""

import argparse
import datetime as dt
import hashlib
import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.core.errors import DocumentProcessingError
from evals import metrics
from evals.dataset import Dataset, load_dataset
from evals.questions import ABSTAIN, ANSWER, Question, generate_questions
from evals.report import RunConfig, RunReport, write_reports

QUESTION_SEED = 7
K_VALUES = (1, 3, 5, 10)
REPORTS_DIR = Path("evals/reports")
WORK_DIR = Path("evals/work")
MODES = ("dense", "lexical", "hybrid", "hybrid_rerank")
CALIBRATED_MODE = "hybrid_rerank"  # the only mode whose threshold is calibrated
CALIBRATION_ABSTAIN_KIND = "unanswerable"  # positives; leak probes stay out (scored on their own)
ABLATION_REPORT = "dev_ablation"
SELECTION_FILE = "dev_selection.json"
# RunConfig fields that must equal the frozen dev configuration before the held-out run.
FROZEN_FIELDS = (
    "mode",
    "candidates",
    "rrf_k",
    "rerank_top",
    "top_k",
    "abstain_threshold",
    "chunker_version",
    "embedding_model",
    "reranker_model",
    "classifier_rules_version",  # ADR-036 A3: the rules that gated indexing
    "identity_rules_version",
)


class ResumeError(Exception):
    """Ingestion cannot continue safely. Messages name report keys and stages, never data."""


@dataclass(frozen=True)
class Outcome:
    """What the system returned for one question, mapped to dataset keys."""

    status: str  # "answered", "insufficient_evidence" or "out_of_scope"
    ranked: tuple[tuple[str, int], ...]  # (report_id, page) best first; foreign pages as ("?", p)
    top_score: float | None
    foreign_pages: int  # passages from a document not owned by the asking patient
    latency_ms: int


def score(questions: Sequence[Question], outcomes: Sequence[Outcome]) -> tuple[dict, dict]:
    """Metrics and counts for one run. Pure: no database, no model."""
    if len(questions) != len(outcomes):
        raise ValueError("one outcome per question is required")
    pairs = list(zip(questions, outcomes, strict=True))
    answerable = [(q, o) for q, o in pairs if q.expect == ANSWER]
    result: dict[str, float | int | None] = {}
    if answerable:
        for k in K_VALUES:
            result[f"recall@{k}"] = metrics.mean(
                [metrics.recall_at_k(o.ranked, q.relevant, k) for q, o in answerable]
            )
            result[f"hit@{k}"] = metrics.mean(
                [metrics.hit_at_k(o.ranked, q.relevant, k) for q, o in answerable]
            )
        result["mrr"] = metrics.mean(
            [metrics.reciprocal_rank(o.ranked, q.relevant) for q, o in answerable]
        )
        result["ndcg@10"] = metrics.mean(
            [metrics.ndcg_at_k(o.ranked, q.relevant, 10) for q, o in answerable]
        )
    # Abstention: answerable vs ordinary unanswerable questions; leak probes are scored apart.
    in_scope = [(q, o) for q, o in pairs if q.expect == ANSWER or q.kind == "unanswerable"]
    abstention = metrics.binary_scores(
        [q.expect == ABSTAIN for q, _ in in_scope],
        [o.status == "insufficient_evidence" for _, o in in_scope],
    )
    result["abstain_precision"] = abstention.precision
    result["abstain_recall"] = abstention.recall
    result["abstain_f1"] = abstention.f1
    for kind in ("advice", "injection"):
        probes = [o for q, o in pairs if q.kind == kind]
        result[f"{kind}_refusal_rate"] = (
            metrics.rate([o.status == "out_of_scope" for o in probes]) if probes else None
        )
    # Cross-patient leakage. Denominator: leak probes only (another patient of the split has it).
    leak_probes = [o for q, o in pairs if q.kind == "leak"]
    result["leak_rate"] = (
        metrics.rate([o.foreign_pages > 0 for o in leak_probes]) if leak_probes else None
    )
    result["leak_probe_abstain_rate"] = (
        metrics.rate([o.status == "insufficient_evidence" for o in leak_probes])
        if leak_probes
        else None
    )
    # Safety net over every question type (must be 0): a foreign page anywhere is a leak.
    result["questions_with_foreign_pages"] = sum(1 for _, o in pairs if o.foreign_pages > 0)
    latencies = [float(o.latency_ms) for _, o in pairs if o.status != "out_of_scope"]
    result["latency_p50_ms"] = metrics.percentile(latencies, 50) if latencies else None
    result["latency_p95_ms"] = metrics.percentile(latencies, 95) if latencies else None
    counts = {
        kind: sum(1 for q in questions if q.kind == kind)
        for kind in sorted({q.kind for q in questions})
    }
    counts["questions"] = len(questions)
    return result, counts


def ingestion_timing(timings: dict[str, dict]) -> dict[str, float | int | None]:
    """Indexing time per page from the timings ``ingest`` recorded. Pure.

    ``index_wall_ms`` is the wall-clock time of ``index_document`` (verify, chunk, embed, insert).
    Reports whose index step completed before a crash have no timing and are only counted.
    """
    timed = [t for t in timings.values() if t.get("index_wall_ms") is not None]
    pages = sum(t["pages"] for t in timed)
    per_page = [t["index_wall_ms"] / t["pages"] for t in timed if t["pages"]]
    return {
        "reports_timed": len(timed),
        "reports_not_timed": len(timings) - len(timed),
        "pages_timed": pages,
        "index_ms_per_page": sum(t["index_wall_ms"] for t in timed) / pages if pages else None,
        "index_ms_per_page_p50": metrics.percentile(per_page, 50) if per_page else None,
        "index_ms_per_page_p95": metrics.percentile(per_page, 95) if per_page else None,
    }


@dataclass(frozen=True)
class CalibrationInput:
    """Top scores used to calibrate the abstention threshold, and which question kinds fed it."""

    answerable_scores: list[float]
    unanswerable_scores: list[float]
    answerable_kinds: tuple[str, ...]
    unanswerable_kinds: tuple[str, ...]
    excluded_kinds: tuple[str, ...]

    def describe(self) -> dict[str, str]:
        return {
            "calibration_answerable_kinds": ",".join(self.answerable_kinds),
            "calibration_unanswerable_kinds": ",".join(self.unanswerable_kinds),
            "calibration_excluded_kinds": ",".join(self.excluded_kinds),
            "calibration_answerable_scored": str(len(self.answerable_scores)),
            "calibration_unanswerable_scored": str(len(self.unanswerable_scores)),
        }


def calibration_input(
    questions: Sequence[Question], outcomes: Sequence[Outcome]
) -> CalibrationInput:
    """Answerable vs ordinary unanswerable questions only. Pure.

    Leak probes, advice and injection questions are excluded, so the leak metrics stay independent
    of the threshold. Questions with no top score (nothing retrieved) carry no score to compare.
    """
    if len(questions) != len(outcomes):
        raise ValueError("one outcome per question is required")
    answerable = [(q, o) for q, o in zip(questions, outcomes, strict=True) if q.expect == ANSWER]
    unanswerable = [
        (q, o)
        for q, o in zip(questions, outcomes, strict=True)
        if q.kind == CALIBRATION_ABSTAIN_KIND
    ]
    used = {q.kind for q, _ in answerable} | {CALIBRATION_ABSTAIN_KIND}
    return CalibrationInput(
        answerable_scores=[o.top_score for _, o in answerable if o.top_score is not None],
        unanswerable_scores=[o.top_score for _, o in unanswerable if o.top_score is not None],
        answerable_kinds=tuple(sorted({q.kind for q, _ in answerable})),
        unanswerable_kinds=(CALIBRATION_ABSTAIN_KIND,),
        excluded_kinds=tuple(sorted({q.kind for q in questions} - used)),
    )


# --- runtime (database + models) ------------------------------------------------------------------


def _work_file(dataset: Dataset, split: str) -> Path:
    return WORK_DIR / dataset.manifest["dataset"] / f"ingest_{split}.json"


def _heldout_marker(dataset: Dataset) -> Path:
    return REPORTS_DIR / dataset.manifest["dataset"] / "heldout.done"


def _save_state(path: Path, state: dict) -> None:
    """Write the ingest state atomically, so a crash never leaves a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _unless_done(
    done_kind: str, step: Callable, *args: object, **kwargs: object
) -> tuple[object | None, int | None]:
    """``(result, ms)`` of ``step``, or ``(None, None)`` if it already completed (``done_kind``)."""
    started = time.perf_counter()
    try:
        result = step(*args, **kwargs)
    except DocumentProcessingError as exc:
        if exc.kind != done_kind:
            raise
        return None, None
    return result, round((time.perf_counter() - started) * 1000)


def _load_state(path: Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for section in ("patients", "progress", "reports", "timings"):
        state.setdefault(section, {})
    return state


def _require_parsed(target: object, report_id: str) -> None:
    """Pages are written in the same transaction that marks the parse run succeeded."""
    page_count = getattr(target, "page_count", None)
    if not page_count or getattr(target, "page_rows", 0) != page_count:
        raise ResumeError(f"Report {report_id}: the database does not confirm a completed parse")


def _require_indexed(target: object, report_id: str, expected_chunks: int | None) -> int:
    """Chunks are written in the same transaction as the succeeded index run."""
    chunks = int(getattr(target, "chunk_count", 0))
    if expected_chunks is None and chunks < 1:
        raise ResumeError(f"Report {report_id}: the database does not confirm a completed index")
    if expected_chunks is not None and chunks != expected_chunks:
        raise ResumeError(f"Report {report_id}: the stored chunk count differs from the index run")
    return chunks


def _require_stage(target: object, report_id: str, flag: str, stage: str) -> None:
    """The database confirms a succeeded classify / identity run (``flag`` on the target)."""
    if not getattr(target, flag, False):
        raise ResumeError(f"Report {report_id}: the database does not confirm a completed {stage}")


def _require_not_blocked(target: object, report_id: str) -> None:
    """A gated document stops ingestion: the corpus is never silently reduced (ADR-036 A3)."""
    reason = getattr(target, "blocked_reason", None)
    if reason is not None:
        raise ResumeError(
            f"Report {report_id}: blocked from indexing ({reason}); ingestion stops instead of "
            "evaluating a smaller corpus"
        )


def ingest(dataset: Dataset, split: str) -> Path:
    """Create, upload, parse, classify, identity-check and index the split's patients.

    Resumable after a crash. Per report, ``progress`` records the document id and the last
    completed stage (``uploaded``, ``parsed``, ``classified``, ``identity_checked``, ``indexed``),
    saved atomically after each stage. Classification and the identity check run before indexing
    (ADR-036 A3); a document the indexing gate refuses stops ingestion with an error. On a rerun:
    - a patient is created only if its id was never recorded; if an unrecorded synthetic patient
      with the same name and date of birth exists (a crash between creating and recording it),
      ingestion stops instead of creating a duplicate or adopting a patient it cannot verify;
    - the upload is repeated (idempotent per patient and content) and must return the recorded id;
    - ``already_parsed`` / ``already_classified`` / ``already_checked`` / ``already_indexed`` are
      accepted only when the database confirms the stage: every page row present / a succeeded
      classify run / a succeeded identity run / chunks present (written with the index run);
    - a parse claim left by a killed process is reclaimed once stale (``recover_stale``); before
      that, ``already_running`` stops the run.
    No step can create a second document, chunk set or succeeded index run for a report.
    """
    from app.core.config import get_settings
    from app.ingestion.parsing import DoclingParser
    from app.records.chunks import ALREADY_INDEXED, get_index_target
    from app.records.repositories import (
        ALREADY_CHECKED,
        ALREADY_CLASSIFIED,
        ProcessingErrorKind,
        create_patient,
        list_patients,
    )
    from app.retrieval.models import BgeEmbedder
    from app.services.check_identity import check_document_identity
    from app.services.classify_document import classify_document
    from app.services.index_document import index_document
    from app.services.process_document import process_document
    from app.services.upload_document import upload_document

    settings = get_settings()
    embedder = BgeEmbedder(settings.embedding_model_revision)
    parser = DoclingParser.from_settings(settings)
    path = _work_file(dataset, split)
    state = _load_state(path)
    for patient in dataset.patients(split):
        key = patient["patient_key"]
        if key not in state["patients"]:
            date_of_birth = dt.date.fromisoformat(patient["date_of_birth"])
            recorded = set(state["patients"].values())
            if any(
                p.is_synthetic
                and p.display_name == patient["display_name"]
                and p.date_of_birth == date_of_birth
                and str(p.id) not in recorded
                for p in list_patients()
            ):
                raise ResumeError(
                    f"Patient {key}: an unrecorded synthetic patient with the same name and date "
                    f"of birth exists; resolve it before ingesting (nothing was created)"
                )
            ref = create_patient(
                patient["display_name"], is_synthetic=True, date_of_birth=date_of_birth
            )
            state["patients"][key] = str(ref.id)
            _save_state(path, state)  # before any upload
        patient_id = uuid.UUID(state["patients"][key])
        for report in patient["reports"]:
            report_id = report["report_id"]
            if report_id in state["reports"]:
                continue
            progress = state["progress"].setdefault(report_id, {"stage": None})
            data = dataset.pdf_path(report_id).read_bytes()
            # Idempotent: the same bytes for the same patient return the existing document.
            uploaded = upload_document(
                settings, patient_id, data, original_filename=f"{report_id}.pdf"
            )
            document_id = uploaded.document.id
            if progress.get("document_id") not in (None, str(document_id)):
                raise ResumeError(f"Report {report_id}: the upload returned another document")
            if progress["stage"] is None:
                progress.update(stage="uploaded", document_id=str(document_id))
                _save_state(path, state)
            if progress["stage"] == "uploaded":
                _, parse_ms = _unless_done(
                    ProcessingErrorKind.ALREADY_PARSED,
                    process_document,
                    settings,
                    parser,
                    patient_id,
                    document_id,
                    recover_stale=True,
                )
                _require_parsed(get_index_target(patient_id, document_id), report_id)
                progress.update(stage="parsed", parse_ms=parse_ms)  # None: done before a crash
                _save_state(path, state)
            if progress["stage"] == "parsed":
                _unless_done(ALREADY_CLASSIFIED, classify_document, patient_id, document_id)
                target = get_index_target(patient_id, document_id)
                _require_stage(target, report_id, "classified", "classification")
                progress.update(stage="classified")
                _save_state(path, state)
            if progress["stage"] == "classified":
                _unless_done(ALREADY_CHECKED, check_document_identity, patient_id, document_id)
                target = get_index_target(patient_id, document_id)
                _require_stage(target, report_id, "identity_checked", "identity check")
                _require_not_blocked(target, report_id)
                progress.update(stage="identity_checked")
                _save_state(path, state)
            if progress["stage"] == "identity_checked":
                result, index_wall_ms = _unless_done(
                    ALREADY_INDEXED, index_document, settings, embedder, patient_id, document_id
                )
                chunks = _require_indexed(
                    get_index_target(patient_id, document_id),
                    report_id,
                    result.chunks if result is not None else None,  # type: ignore[attr-defined]
                )
                progress.update(
                    stage="indexed",
                    chunks=chunks,
                    index_ms=result.duration_ms if result is not None else None,  # type: ignore[attr-defined]
                    index_wall_ms=index_wall_ms,  # None: indexed before a crash, not re-timed
                )
                _save_state(path, state)
            if progress["stage"] != "indexed":
                raise ResumeError(f"Report {report_id}: unknown stage in the ingest state")
            state["reports"][report_id] = {"document_id": str(document_id), "patient_key": key}
            state["timings"][report_id] = {
                "pages": len(report["pages"]),
                "parse_ms": progress.get("parse_ms"),
                "index_ms": progress.get("index_ms"),  # service time; excludes the insert
                "index_wall_ms": progress.get("index_wall_ms"),
                "chunks": progress.get("chunks"),
            }
            _save_state(path, state)
    return path


def _run(
    questions: Sequence[Question],
    state: dict,
    ask: Callable[[uuid.UUID, str], object],
) -> list[Outcome]:
    document_to_report = {
        uuid.UUID(v["document_id"]): (k, v["patient_key"]) for k, v in state["reports"].items()
    }
    outcomes = []
    for question in questions:
        patient_id = uuid.UUID(state["patients"][question.patient_key])
        started = time.perf_counter()
        answer = ask(patient_id, question.text)
        latency = round((time.perf_counter() - started) * 1000)
        ranked, foreign = [], 0
        for passage in answer.passages:  # type: ignore[attr-defined]
            report_id, owner = document_to_report.get(passage.document_id, ("?", "?"))
            if owner != question.patient_key:
                foreign += 1
            ranked.append((report_id, passage.page_number))
        outcomes.append(
            Outcome(answer.status.value, tuple(ranked), answer.top_score, foreign, latency)  # type: ignore[attr-defined]
        )
    return outcomes


def _asker(mode: str, threshold: float | None, top_k: int = 10) -> tuple[Callable, RunConfig]:
    from app.core.config import get_settings
    from app.ingestion.classification import CLASSIFIER_RULES_VERSION
    from app.ingestion.identity import IDENTITY_RULES_VERSION
    from app.retrieval.chunking import CHUNKER_VERSION
    from app.retrieval.evidence import RetrievalConfig, RetrievalMode
    from app.retrieval.models import BgeEmbedder, MiniLmReranker
    from app.services.search_evidence import search_evidence

    settings = get_settings()
    config = RetrievalConfig(
        mode=RetrievalMode(mode),
        candidates=settings.retrieval_candidates,
        rrf_k=settings.retrieval_rrf_k,
        rerank_top=settings.retrieval_rerank_top,
        top_k=top_k,  # 10 pages, so Recall@10 is measurable
        abstain_threshold=threshold,
    )
    embedder = BgeEmbedder(settings.embedding_model_revision) if config.mode.uses_dense else None
    reranker = (
        MiniLmReranker(settings.reranker_model_revision) if config.mode.uses_reranker else None
    )

    def ask(patient_id: uuid.UUID, text: str) -> object:
        return search_evidence(
            patient_id, text, config=config, embedder=embedder, reranker=reranker
        )

    run_config = RunConfig(
        dataset_version="",
        split="",
        mode=mode,
        candidates=config.candidates,
        rrf_k=config.rrf_k,
        rerank_top=config.rerank_top,
        top_k=config.top_k,
        abstain_threshold=threshold,
        chunker_version=CHUNKER_VERSION,
        embedding_model=embedder.model_name if embedder else None,
        reranker_model=reranker.model_name if reranker else None,
        classifier_rules_version=CLASSIFIER_RULES_VERSION,
        identity_rules_version=IDENTITY_RULES_VERSION,
    )
    return ask, run_config


def _with(config: RunConfig, dataset: Dataset, split: str) -> RunConfig:
    return replace(config, dataset_version=dataset.version, split=split)


def ablate(dataset: Dataset) -> Path:
    from app.retrieval.evidence import calibrate_threshold

    if _heldout_marker(dataset).exists():
        raise SystemExit(
            "The held-out split was already evaluated; dev ablation is closed for this dataset."
        )
    state = json.loads(_work_file(dataset, "dev").read_text(encoding="utf-8"))
    questions = generate_questions(dataset.patients("dev"), seed=QUESTION_SEED)
    reports = []
    rerank_outcomes: list[Outcome] = []
    for mode in MODES:
        ask, config = _asker(mode, None)
        outcomes = _run(questions, state, ask)
        values, counts = score(questions, outcomes)
        reports.append(
            RunReport(_with(config, dataset, "dev"), values, counts, ("no abstention threshold",))
        )
        if mode == CALIBRATED_MODE:
            rerank_outcomes = outcomes
    inputs = calibration_input(questions, rerank_outcomes)
    calibration = calibrate_threshold(inputs.answerable_scores, inputs.unanswerable_scores)
    if calibration is not None:
        ask, config = _asker(CALIBRATED_MODE, calibration.threshold)
        outcomes = _run(questions, state, ask)
        values, counts = score(questions, outcomes)
        reports.append(
            RunReport(
                replace(_with(config, dataset, "dev"), extra=inputs.describe()),
                values,
                counts,
                (
                    f"threshold {calibration.threshold!r} calibrated on dev (in-sample); exact "
                    "value in config.abstain_threshold",
                    f"calibration questions: answerable kinds {inputs.answerable_kinds} vs "
                    f"{inputs.unanswerable_kinds}; excluded kinds {inputs.excluded_kinds}",
                ),
            )
        )
    json_path, _ = write_reports(
        reports,
        REPORTS_DIR / dataset.manifest["dataset"],
        ABLATION_REPORT,
        "R1 evidence retrieval: dev ablation",
        ingestion=ingestion_timing(state["timings"]),
    )
    return json_path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def freeze(dataset: Dataset, mode: str) -> Path:
    """Freeze the chosen dev configuration into ``dev_selection.json`` (written once).

    For ``hybrid_rerank`` the calibrated run is chosen when calibration produced one; every other
    mode has no threshold. The threshold is copied from the JSON report without rounding and also
    stored as ``float.hex()``.
    """
    directory = REPORTS_DIR / dataset.manifest["dataset"]
    if _heldout_marker(dataset).exists():
        raise SystemExit("The held-out split was already evaluated; nothing can be frozen now.")
    selection_path = directory / SELECTION_FILE
    if selection_path.exists():
        raise SystemExit(f"{SELECTION_FILE} already exists; a frozen selection is never replaced.")
    ablation_path = directory / f"{ABLATION_REPORT}.json"
    if not ablation_path.is_file():
        raise SystemExit("No dev ablation report; run `ablate` first.")
    data = ablation_path.read_bytes()
    runs = [r for r in json.loads(data)["runs"] if r["config"]["mode"] == mode]
    calibrated = [r for r in runs if r["config"]["abstain_threshold"] is not None]
    chosen = calibrated or runs
    if len(chosen) != 1:
        raise SystemExit(f"Mode {mode!r} has {len(chosen)} candidate runs in the dev report.")
    run = chosen[0]
    config = run["config"]
    if config["split"] != "dev" or config["dataset_version"] != dataset.version:
        raise SystemExit("The dev report belongs to another split or dataset version.")
    threshold = config["abstain_threshold"]
    selection = {
        "dataset_version": dataset.version,
        "heldout_sha256": dataset.manifest["heldout_sha256"],
        "dev_ablation_sha256": _sha256(data),
        "mode": mode,
        "abstain_threshold": threshold,
        "abstain_threshold_hex": float(threshold).hex() if threshold is not None else None,
        "config": config,
        "dev_metrics": run["metrics"],
        "frozen_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    with open(selection_path, "x", encoding="utf-8") as handle:  # exclusive: never overwrites
        handle.write(json.dumps(selection, indent=2, sort_keys=True) + "\n")
    return selection_path


def _frozen_threshold(selection: dict) -> float | None:
    """The exact frozen threshold; the JSON number and its float.hex() must agree."""
    value, hexed = selection.get("abstain_threshold"), selection.get("abstain_threshold_hex")
    if value is None and hexed is None:
        return None
    if not isinstance(value, float) or not isinstance(hexed, str) or float.fromhex(hexed) != value:
        raise SystemExit(f"{SELECTION_FILE} has an inconsistent threshold.")
    return float.fromhex(hexed)


def heldout(dataset: Dataset) -> Path:
    """Run the frozen dev configuration once on the held-out split.

    Everything is validated before any held-out question is generated: the frozen file, the dev
    report's hash, the dataset version and held-out digest, and the current settings and model
    revisions (``FROZEN_FIELDS``).
    """
    directory = REPORTS_DIR / dataset.manifest["dataset"]
    marker = _heldout_marker(dataset)
    if marker.exists():
        raise SystemExit("The held-out split was already evaluated once; it is not run again.")
    selection_path = directory / SELECTION_FILE
    if not selection_path.is_file():
        raise SystemExit(f"No {SELECTION_FILE}; run `freeze` after `ablate` first.")
    selection_bytes = selection_path.read_bytes()
    selection = json.loads(selection_bytes)
    ablation_path = directory / f"{ABLATION_REPORT}.json"
    if (
        not ablation_path.is_file()
        or _sha256(ablation_path.read_bytes()) != selection["dev_ablation_sha256"]
    ):
        raise SystemExit("The dev report changed (or is missing) since it was frozen.")
    if (
        selection["dataset_version"] != dataset.version
        or selection["heldout_sha256"] != dataset.manifest["heldout_sha256"]
    ):
        raise SystemExit("The dataset differs from the one the selection was frozen on.")
    threshold = _frozen_threshold(selection)
    ask, config = _asker(selection["mode"], threshold)
    current = asdict(config)
    changed = [name for name in FROZEN_FIELDS if current[name] != selection["config"].get(name)]
    if changed:
        raise SystemExit(
            "Current settings differ from the frozen dev configuration: " + ", ".join(changed)
        )
    state = json.loads(_work_file(dataset, "heldout").read_text(encoding="utf-8"))
    questions = generate_questions(dataset.patients("heldout"), seed=QUESTION_SEED)
    outcomes = _run(questions, state, ask)
    values, counts = score(questions, outcomes)
    selection_sha = _sha256(selection_bytes)
    report = RunReport(
        replace(_with(config, dataset, "heldout"), extra={"dev_selection_sha256": selection_sha}),
        values,
        counts,
        ("single held-out run of the frozen dev selection",),
    )
    json_path, _ = write_reports(
        [report],
        directory,
        "heldout",
        "R1 evidence retrieval: held-out",
    )
    marker.write_text(
        json.dumps(
            {
                "mode": selection["mode"],
                "abstain_threshold": threshold,
                "abstain_threshold_hex": selection["abstain_threshold_hex"],
                "dev_selection_sha256": selection_sha,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R1 evidence-retrieval evaluation.")
    parser.add_argument("stage", choices=("ingest", "ablate", "freeze", "heldout"))
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "heldout"), default="dev")
    parser.add_argument("--mode", choices=MODES, help="freeze only: the chosen dev mode")
    args = parser.parse_args(argv)
    if args.stage == "freeze" and args.mode is None:
        parser.error("freeze requires --mode")
    if args.stage != "freeze" and args.mode is not None:
        parser.error("--mode is only for freeze; heldout reads the frozen selection")
    dataset = load_dataset(args.dataset)  # verifies every hash and the frozen held-out digest
    if args.stage == "ingest":
        print(ingest(dataset, args.split))
    elif args.stage == "ablate":
        print(ablate(dataset))
    elif args.stage == "freeze":
        print(freeze(dataset, args.mode))
    else:
        print(heldout(dataset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
