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
| 1.8 | Patient-scoped repository pattern for patients and documents, plus **isolation tests**<br>**Partly done in Task 2.5a (2026-11-15):** document creation and the parse-claim functions, patient-scoped, with isolation tests against `medimind_test`.<br>**Partly done in Task 2.6a (2026-11-15):** `create_patient` (validated) and `patient_exists` (ADR-033).<br>✅ **Done (2026-11-16, ADR-034):** `list_patients`, `update_patient`, `delete_patient` (empty only); `list_documents`, `get_document`, `rename_document`, `delete_document` (rows only, files kept); isolation matrix and complete public-API scoping test. | M | `app/records/repositories.py`, tests |
| 1.9 | ✅ **Done in Milestone R1.1 (2026-11-16, ADR-035):** `evals/synthetic/`, dataset `data/synthetic/lab_v0`. Synthetic data generator v0 (ADR-010): ground-truth JSON for lab reports, rendered to PDF in 2–3 **Indian-style layouts** (DD/MM/YYYY dates, Indian units, lakh notation, some ambiguous dates on purpose) | M | `evals/synthetic/`, sample files in `data/synthetic/` |
| 1.10 | ✅ **Done in Milestone R1.2 (2026-11-16):** `evals/` (manifest loader, question set, metrics, reports). Evaluation harness skeleton: dataset manifest, dev/held-out split by patient, metric interface, report writer | M | `evals/` |
| 1.11 | Minimal Streamlit shell: patient selector, empty pages, demo-mode banner. *Planned to be delivered within Task 2.9b (2026-11-17, ADR-036).* *Delivered with Task 2.9b (2026-11-17):* patient selector and demo-mode banner in `app/ui/`, with working pages rather than empty ones. Not yet verified against PostgreSQL. | S | `app/ui/` |
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

Tasks 2.1–2.5 cover the outline's "Docling spike", "upload validation", "storage and hashing" and "parse into `pages`, with OCR fallback". Task 2.6 starts the upload part of "upload and document library UI" (the service, not the UI). The other subtasks (rule-based classifier, identity check, document library UI) are not yet broken into tasks. *Update 2026-11-17:* they are now planned as Tasks 2.7–2.9 (owner-approved, ADR-036); see the rows below and "Tasks 2.7–2.9 in detail".

