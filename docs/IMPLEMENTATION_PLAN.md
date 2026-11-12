# Implementation Plan

> **Status: Proposed.** Phase 1 is planned down to individual tasks. Later phases are outlines, refined into tasks when each phase starts, so we don't plan details we can't yet know. Effort sizes (S/M/L) are relative estimates, not deadlines.

Every phase follows the same template: **Objective · Subtasks · Dependencies · Deliverables · Technologies · Tests · Acceptance criteria · Risks.**

**Phase gate:** A phase is complete only when its acceptance criteria pass, its tests pass, the docs are updated, and the owner approves.

---

## Phase 0: Product planning and system design (current)

- **Objective:** Agree on scope, architecture, data model, evaluation strategy and roadmap before writing any code.
- **Deliverables:** The eight docs in `docs/`, plus the README.
- **Acceptance criteria:**
  - The docs agree with one another.
  - Every open question is answered or logged with a default.
  - Every ADR has a status.
  - The owner has reviewed the docs before the first commit.
- **Risk:** Over-planning. Mitigation: only Phase 1 is detailed.

---

## Phase 1: Development environment and project foundation

- **Objective:** A reproducible environment with a database, migrations, configuration, logging, tests and the evaluation skeleton. **No AI features yet.**
- **Dependencies:** Phase 0 approved. Open question Q11 (Python version and package manager) resolved.
- **Technologies:** Python 3.12 (Docling compatibility to be verified), uv (proposed), Ruff, pytest, pydantic-settings, SQLAlchemy 2.x, Alembic, psycopg 3, Docker Compose, `pgvector/pgvector` Postgres image, Streamlit.

### Tasks

| # | Task | Size | Output |
|---|---|---|---|
| 1.1 | **Compatibility test (ADR-026):**<br>• resolve the planned dependency set on Windows with Python 3.12 + uv, without installing it;<br>• then, with approval, run a smoke test importing Docling in a throwaway environment;<br>• fall back to Python 3.11 if needed. | S | Test result recorded in ADR-026 |
| 1.2 | Create `pyproject.toml` with only the Phase 1 dependencies, plus the Ruff and pytest config | S | `pyproject.toml`, lock file |
| 1.3 | `app/core/config.py`: typed settings from `.env` (including `DEMO_MODE` and the region profile); commit `.env.example` | S | Config module and tests |
| 1.4 | `app/core/logging.py`: structured logging with run IDs and no document content | S | Logging module |
| 1.5 | `docker-compose.yml`: a single Postgres + pgvector service with a named volume and health check | S | Compose file, README setup steps |
| 1.6 | SQLAlchemy engine and session; initialize Alembic | M | `app/records/db.py`, `migrations/` |
| 1.7 | Baseline migration: `patients`, `documents` (core columns), `pipeline_runs`; enable the `vector` extension | M | Migration 0001 |
| 1.8 | Patient-scoped repository pattern for patients and documents, plus **isolation tests** | M | `app/records/repositories.py`, tests |
| 1.9 | Synthetic data generator v0 (ADR-010): ground-truth JSON for lab reports, rendered to PDF in 2–3 **Indian-style layouts** (DD/MM/YYYY dates, Indian units, lakh notation, some ambiguous dates on purpose) | M | `evals/synthetic/`, sample files in `data/synthetic/` |
| 1.10 | Evaluation harness skeleton: dataset manifest, dev/held-out split by patient, metric interface, report writer | M | `evals/` |
| 1.11 | Minimal Streamlit shell: patient selector, empty pages, demo-mode banner | S | `app/ui/` |
| 1.12 | Update docs and log progress | S | `PROGRESS.md` |

**Tests:**
- Configuration loading.
- The migration runs on an empty database.
- Repository create and read operations.
- Cross-patient isolation.
- The generator is deterministic for a fixed seed.

**Acceptance criteria:**
- `docker compose up` followed by the migration leaves the database ready.
- `pytest` passes.
- The Streamlit shell runs.
- The generator produces PDFs together with their ground-truth JSON.
- All of this works on Windows 11.

**Risks:**
- Docker Desktop memory use on WSL2. Mitigation: a `.wslconfig` memory cap.
- Picking a PDF rendering library: prefer one that is pure Python on Windows, e.g. ReportLab.
- Synthea needs Java. Generator v0 is pure Python, and Synthea adoption is reviewed in Phase 3 (ADR-010).

---

## Phase 2: Medical document intelligence (outline)

