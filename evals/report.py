"""Run configuration and report writing (Task 1.10): JSON for machines, Markdown for people.

A report states the dataset version, split, configuration and models, and only measured numbers.
A metric that could not be measured is written as "not measured", never estimated.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RunConfig:
    dataset_version: str
    split: str
    mode: str
    candidates: int
    rrf_k: int
    rerank_top: int
    top_k: int
    abstain_threshold: float | None
    chunker_version: str
    embedding_model: str | None
    reranker_model: str | None
    # The rules that decided which documents could be indexed (ADR-036 A3): frozen with the run.
    classifier_rules_version: str | None = None
    identity_rules_version: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunReport:
    config: RunConfig
    metrics: dict[str, float | int | None]  # None = not measured
    counts: dict[str, int]
    notes: tuple[str, ...] = ()


def _cell(value: float | int | None) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def to_markdown(
    reports: list[RunReport], title: str, ingestion: dict[str, float | int | None] | None = None
) -> str:
    lines = [f"# {title}", ""]
    if not reports:
        return "\n".join([*lines, "No runs."])
    first = reports[0].config
    lines += [
        f"- Dataset: `{first.dataset_version}`, split `{first.split}`",
        f"- Chunker version: `{first.chunker_version}`",
        f"- Embedding model: `{first.embedding_model or 'none'}`",
        f"- Reranker: `{first.reranker_model or 'none'}`",
        f"- Classifier rules: `{first.classifier_rules_version or 'not recorded'}`; identity "
        f"rules: `{first.identity_rules_version or 'not recorded'}`",
        "",
    ]
    names = sorted({name for report in reports for name in report.metrics})
    lines.append("| Mode | " + " | ".join(names) + " |")
    lines.append("|---|" + "---|" * len(names))
    for report in reports:
        cells = " | ".join(_cell(report.metrics.get(name)) for name in names)
        lines.append(f"| {report.config.mode} | {cells} |")
    for report in reports:
        if report.notes:
            lines += ["", f"Notes ({report.config.mode}):", *[f"- {note}" for note in report.notes]]
    if ingestion is not None:
        lines += ["", "## Ingestion timing (recorded by `ingest`)", ""]
        lines += ["| Measure | Value |", "|---|---|"]
        lines += [f"| {name} | {_cell(value)} |" for name, value in ingestion.items()]
    return "\n".join(lines) + "\n"


def write_reports(
    reports: list[RunReport],
    directory: Path,
    name: str,
    title: str,
    *,
    ingestion: dict[str, float | int | None] | None = None,
) -> tuple[Path, Path]:
    """Write ``<name>.json`` and ``<name>.md``; never overwrites an existing report.

    JSON shape: ``{"runs": [...], "ingestion": {...} or null}``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / f"{name}.json", directory / f"{name}.md"
    if json_path.exists() or md_path.exists():
        raise FileExistsError(f"report {name} already exists")
    payload = {"runs": [asdict(r) for r in reports], "ingestion": ingestion}
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(reports, title, ingestion), encoding="utf-8")
    return json_path, md_path
