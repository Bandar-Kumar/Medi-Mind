# Progress

## Current phase

**Phase 5: Advanced hybrid RAG (evidence-grounded copilot) — implemented 2026-11-18 under [PHASE_5_PLAN.md](PHASE_5_PLAN.md) (ADR-040), on the owner's one-pass instruction. Awaiting the owner's review; Phase 3 and 4 gates are also still open.**

| Item | Status |
|---|---|
| R1.8 retrieval evaluation | **Complete.** Dev ablation, `lexical` frozen (no threshold), held-out run once: MRR 0.891, Recall@5 0.971, leak 0, abstention recall 0.375 |
| Router, SQL tools, extractive generation, citation validator, Copilot page | Implemented; unit, UI and PostgreSQL integration tests pass |
| LLM provider evaluation (ADR-024) | **Blocked:** no credentials, no local model server. Interface, mock and an untested OpenAI-compatible adapter exist; no provider selected |
| Copilot evaluation (`copilot_v0`) | Held-out run once: routing 0.990, correctness 0.913, citation recall 0.944, **citation precision 0.605 (target 0.95 missed)**, 0 unsupported claims, 0 leaks/exposures, 0/304 attacks accepted |
| Tests (2026-11-18) | Default suite 1894 passed, 282 deselected (0 failed, 0 skipped); integration 221 passed; real-model 3 passed; Ruff check and format clean |
| Migration | None (one read-only repository function, `stored_chunks`) |
| **Remediation (ADR-041)** | Patient vocabulary, abstention on unrecognised test names, passage relevance gate; lexical threshold rejected on dev. New `copilot_v1` (new held-out patients) run once: **citation precision 1.000** (was 0.605), all targets met; same-author caveat (PHASE_5_PLAN §12.4) |
| Tests after remediation | Default 1919 passed, 282 deselected (0 failed, 0 skipped); integration 221 passed; real-model 3 passed; Ruff clean |

*The Phase 4 status below is kept.*

**Phase 4: Database and longitudinal intelligence — implemented 2026-11-18 under [PHASE_4_PLAN.md](PHASE_4_PLAN.md) (ADR-039), on the owner's instruction. Awaiting the owner's review; Phase 3's gate is also still open (no approval recorded).**

| Item | Status |
|---|---|
| Timeline, trends, date comparison, derived duplicates, conflict presentation; Overview, Timeline and Lab trends pages | Implemented; unit, UI and PostgreSQL integration tests pass |
| Longitudinal evaluation (`longitudinal_v0`) | Every pre-registered target met on dev and on held-out (run once); see the entry below |
| Migration | None needed (no schema change) |

*The Phase 3 status below is kept.*

**Phase 3: Structured clinical extraction — implemented 2026-11-17 under the approved [PHASE_3_PLAN.md](PHASE_3_PLAN.md) (revision 3; ADR-037, ADR-038). Awaiting the owner's final review.**

| Item | Status |
|---|---|
| Batches A–F (extraction core, dataset and evaluation, migration 0004 and repository, services and pipeline, UI, documentation) | Implemented; unit tests pass |
| Dev and held-out extraction evaluation (`extract_v0`) | Measured; held-out run once; see the entry below |
| PostgreSQL integration tests, migration 0004 on a database, live UI run | ~~Not run (Docker unavailable)~~ **Done 2026-11-18:** migration 0004 applied and schema matches the ORM; seeding idempotent; **205 integration tests passed** (0 failed, 0 skipped) after 6 defect fixes; end-to-end UI acceptance passed (see the 2026-11-18 entry) |
| Batch G (optional LLM comparison) | Not started; runs only if the owner selects it |

*The Phase 2 status below is unchanged.*

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
| **R1.8 retrieval evaluation** | Deferred. **No retrieval metric has been measured.**  *Completed 2026-11-18 (Phase 5 entry).* |

The Task D7–2.6 work was committed by the owner as `6523677`. ~~Task 1.8 and everything after it are **not committed yet** (the owner commits manually).~~ *Corrected 2026-11-17:* Task 1.8, Milestone R1 and Tasks 2.7–2.9b were committed by the owner as `f989b98` ("complete ph 2 document intelligence"). The Phase 3 work is **not committed yet** (the owner commits manually). The remaining Phase 2 subtasks (rule-based classifier, identity check, document library UI) are not yet broken into tasks. *Update 2026-11-17:* they are now planned as **Tasks 2.7–2.9**, approved by the owner (ADR-036, Accepted); ~~not started~~ implemented later the same day (above). Phase 0 is complete, but some Proposed ADRs are still open.

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