| # | Task | Status | Output |
|---|---|---|---|
| 2.1 | Docling spike: runtime smoke test (ADR-026) and speed, memory and accuracy on a native and a scanned synthetic PDF, outside the repository | ✅ Done, owner approved (2026-11-14) | Measurements in SUMMARY.md and ADR-026 |
| 2.2 | Dependencies and settings: `docling==2.150.1` (CPU PyTorch), dev dependency `reportlab`; settings `max_upload_bytes`, `max_pages`, `parse_timeout_seconds`, `uploads_dir`, `parsed_dir`; errors `DocumentValidationError`, `DocumentParseError`; opt-in `docling` test marker | ✅ Done, approved (2026-11-14) | `pyproject.toml`, `uv.lock`, `app/core/` |
| 2.3 | Parse IR (schema v1), PDF validation, write-once read-only content-addressed storage of originals | ✅ Done, approved (2026-11-14) | `app/ingestion/ir.py`, `validation.py`, `storage.py`; fixtures built in code |
| 2.4 | Docling parser: pinned configuration, mapping to the IR, review flags (ADR-028–031) | ✅ Done, approved (2026-11-14); documentation committed in `f42f3f7` | `app/ingestion/parsing.py`, `docling_mapping.py`; unit tests and opt-in Docling tests |
| 2.5 | Persist the IR and Docling JSON files (freezes IR schema version 1) and write `pages` rows (ADR-029, ADR-030 decision 6, ADR-032) | ✅ Done (2026-11-15):<br>• step 0 (D7): `replacement_character` flag;<br>• 2.5a: patient-scoped document and parse-claim repositories;<br>• 2.5b: write-once parse outputs with a manifest;<br>• 2.5c: IR → `pages` rows;<br>• 2.5d: `process_document` orchestration, lone-surrogate rule in the IR;<br>• 2.5e: milestone verification (unit, Docling, PostgreSQL integration, end-to-end), 0 failures, 0 skips;<br>• 2.5f: documentation, IR version 1 frozen.<br>A successful parse leaves the document `processing` (ADR-032) | `app/records/repositories.py`, `app/ingestion/parse_outputs.py`, `app/services/`; unit, integration and end-to-end tests |
| 2.6 | `upload_document` service: type checks → `validate_pdf_bytes` → `patient_exists` → `store_original` (SHA-256, write-once) → `create_document` (patient-scoped duplicate detection); plus a **minimal patient repository** (needed to upload at all). Upload does not start parsing. The rest of Task 1.8 stays open (ADR-033) | ✅ Done (2026-11-15):<br>• 2.6a: `PatientError`, `create_patient`, `patient_exists`, exact patient-FK mapping in `create_document`;<br>• 2.6b: `upload_document` and `UploadResult`;<br>• 2.6c: verification (786 default, 115 integration), 0 failures, 0 skips;<br>• 2.6d: documentation, ADR-033 | `app/records/repositories.py`, `app/services/upload_document.py`; unit and integration tests |
| 2.7 | Rule-based document classification: `lab_report` / `other` / `unknown`, rule score, versioned rules, `doctype_v0` dataset, offline accuracy evaluation (ADR-036) | ✅ Implemented (2026-11-17); unit-tested; dev measured (rules v2: accuracy 0.9464, `lab_report` precision 1.0000 / recall 0.8750, unknown rate 0.0536); owner targets (D9): accuracy >= 0.90, lab precision >= 0.95, lab recall >= 0.85, unknown rate <= 0.10, all met on dev; **frozen** (rules v2) and held-out evaluated once: accuracy 0.9643, lab precision 1.0000 / recall 1.0000, unknown rate 0.0357; PostgreSQL integration tests written, **not run** (Docker unavailable) | `app/ingestion/classification.py`, `app/services/classify_document.py`, `record_classification`, `evals/synthetic/doctype.py`, `evals/run_classification.py`; tests |
| 2.8 | Patient identity verification: `match` / `mismatch` / `unknown`, strict name matching, `mismatch` → `needs_review` and no indexing; identity gate in `index_document` (ADR-036) | ✅ Implemented (2026-11-17); unit-tested; dev measured on `identity_v0` (rules v1, 640 pairs): 0 false matches on distinguishable swapped profiles, 0 own-profile false mismatches. *Rules v2 (2026-11-17, review fixes, ADR-036 update):*<br>• dev `identity_v1` (new edge-case dataset, 336 pairs): 0 false matches, 0 own false mismatches beyond 2 documented limits;<br>• dev `identity_v0` (640 pairs): unchanged, 0 / 0.<br>**Frozen** (rules v2). Held-out evaluated once each, **not blind**:<br>• `identity_v1` (336 pairs): 0 false matches, 0 own false mismatches;<br>• `identity_v0` (160 pairs): 0 / 0.<br>Accepted limitations are in ADR-036. PostgreSQL integration tests written, **not run** (Docker unavailable) | `app/ingestion/identity.py`, `app/services/check_identity.py`, `record_identity_check`, gate in `app/services/index_document.py`, `evals/run_identity.py`, `evals/run_identity_v1.py`; tests |
| 2.9 | Document library UI: 2.9a pipeline orchestration and service wrappers; 2.9b thin Streamlit pages (includes Task 1.11) | **2.9a** ✅ Implemented (2026-11-17): `run_document_pipeline` / `run_patient_pipeline` (four stage results per document, A4) and the `library` wrappers; unit-tested with stand-ins; PostgreSQL integration **not run** (Docker unavailable). **2.9b** ✅ Implemented (2026-11-17): local Streamlit UI (`app/ui/`), `streamlit` and `demo_mode` added (D8); AppTest tests with stand-in services; started on 127.0.0.1 and smoke-checked without a database. **Not verified against PostgreSQL** (Docker unavailable). The demo database seeding (ADR-014) is not implemented | `app/services/document_pipeline.py`, `app/services/library.py`, `app/ui/`; tests |

### Tasks 2.7–2.9 in detail (approved 2026-11-17; ADR-036)

