# Progress

## Current phase

**Phase 1: Development environment and project foundation.** Task 1.2 (project foundation) is done and waiting for review. Phase 0 is complete, but some Proposed ADRs are still open.

## Completed

### 2026-11-12: Minimal project structure
- Initialized the git repository.
- **Git history, as of 2026-11-13:**
  - `d9dd20e`: "1st commit", by the owner;
  - `f34a9f8`: GitHub's "Initial commit" (placeholder README);
  - `1575603`: merge commit by the owner, after the README conflict was resolved. It is pushed: `origin/main` = `1575603`.
  - All later work (Task 1.2 and these doc updates) is uncommitted.
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

## Next steps

1. The owner reviews Task 1.2 and approves a commit.
2. Task 1.1: Docling dependency-resolution check (ADR-026). It is still outstanding and should run before any Phase 2 work.
3. The owner decides ADRs 009, 016 and 020 and the remaining ADR-002 additions. These are needed before the database tasks (1.5–1.8).
4. The owner confirms ADR-025 (India).

## Open questions

| # | Question | Status |
|---|---|---|
| Q3 | India / English / DD/MM/YYYY region profile | **Awaiting final confirmation** (ADR-025) |
| Q5 | LLM provider | Deferred to the start of Phase 3 (ADR-024) |
| Q10 | LangGraph record-analysis use case | Deferred (ADR-012) |
| Q12 | Auto-accept an ambiguous date when the same document confirms DD/MM order? | Open. Default: no, always flag. Decide with Phase 3 flag-rate data. |
| — | Proposed ADRs still to be decided: 002 (additions), 007, 008, 009, 011, 016, 017, 020, 021, 027 | Awaiting owner review |
| — | Hosting target for the public demo | Decided in Phase 9, or at the ADR-027 checkpoint |