### 2026-11-17: Phase 3 — structured lab extraction (Batches 0–F; ADR-037, ADR-038)

- **Batch 0:** `docker info` failed once (Docker Desktop engine not found) and was not retried; database acceptance is pending.
- **Code:** `app/extraction/` (pure: exact spans, grounding, normalizers, reference CSVs, `lab-rules-2`, comparison); migration `0004_extraction` and mirrored models; `app/records/facts.py`; services `extract_document`, `review_fact`, `facts`, `fact_conflicts`, `reference_data`; a fifth pipeline stage and the completion rule; CLI `extract`, `facts`, `seed-reference`; UI results, review, run history and conflicts.
- **Dataset:** `data/synthetic/extract_v0` (16 fictional patients, 112 documents, 11 dev / 5 held-out by patient; held-out digest `2f0e7a67…` frozen at build, before any rule was tuned).
- **Measured** (synthetic; not blind; no clinical accuracy claimed):

| Run | Group | Gold | Predicted | Entity P | Entity R | Fully correct R | Silent error rate |
|---|---|---|---|---|---|---|---|
| Dev, `lab-rules-1` | native | 275 | — | — | 0.9273 | 0.9273 | 0.0 |
| Dev, `lab-rules-2` | native | 275 | 275 | 1.0 | 1.0 | 1.0 | 0.0 |
| Dev, `lab-rules-2` | raster (OCR) | 132 | 119 | 1.0 | 0.9015 | 0.7045 | — (all flagged) |
| Held-out (once) | native | 128 | 128 | 1.0 | 1.0 | 1.0 | 0.0 |
| Held-out (once) | raster (OCR) | 63 | 60 | 1.0 | 0.9524 | 0.7143 | — (all flagged) |

