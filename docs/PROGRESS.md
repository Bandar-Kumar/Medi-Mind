# Progress

## Current phase

**Phase 2: Medical document intelligence.** Tasks 2.1–2.6 are complete:
- Task 2.4's documentation was committed in `f42f3f7`.
- Task 2.5 (parse persistence) was implemented, verified and documented on 2026-11-15, with IR schema version 1 frozen.
- Task 2.6 (document upload service with a minimal patient repository, ADR-033) was implemented, verified and documented on 2026-11-15.

The Task D7–2.6 work is not committed yet. The remaining Phase 2 subtasks (rule-based classifier, identity check, document library UI) are not yet broken into tasks. Phase 0 is complete, but some Proposed ADRs are still open.

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

## Next steps

1. **Owner:** review and commit the Task D7–2.6 work.
2. **Task 1.8** (rest still open): patient listing, update and delete; document listing, update and delete; with isolation tests.
3. **Phase 2:** break the remaining subtasks (rule-based classifier, identity check, document library UI) into tasks.

## Open questions

| # | Question | Status |
|---|---|---|
| Q3 | India / English / DD/MM/YYYY region profile | **Resolved**: accepted 2026-11-13 (ADR-025) |
| Q5 | LLM provider | Deferred to the start of Phase 3 (ADR-024) |
| Q10 | LangGraph record-analysis use case | Deferred (ADR-012) |
| Q12 | Auto-accept an ambiguous date when the same document confirms DD/MM order? | Open. Default: no, always flag. Decide with Phase 3 flag-rate data. |
| — | Proposed ADRs still to be decided: 002 (BGE-small, MiniLM cross-encoder), 007, 008, 011, 017, 021, 027 | Awaiting owner review |
| — | Hosting target for the public demo | Decided in Phase 9, or at the ADR-027 checkpoint |
| D6 | Re-processing documents after a parser upgrade (new outputs never overwrite `v1/`) | Deferred (ADR-029 versioning policy, ADR-032) |