**Common rules for all three tasks:**
- **No migration:** every needed column already exists (`doc_type`, `doc_type_confidence`, `identity_check`, `processing_status`).
- **No new dependency** except `streamlit`, added when Task 2.9b begins (D8).
- All database access goes through new patient-scoped functions in `app/records/repositories.py` (ADR-016); `patient_id` is the first argument; another patient's document behaves as `document_not_found`.
- Page text and parse outputs are read only, never modified (ADR-029, ADR-030, ADR-032).
- Logs, run rows, errors and result objects carry identifiers, counts, outcomes, rule versions and fixed reason codes only: never page text, names or dates of birth.
- Classification and identity verification are **independent**: each reads the parsed pages, and neither needs the other to succeed (ADR-036 R2).
- **Idempotency (ADR-036 A2):** a stage with a succeeded run is not repeated automatically (`already_classified` / `already_checked` in the repository; `already_done` in services and the pipeline); a failed run can be retried with a new run row; re-running after a rule-version change is out of scope.
- **Indexing gate (ADR-036 A1):** indexing needs a succeeded `classify` run and a succeeded `identity` run, and `identity_check <> 'mismatch'`; enforced inside `index_document`, which every indexing path uses.
- Database-dependent tests are written as integration tests; while Docker is unavailable they are reported as **not run**, never as passing.

#### Task 2.7: Rule-based document classification (M)

- **Objective:** assign `lab_report`, `other` or `unknown` from the parsed pages, and measure the accuracy.
- **Prerequisites:** parsed pages (Task 2.5). No database is needed for the rules or their evaluation.
- **Reuses:** `pages` rows; the `pipeline_runs` pattern of `PARSE_STAGE`; `session_scope`; the scoped lookup used by `get_index_target`; `evals/` metrics and report writer; reportlab (existing dev dependency).
- **Sequence:**
  1. `app/ingestion/classification.py`: pure `classify_pages(pages) -> Classification(doc_type, score, rule_ids, rules_version)`; `CLASSIFIER_RULES_VERSION = "1"`. Lab cues: `Investigation` / `Test Name`, `Result`, `Biological Reference Interval` / `Reference Range`, `Sample ID` / `Collected`, lab or pathology letterhead, `has_tables`. Non-lab cues: `Rx`, `Discharge Summary`, `Date of Admission`, `Diagnosis:`. Records rule IDs, never matched text.
  2. `record_classification(patient_id, document_id, doc_type, score, rules_version)` in `app/records/repositories.py`: locks the document, writes `doc_type`, `doc_type_confidence` (rule score, D3) and a `classify` run; refuses `already_classified` when a succeeded run exists; never changes `processing_status`.
  3. `app/services/classify_document.py`, and the command `python -m app classify`.
  4. `evals/synthetic/doctype.py` → `data/synthetic/doctype_v0` (D2): fictional non-lab documents and harder lab layouts; patient-level split; held-out digest frozen before tuning.
  5. `evals/run_classification.py`: offline Docling parse, no database; accuracy, `lab_report` precision and recall, confusion matrix, `unknown` rate.
- **Tests:** unit tests per rule; empty pages, U+FFFD, a single weak cue, conflicting cues; determinism; repository behaviour with a stand-in (scoping, invalid type or score, run row, no text); architecture test (`ingestion/classification.py` does not import `records`). Integration (PostgreSQL): cross-patient `document_not_found`, CHECK constraints, `already_classified`.
- **Acceptance:** dev accuracy and confusion matrix measured and reported; the target is set by the owner after the first dev measurement (D9); held-out reported once after the rules are frozen; every classified document has a `classify` run.
- **Risks:** overfitting to self-generated templates (mitigated by the frozen held-out split and layout variety); OCR noise hiding cues.
- **Excluded:** LLM classification; Phase 6 types; document-date extraction; user type overrides; reclassification after a rule change.

#### Task 2.8: Patient identity verification (M)

