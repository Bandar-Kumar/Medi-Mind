# Progress

## Current phase

**Phase 1: Development environment and project foundation.** Tasks 1.1 (dependency compatibility, resolution only), 1.2 (project foundation), 1.5 (Docker Compose PostgreSQL + pgvector) and 1.6 (SQLAlchemy connection + authenticated database health) are complete. Phase 0 is complete, but some Proposed ADRs are still open.

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

## Next steps

1. **Task 1.7:** initialize Alembic, then the baseline migration (`patients`, `documents`, `pipeline_runs`; enable `vector`).
2. **Task 1.8:** patient-scoped repositories with cross-patient read, update and delete isolation tests.
3. At the start of Phase 2: the runtime smoke test (ADR-026), with owner approval.

## Open questions

| # | Question | Status |
|---|---|---|
| Q3 | India / English / DD/MM/YYYY region profile | **Resolved**: accepted 2026-11-13 (ADR-025) |
| Q5 | LLM provider | Deferred to the start of Phase 3 (ADR-024) |
| Q10 | LangGraph record-analysis use case | Deferred (ADR-012) |
| Q12 | Auto-accept an ambiguous date when the same document confirms DD/MM order? | Open. Default: no, always flag. Decide with Phase 3 flag-rate data. |
| — | Proposed ADRs still to be decided: 002 (BGE-small, MiniLM cross-encoder), 007, 008, 011, 017, 021, 027 | Awaiting owner review |
| — | Hosting target for the public demo | Decided in Phase 9, or at the ADR-027 checkpoint |
