"""Lexical abstention threshold, calibrated on development data only (Phase 5 remediation; ADR-041).

R1.8 froze ``lexical`` without a threshold (its runner calibrates ``hybrid_rerank`` only), so
lexical abstains only when no query word matches. This script measures, on the ``lab_v0`` **dev**
split only (the R1.8 question generator, seed 7), the lexical top score (``ts_rank_cd``) of
answerable questions against ordinary unanswerable ones, and the F1-best threshold
(``app.retrieval.evidence.calibrate_threshold``, unchanged). It never reads the held-out split and
never touches the frozen R1.8 reports.

    uv run python -m evals.calibrate_lexical

Writes ``evals/reports/lab_v0/lexical_abstention_dev.{json,md}``. In-sample numbers: the
threshold is chosen and scored on the same questions.
"""

import json
import uuid
from pathlib import Path

from evals import metrics
from evals.dataset import load_dataset
from evals.questions import ANSWER, generate_questions
from evals.run_retrieval import QUESTION_SEED, calibration_input

DATASET = Path("data/synthetic/lab_v0")
OUT = Path("evals/reports/lab_v0/lexical_abstention_dev")


def main() -> int:
    from app.retrieval.evidence import RetrievalConfig, RetrievalMode, calibrate_threshold
    from app.services.search_evidence import search_evidence
    from evals.run_retrieval import Outcome

    dataset = load_dataset(DATASET)
    state = json.loads(Path("evals/work/lab_v0/ingest_dev.json").read_text(encoding="utf-8"))
    questions = generate_questions(dataset.patients("dev"), seed=QUESTION_SEED)
    config = RetrievalConfig(mode=RetrievalMode.LEXICAL, top_k=10)
    outcomes = []
    for q in questions:
        answer = search_evidence(uuid.UUID(state["patients"][q.patient_key]), q.text, config=config)
        outcomes.append(Outcome(answer.status.value, (), answer.top_score, 0, 0))
    inputs = calibration_input(questions, outcomes)
    calibration = calibrate_threshold(inputs.answerable_scores, inputs.unanswerable_scores)
    in_scope = [
        (q, o) for q, o in zip(questions, outcomes, strict=True)
        if q.expect == ANSWER or q.kind == "unanswerable"
    ]  # fmt: skip

    def scores(threshold: float | None) -> dict:
        predicted = [
            o.top_score is None or (threshold is not None and o.top_score < threshold)
            for _, o in in_scope
        ]
        result = metrics.binary_scores([q.expect != ANSWER for q, _ in in_scope], predicted)
        answerable_lost = sum(
            1 for (q, _), p in zip(in_scope, predicted, strict=True) if q.expect == ANSWER and p
        )
        return {
            "precision": result.precision,
            "recall": result.recall,
            "f1": result.f1,
            "answerable_abstained": answerable_lost,
        }

    report = {
        "dataset": dataset.version,
        "split": "dev",
        "mode": "lexical",
        "questions_in_scope": len(in_scope),
        "answerable_scored": len(inputs.answerable_scores),
        "unanswerable_scored": len(inputs.unanswerable_scores),
        "unanswerable_without_any_match": sum(
            1 for q, o in in_scope if q.kind == "unanswerable" and o.top_score is None
        ),
        "no_threshold": scores(None),
        "calibrated_threshold": None if calibration is None else calibration.threshold,
        "calibrated_threshold_hex": None if calibration is None else calibration.threshold.hex(),
        "with_threshold": None if calibration is None else scores(calibration.threshold),
        "answerable_min_score": min(inputs.answerable_scores, default=None),
        "unanswerable_max_score": max(inputs.unanswerable_scores, default=None),
        "note": "in-sample: chosen and scored on the same dev questions",
    }
    OUT.with_suffix(".json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = ["# Lexical abstention threshold (lab_v0 dev only, in-sample)", ""]
    lines += [f"- `{k}`: `{v}`" for k, v in report.items()]
    OUT.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