- **Objective:** compare the document's printed name (and date of birth, when printed) with the profile; record `match`, `mismatch` or `unknown`; keep `mismatch` documents out of the index.
- **Prerequisites:** parsed pages (Task 2.5); the profile via the patient repository. **Independent of Task 2.7's result.**
- **Reuses:** `pages`; `PatientSummary` fields; `IdentityCheck`; ADR-025 date rules (DD/MM, ambiguous dates flagged); `index_document` and `list_unindexed_documents`.
- **Sequence:**
  1. `app/ingestion/identity.py`: pure `check_identity(pages, display_name, date_of_birth) -> IdentityResult(outcome, reason_codes, page_number, rules_version)`; `IDENTITY_RULES_VERSION = "1"`. Labelled fields (`Patient Name:`, `Name:`, `Pt. Name`, `DOB` / `Date of Birth`); normalization of case, whitespace, punctuation, honorifics (Mr, Mrs, Ms, Dr, Baby, Master) and token order. Outcomes per D4 and D5: `match` only for equal full token sets; partial or initials-only, several names, no name, U+FFFD, ambiguous date of birth → `unknown`.
     - *Update (2026-11-17, rules v2):* `Baby` is no longer an honorific; a bare `Name:` is not identity evidence; a full name is needed on both sides; other people's DOB labels are skipped. The full v2 rule list is in the ADR-036 update.
  2. `record_identity_check(patient_id, document_id, outcome, rules_version)`: writes `identity_check` and an `identity` run; on `mismatch` sets `needs_review` in the same transaction; refuses `already_checked` when a succeeded run exists.
  3. **Indexing gate (R1, A1; needs Task 2.7):** `index_document` refuses with `not_classified` (no succeeded `classify` run), `identity_not_checked` (no succeeded `identity` run) or `identity_mismatch`; checked before chunking and embedding, and re-checked under the document lock in the chunk-insert transaction (`insert_document_chunks`). `list_unindexed_documents` is unchanged (gated documents are still listed), and `index_patient` returns an explicit indexed-or-blocked result per document instead of stopping or hiding one. Evidence from an `unknown`-identity document is labelled "identity not confirmed" (ranking unchanged).
  4. **R1.8 runner (A3):** ingest becomes upload → parse → classify → identity → index, each a recorded resumable stage; a blocked document stops ingestion with an error (the corpus is never silently reduced); the classifier and identity rule versions go into the run configuration and are validated by `heldout` against `dev_selection.json`. No retrieval, chunking, model or threshold change. **Not run now** (R1.8 deferred, Docker unavailable).
  5. `app/services/check_identity.py`, and the command `python -m app identity`.
  6. `evals/run_identity.py`, offline: each dev report against its own profile (expected `match`) and against another dev patient's profile (expected `mismatch` or `unknown`).
- **Tests:** normalization (honorifics, order, spacing); strict and partial cases; the result type cannot carry text; logging tests through the JSON formatter (no names); `mismatch` → `needs_review`, `match` / `unknown` leave the status unchanged. Required gate and R1 compatibility tests (unit tests use stand-ins and temporary fictional data, never `lab_v0`):
  - **Gate refusals:** `mismatch` → `identity_mismatch`; no succeeded `classify` run → `not_classified`; no succeeded `identity` run → `identity_not_checked`; a failed run of either stage counts as no run.
  - **"Unknown" accepted only after its run:** `identity_check = 'unknown'` is accepted only with a succeeded `identity` run, and `doc_type = 'unknown'` only with a succeeded `classify` run; the same column values as untouched defaults (no run) are refused.
  - **Mixed bulk indexing:** `index_patient` over a patient with indexable and gated documents returns one result per document, indexed or blocked with its kind, in order; it neither stops at the first blocked document nor omits one; `list_unindexed_documents` still lists the gated documents.
  - **Identity-unknown evidence label:** evidence from an `unknown`-identity document carries "identity not confirmed"; evidence from a `match` document does not; scores and ranking are identical with and without the label.
  - **R1.8 frozen configuration:** `dev_selection.json` records the classifier and identity rule versions; `heldout` refuses when either version differs from the frozen one; the runner stops on a blocked document.
  - **Integration (PostgreSQL):** scoping; the atomic `mismatch` → `needs_review` change with the run row; **gate re-check under the document lock**: a mismatch recorded after `index_document`'s pre-check but before the chunk insert makes the insert refuse with `identity_mismatch` and write no chunks and no index run; gated documents still returned by `list_unindexed_documents`.
- **Acceptance:** **0 false `match`** on swapped-profile dev pairs (measured); match rate and `unknown` rate reported; no `mismatch` document gets chunks (tested); no personal data in logs or run rows.
- **Risks:** shared surnames in a family; transliteration variants (Mohd / Mohammed, Kumar / Kr.); a renamed profile leaves the stored outcome stale (no recheck in this phase, D6).
- **Excluded:** fuzzy or phonetic matching; age checks; override or "keep anyway"; moving documents between patients; automatic re-checks; Phase 3 extraction behaviour on `mismatch`.

