# Progress

## Current phase

**Phase 2: Medical document intelligence.** Tasks 2.1–2.6 are complete:
- Task 2.4's documentation was committed in `f42f3f7`.
- Task 2.5 (parse persistence) was implemented, verified and documented on 2026-11-15, with IR schema version 1 frozen.
- Task 2.6 (document upload service with a minimal patient repository, ADR-033) was implemented, verified and documented on 2026-11-15.
- Task 1.8 (the rest of the patient and document repositories, ADR-034) was implemented and documented on 2026-11-16. Its unit tests pass; its integration tests have not run yet (Docker was down).
- Milestone R1 (evidence retrieval slice, pulled forward from Phase 5; ADR-035) was implemented and documented on 2026-11-16, including Tasks 1.9 and 1.10. Unit tests pass. The owner approved the pinned model revisions; both models are cached, CPU inference is verified and the 3 real-model tests pass (R1.5, 2026-11-16). Integration tests and the R1.8 evaluation are still pending (Docker down); no retrieval metric has been measured. The R1.8 readiness corrections (owner-approved) are implemented and unit-tested; see the R1 entry.
- **R1.8 is Deferred — Docker unavailable (2026-11-17).** It is not complete. The owner chose to move on to Phase 2 planning; see the R1 entry.
- **Task 2.7 (rule-based classification)** was implemented on 2026-11-17. Rules v2 are frozen, and the held-out split was evaluated once.
- **Task 2.8 (patient identity verification and the indexing gate)** was implemented on 2026-11-17. Rules v2 are frozen, and both identity held-out splits were evaluated once; these are not blind validations.
- **Task 2.9a (pipeline orchestration and library wrappers)** was implemented on 2026-11-17.
- **Task 2.9b (local Streamlit UI, `demo_mode`)** was implemented on 2026-11-17.

**Status as of 2026-11-17:** the Phase 2 tasks (2.1–2.9) are **implemented and unit-tested**. Two things remain open and are **not** complete:

| Open item | Why it is still open |
|---|---|
| **PostgreSQL integration verification** | Not run since Docker became unavailable. It covers the integration tests for Task 1.8, R1, 2.7, 2.8 and the 2.9a summary fields, and the 2.9b end-to-end acceptance run through the UI. |
| **R1.8 retrieval evaluation** | Deferred. **No retrieval metric has been measured.** |

The Task D7–2.6 work was committed by the owner as `6523677`. Task 1.8 and everything after it are **not committed yet** (the owner commits manually). The remaining Phase 2 subtasks (rule-based classifier, identity check, document library UI) are not yet broken into tasks. *Update 2026-11-17:* they are now planned as **Tasks 2.7–2.9**, approved by the owner (ADR-036, Accepted); ~~not started~~ implemented later the same day (above). Phase 0 is complete, but some Proposed ADRs are still open.

> This file has no entries for Task 1.7, migration safety, or Tasks 2.1–2.3. Their record is in [SUMMARY.md](../SUMMARY.md) (2026-11-13 and 2026-11-14).

## Completed

### 2026-11-12: Minimal project structure
- Initialized the git repository.
- **Git history, as of 2026-11-13:**
  - `d9dd20e`: "1st commit", by the owner;
  - `f34a9f8`: GitHub's "Initial commit" (placeholder README);
  - `1575603`: merge commit by the owner, after the README conflict was resolved. It is pushed: `origin/main` = `1575603`.
  - Task 1.2 was committed by the owner as `58033ec`, "Foundation ok". An earlier commit of the same work, `e9f708f`, was removed from GitHub at the owner's request.
- Created `README.md`, `.gitignore`, `app/`, `tests/` and `data/synthetic/`. The ignore rules were verified with dummy files.

### 2026-11-12: Phase 0 critical review and design docs
- Reviewed the master brief critically and wrote all eight design docs.

### 2026-11-12: Owner decisions recorded
- **Accepted:**
  - Q1 single-user local (ADR-013)
  - Q2 synthetic-only demo (ADR-014)
  - Q4 lab reports first (ADR-019)
  - Q6 records-only copilot (ADR-022)
  - Q7 status lifecycle with no silent verification (ADR-023)
  - Q8 pure-Python generator (ADR-010)
  - appointment-prep LangGraph (ADR-012)
- **Accepted (provisional):** Q11 Python 3.12 + uv, pending the compatibility test (ADR-026).
- **Deferred:**
  - Q5 LLM provider, to be chosen by measured comparison at the start of Phase 3, free preferred (ADR-024)
  - Q10 record-analysis workflow (ADR-012)
  - Synthea (ADR-010)
- **Proposed, awaiting confirmation:** Q3 India region profile (ADR-025).
- **Q9 answered:** the owner works full time and the goal is job readiness. The roadmap now has full-time estimates and a proposed portfolio checkpoint at the MVP (ADR-027).
- Updated `ARCHITECTURE`, `DATABASE_DESIGN`, `USER_FLOWS`, `IMPLEMENTATION_PLAN`, `PROJECT_BRIEF` and `ROADMAP` to match.

### 2026-11-13: Phase 1, Task 1.2: Project foundation
- **Built:**
  - `pyproject.toml` + `uv.lock` (Python `>=3.12,<3.13`);
  - entry point `python -m app health`;
  - Pydantic Settings configuration;
  - `.env.example`;
  - centralized logging (text and JSON);
  - error hierarchy with exit codes;
  - health checks;
  - pytest setup.