- Hard gates 0 on dev and held-out (unsupported facts, traps, wrong subject, facts for mismatch documents: 55 dev and 25 held-out mismatch pairs all blocked). Ambiguous-date flag rate on dev: 129 of 394 facts (Q12 data).
- **Tests:** default suite **1659 passed** (266 deselected); new integration file `tests/integration/test_facts_integration.py` (24 tests) **skipped**, together with every other integration test (205 skipped): no database.
- **Deviations from the plan:** `lab-rules-2` (joined-line segments), the `:s<k>` locator segment, the `member_added` closed reason (recorded in ADR-037/038 and the plan's implementation note).

### 2026-11-18: Q12 closed; PostgreSQL acceptance blocked

- **Q12 closed:** `ambiguous_date` review retained for numeric dates where both day and month are plausible. Measured dev rate 129 of 394 facts (32.7%) from the frozen `extraction_dev_lab-rules-2` report; rationale in DECISIONS.md. No extraction rule, dataset or report changed.
- **Database:** `docker info` checked once and failed (Docker Desktop engine not running); nothing was listening on port 5432. Not retried. Migration 0004, reference seeding, all integration tests and the Streamlit end-to-end run were **not run**: no pass or fail result exists for them.

### 2026-11-18 (later): Phase 3 PostgreSQL acceptance — passed

Docker Desktop running; `medimind-db-1` reported `healthy` and `pg_isready` accepted connections.

**Migration and schema**
- `uv run python -m app migrate` on the empty `medimind` database first **failed** at 0003 → 0004 (`UndefinedObject`; the transaction rolled back). After the fix (defect 1 below) it applied 0001 → 0004.
- Schema against the ORM (live `medimind`): `alembic` `compare_metadata` with type and server-default comparison → 0 differences; 173/173 columns, 0 nullability differences; every ORM constraint and index name present (the only extra names are PostgreSQL 18's automatic `NOT NULL` constraints); JSONB (`bbox`, `component_versions`, `corrected_values`), `vector` and `tsvector` columns present; the composite foreign keys of facts, runs, review decisions and conflict members with their `CASCADE` / `RESTRICT` actions as designed. The integration tests `test_alembic_check_finds_no_differences` and `test_models_and_migrations_build_identical_catalogs` (full constraint and index definitions) passed.
- 0004 was taken down one step and up again on `medimind` and `medimind_test` (both had 0 patients, documents, facts, reviews and conflicts) so both carry the corrected check constraint; this also exercised the 0004 downgrade.

**Reference seeding:** first run added 31 analytes, 89 aliases, 9 conversions (`ref-535ecf5cbb7dc1ee`), matching the CSV row counts; the second run added 0 of each; full-row hashes of the three tables (ids included) were identical before and after the second run.

**Integration tests** (`uv run pytest -m integration`): first run 41 failed / 164 passed; final run **205 passed, 0 failed, 0 skipped, 1725 deselected** (7 third-party deprecation warnings from Docling, pydantic and torch). Includes the 24 extraction tests and the 181 earlier ones (Tasks 1.7, 1.8, 2.5–2.9a, R1).

**Defects found and fixed** (each with a regression test where a unit test can see it):
1. *Migration 0004:* dropping check constraints by plain name re-applied the `ck_%(table_name)s_` naming convention (`ck_conflicts_ck_conflicts_status_values`), so the upgrade and the downgrade failed. Names wrapped in `op.f()`. Test: every dropped constraint must have been created under that exact name (`tests/test_migrations.py`).
2. *Locator check (0004 and model):* SQLAlchemy `text()` read `:r` and `:s` in the regex as bind parameters, so PostgreSQL stored `NULL` in their place and rejected every valid locator. Escaped as `\:`. Test: the rendered SQL and the compiled model constraint contain the exact regex.
3. *Nullable JSONB (`review_decisions.corrected_values`, `extracted_facts.bbox`):* Python `None` was written as JSON `'null'`, which is `NOT NULL` to PostgreSQL, so every verify or reject decision violated `corrected_values_match`. Set `none_as_null=True` (no DDL change). Test in `tests/test_models.py`.
4. *Repository foreign-key mapping (Task 1.8):* PostgreSQL reports a delete blocked by `ON DELETE RESTRICT` as SQLSTATE `23001`, not `23503`, so `patient_not_empty` and `document_referenced` were never recognized. `_fk_violation` accepts both, still matched by constraint name. Unit cases added.
5. *Storage containment (Task 2.6):* with the default relative `MEDIMIND_DATA_DIR=data`, `mkstemp`'s absolute temp path was compared with the relative data dir, so every upload failed with `outside_data_dir`. Both paths are made absolute with `Path.absolute()` (no `..` normalization, no link following). Tests: a relative data dir stores, and still refuses a junction redirect.
6. *Stale tests (written before 0004 or never run):* migration tests still expected head `0003`; schema-constraint and repository tests inserted facts without the 0004 provenance columns, dated observations without `effective_date_source`, and expected a conflict to stay `open` after its document was deleted (ADR-038 closes it as `member_deleted`); the identity lock test called `begin()` after an autobegin. Updated to the approved behaviour; no assertion was weakened.

**End-to-end acceptance** (application database `medimind`; fictional dev-split documents of `extract_v0`, P02 profile). Driven through the real Streamlit script with Streamlit's `AppTest` (patient form, file uploader, "Process this document", review form, "Acknowledge"), not through a browser; the server itself started headless and answered `/_stcore/health` (`ok`). Persisted state was checked with read-only SQL:
- **Pipeline:** P02-CL, P02-RS, P02-HD, P02-LN, P02-FM, P02-RF: parse, classify, identity, index, extract `succeeded`, status `processed`. P02-RX (`other`): extract run `skipped` (`unsupported_doc_type`), status `processed`. Repeat processing and a duplicate upload: every stage `already_done`, no new runs.
- **Facts and raw/normalized separation:** native documents match gold on every field (CL 14/14, HD 9/9, LN 4/4: printed analyte, value, unit and range; normalized value and unit; date). OCR document RS 14/14 facts, all `needs_review` (`ocr_unverified_source`); 3 printed ranges keep the OCR spacing (`3.5- 7.2`), as extracted.
- **Provenance:** for all 41 stored facts, `source_excerpt` equals `pages.text[char_start:char_end]` of the same patient, document and page; locator page matches; `extraction_run_id` is that document's succeeded extract run; every dated observation's `date_raw` equals its own span.
- **Ambiguous dates:** CL and RS 14/14 facts flagged `ambiguous_date` (gold 14/14).
- **Identity mismatch:** P01-CL uploaded to the P02 profile → identity `mismatch`, index and extract `blocked` (`identity_mismatch`), status `needs_review`, 0 chunks, 0 facts, no index or extract run; the UI shows the mismatch error.
- **Identity unknown:** P02-FM and P02-RF → `unknown`, shown as "Identity not confirmed", indexed and extracted per the approved gate. Both have 0 gold facts, so the display of an `unknown` document *with* facts was not seen in the UI.
- **Review and conflicts** (one CL/RS pair, through the review form): verify CL → `verified`; verify RS (equal values) → no conflict; correct RS to a different value → one `open` conflict with 2 members; Acknowledge → `acknowledged`; reject RS → closed `member_rejected`; verify RS again → no new conflict, the closed one stays closed; correct RS to a disagreeing value → a new conflict (new id); correct RS back to the agreeing value → closed `member_corrected`. RS decisions appended in order (verify, correct, reject, verify, correct, correct); the latest decides; the original extracted value is unchanged; `corrected_values` is SQL `NULL` for non-corrections. An empty correction is refused and writes nothing.
- **Patient isolation:** a second fictional patient gets `document_not_found` / `fact_not_found` for the first patient's document, facts, runs, review and delete, sees 0 conflicts, and nothing is written; the temporary patient was then deleted (empty).
- **Covered by integration tests, not by the UI run:** atomic extraction and rollback of a failed insert, concurrent extraction, the identity gate re-checked under the row lock, re-extraction refused after review, document deletion closing conflicts as `member_deleted`, and `processed` requiring both index and extract.

**Regression:** default suite 1666 passed (266 deselected); `ruff check` and `ruff format --check` clean; `git diff --check` clean. Frozen artifacts (`evals/reports/`, `data/synthetic/`, `app/extraction/`, migrations 0001–0003) byte-identical before and after.

**Left in `medimind`:** one fictional patient (the P02 profile) with 8 documents, 41 facts, 7 review decisions and 2 closed conflicts, plus their files under `data/uploads/` and `data/parsed/`; reference data seeded.

### 2026-11-18 (later): Phase 4 — longitudinal intelligence implemented (ADR-039)

- **Scope** (IMPLEMENTATION_PLAN Phase 4 outline; [PHASE_4_PLAN.md](PHASE_4_PLAN.md)):
  - `app/longitudinal/` (pure): dates, duplicates, timeline, trends and date comparison;
  - `app/records/longitudinal.py` (read-only, patient-scoped, fixed query count);
  - `app/services/longitudinal.py`;
  - `app/ui/longitudinal_views.py`: Overview, Timeline and Lab trends pages.
- **No migration.** Duplicates are derived at read time and never stored.
- **Dataset** `data/synthetic/longitudinal_v0`: 16 fictional scenario patients covering cases C1–C14, plus one 1,050-result performance patient. Held-out digest `30fb83de…` was frozen at build, before any evaluation.
- **Measured** (synthetic; not blind; no clinical accuracy claimed): every pre-registered target met on dev (11 patients) and held-out (5, once):
  - timeline precision, recall, order and date source: 1.0;
  - trend points and changes, and unit series: 1.0;
  - duplicates: 1.0 / 1.0;
  - conflicts: 1.0 / 1.0;
  - provenance and corrected values: 1.0;
  - leaks and excluded items shown: 0.

  On the performance patient, the timeline page median is 291 ms.
- **Tests (final):**
  - default suite 1717 passed (275 deselected);
  - integration 214 passed, 0 failed, 0 skipped;
  - Docling and models 64 passed.

  New: 43 unit and UI tests, 2 architecture tests, 9 PostgreSQL tests (including the runner end to end and a negative control).
- **Also fixed** (Task 2.6 storage, found by the existing concurrent-upload test, which failed in 2 of 8 runs): Windows extended-length `resolve()` paths, and the read-only safeguard blocking removal of the temporary link (ADR-033 update). Regression tests fail without the fixes; the concurrency test then passed 10 of 10 runs.
- **End to end:** the new pages ran through the Streamlit script (`AppTest`) against PostgreSQL for a scenario patient and the performance patient, with no exceptions; the Haemoglobin filter matched the specification (9 entries). No manual browser session.
- **Left in `medimind`:** 16 fictional evaluation patients plus the performance patient (ids in `evals/work/longitudinal_v0/`, git-ignored).

### 2026-11-18 (later): Phase 5 — evidence-grounded copilot implemented (ADR-040)
- **R1.8 completed** (Docker available): dev ingest 35 reports / 57 pages (indexing 2,015 ms per page); dev ablation of dense, lexical, hybrid, hybrid_rerank and calibrated hybrid_rerank; **lexical frozen** (best MRR 0.888 and Hit@1 0.808; reranking +0.004 nDCG at ~57× latency, so not kept per ARCHITECTURE §9.4); held-out ingest 15 reports and **one** held-out run: MRR 0.891, nDCG@10 0.876, Recall@5 0.971, leak 0, refusals 1.0, abstention P 1.0 / R 0.375. Reports: `evals/reports/lab_v0/`.
- **Built:** `app/llm/` (provider protocol, scripted mock, stdlib OpenAI-compatible adapter, loopback-only by default); `app/answering/` (models, guardrails, deterministic router, context builder, extractive composer, LLM generator, deterministic citation validator with fail-closed source checks); `app/services/copilot_tools.py` (six allowlisted typed tools) and `app/services/copilot.py`; `stored_chunks` read in `app/records/chunks.py`; Copilot page; settings `MEDIMIND_COPILOT_RETRIEVAL_MODE` (default lexical) and `MEDIMIND_LLM_*`.
- **LLM provider evaluation blocked:** no credentials and no local model server; nothing selected or measured live.
- **Copilot evaluation:** `copilot_v0` built and frozen before tuning (dev 231 questions `9519e69d…`, held-out 103 `21e7b74d…`); the dev run found a real leak path (an uncited foreign source would have been displayed), fixed by fail-closed source validation; held-out run once. Held-out: routing 0.990, correctness 0.913, citation recall 0.944, citation precision **0.605 (missed)**, unsupported claims 0/193, abstention F1 0.909, refusals 1.0, leaks/rejected/needs-review/mismatch exposed 0, attacks accepted 0/304, p50 83 ms, 0 tokens. Main failure cause: unrecognised patient phrasings ("A1c", "fasting sugar"). Reports: `evals/reports/copilot_v0/`.
- **Tests:** 177 new unit/UI tests and 7 PostgreSQL integration tests; default suite 1894 passed, 282 deselected (0 failed, 0 skipped); integration 221 passed; real-model 3 passed; Ruff clean.
- **Deviations for owner review:** deterministic router (ARCHITECTURE §9.1 proposed an LLM classifier), rule-chosen tools (ADR-007 said the LLM chooses), no LangChain (ADR-011), no conversation history.

### 2026-11-18 (later): Phase 5 remediation — routing, abstention, `copilot_v1` (ADR-041)
- **Cause found:** `copilot_v0` held-out precision 0.605 came from unknown everyday test names being widened to "all tests".
- **Built:** `app/answering/vocabulary.py` (`vocab-1`); unrecognised-phrase detection in the router (`RoutePlan.unrecognised`); abstention without any lookup for unknown names; partial answers for known + unknown; passage relevance gate in `app/services/copilot.py`.
- **Lexical threshold:** calibrated on lab_v0 dev only (`evals/calibrate_lexical.py`, `lexical_abstention_dev.md`); rejected (scores overlap; it would abstain on 107/156 answerable).
- **New data:** `lab_v1` (6 new patients, 26 reports, Docling-ingested; one native crash resumed after the stale-claim window) and `longitudinal_v1` (M01–M06), both frozen; `copilot_v1` built and frozen before the remediation code (dev 425 `93955248…`, held-out 167 `d7bb8b46…`).
- **Results:** dev (in-sample) all 1.0; configuration frozen; held-out run once: routing, correctness, citation precision and recall, abstention F1 all **1.000**; 0 unsupported claims (263), 0 leaks/exposures, 0/320 attacks accepted. Two earlier `heldout` invocations aborted in a harness bug before any metric existed; harness fixed (not the frozen code), then the single scored run.
- **Caveat:** held-out phrasings and the vocabulary were written by the same author in the same session; all held-out-only phrasings are in the vocabulary.
- **Tests:** +25 (vocabulary/unrecognised routing 21, service 4); default 1919 passed, 282 deselected; integration 221 passed; real-model 3 passed; Ruff clean.
- `copilot_v0`, R1.8 and Phase 1–4 artifacts untouched; nothing staged or committed.

### 2026-11-18 (later): Phase 6 — multi-document clinical intelligence implemented (ADR-042)

- **Baseline before changes:** default 1919 passed / 282 deselected; integration 221 passed; Ruff clean.
- **Built:**
  - `app/clinical/` (pure): classifier `clinical-doctype-1`; segmentation, medication, allergy and event extraction `clinical-rules-1`; a fictional lexicon; `clinical-compare-1`; `clinical-qa-1`.
  - Migration `0005_clinical` (additive) and mirrored models; `app/records/clinical.py`.
  - Services `extract_clinical` (sixth stage `clinical_extract`), `review_clinical` and `clinical_records`.
  - The Clinical records page, and records answers in the Copilot box.
  - `evals/synthetic/clinical.py`, `evals/run_clinical.py`.
- **Dataset** `clinical_v1`: 16 fictional patients, 136 documents, 677 facts, 39 conflicts, 208 questions. The held-out digest `c3f4db6d…` was frozen at build, before any rule existed. It regenerates byte-identically.
- **Measured** (synthetic, same-author, not blind):
  - dev and held-out (once) met every pre-registered target;
  - native extraction, provenance, conflicts (27 dev / 12 held-out) and comparison are all 1.000;
  - Q&A correctness: 0.986 dev, 0.985 held-out;
  - OCR entity recall: 0.90 on both splits (all OCR facts are flagged);
  - hard gates: 0.

  Reports: `evals/reports/clinical_v1/`; details in [PHASE_6_EVALUATION.md](PHASE_6_EVALUATION.md).
- **Defects found and fixed during the work:**
  - a regex `^` used with `Pattern.match(pos)`;
  - merged and dropped list bullets;
  - an empty correction dict that would have skipped validation in `review_clinical_fact`.

  All are covered by tests.
- **Tests:**
  - **Default:** 2032 passed. That includes 120 new unit and UI tests, and these existing-test updates:
    - the head revision moves to 0005 in the migration and health tests;
    - the sixth stage is added to the pipeline tests;
    - the architecture allow-list gains `app.records.clinical`;
    - the schema-constraint test supplies 0005's NOT NULL columns.

    No assertion was weakened.
  - **Integration:** 232 passed (11 new).
  - **Ruff:** clean.
- **Database:**
  - `medimind_test`: upgrade, downgrade, round trip, `alembic check` and catalog identity all verified.
  - `medimind` upgraded to 0005, with every table's rows and hashes unchanged.
  - Real Docling end to end for fictional dev patient P01 (8 documents, all six stages succeeded, conflicts equal gold). P01 is left in `medimind`.
- **Phase 5 regression:** none of the 926 frozen questions is routed to the clinical path; `copilot_v1` dev re-evaluated in memory is identical; the frozen reports are byte-identical.
- **Not done:**
  - no manual browser session (UI covered by AppTest);
  - no LLM extraction (ADR-024 deferred);
  - clinical re-extraction is not offered.

### 2026-11-18 (later): Phase 6 acceptance fixes (audit recommended HOLD)

- **Fix 1, safety:** the records-question dispatcher now answers only questions that fully match a plain-lookup allow-list (ADR-042 amendment).
  - All 12 audit bypass examples are refused: 9 keep their Phase 5 refusal and 3 get the fixed clinical refusal.
  - 57 of 57 hand-written probes give the expected decision.
  - All 926 frozen Phase 5 questions are still dispatched to Phase 5.
- **Fix 2, identity:** the identity outcome is carried from `documents` through the fact views, comparison, conflicts, answers and citations. "Identity not confirmed" is shown wherever a source is unconfirmed and never for confirmed ones. Mismatch facts are dropped at read.
- **Fix 3:** the `clinical_extract` stage now displays as "Extract clinical records".
- **Fix 4:** citations are checked against this patient's stored page when an answer is built. Any mismatch or missing source withholds the answer.
- **Fix 5:** `evals/run_clinical_safety.py` and `evals/reports/clinical_safety_v1/`.
- **Tests:** default 2116 passed; integration 234 passed; Ruff clean. New tests:
  - 80 safety tests;
  - 4 UI tests;
  - 2 PostgreSQL tests.

  `clinical_v1` dev re-evaluated in memory is identical to the frozen dev report, and held-out was not re-run.
- **Not done:** a full `copilot_v1` re-evaluation. It needs the evaluation patients that exist only in `medimind`, which this task forbids using. The Phase 5 code is unchanged against HEAD, and every frozen Phase 5 question is dispatched to the unchanged Phase 5 path.

## Next steps

0. **Phase 6 (2026-11-18):**
   - **Owner:** review Phase 6. That covers [PHASE_6_PLAN.md](PHASE_6_PLAN.md), ADR-042, [PHASE_6_EVALUATION.md](PHASE_6_EVALUATION.md), migration 0005 (already applied to `medimind`) and the guardrail exemption for records lookups. Then decide the phase gate.
   - Optional: a manual browser pass of the Clinical records page (`uv run streamlit run app/ui/streamlit_app.py`, patient P01).
   - Next per ROADMAP: Phase 7 (LangGraph appointment preparation), after approval.
0. **Phase 5 (2026-11-18):**
   - **Owner:** review Phase 5 (`docs/PHASE_5_PLAN.md`, ADR-040, `evals/reports/lab_v0/`, `evals/reports/copilot_v0/`) including the deviations above and the lexical selection.
   - To unblock ADR-024: provide an API key or approve a local model (exact revision); then compare against the extractive baseline on dev and a new held-out split (`copilot_v1`).
   - Next improvements need `copilot_v1`: a reviewed patient-vocabulary table and abstaining (instead of "all tests") when a test-like word is not recognised.
   - Portfolio checkpoint (ADR-027): demo seed data and walkthrough video not done.
0. **Phase 4 (2026-11-18):**
   - **Owner:** review Phase 4 (`docs/PHASE_4_PLAN.md`, ADR-039, `evals/reports/longitudinal_v0/`) and the ADR-033 storage fixes, then decide both the Phase 3 and Phase 4 gates.
   - Optional: a manual browser pass of the Overview, Timeline and Lab trends pages (`uv run streamlit run app/ui/streamlit_app.py`).
   - Next phase per ROADMAP: Phase 5 (hybrid RAG), which starts only after the owner's approval; R1.8 can run now that the database is available.
0. **Phase 3 (2026-11-17):**
   - **Owner:** review Phase 3 (code, `docs/PHASE_3_PLAN.md`, ADR-037, ADR-038, `evals/reports/extract_v0/`) and decide the phase gate.
   - ~~**When Docker is running:** `uv run python -m app migrate`, `uv run python -m app seed-reference`, then `uv run pytest -m integration` (includes the 24 new extraction tests and the earlier pending suites), then one synthetic PDF through all five stages in the UI.~~ Done 2026-11-18: see the entry above (205 integration tests passed; end-to-end acceptance passed; 6 defects fixed).
   - **Owner:** review the 2026-11-18 defect fixes (migration 0004, `models.py`, `repositories.py`, `storage.py` and the updated tests) together with Phase 3.
   - Optional Batch G (LLM comparison) only if the owner selects it. ~~Q12 can be decided with the measured ambiguous-date rate.~~ Done 2026-11-18: flagging retained.
   - *2026-11-18:* PostgreSQL acceptance attempted once and blocked (Docker Desktop not running); ~~the "When Docker is running" step above is still open~~ completed later the same day.
1. ~~**Run the Task 1.8 and R1 integration tests**~~ Done 2026-11-18 (all integration suites passed). against `medimind_test` once Docker is running (`uv run pytest -m integration tests/integration/test_repositories_integration.py tests/integration/test_isolation_integration.py tests/integration/test_chunks_integration.py`).
2. ~~**Owner:** approve the pinned model revisions (ADR-035), download the two models once, set the two revision settings; then `uv run pytest -m models`.~~ Done 2026-11-16 (R1.5): 3 passed.
3. ~~**R1.8:** run `evals/run_retrieval.py` (ingest dev → ablate → ingest held-out → one held-out run) and record the measured results. *Updated:* dev only first: `ingest --split dev`, then `ablate`; after owner review, `freeze --mode <mode>`; the held-out ingest and `heldout` run come later and read only `dev_selection.json`. *Deferred 2026-11-17 (Docker unavailable); resume when Docker Desktop is running.*~~ Done 2026-11-18 (Phase 5 entry).
4. ~~**Owner:** review and commit the Task 1.8 and R1 work.~~ Done: committed as `f989b98` (2026-11-17).
5. ~~**Phase 2:** break the remaining subtasks (rule-based classifier, identity check, document library UI) into tasks.~~ Done 2026-11-17: Tasks 2.7–2.9 planned and approved (ADR-036).
6. ~~**Task 2.7:** implement rule-based classification (first Phase 2 implementation task), then 2.8 → 2.9a → 2.9b.~~ Implemented 2026-11-17 (rules v2, dev measured).
7. ~~**Owner:** review the Task 2.7 dev report (`evals/reports/doctype_v0/classification_dev_rules_v2.md`), set the accuracy target (D9), then `uv run python -m evals.run_classification freeze` and, once, `heldout`.~~ Done 2026-11-17: targets set, frozen, held-out evaluated once.
8. ~~Run `tests/integration/test_classification_integration.py` when Docker is running; then Task 2.8.~~ Task 2.8 implemented 2026-11-17; integration tests still pending.
9. ~~**When Docker is running:** `uv run pytest -m integration tests/integration/test_classification_integration.py tests/integration/test_identity_integration.py tests/integration/test_chunks_integration.py`, then the earlier Task 1.8 and R1 integration tests~~ Done 2026-11-18. R1.8 remains open (step 3).
10. ~~**Owner:** review the identity dev report (`evals/reports/identity_v0/identity_dev_rules_v1.md`); decide whether a held-out identity run is wanted; then Task 2.9a.~~ Superseded 2026-11-17 by rules v2 (step 11).
11. ~~**Owner:** review identity rules v2 and its dev reports:~~
    - ~~`evals/reports/identity_v1/identity_dev_rules_v2.md`~~
    - ~~`evals/reports/identity_v0/identity_dev_rules_v2.md`~~

    ~~Then decide whether to freeze v2, which held-out split(s) to evaluate once (neither is blind; see the caveat), and whether the documented limits are acceptable, given that D6 allows no override. Then Task 2.9a.~~ Done 2026-11-17: v2 frozen, limits accepted, both held-out splits evaluated once.
12. ~~**When Docker is running:** run the identity and chunk PostgreSQL integration tests~~ Done 2026-11-18 (passed) (gate re-check under the row lock, atomic `needs_review`). ~~Then Task 2.9a.~~ Task 2.9a implemented 2026-11-17. Also run the repository integration tests: `DocumentSummary` gained two fields.
13. ~~**Owner:** review Task 2.9a. Then Task 2.9b (Streamlit; adds `streamlit` and `demo_mode`). The 2.9b delete confirmation should say that a deleted document's files stay on disk (Task 1.8e). 2.9b also needs a decision on whether the UI offers stale-parse recovery.~~ Done 2026-11-17: 2.9b implemented. The delete confirmation explains that files stay on disk, and the UI offers no stale-parse recovery (shown and explained only).
14. **Owner:** review Task 2.9b.
15. ~~**When Docker is running:** run the 2.9b acceptance end to end: `uv run streamlit run app/ui/streamlit_app.py`, then take a synthetic PDF from upload through every stage, with type and identity shown. Run it together with step 12.~~ Done 2026-11-18 through the Streamlit script with `AppTest` (upload through every stage, type and identity shown); the headless server answered its health check. A manual browser pass by the owner is still worthwhile.

## Open questions

| # | Question | Status |
|---|---|---|
| Q3 | India / English / DD/MM/YYYY region profile | **Resolved**: accepted 2026-11-13 (ADR-025) |
| Q5 | LLM provider | Deferred to the start of Phase 3 (ADR-024) |
| Q10 | LangGraph record-analysis use case | Deferred (ADR-012) |
| Q12 | Auto-accept an ambiguous date when the same document confirms DD/MM order? | **Closed 2026-11-18:** no; `ambiguous_date` review retained (dev rate 129/394 = 32.7%, synthetic). See DECISIONS.md. |
| — | Proposed ADRs still to be decided: 007, 008, 011, 017, 021, 027 | Awaiting owner review |
| — | Hosting target for the public demo | Decided in Phase 9, or at the ADR-027 checkpoint |
| D6 | Re-processing documents after a parser upgrade (new outputs never overwrite `v1/`) | Deferred (ADR-029 versioning policy, ADR-032) |