#### Task 2.9: Document library UI (L)

**2.9a: Orchestration and service wrappers (S)**
- `app/services/document_pipeline.py`: runs parse → classify → identity → index synchronously and returns one result per stage: `succeeded`, `already_done`, `failed (<kind>)` or `blocked (<reason>)` (A4). Parse failure blocks classify, identity and index (`not_parsed`); a classification failure blocks only indexing (`not_classified`) and identity still runs; an identity failure blocks only indexing (`identity_not_checked`) and classification still runs; identity `mismatch` blocks indexing (`identity_mismatch`). No stage is skipped silently. Full table in ADR-036 A4.
- `app/services/library.py`: thin wrappers over the patient and document repositories (create, list, get, rename, delete), so `ui/` imports services only. `DocumentSummary` gains read-only `identity_check` and `doc_type_confidence` fields so the library can show them (no schema change).
- Tests: every stage-result combination with stand-in services; failures are reported, never swallowed.

**2.9b: Streamlit pages (includes Task 1.11)**
- When this part begins: add `streamlit` (D8) and the `demo_mode` setting (ADR-014); `.streamlit/config.toml` with `server.address = 127.0.0.1`, `browser.gatherUsageStats = false`, and `server.maxUploadSize` matching `max_upload_bytes`.
- `app/ui/app.py`: patient selector and create form (F1).
- `app/ui/pages/documents.py`: library table (filename, type, rule score, page count, status, identity badge, upload date); type and status filters; PDF upload through `upload_document` with its specific errors and duplicate message (F2); "Process" with per-stage progress from `document_pipeline`; rename; delete with a confirmation showing page and run counts (F10); read-only detail with type, rule version, identity outcome and reason codes, processing history. A `processing` document is labelled "parsed, awaiting extraction" (D7); `unknown` identity is shown, not hidden.
- **Thin UI (R3):** no SQL, rules, identity logic or orchestration in `app/ui/`; `st.session_state` holds IDs only.
- Tests: Streamlit's `AppTest` with stand-in services (select or create a patient, empty state, upload errors, demo mode hides upload, delete confirmation, badges, including the "identity not confirmed" badge for an `unknown`-identity document and the `needs_review` badge for a `mismatch`); architecture test: `app/ui` imports only `app.services` (plus Streamlit and the standard library).
- *As built (2026-11-17), with three deviations:*
  - The entry file is `app/ui/streamlit_app.py`, not `app/ui/app.py`. Streamlit puts the script's folder on `sys.path`, so a file named `app.py` there could shadow the `app` package.
  - The pages are functions in `app/ui/views.py` with sidebar navigation, not a `pages/` folder.
  - The live acceptance run (a synthetic PDF through every stage against PostgreSQL) is **pending**, because Docker is unavailable.
- **Acceptance:** `uv run streamlit run app/ui/app.py` starts on localhost; a synthetic PDF goes from upload through every pipeline stage, with its type and identity outcome shown; every call carries the selected `patient_id`; AppTest tests pass.
- **Excluded:** page image viewer and facts panel (Phase 3); PNG and JPEG upload; revision linking (`supersedes_document_id`); moving documents between patients; authentication; background worker; timeline, trends and copilot pages.

**Sequence:** 2.7 → 2.8 → 2.9a → 2.9b. Tasks 2.7 and 2.8 can be built and measured offline; their repository functions and the gate need PostgreSQL for integration verification.

## Phase 3: Structured clinical extraction (outline)

> **Superseded by [PHASE_3_PLAN.md](PHASE_3_PLAN.md) (approved 2026-11-17).** Status on 2026-11-17: Batches A–F implemented; the rule-based extractor replaced the LLM-first outline (D-1; ADR-037, ADR-038); dev and held-out measured once on `extract_v0`; PostgreSQL integration tests and the live end-to-end run **not run** (Docker unavailable); optional Batch G (LLM comparison) not started. The outline below is kept for history.

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

> **Implemented 2026-11-18 under [PHASE_4_PLAN.md](PHASE_4_PLAN.md) (ADR-039).**
> - **Built:** every subtask below; comparison is between two dates (document comparison stays in Phase 6).
> - **Acceptance met on `longitudinal_v0`:** trends match the ground truth exactly on dev and on held-out (run once); no LLM calls.
> - **Status:** awaiting the owner's review. The outline is kept for history.

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

