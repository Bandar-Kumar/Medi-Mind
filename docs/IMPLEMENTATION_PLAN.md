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
| 1.2 | ✅ **Project foundation (done 2026-11-13).** Covers original tasks 1.2–1.4, plus error handling and a health check:<br>• `pyproject.toml`, `uv.lock`, `.python-version`;<br>• `app/main.py` entry point (`python -m app health`);<br>• `app/core/config.py`;<br>• `app/core/logging.py` (text + JSON);<br>• `app/core/errors.py`;<br>• `app/core/health.py`;<br>• `.env.example`;<br>• 32 pytest tests.<br>**Deviations:**<br>• `DEMO_MODE` and the region profile are deferred until first used (the UI, and Phase 3);<br>• run IDs are deferred to the `pipeline_runs` task. | M | See SUMMARY.md |
| 1.3 | *(merged into 1.2)* | — | — |
| 1.4 | *(merged into 1.2)* | — | — |
| 1.5 | ✅ **Done (2026-11-13).** `docker-compose.yml`:<br>• a single PostgreSQL 18 + pgvector service, `pgvector/pgvector:0.8.7-pg18-trixie`, pinned by digest `sha256:ab49524d…b754fa`;<br>• named volume `medimind_pgdata` at `/var/lib/postgresql`;<br>• port bound to `127.0.0.1` only;<br>• required password;<br>• `pg_isready` health check, `mem_limit: 1g`, `restart: "no"`.<br>App side:<br>• `MEDIMIND_DB_*` settings, with the password as a `SecretStr`;<br>• `database_tcp` and `database_password` health checks. These are **warnings only**, and TCP reachability only.<br>Not in this task: no SQLAlchemy, extension, tables or migrations. | S | Compose file, README "Database (Docker)" section, 14 new tests |
| 1.6 | ✅ **Done (2026-11-13).** SQLAlchemy 2.1.8 + psycopg 3.3.9 (binary) connection in `app/records/db.py` (the only DB access path, ADR-016):<br>• lazy, process-wide pooled engine: `pool_size` 2, `max_overflow` 3, `pool_timeout` 5 s, `pool_recycle` 1800 s, `pool_pre_ping`;<br>• throwaway NullPool engines (no pool-sizing args) for one-shot checks;<br>• `hide_parameters`, `connect_timeout` 2 s, `session_scope()` (records layer only);<br>• sanitized `DatabaseError` (type + SQLSTATE + bounded TCP check, never message text).<br>Authenticated `database_connection` health check: **warn** when unreachable, **fail** when the login is rejected.<br>Alembic moved to 1.7 (D1). No tables, extensions or migrations. | M | `app/records/db.py`, ADR-016 architecture test, 125 unit + 5 opt-in integration tests |
| 1.7 | Initialize Alembic (moved from 1.6, decision D1); baseline migration: `patients`, `documents` (core columns), `pipeline_runs`; enable the `vector` extension | M | `migrations/`, migration 0001 |
| 1.8 | Patient-scoped repository pattern for patients and documents, plus **isolation tests**<br>**Partly done in Task 2.5a (2026-11-15):** document creation and the parse-claim functions, patient-scoped, with isolation tests against `medimind_test`.<br>**Partly done in Task 2.6a (2026-11-15):** `create_patient` (validated) and `patient_exists` (ADR-033).<br>**Still open:** patient listing, update and delete; document listing, update and delete; and their isolation tests. | M | `app/records/repositories.py`, tests |
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

## Phase 2: Medical document intelligence (in progress)

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

### Tasks (refined as the phase runs)

Tasks 2.1–2.5 cover the outline's "Docling spike", "upload validation", "storage and hashing" and "parse into `pages`, with OCR fallback". Task 2.6 starts the upload part of "upload and document library UI" (the service, not the UI). The other subtasks (rule-based classifier, identity check, document library UI) are not yet broken into tasks.

| # | Task | Status | Output |
|---|---|---|---|
| 2.1 | Docling spike: runtime smoke test (ADR-026) and speed, memory and accuracy on a native and a scanned synthetic PDF, outside the repository | ✅ Done, owner approved (2026-11-14) | Measurements in SUMMARY.md and ADR-026 |
| 2.2 | Dependencies and settings: `docling==2.150.1` (CPU PyTorch), dev dependency `reportlab`; settings `max_upload_bytes`, `max_pages`, `parse_timeout_seconds`, `uploads_dir`, `parsed_dir`; errors `DocumentValidationError`, `DocumentParseError`; opt-in `docling` test marker | ✅ Done, approved (2026-11-14) | `pyproject.toml`, `uv.lock`, `app/core/` |
| 2.3 | Parse IR (schema v1), PDF validation, write-once read-only content-addressed storage of originals | ✅ Done, approved (2026-11-14) | `app/ingestion/ir.py`, `validation.py`, `storage.py`; fixtures built in code |
| 2.4 | Docling parser: pinned configuration, mapping to the IR, review flags (ADR-028–031) | ✅ Done, approved (2026-11-14); documentation committed in `f42f3f7` | `app/ingestion/parsing.py`, `docling_mapping.py`; unit tests and opt-in Docling tests |
| 2.5 | Persist the IR and Docling JSON files (freezes IR schema version 1) and write `pages` rows (ADR-029, ADR-030 decision 6, ADR-032) | ✅ Done (2026-11-15):<br>• step 0 (D7): `replacement_character` flag;<br>• 2.5a: patient-scoped document and parse-claim repositories;<br>• 2.5b: write-once parse outputs with a manifest;<br>• 2.5c: IR → `pages` rows;<br>• 2.5d: `process_document` orchestration, lone-surrogate rule in the IR;<br>• 2.5e: milestone verification (unit, Docling, PostgreSQL integration, end-to-end), 0 failures, 0 skips;<br>• 2.5f: documentation, IR version 1 frozen.<br>A successful parse leaves the document `processing` (ADR-032) | `app/records/repositories.py`, `app/ingestion/parse_outputs.py`, `app/services/`; unit, integration and end-to-end tests |
| 2.6 | `upload_document` service: type checks → `validate_pdf_bytes` → `patient_exists` → `store_original` (SHA-256, write-once) → `create_document` (patient-scoped duplicate detection); plus a **minimal patient repository** (needed to upload at all). Upload does not start parsing. The rest of Task 1.8 stays open (ADR-033) | ✅ Done (2026-11-15):<br>• 2.6a: `PatientError`, `create_patient`, `patient_exists`, exact patient-FK mapping in `create_document`;<br>• 2.6b: `upload_document` and `UploadResult`;<br>• 2.6c: verification (786 default, 115 integration), 0 failures, 0 skips;<br>• 2.6d: documentation, ADR-033 | `app/records/repositories.py`, `app/services/upload_document.py`; unit and integration tests |

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