- **Verified:** 32 tests pass, alone and in reverse order; Ruff is clean; the health check exits with 0. Details are in [SUMMARY.md](../SUMMARY.md).
- **Pre-commit audit and fixes:**
  - Fixed audit finding #1: a configuration error no longer carries the original pydantic error, so no traceback can show the rejected value. A regression test was added.
  - Corrected the documentation (findings #2 and #3, plus the other doc fixes).
  - **33 tests pass.**
  - Findings #4–#12 are deferred (listed in SUMMARY.md).

### 2026-11-13: Phase 1, Task 1.1: Dependency compatibility (resolution only) ✅
- **Result:** the full planned stack resolves for Python 3.12 on Windows x86_64. 146 packages, prebuilt wheels only, no conflicts.
- **No version changes:** every package in `uv.lock` resolves to the same version.
- **Project unchanged:** `pyproject.toml`, `uv.lock`, `.venv` and git state were all checked unchanged.
- **Runtime compatibility is not yet established.** Docling's OCR backend is missing from the resolution. The runtime smoke test is the first step of the Phase 2 Docling spike (ADR-026).

### 2026-11-13: Architecture decisions finalized
- **Accepted by the owner:**
  - **ADR-009:** Docker Compose for PostgreSQL infrastructure only; the app is not containerized.
  - **ADR-016:** patient-scoped repositories. Isolation is enforced in every query and service, with mandatory cross-patient read, update and delete tests.
  - **ADR-020:** PostgreSQL infrastructure in Phase 1.
  - **ADR-002:** SQLAlchemy, Alembic and psycopg.
  - **ADR-025:** India as the initial region and intended privacy/data-protection jurisdiction. The architecture stays adaptable, and **no legal or regulatory compliance is claimed**.
- **Unchanged:** ADR-026 stays "Accepted — resolution verified (runtime not yet tested)".
- **Docs updated:** `docs/DECISIONS.md` (the history of each ADR kept), this file, `SUMMARY.md`, and one line in `docs/PROJECT_BRIEF.md`.

### 2026-11-13: Phase 1, Task 1.5: Docker Compose PostgreSQL + pgvector ✅
- **Built:**
  - `docker-compose.yml`: one `db` service, `pgvector/pgvector:0.8.7-pg18-trixie`, pinned by digest `sha256:ab49524d…b754fa`;
  - volume `medimind_pgdata` at `/var/lib/postgresql`, port bound to `127.0.0.1` only, required password, `pg_isready` health check, `mem_limit: 1g`, `restart: "no"`;
  - `MEDIMIND_DB_*` settings (the password is a `SecretStr` and is never displayed);
  - `database_tcp` and `database_password` health checks (**warnings only**; TCP reachability only);
  - 14 new tests (47 total) and a README "Database (Docker)" section.
- **Verified (measured):**
  - the pulled image's digest matches the pin;
  - healthy about 6 s after start;
  - PostgreSQL 18.7;
  - `vector` 0.8.7 available but not installed;
  - bound to `127.0.0.1:5432` only;
  - `system_identifier` unchanged across `stop`/`start` and `down`/`up` (persistence);
  - idle memory 23–26 MiB of the 1 GiB limit;
  - app health ok while running, `database_tcp` warn while stopped (exit 0 both times);
  - 47 tests pass, Ruff clean.
- **Not in this task:** no SQLAlchemy connection, extension, tables or migrations.
- **Left stopped**, with the volume kept.

### 2026-11-13: Phase 1, Task 1.6: SQLAlchemy connection + authenticated database health ✅
- **Built:**
  - dependencies `sqlalchemy` 2.1.8, `psycopg[binary]` 3.3.9 (libpq 18.4 bundled), plus `tzdata` 2026.6; no `greenlet`, no Alembic (moved to Task 1.7, decision D1);
  - `app/records/db.py`, the only database access path (ADR-016): connection URL with a masked password, lazy pooled engine (at most 5 connections, `pool_pre_ping`), NullPool engines for one-shot checks, `session_scope()`, read-only `probe_database()`;
  - sanitized `DatabaseError`: classified by exception type, SQLSTATE and (only when ambiguous) one bounded TCP check. The driver's message text is never read, logged or shown;
  - `database_connection` health check (decision D2): **warn** when unreachable or no password is set, **fail** when the login is rejected or any other database error occurs;
  - entry-point sanitization in `app/main.py`: database errors are logged as category + class + SQLSTATE, with no traceback;
  - ADR-016 architecture test: outside `app/records/`, no code imports SQLAlchemy or psycopg or uses sessions;
  - 125 unit tests (no database needed) and 5 opt-in, read-only integration tests (`uv run pytest -m integration`).
- **Verified (measured):**
  - the live container showed that a wrong password and a missing database both arrive as a plain `psycopg.OperationalError` with **no SQLSTATE** (libpq 18.4), so they share the category `connection_rejected`; a closed port arrives as `ConnectionTimeout` after the 2 s timeout;
  - integration tests: **5 passed** (run by the owner after the fix); health with the database running: all checks ok, exit 0;
  - pool: 5 connections held, the 6th request timed out after 5.01 s; returned connections leave 2 open; 0 after dispose;
  - `pool_pre_ping`: after `docker compose restart db` the same engine answered in 0.03 s;
  - memory: 54.6 MiB idle → 62.0 MiB with 5 connections (1 GiB limit);
  - database snapshot (tables, extensions, roles, schemas, databases) identical before and after all runs: 0 tables, `plpgsql` only;
  - with the database stopped: `database_connection` warn, exit 0, about 4.4 s.
- **Left stopped**, with the volume kept.

### 2026-11-14: Phase 2, Task 2.4: Docling parser (implementation and verification approved; documentation outstanding)
- **Built:** Docling parsing into the versioned IR (`app/ingestion/parsing.py`, `app/ingestion/docling_mapping.py`):
  - pages matched by number;
  - Docling's text passed through verbatim;
  - normalization detected and flagged, never repaired;
  - review flags stored in the IR.
- **Invisible PDF text:**
  - PDFium checks each character's render mode. Modes 3 (invisible) and 7 (clip only) are excluded from `native_char_count`, `mixed_native_and_ocr` and both `docling_normalized_text` checks.
  - Their presence sets the page-level `invisible_text_layer` flag. Text is never removed or changed.
- **OCR provenance:** a page is `ocr` only when Docling has a non-blank `from_ocr` line cell for it. "No visible native text" no longer counts as OCR, so a page with only invisible text is `native`.
- **Verified (offline, measured):**
  - 235 focused tests passed (1 deselected); 565 default-suite tests passed (101 deselected); 48 opt-in Docling tests passed;
  - Ruff check and format, `uv lock --check --offline` and `git diff --check` clean;
  - Hugging Face cache unchanged, so no model downloads.
- **Known limitation:** OCR that runs but recognizes no text can't be distinguished from OCR not running; such a page is `native` (and `empty_page` if it has no text).
- **Outstanding:** the README section (first-run model download, `HF_HUB_OFFLINE`) and the IMPLEMENTATION_PLAN and DECISIONS updates. These have not been written.
- **Not started:** Task 2.5.
- **Correction (2026-11-15):** the two lines above are out of date. The README, IMPLEMENTATION_PLAN and DECISIONS updates were written and committed by the owner in `f42f3f7`, and Task 2.5 is now done (next entry). The original wording is kept as history.

### 2026-11-15: Phase 2, Task 2.5: Parse persistence (done; IR schema version 1 frozen)

**Implementation (accepted by the owner, sub-task by sub-task):**
- **Step 0, D7:** page-level `replacement_character` flag for U+FFFD (ADR-030, decision 6), with a hand-written Type3 fixture and a fallback fixture.
- **2.5a:** `app/records/repositories.py`: `create_document`, `claim_document_for_parsing`, `persist_parsed_pages`, `record_parse_failure` (later also `parse_run_belongs_to_document`). Patient-scoped, one transaction each, plain dataclasses. New `DocumentProcessingError` in `app/core/errors.py`.
- **2.5b:** `app/ingestion/parse_outputs.py`: `data/parsed/<patient_id>/<document_id>/v1/` with `ir.v1.json`, `docling.json` and `manifest.json`, published write-once by one directory rename; artifacts are verified against the manifest (SHA-256 and size) before any reuse.
- **2.5c:** `app/services/parsed_pages.py`: verified IR → `pages` rows, text unchanged.
- **2.5d:** `app/services/process_document.py`: claim → verify the original → reuse or parse and publish → pages; failures recorded without masking the original error. The IR now rejects lone surrogates.
- **2.5e (test additions):** `tests/integration/conftest.py` redirect and patient fixtures; the repository also rejects lone surrogates (`text_not_storable`).
- **Decisions:** D1–D8 and A1–A8, recorded in ADR-032 (D7 in ADR-030). IR version 1 contract and freeze in ADR-029.

**Document lifecycle (ADR-032):** `uploaded` → claim → `processing` with a `running` parse run → on failure the run and the document become `failed` (claimable again); on success the parse run becomes `succeeded` and the **document stays `processing`**, because `processed` / `needs_review` mean the whole pipeline is done and are set by later stages. Stale claims: fixed 1,200-second threshold, explicit recovery only; not linked to `parse_timeout_seconds`.

**Milestone verification (Task 2.5e, measured 2026-11-15):**

| Run | Result |
|---|---|
| Default suite (`uv run pytest`) | **685 passed**, 0 failed, 0 skipped, 153 deselected |
| Opt-in Docling, offline | **58 passed**, 0 skipped; model caches unchanged |
| Existing integration (`medimind_test`) | **52 passed** (5 database, 8 migrations incl. model/migration parity and `alembic check`, 39 schema constraints) |
| New integration (`medimind_test`) | **40 passed** (33 repositories, 7 orchestration) |
| End-to-end, real PostgreSQL + Docling | **3 passed** (native, scanned, retry reusing outputs without Docling) |
| Ruff, format, `git diff --check` | Clean |

- **Test count:** 593 (after D7) + 8 (Task 2.5d IR tests) + 84 (Task 2.5e unit tests) = 685. The 2.5d report said "10" new IR tests; the correct number is 8.
- **Database isolation evidence:**
  - **Primary, connection level:** every repository and service connection in the new integration and end-to-end tests went through a redirected session factory; a listener checked `current_database()` on each new connection before any statement, and each test asserted that the only database seen was `medimind_test`.
  - **Secondary guard:** the application database's schema and row-count snapshot (`patients`, `documents`, `pages`, `pipeline_runs`, where present) was identical before and after each integration run.
  - **Separate observation:** the application database `medimind` has no tables in its `public` schema, so these rows could not have been written there in any case.
  - `medimind_test` held 0 leftover test rows afterwards.
- Docker was restored to its starting state (engine not running); `data/`, the model caches and every earlier working-tree change were unchanged.

### 2026-11-15: Phase 2, Task 2.6: Document upload service ✅

**Implementation (accepted by the owner, sub-task by sub-task; decisions Q1–Q7 in ADR-033):**
- **2.6a:** `PatientError` in `app/core/errors.py`. In `app/records/repositories.py`:
  - `create_patient` returns a `PatientRef` (`id`, `is_synthetic`; never the name);
  - `patient_exists` is a read-only `SELECT EXISTS`;
  - in `create_document`, a violation of exactly `fk_documents_patient_id_patients` (SQLSTATE 23503 and that name) becomes `patient_not_found`; every other integrity error propagates unchanged.
- **Patient validation:**
  - display name: stripped, 1–200 characters; Cc characters, lone surrogates, the bidi embedding/override/isolate controls, U+200B and U+FEFF are rejected; ZWJ, ZWNJ, LRM and RLM are allowed; no normalization;
  - date of birth: optional calendar date, not in the future;
  - `is_synthetic`: required `bool`;
  - all checked before any database session; errors never contain the values.
- **2.6b:** `app/services/upload_document.py`. The steps are type checks → `validate_pdf_bytes` → `patient_exists` (before any upload directory exists) → `store_original` (write-once, never overwrites) → `create_document` with the sanitized filename. No transaction is open during file work. Upload **does not start parsing**: `process_document` is a separate call. Three names were added to the architecture allow-list: `PATIENT_NOT_FOUND`, `create_document`, `patient_exists`.
- **Duplicates and flags:**
  - duplicate detection is patient-scoped (`UNIQUE(patient_id, sha256)`), with no separate pre-check;
  - `duplicate` means an existing record was returned; `original_stored` means this call created the file;
  - both are true when a missing original is published again, or in a concurrent-upload race. That case is logged as a warning with identifiers only, and the warning does not claim which case occurred.
- **Retries:** a database failure after storing leaves a harmless orphan original, and a retry creates the record. Different bytes at the content address raise `integrity_mismatch`, and the file is left untouched.

**Verification (Task 2.6c, measured 2026-11-15):**

| Run | Result |
|---|---|
| Ruff check, `ruff format --check`, `git diff --check` | Clean |
| Default suite (`uv run pytest`) | **786 passed**, 0 failed, 0 skipped, 176 deselected, no warnings (685 + 66 patient unit tests + 35 upload unit tests) |
| Integration, `-m "integration and not docling"` (`medimind_test`) | **115 passed**, 0 failed, 0 skipped: 52 database, migrations and schema; 47 repositories (33 + 14 patient); 7 `process_document`; 9 upload |
| Concurrency and retry tests, repeated 15 times | **105 of 105 passed** (7 tests × 15) |

- **Covered by the integration tests:**
  - concurrent identical uploads (4 threads) produce one record, one file and no `.part` files;
  - retry after a database failure;
  - a missing original published again;
  - the same bytes for two patients give separate rows and files, and each patient's duplicate check sees only its own;
  - an unknown patient or an invalid PDF writes nothing;
  - an uploaded document is then processed separately by `process_document` with a fake parser.
- The Docling end-to-end tests were not re-run: Task 2.6 changes no parsing code.
- **Database isolation evidence:**
  - every repository connection went through the `medimind_test` connection guard;
  - `main_database_unchanged` ran on every integration test;
  - `medimind_test` had 0 rows in `patients`, `documents`, `pages` and `pipeline_runs` before and after;
  - `medimind` had 0 tables in `public` before and after.
- `data/` was unchanged (same fingerprint), there were no `.part` or `.tmp-` files, and Docker was restored to its starting state (engine not running, container `Exited`).

### 2026-11-16: Task 1.8: Patient and document repositories ✅

**Implementation (plan approved by the owner with decisions D1–D4 and O1–O5; recorded in ADR-034):**
- **1.8a:** `list_patients` (`PatientSummary`, oldest first; the second unscoped exception after `create_patient`) and `update_patient` (only the given fields; `None` clears the date of birth; `create_patient`'s validation reused; returns `PatientRef`).
- **1.8b:** `delete_patient`, empty patients only: exactly the three `ON DELETE RESTRICT` foreign keys to `patients` map to `patient_not_empty`.
- **1.8c:** `list_documents` (newest first; `()` for an empty patient, `patient_not_found` for an unknown one) and `get_document`; `DocumentSummary` has no storage path or hash, and the filename is `repr=False`.
- **1.8d:** `rename_document`, the only document update. The repository accepts only canonical display filenames (`invalid_filename` otherwise, never rewritten) and does not import ingestion; a unit test checks its rules against `safe_display_filename`.
- **1.8e:** `delete_document` hard-deletes the row (cascading pages, pipeline runs, chunks, facts, reviews, conflict memberships) and returns page and run counts. Refused with `already_running` while a non-stale parse run exists; `document_referenced` when a newer version supersedes it. **No file is deleted or changed.**
- **1.8f:** `tests/test_architecture.py` lists the complete public repository API and checks that every function except `create_patient` and `list_patients` takes `patient_id` first; `tests/integration/test_isolation_integration.py` calls every document operation with another patient's id and with a random id and requires identical `document_not_found` errors and unchanged rows.
- **1.8g:** ADR-034, with clarification notes on ADR-016, ADR-032 and ADR-033; DATABASE_DESIGN §5, USER_FLOWS F10, ARCHITECTURE and IMPLEMENTATION_PLAN updated.
- No migration, schema change, dependency, service or `app/` module was added. The architecture allow-list is unchanged: nothing outside `app/records/` calls the new functions yet.

**Verification (measured 2026-11-16):**

| Run | Result |
|---|---|
| Unit: `tests/test_patient_repository.py`, `tests/test_document_repository.py`, `tests/test_architecture.py` | **192 passed**, 0 failed |
| Integration: `tests/integration/test_repositories_integration.py` (76) and `test_isolation_integration.py` (2) | **Not run: 78 skipped**, because the Docker engine was not running ("database not reachable") |
| Ruff check and `ruff format --check` on the changed files | Clean |

- The new integration tests cover patient and document CRUD against `medimind_test`, cross-patient behaviour, the RESTRICT and supersedes foreign keys, conflicts left without members, two concurrency races, file immutability after deletion, and the stale-worker race. **They have not run yet**; the result above must not be read as passing.

### 2026-11-16: Milestone R1: Evidence retrieval slice (implemented; evaluation pending)

**Scope** (owner-approved R1 plan; ADR-035): the document-retrieval subset of Phase 5, pulled forward, with Tasks 1.9 and 1.10. Evidence only: no LLM.

- **R1.1 / Task 1.9:** `evals/synthetic/` (catalogue of 26 Indian lab analytes with aliases and Indian units, ground truth first, 3 reportlab layouts with deterministic bytes and a synthetic disclaimer on every page). Dataset `data/synthetic/lab_v0`: 12 fictional patients, 50 reports, 81 pages; split by patient 8 dev / 4 held-out; held-out digest frozen in the manifest before any tuning.
- **R1.2 / Task 1.10:** `evals/dataset.py` (hash-verified loading), `questions.py` (dev 204, held-out 92 questions: value, lab, latest, unanswerable, leak, advice, injection), `metrics.py`, `report.py`.
- **R1.3:** `app/retrieval/chunking.py`. Chunker on real Docling output, offline (3 dev reports, one per layout, 6 pages): Docling found tables in all three layouts (grid 4, plain 4, compact 2); 18, 18 and 21 chunks (2 text each, 16/16/19 table rows); longest chunk 682-758 characters; **16 of 16** ground-truth values of each report appear verbatim in its table-row chunks. Parse time 7.0-7.7 s per 2-page report warm (77.8 s for the first, including model loading).
- **R1.4:** `app/records/chunks.py` (patient-scoped insert with an `index` pipeline run, dense and lexical search, page context). No migration.
- **R1.5:** `app/retrieval/models.py`: BGE-small and MiniLM via `transformers`, lazy, pinned revision required, local cache only. ~~Waiting for owner approval of the revisions and the download; neither model is cached.~~ Revisions approved, both models cached and verified; see *Real-model verification* below.
- **R1.6 / R1.7:** `index_document` and `search_evidence` services; `python -m app index` and `python -m app ask`; RRF k = 60, rerank top 20, top 5 pages, page grouping, calibrated abstention, advice/injection guardrail.
- **R1.8:** `evals/run_retrieval.py` (ingest → dev ablation of dense / lexical / hybrid / hybrid_rerank → dev calibration → one held-out run). **Not run.** *Status 2026-11-17: **Deferred — Docker unavailable**; see below.*
- **R1.9:** ADR-035, ADR-002 update, ROADMAP, IMPLEMENTATION_PLAN, ARCHITECTURE §9.3, README, this entry, SUMMARY.

**Verification (measured 2026-11-16):**

| Run | Result |
|---|---|
| New unit tests (chunking 14, logic 35, services 16, evals 13, CLI 4) + architecture (12, incl. 2 new) + config + main | **131 passed** in the touched files |
| New chunk integration tests (7) | **Not run: skipped**, Docker engine not running |
| Real-model tests (`-m models`, 3) | **Not run: skipped**, no approved revision configured, models not cached (superseded below) |
| Retrieval evaluation (R1.8) | **Not run**: no retrieval, abstention, leak or latency metric exists yet |
| Ruff check and format on the changed files | Clean |

**Real-model verification (R1.5, measured 2026-11-16, after owner approval):**

- Approved pinned revisions (must not be substituted or updated):
  - `BAAI/bge-small-en-v1.5` @ `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (MIT)
  - `cross-encoder/ms-marco-MiniLM-L6-v2` @ `233902d25c440f23af6f7d6e94d2946bac0bee0a` (Apache-2.0)
- Both commits and licences were checked on the Hugging Face Hub. Both revisions are set in `.env` (git-ignored) and read back exactly by `Settings`.
- Each model was downloaded **once** into the local Hugging Face cache through `models.py` (`allow_download=True`). Only config, safetensors weights and tokenizer files were fetched.
- Runtime loading is unchanged: local cache only (`local_files_only=True`), no runtime downloads. All checks after the download ran with `HF_HUB_OFFLINE=1`.
- CPU inference verified: embeddings 384-dimensional with unit norm; reranker logits shape `(n, 1)`; about 670 MB of process memory with both models loaded. These are model smoke checks, not retrieval metrics.
- **Test configuration fix:** `tests/test_retrieval_models_real.py` now builds `Settings(_env_file=REPO_ENV_FILE)` with an absolute path to the repository `.env`. Before the fix the tests always skipped: the autouse `isolated_environment` fixture removes `MEDIMIND_*` variables and changes into a temporary directory.
- Model architecture, pooling, normalization, query instruction, scoring, chunking and retrieval thresholds were **not** changed.

| Run | Result |
|---|---|
| Real-model tests (`-m models`, 3), downloads disabled | **3 passed** |
| Default suite | **990 passed, 217 deselected** |
| Ruff check, `ruff format --check`, `git diff --check` | Clean |

**Still pending (not run, nothing measured):**
- Database integration tests (Task 1.8 and R1 chunks): Docker was unavailable.
- R1.8 dev ablation, threshold calibration and held-out evaluation.
- No retrieval performance metric (recall, MRR, abstention, leak, latency) has been measured.

**Chunk integration tests extended (2026-11-16, written, not run):** `tests/integration/test_chunks_integration.py` gained 5 tests: another patient's document id behaves like a missing one for chunk operations; dense search skips chunks without embeddings; lexical search finds `eGFR`; failed or losing indexing attempts record no index run; a database failure part-way through an insert rolls back the chunks and the run. They need PostgreSQL and have **not run**.

**R1.8 readiness corrections (2026-11-16, owner-approved, implemented; ADR-035 update):**
- Leak probes come from the same split only (dev questions no longer depend on held-out ground truth).
- Leakage reported on leak probes only (`leak_rate`, `leak_probe_abstain_rate`), plus `questions_with_foreign_pages` over all questions; abstention over answerable and ordinary unanswerable questions; advice and injection refusal rates separate.
- Calibration (`hybrid_rerank` only) uses answerable vs ordinary unanswerable questions; leak, advice and injection questions excluded and the kinds recorded in the report.
- Indexing time per page in the dev report, from recorded wall-clock timings.
- Ingestion resumable after a crash at any stage, with database confirmation before skipping a stage.
- `freeze` writes `dev_selection.json` (exact threshold); `heldout` runs only that selection after validating hashes, settings and model revisions; `ablate` and `freeze` refuse after `heldout.done`.
- `.gitignore`: `/evals/work/` only.

| Run | Result |
|---|---|
| `tests/test_evals.py` (41, of which 28 new) | **41 passed** |
| Default suite | **1018 passed, 222 deselected** |
| Ruff check, `ruff format --check` (`evals/`, `tests/test_evals.py`), `git diff --check` | Clean |
| Dev question recount (dev ground truth only) | **204**: value 105, lab 35, latest 16, unanswerable 16, leak 16, advice 8, injection 8 |
| Held-out question count | **Not re-verified** (92 was produced by the previous generator) |

The resume, freeze and held-out tests use an in-memory stand-in for the database services and a temporary fictional dataset, never `lab_v0`. Nothing was run against PostgreSQL, and no held-out data of `lab_v0` was used.

**R1.8 status: Deferred — Docker unavailable (2026-11-17).** R1.8 is **not complete**.
- On 2026-11-17, `docker info` failed (`failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine ... The system cannot find the file specified.`). The run stopped there, per the run protocol. The owner then chose to defer R1.8 and move on to Phase 2 planning.
- **Implemented:** the evaluation runner (`evals/run_retrieval.py`) and its offline tests (see the tables above).
- **Not executed:** dev ingestion (`ingest --split dev`) and the dev ablation (`ablate`). No `evals/work/` state and no `evals/reports/` output exist.
- **Unmeasured:** Recall@k, MRR, nDCG, leakage, abstention, advice and injection refusal rates, and latency (including indexing time per page).
- **No calibrated abstention threshold and no frozen configuration** (`dev_selection.json`) exist.
- **Held-out evaluation remains blocked:** `heldout` needs the frozen dev selection.
- **The PostgreSQL chunk and isolation integration tests remain unrun.**
- **No measured evidence currently supports choosing dense-only, hybrid or hybrid plus reranking.** The architecture decision stands (ARCHITECTURE §9.4, *Ablation*): hybrid retrieval and reranking stay only if they show a measured improvement. Until R1.8 runs, the retrieval configuration in code is a default, not a measured choice.

### 2026-11-17: Phase 2 plan for Tasks 2.7–2.9 (approved; ADR-036)

Planning and documentation only: no application code, tests, dependencies or migrations changed.

| Task | Scope | Status |
|---|---|---|
| 2.7 | Rule-based classification (`lab_report` / `other` / `unknown`), rule score, versioned rules, new `doctype_v0` dataset, offline accuracy evaluation | Approved, not started |
| 2.8 | Identity verification (`match` / `mismatch` / `unknown`, strict name matching), indexing gate in `index_document`, R1.8 runner gains classify and identity stages | Approved, not started |
| 2.9 | 2.9a pipeline orchestration and service wrappers; 2.9b thin Streamlit pages (includes Task 1.11) | Approved, not started |

- **ADR-036 Accepted:** D1–D10 (document types unchanged and ARCHITECTURE §5 corrected; `doctype_v0`; rule score; name and printed date of birth only; strict matching; no override or recheck; documents stay `processing` until Phase 3; `streamlit` added only at 2.9b; accuracy target set after the first dev measurement), requirements R1–R3, and A1–A4 with the owner's clarifications.
- **Indexing gate (A1):** a succeeded `classify` run, a succeeded `identity` run, and `identity_check <> 'mismatch'`, enforced inside `index_document` for every indexing path; `unknown` identity is indexed with the uncertainty kept visible.
- **Stages (A2, A4):** successful stages are not repeated; failed stages can be retried; each stage reports `succeeded`, `already_done`, `failed` or `blocked`, and nothing is skipped silently.
- **No migration** is needed: every column already exists. The "not yet checked" vs "checked, unknown" distinction comes from `pipeline_runs`.
- Documentation updated: IMPLEMENTATION_PLAN (Tasks 2.7–2.9, R1.5 row corrected, R1.8 row), DECISIONS (ADR-036, ADR-035 update), ARCHITECTURE (§3, §4.1 diagram, §5), USER_FLOWS (F2 identity flows), this file and SUMMARY.

### 2026-11-17: Task 2.7 — rule-based document classification (implemented; ADR-036)

- **Code:** `app/ingestion/classification.py` (pure rules, `CLASSIFIER_RULES_VERSION = "2"`); `list_document_pages`, `record_classification`, `record_classification_failure` in `app/records/repositories.py` (patient-scoped, row lock, `already_classified`, `not_parsed`, no status change); `app/services/classify_document.py`; `python -m app classify --patient … --document …`. No migration, no new dependency.
- **Dataset:** `data/synthetic/doctype_v0` (seed 20261117): 12 fictional patients, 84 documents (7 kinds), split by patient 8 dev (56 documents, 16 lab) / 4 held-out (28, 8 lab). `heldout_sha256 = 8d71ffb5f01fd7bcbd1cd10a1047482ade0d2607ef1a7891e0bd602522d7a01d`, frozen at build time, before any rule was written. `lab_v0` unchanged.
- **Evaluation:** `evals/run_classification.py` (`dev`, `freeze`, `heldout`); offline Docling parse, no database.

| Run (dev split, 56 documents, measured) | Accuracy | Decided accuracy | `lab_report` precision | `lab_report` recall | Unknown rate |
|---|---|---|---|---|---|
| Rules v1 (untuned) | 0.9107 | 1.0000 | 1.0000 | 0.7500 | 0.0893 |
| Rules v2 (current) | 0.9464 | 1.0000 | 1.0000 | 0.8750 | 0.0536 |

- Rules v2 confusion (true → predicted): `lab_report` 14 / `other` 0 / `unknown` 2; `other`: 0 / 39 / 1. The three `unknown` results: two culture reports without values or ranges, one discharge summary with a printed lab table (`doc.conflict`).
- ~~**Not done:** `freeze` and the held-out run (owner decision after reviewing dev, D9); no accuracy target is set.~~ *Done later the same day, see below.*
- **Owner dev targets (D9, 2026-11-17):** accuracy >= 0.90, `lab_report` precision >= 0.95, recall >= 0.85, unknown rate <= 0.10. Rules v2 meets all four on dev.
- **Freeze (2026-11-17):** before it, the current rules re-scored dev from the cached parses with identical outcomes and metrics, and the dataset and held-out digest verified. `classification_selection.json`: rules version `2`, dataset `doctype_v0@8d71ffb5f01f`, `heldout_sha256 8d71ffb5f01fd7bc…a7d01d`, dev report SHA-256 `133b8d72…66a6e6`.
- **Held-out, evaluated once (rules v2, 28 documents, measured):** accuracy 0.9643, decided accuracy 1.0000, `lab_report` precision 1.0000, recall 1.0000, unknown rate 0.0357. Confusion: `lab_report` 8 / 0 / 0; `other` 0 / 19 / 1. The one `unknown` is a discharge summary with a printed lab table (`doc.conflict`). No rule was changed after the held-out run; `classification_heldout.done` blocks any re-run.
- **Caveat:** the generator and the rules were written by the same developer and held-out uses the same templates, so these numbers are optimistic for real documents (synthetic, native-text only).

| Tests | Result |
|---|---|
| New unit tests: rules 39, repository 43, service and CLI 14, dataset and evaluation 13; architecture 13 (1 new) | **122 passed** (focused run with neighbouring files: 361 passed) |
| Default suite | **1128 passed, 230 deselected** |
| New PostgreSQL integration tests (`tests/integration/test_classification_integration.py`, 8) | **Not run: 8 skipped**, database not reachable (Docker unavailable) |
| Ruff check and `ruff format --check` (`app`, `evals`, `tests`) | Clean |

### 2026-11-17: Task 2.8 — patient identity verification and the indexing gate (implemented; ADR-036)

- **Code:** `app/ingestion/identity.py` (pure, `IDENTITY_RULES_VERSION = "1"`); `get_patient`, `record_identity_check` (mismatch → `needs_review` atomically), `record_identity_failure` in `app/records/repositories.py`; `app/services/check_identity.py`; `python -m app identity`. Indexing gate in `app/records/chunks.py` (pre-check in `get_index_target`, re-check under the row lock in `insert_document_chunks`), `index_document` pre-check, `index_patient` per-document outcomes, "identity not confirmed" evidence label, `python -m app index` prints blocked documents. R1.8 runner: classify and identity stages before indexing; both rule versions frozen and validated. No migration, no new dependency.
- **Dataset:** `data/synthetic/identity_v0` (seed 20261118): 12 fictional patients (families sharing surnames; one namesake pair per split), 9 PDF variants + 1 text-level U+FFFD case each = 120 documents; split fixed by patient (I01–I08 dev, I09–I12 held-out); `heldout_sha256 = 1b4ec4a51b35108e8cc81429c457b1883c76044958dcef493e05c859f2652b90`, frozen at build time before any evaluation. `lab_v0` and `doctype_v0` unchanged.

| Identity dev evaluation (rules v1, 80 documents × 8 profiles = 640 pairs, measured) | Result |
|---|---|
| False matches on swapped profiles (distinguishable) | **0** |
| Own profile: match / unknown / mismatch | 29 / 51 / 0 (0 false mismatches) |
| Swapped profiles: match / unknown / mismatch | 4 / 337 / 219 (the 4 matches are the namesake pairs with no printed DOB) |
| Agreement with the expected outcome (by construction) | 640 / 640 |

- **Caveat:** the expected outcomes and the rules were written by the same developer, so agreement is partly by design; the evaluation's value is the false-match count and the edge-case coverage. The held-out split was not evaluated.

| Tests | Result |
|---|---|
| New unit tests: identity rules 60, identity repository and gate 34, service, CLI and bulk indexing 19, dataset and evaluation 13; updated retrieval, CLI and R1.8 runner tests | **Default suite 1272 passed, 242 deselected** |
| PostgreSQL integration: new `test_identity_integration.py` (12, incl. a lock race), updated `test_chunks_integration.py` (12), `test_classification_integration.py` (8) | **Not run: 32 skipped**, Docker unavailable |
| Ruff check and `ruff format --check` (`app`, `evals`, `tests`) | Clean |

### 2026-11-17: Task 2.8 — identity rules v2 (review fixes; ADR-036 update)

- **Why:** a review of rules v1 found false matches (newborn vs mother, single-token profiles) and false mismatches (an initial in the profile, other people's DOB labels, Unicode format characters), none of them exercised by `identity_v0`.
- **Code:** `app/ingestion/identity.py` only (`IDENTITY_RULES_VERSION = "2"`). Two new reason codes: `name_generic_label` and `profile_name_incomplete`. Rules are listed in ADR-036. Unchanged: the indexing gate, the repositories, the services, the schema and the dependencies.
- **Dataset:** new `data/synthetic/identity_v1`.
  - 28 fictional patients, 48 documents (42 PDFs, 6 text-level format-character cases).
  - Split by patient: E01–E14 dev, H01–H14 held-out; `heldout_sha256 = 12ea2976caac91531fd88787b6a8d3041802b69e2c06026ee8b8a15c8e72415c`, frozen at build time.
  - Expectations are written by hand from each document's true subject.
  - Generator: `evals/synthetic/identity_v1.py`. Runner: `evals/run_identity_v1.py`.
  - `identity_v0`, its digest and its v1 reports are unchanged (hash-verified).

| Identity dev evaluation (rules v2, Docling parse, measured) | `identity_v1` (336 pairs) | `identity_v0` (640 pairs) |
|---|---|---|
| False matches on distinguishable swapped profiles | **0** | **0** |
| Own-profile false mismatches (excluding documented limits) | **0** | **0** |
| Own-profile documented-limit mismatches | 2 (spelling, spacing) | n/a |
| Indistinguishable swapped pairs: match | 3 of 3 (misspelling, token order, namesake) | 4 of 4 (namesakes without DOB) |
| Own profile: match / unknown / mismatch | 13 / 9 / 2 | 29 / 51 / 0 |
| Swapped profiles: match / unknown / mismatch | 3 / 107 / 202 | 4 / 337 / 219 |
| Unknown rate (all pairs) | 0.345 | 0.606 |

- **Dev-found defect:** the first v2 draft read "a date anywhere" in an `Age/DOB` field. On real Docling output it gave 2 own-profile false mismatches, because Docling merges `Age/DOB: 38 Y` with `Report Date: …`. The rule was narrowed and regression tests were added; ADR-036 records the draft.
- **Caveat:** the rules, the `identity_v1` cases and their expectations were written by the same developer after the defects were known. Dev agreement does not show generalization, and a held-out run on `identity_v1` is not a blind test.
- **Held-out:** neither dataset's held-out split has been parsed or evaluated; the IR caches hold dev PDFs only (72 and 21 files).

| Tests | Result |
|---|---|
| Identity unit tests (`tests/test_identity.py`, v1 cases + v2 regressions) and `identity_v1` dataset tests (`tests/test_identity_v1_dataset.py`) | Passed (counts in SUMMARY.md) |
| Default suite | **1347 passed, 242 deselected** |
| PostgreSQL integration | **Not run** (Docker unavailable) |
| Ruff check and `ruff format --check` | Clean |

### 2026-11-17: Task 2.8 — identity rules v2 frozen; held-out evaluated once (ADR-036 update)

- **Owner decisions:**
  - Rules v2 is kept unchanged and frozen.
  - The documented limitations are accepted for the local prototype and listed in ADR-036.
  - No fuzzy matching, phonetic matching, accent folding or override.
- **Protocol:** `freeze` and a one-time `heldout` were added to `evals/run_identity.py` and shared with `evals/run_identity_v1.py`.
  - `freeze` re-scored dev and matched both stored dev reports pair-for-pair. The rules source SHA-256 is `48c58bd9…`.
  - The `identity_heldout.done` marker is written before evaluating. Both guards now refuse a second run.
- **Held-out (measured once, `identity_v1` then `identity_v0`).** **Neither run is a blind or independent validation**; both reports say so.

| Held-out (rules v2, Docling) | `identity_v1` (336 pairs) | `identity_v0` (160 pairs) |
|---|---|---|
| False matches on distinguishable swapped profiles | **0** | **0** |
| Matches on indistinguishable pairs (documented limit) | 3 of 3 | 4 of 4 (namesakes without DOB) |
| Own-profile false mismatches | **0** | **0** |
| Own-profile documented-limit mismatches | 2 (spelling, spacing) | — |
| Own profile: match / unknown / mismatch | 13 / 9 / 2 | 15 / 25 / 0 |
| Swapped profiles: match / unknown / mismatch | 3 / 99 / 210 | 4 / 73 / 43 |
| Agreement with the expected outcome | 24/24 own (hand-written) | 160/160 (by construction) |

- **Unchanged during the evaluations (hash-verified):** identity v2 code; both datasets, manifests and digests; the generators; the earlier reports; classification v2 and its frozen artifacts.
- **Docling caches:** they now hold the held-out IRs. `identity_v1`: 42 = 21 dev + 21 held-out. `identity_v0`: 108 = 72 dev + 36 held-out.

### 2026-11-17: Task 2.9a — pipeline orchestration and service wrappers (implemented; ADR-036 update)

- **Code:**
  - `app/services/document_pipeline.py`: `run_document_pipeline` and `run_patient_pipeline`, which return four `StageResult`s per document (`succeeded`, `already_done`, `blocked`, `failed`) with a fixed reason kind.
  - `app/services/library.py`: patient-scoped wrappers for the UI.
  - `DocumentSummary` gains read-only `doc_type_confidence` and `identity_check`.
  - The architecture allow-list gains the library's repository names.
- **Contract (ADR-036 update):**
  - Each stage runs through its existing service and is attempted once per call. The services and the single indexing gate decide blocked or already done, and the pipeline only maps their results.
  - Errors are reported per stage (kind and class only), and the remaining stages and documents still run.
  - No retries, no new statuses, no migration, no dependency.

| Tests | Result |
|---|---|
| New `tests/test_document_pipeline.py` (pipeline and library) with updated `test_architecture.py` and `test_document_repository.py` | **132 passed** |
| Regressions: identity, classification, retrieval services, CLI, repositories, evals | **448 passed** |
| Regressions: process, upload and parsed-pages services | **71 passed** |
| `tests/integration/test_repositories_integration.py` | **76 skipped** (no database; Docker not retried) |
| Ruff check and format | Clean |

### 2026-11-17: Task 2.9b — local Streamlit UI (implemented; ADR-036 update)

- **Added:**
  - `app/ui/streamlit_app.py`, `app/ui/views.py` and `app/ui/texts.py`;
  - `app/services/ui_runtime.py`;
  - `library.get_document_state`, and `synthetic_only` on `library.list_patients` and `library.get_patient`;
  - `.streamlit/config.toml`;
  - the `demo_mode` setting;
  - `streamlit` 1.67.0 (D8; the lock only gained packages, and no existing version changed).
- **Pages:**
  - Patients: list, create, view, edit, delete.
  - Documents: upload, library table, rename, delete with a files-stay-on-disk warning, processing with per-stage progress and results, identity and type warnings, and the unfinished-parse notice.
  - Evidence search: cited excerpts or an abstention.
- **Demo mode:** synthetic patients only, patients created synthetic, upload disabled, and a banner. No seeding.

| Tests and checks | Result |
|---|---|
| New `tests/test_ui.py` (AppTest with stand-in services; architecture, demo mode, scoping, upload, pipeline rendering, warnings, search, errors) | **40 passed** |
| Regressions: architecture, config, CLI, health, logging, document pipeline | **126 passed** (166 in the same run) |
| AppTest against the real services, no database | No exception; fixed "database could not be reached" text only |
| `streamlit run` on port 8599 | Listened on 127.0.0.1 only, health `ok`, then stopped |
| PostgreSQL integration, end-to-end UI run | **Not run** (Docker unavailable) |
| Ruff check and format | Clean |

## Next steps

1. **Run the Task 1.8 and R1 integration tests** against `medimind_test` once Docker is running (`uv run pytest -m integration tests/integration/test_repositories_integration.py tests/integration/test_isolation_integration.py tests/integration/test_chunks_integration.py`).
2. ~~**Owner:** approve the pinned model revisions (ADR-035), download the two models once, set the two revision settings; then `uv run pytest -m models`.~~ Done 2026-11-16 (R1.5): 3 passed.
3. **R1.8:** run `evals/run_retrieval.py` (ingest dev → ablate → ingest held-out → one held-out run) and record the measured results. *Updated:* dev only first: `ingest --split dev`, then `ablate`; after owner review, `freeze --mode <mode>`; the held-out ingest and `heldout` run come later and read only `dev_selection.json`. *Deferred 2026-11-17 (Docker unavailable); resume when Docker Desktop is running.*
4. **Owner:** review and commit the Task 1.8 and R1 work.
5. ~~**Phase 2:** break the remaining subtasks (rule-based classifier, identity check, document library UI) into tasks.~~ Done 2026-11-17: Tasks 2.7–2.9 planned and approved (ADR-036).
6. ~~**Task 2.7:** implement rule-based classification (first Phase 2 implementation task), then 2.8 → 2.9a → 2.9b.~~ Implemented 2026-11-17 (rules v2, dev measured).
7. ~~**Owner:** review the Task 2.7 dev report (`evals/reports/doctype_v0/classification_dev_rules_v2.md`), set the accuracy target (D9), then `uv run python -m evals.run_classification freeze` and, once, `heldout`.~~ Done 2026-11-17: targets set, frozen, held-out evaluated once.
8. ~~Run `tests/integration/test_classification_integration.py` when Docker is running; then Task 2.8.~~ Task 2.8 implemented 2026-11-17; integration tests still pending.
9. **When Docker is running:** `uv run pytest -m integration tests/integration/test_classification_integration.py tests/integration/test_identity_integration.py tests/integration/test_chunks_integration.py`, then the earlier Task 1.8 and R1 integration tests and R1.8.
10. ~~**Owner:** review the identity dev report (`evals/reports/identity_v0/identity_dev_rules_v1.md`); decide whether a held-out identity run is wanted; then Task 2.9a.~~ Superseded 2026-11-17 by rules v2 (step 11).
11. ~~**Owner:** review identity rules v2 and its dev reports:~~
    - ~~`evals/reports/identity_v1/identity_dev_rules_v2.md`~~
    - ~~`evals/reports/identity_v0/identity_dev_rules_v2.md`~~

    ~~Then decide whether to freeze v2, which held-out split(s) to evaluate once (neither is blind; see the caveat), and whether the documented limits are acceptable, given that D6 allows no override. Then Task 2.9a.~~ Done 2026-11-17: v2 frozen, limits accepted, both held-out splits evaluated once.
12. **When Docker is running:** run the identity and chunk PostgreSQL integration tests (gate re-check under the row lock, atomic `needs_review`). ~~Then Task 2.9a.~~ Task 2.9a implemented 2026-11-17. Also run the repository integration tests: `DocumentSummary` gained two fields.
13. ~~**Owner:** review Task 2.9a. Then Task 2.9b (Streamlit; adds `streamlit` and `demo_mode`). The 2.9b delete confirmation should say that a deleted document's files stay on disk (Task 1.8e). 2.9b also needs a decision on whether the UI offers stale-parse recovery.~~ Done 2026-11-17: 2.9b implemented. The delete confirmation explains that files stay on disk, and the UI offers no stale-parse recovery (shown and explained only).
14. **Owner:** review Task 2.9b.
15. **When Docker is running:** run the 2.9b acceptance end to end: `uv run streamlit run app/ui/streamlit_app.py`, then take a synthetic PDF from upload through every stage, with type and identity shown. Run it together with step 12.

## Open questions

| # | Question | Status |
|---|---|---|
| Q3 | India / English / DD/MM/YYYY region profile | **Resolved**: accepted 2026-11-13 (ADR-025) |
| Q5 | LLM provider | Deferred to the start of Phase 3 (ADR-024) |
| Q10 | LangGraph record-analysis use case | Deferred (ADR-012) |
| Q12 | Auto-accept an ambiguous date when the same document confirms DD/MM order? | Open. Default: no, always flag. Decide with Phase 3 flag-rate data. |
| — | Proposed ADRs still to be decided: 007, 008, 011, 017, 021, 027 | Awaiting owner review |
| — | Hosting target for the public demo | Decided in Phase 9, or at the ADR-027 checkpoint |
| D6 | Re-processing documents after a parser upgrade (new outputs never overwrite `v1/`) | Deferred (ADR-029 versioning policy, ADR-032) |