## Milestone R1: Evidence retrieval slice (pulled forward from Phase 5; ADR-035)

| Step | Deliverable | Status (2026-11-16) |
|---|---|---|
| R1.1 | Synthetic generator v0 (Task 1.9): `evals/synthetic/`; dataset `data/synthetic/lab_v0` | ✅ Built: 12 patients, 50 reports, 81 pages, 3 layouts, 8/4 patient split, held-out digest frozen |
| R1.2 | Evaluation harness (Task 1.10): `evals/dataset.py`, `questions.py`, `metrics.py`, `report.py` | ✅ Unit-tested (13 tests) |
| R1.3 | `app/retrieval/chunking.py` (`CHUNKER_VERSION` 1) | ✅ Unit-tested (14); measured on real Docling output (3 reports) |
| R1.4 | `app/records/chunks.py` | ✅ Written; integration tests (7) **pending** (Docker down). *Updated 2026-11-17:* Task 2.8 added the indexing gate to this module and extended `test_chunks_integration.py` to 12 tests, still **not run** |
| R1.5 | `app/retrieval/models.py` (BGE, MiniLM; lazy; pinned revision; cache only) | ✅ Written; ~~real-model tests (`-m models`, 3) **pending owner approval of revisions and download**~~ *Corrected 2026-11-17:* revisions approved by the owner and pinned (2026-11-16, ADR-002/ADR-035), both models downloaded once into the local cache, CPU inference verified; real-model tests (`-m models`) **3 passed** |
| R1.6 | `app/services/index_document.py`, `python -m app index` | ✅ Written, unit-tested with fakes |
| R1.7 | `fusion.py`, `evidence.py`, `query.py`, `app/services/search_evidence.py`, `python -m app ask` | ✅ Written, unit-tested with fakes (35 + 16 + 4) |
| R1.8 | `evals/run_retrieval.py`: ingest, dev ablation, calibration, single held-out run | ⏸ Runner written and scoring unit-tested; **no retrieval metric measured yet** (needs the database ~~and the approved models~~; *corrected 2026-11-17:* the models are approved and cached since R1.5, so only the database is missing). Readiness corrections done 2026-11-16 (ADR-035 update): split-local leak probes, separate leak and refusal metrics, calibration on answerable vs ordinary unanswerable only, indexing time per page, resumable ingestion, `freeze` → `dev_selection.json` → `heldout`. **Deferred — Docker unavailable (2026-11-17).**<br>*Planned change (Task 2.8, ADR-036 A3)*, ~~planned~~ *implemented in Task 2.8 (2026-11-17), unit-tested, not run:* ingest becomes upload → parse → classify → identity → index; a blocked document stops ingestion; rule versions recorded and validated by `heldout` |
| R1.8 (update 2026-11-18) | Run completed | ✅ Docker available: dev ingest (35 reports, 57 pages), ablation, `lexical` frozen, held-out ingest (15 reports) and one held-out run. Results in [PHASE_5_PLAN.md](PHASE_5_PLAN.md) §2 |
| R1.9 | Documentation | ✅ This update |

## Phase 5: Advanced hybrid RAG (outline)

> **Implemented 2026-11-18 under [PHASE_5_PLAN.md](PHASE_5_PLAN.md) (ADR-040); awaiting the owner's review.**
> - **Done:** R1.8 (ablation reported; lexical frozen; held-out once), router, SQL tools, answer generation (extractive default; LLM path offline-tested), citation validator, Copilot UI, `copilot_v0` evaluation (dev + held-out once).
> - **Acceptance:** ablation results reported; every shown answer passed citation validation (0/304 attacks accepted, 0 unsupported claims on held-out). Pre-registered citation precision ≥ 0.95 **not met** (held-out 0.605).
> - **Not done:** LLM provider evaluation (blocked), abstention threshold for lexical, portfolio checkpoint (demo seed, video). The outline below is kept for history.


> **Remediation pass (2026-11-18, ADR-041):** patient vocabulary, abstention on unrecognised test names, passage relevance gate, lexical threshold evaluated on dev (rejected), new evaluation `copilot_v1` (new held-out patients; run once). Results: PHASE_5_PLAN §12.

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