- **Objective:** Turn uploaded files into stored pages, text and tables, with a document type, duplicate detection and an identity check.
- **Subtasks:**
  - **Docling spike first** (speed and memory on native and scanned PDFs);
  - upload validation;
  - storage and hashing;
  - parse into `pages`, with OCR fallback;
  - rule-based classifier;
  - identity check;
  - upload and document library UI.
- **Dependencies:** Phase 1.
- **Tests:** File validation cases, duplicate detection, page provenance, classifier accuracy on the dev set.
- **Acceptance criteria:**
  - Synthetic lab reports, both native and scanned variants, are parsed with correct page numbers.
  - Classification accuracy is measured and reported.
- **Risks:** Docling resource use; OCR quality on degraded scans.

## Phase 3: Structured clinical extraction (outline)

- **Objective:** Extract grounded, normalized lab observations first. Medications, allergies and events are scaffolded but broadened in Phase 6.
- **Subtasks:**
  - **LLM provider evaluation first (ADR-024):** compare free-tier candidates and a local model on about 20 dev-set pages, measuring cost, schema-valid rate, accuracy, latency, rate limits and privacy terms;
  - Pydantic schemas;
  - LLM provider interface, prompt registry and response cache;
  - page-level extraction;
  - grounding verifier;
  - analyte, unit and date normalizers;
  - plausibility flags;
  - review statuses and the review UI;
  - migrations for the fact tables;
  - rule-based baseline for comparison.
- **Tests:** Normalizer unit tests; grounding verifier cases; extraction precision, recall and F1 on the dev set.
- **Acceptance criteria:**
  - Extraction metrics are reported against the dev set.
  - Every stored fact has provenance.
  - Facts that fail grounding go to `needs_review`.
- **Risks:** Hallucinated values; analyte variety; LLM cost.

## Phase 4: Database and longitudinal intelligence (outline)

- **Objective:** Timeline, lab trends and comparisons between dates, all deterministic. **This completes the first vertical slice.**
- **Subtasks:**
  - timeline query;
  - duplicate grouping;
  - trend series with unit conversion;
  - comparison queries;
  - Timeline and Lab trends UI.
- **Tests:** SQL and Python computations against hand-calculated fixtures; date-precision display.
- **Acceptance criteria:**
  - Trends match the ground truth exactly.
  - There are no LLM calls in this layer.
- **Risks:** Mixed units; partial dates.

## Phase 5: Advanced hybrid RAG (outline)

- **Objective:** Cited question answering with routing and abstention.
- **Subtasks:**
  - chunking;
  - BGE embeddings;
  - `chunks` migration;
  - dense and full-text retrieval;
  - RRF;
  - reranker;
  - query router;
  - SQL tools;
  - answer generation;
  - citation validator;
  - abstention calibration;
  - Copilot UI;
  - **ablation study**.
- **Tests:**
  - Retrieval metrics.
  - Citation validator unit tests.
  - Router accuracy.
  - Abstention on unanswerable questions.
  - Injection prompts.
- **Acceptance criteria:** Ablation results are reported, and every answer either carries valid citations or abstains.
- **Portfolio checkpoint (ADR-027, Proposed):**
  - demo mode with seeded synthetic data;
  - README with an architecture diagram and the measured evaluation results;
  - a short walkthrough video.
- **Risks:** Small corpus limits the gains; threshold calibration.

## Phase 6: Healthcare intelligence features (outline)

- **Objective:** Extend to prescriptions and discharge summaries. Add medications, allergies, events, conflict detection and document comparison.
- **Acceptance criteria:** Conflict detection precision and recall are measured; comparisons cite both sources.

## Phase 7: LangGraph workflows (outline)

- **Objective:** The appointment-preparation graph, with retries and a human-review interrupt, plus export. Record analysis only if ADR-012 is revisited.
- **Acceptance criteria:**
  - The graph is tested path by path, including retry exhaustion.
  - Exported summaries pass citation validation.

## Phase 8: Evaluation, security and reliability (outline)

- **Objective:**
  - final held-out evaluation;
  - security tests (isolation, injection, malformed files);
  - error handling;
  - performance and cost profiling;
  - optional row-level security and local-only provider.
- **Acceptance criteria:** One evaluation report, with dataset versions and measured results only.

## Phase 9: Frontend integration, deployment and portfolio documentation (outline)

- **Objective:**
  - polish the UI;
  - demo mode with preloaded synthetic data;
  - choose a hosting target;
  - write the final README with architecture diagrams, evaluation results and limitations.
- **Acceptance criteria:**
  - The demo runs on synthetic data, with uploads disabled.
  - The documentation explains the trade-offs and limitations honestly.
