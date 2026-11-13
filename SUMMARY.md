# Daily Summary

A dated journal of MediMind's development, with the newest day at the top. It is updated at two points (see the rule in CLAUDE.md):
- **when a significant task is completed:** it is recorded in that day's entry (created if needed), together with `docs/PROGRESS.md`;
- **when the owner says "wrap up"** at the end of the day: that day's entry is finalized.

Only work that actually happened and was verified is recorded.

- **[docs/PROGRESS.md](docs/PROGRESS.md)**: the *current state* (phase, open questions, next steps).
- **This file**: the *history*, a short record of each day's work, decisions and lessons.

**Entry template:**
```
## YYYY-MM-DD: <headline>
**Phase:** …
### Done
### Decisions
### Learned
### Issues / blockers
### Next
```

---

## 2026-11-13: Phase 1, Tasks 1.2, 1.1 and 1.5, plus architecture decisions

**Phase:** 1, development environment and project foundation

### Task 1.5: Docker Compose PostgreSQL + pgvector
- **Git housekeeping first.** The stale rebase metadata was confirmed stale: nothing done, only `noop` left, HEAD = `main` = `origin/main` = `b732040`.
  - Claude ran `git rebase --quit` only, never `--abort`.
  - The owner's uncommitted doc changes were byte-for-byte identical before and after (same diff SHA-256).
- **Environment (owner-installed):** Docker Desktop 4.99.0, Engine 29.9.3, Compose v5.7.0, WSL2 Ubuntu-24.04. Docker reports 16 CPUs and about 7.6 GiB of memory.
  - Docker's CLI is on the *user* PATH (`%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin`). Terminals opened before the install need reopening.
- **Image:**
  - `pgvector/pgvector:0.8.7-pg18-trixie`, pinned by its multi-platform index digest `sha256:ab49524d18df7200b74ac9a475e1db6297fc1eb57da4c214c63d10ecc4b754fa` (amd64 manifest `sha256:a5b06db7…3760`).
  - The image's own config shows `PGDATA=/var/lib/postgresql/18/docker` and declares the volume `/var/lib/postgresql`, so the named volume is mounted there.
- **Files:**
  - new: `docker-compose.yml`;
  - changed: `.env.example` (5 `MEDIMIND_DB_*` keys, password left empty on purpose), `app/core/config.py` (database settings; port 1–65535; `db_password: SecretStr | None`, never on the display list), `app/core/health.py` (`database_tcp`, `database_password`), `tests/conftest.py` (`tcp_listener` and `closed_port` fixtures, 0.1 s test timeout), `tests/test_config.py`, `tests/test_health.py`, `README.md` ("Database (Docker)" section), `docs/IMPLEMENTATION_PLAN.md`, `docs/PROGRESS.md`.
  - **No dependencies, `pyproject.toml` or `uv.lock` changes**: both hashes still match the Task 1.1 baseline.
- **`.env`:** the owner added the 5 keys and set their own password. Claude only checked the keys were present and non-empty, using line counts, and never read, printed or changed the value.
- **Verified (actually run):**
  - `docker compose config --quiet`: with no password, refused with our message (exit 1); with the owner's `.env`, valid (exit 0).
  - Pull: the image's `RepoDigests` = `sha256:ab49524d…b754fa`, the same as the pin and the live registry; linux/amd64; Docker reports 677,229,047 bytes.
  - `up -d`: **healthy** after about 6 s. `ps` shows `127.0.0.1:5432->5432/tcp`.
  - `SELECT version()`: **PostgreSQL 18.7** (Debian 18.7-1.pgdg13+2). `data_directory` = `/var/lib/postgresql/18/docker`.
  - `pg_available_extensions`: `vector` **0.8.7**, `installed_version` empty (not created). Installed extensions: `plpgsql` only.
  - `docker compose port db 5432`: `127.0.0.1:5432`. Port bindings: HostIp `127.0.0.1` only.
  - **Persistence:** `system_identifier` = `7707590115500060715`, unchanged after `stop`/`start` and after `down`/`up`. `down` removed the container and kept the `medimind_pgdata` volume. `down -v` was never used.
  - `docker stats` (idle, 3 samples): **23.34–26.11 MiB** of the 1 GiB limit (2.28–2.55%). Limit enforced: 1073741824 bytes. Restart policy: `no`.
  - App health: database running, every check **ok** (exit 0); database stopped, `database_tcp` **warn** (`TimeoutError`) and overall OK (exit 0).
  - `uv run pytest`: **47 passed**. `ruff check`: passed. `ruff format --check`: 25 files formatted. `git diff --check`: clean.
  - `.env` ignored and untracked; `.env.example` and `docker-compose.yml` not ignored. No database files inside the repo.
- **Final state:** container `medimind-db-1` **stopped** (Exited 0), volume `medimind_pgdata` **kept**.
- **Learned:**
  - **Windows and closed ports:** on Windows, a closed loopback port is refused only after about **2.06 s** (measured), because of connection retries. The health check's 1 s timeout fires first, so a stopped database shows `TimeoutError` and adds about 1 s. Tests use a 0.1 s timeout via the isolation fixture, which took the suite from 9.8 s to 1.3 s.
  - **PostgreSQL 18's data folder:** `PGDATA` is a versioned subfolder, so the volume must be mounted at `/var/lib/postgresql`, not `.../data`.
  - **Persistence without test data:** `system_identifier` proves the data survived without creating any test data.

### Architecture decisions finalized (documentation only)
- **Accepted by the owner:**
  - **ADR-009:** Docker Compose for PostgreSQL infrastructure only; the application is not containerized.
  - **ADR-016:** patient-scoped repositories. Isolation is enforced in every query and service, with mandatory cross-patient read, update and delete tests.
  - **ADR-020:** PostgreSQL infrastructure in Phase 1 (Tasks 1.5–1.8).
  - **ADR-002:** SQLAlchemy, Alembic and psycopg. BGE-small and the MiniLM cross-encoder stay Proposed.
  - **ADR-025:** India as the initial region and intended privacy/data-protection jurisdiction. The architecture stays adaptable for other countries, and **no legal or regulatory compliance is claimed**. India's DPDP Act 2023 is a design reference only. Q12 stays open.
- **Unchanged:** ADR-026, "Accepted — resolution verified (runtime not yet tested)".
- **Files changed:** `docs/DECISIONS.md` (earlier wording kept, with dated decision blocks), `docs/PROGRESS.md`, `docs/PROJECT_BRIEF.md` (one line), this file. No code, dependencies, `pyproject.toml` or `uv.lock` changed. Nothing installed.
- **Task 1.5** (Docker Compose for PostgreSQL + pgvector) is approved in principle, with the owner's refinements. Its implementation checklist is waiting for separate approval.
- **Current state:**
  - Tasks 1.1 and 1.2 are complete.
  - HEAD = `origin/main` = `b732040`, the owner's commit of the Task 1.1 docs.
  - Docker is **not installed** yet; WSL2 with Ubuntu-24.04 is available.
- **Git housekeeping:** stale rebase bookkeeping (`.git/rebase-merge`, onto `e9f708f`) was still present when rechecked on 2026-11-13.
  - The safe fix is `git rebase --quit`, which leaves the branch and files alone.
  - **Do not run `git rebase --abort`:** it would move `main` back to the removed commit `e9f708f`.
  - Not touched, because that's the owner's decision.

### Task 1.1: Dependency compatibility (resolution only)
- **Method:**
  - `uv pip compile` ran in `%TEMP%\medimind-task-1.1\`, outside the repo, and installed nothing.
  - Packages: docling, sentence-transformers, langchain, langgraph, sqlalchemy, psycopg[binary], streamlit, pydantic-settings.
  - Flags:
    - `--python-version 3.12 --python-platform x86_64-pc-windows-msvc` (Python 3.12, 64-bit Windows);
    - `--only-binary :all:` (ready-made wheels only, no builds, so no package code ran);
    - `--no-config` (ignore the project's uv settings).
- **Result:** exit code 0. **146 packages resolved** with no conflicts, and every one has a Windows x86_64 wheel that works on Python 3.12.
- **Key versions:**
  - Docling: `docling 2.150.0`, `docling-ibm-models 4.0.4`, `docling-parse 7.25.0`, `rapidocr 3.10.0`;
  - ML: `torch 2.15.0` (CPU build on Windows: its NVIDIA/CUDA requirements apply to Linux only, and no NVIDIA packages resolved), `torchvision 0.29.2`, `transformers 5.22.2`, `sentence-transformers 6.3.0`, `numpy 2.5.4`;
  - LLM: `langchain 1.5.0`, `langgraph 1.2.21`, `langchain-core 1.8.0` (one version shared by both);
  - data and UI: `sqlalchemy 2.1.8`, `psycopg 3.3.9` + `psycopg-binary 3.3.9`, `streamlit 1.67.0`.
- **Compared with `uv.lock`:**
  - every locked package resolves to the same version (pydantic 2.14.3, pydantic-core 2.50.4, typing-extensions 4.16.0 and the others);
  - transformers, torch and numpy would be new additions;
  - nothing would be upgraded, downgraded or replaced.
- **Download sizes** (from PyPI metadata): torch 126.7 MB, opencv 43.8 MB, scipy 36.7 MB, pyarrow 29.1 MB, transformers 13.1 MB, numpy 12.6 MB, psycopg-binary 2.9 MB.
- **Project unchanged (verified):** the SHA-256 of `pyproject.toml` and `uv.lock` matched before and after, `uv pip list` was identical (14 packages), and `git status` was clean.
- **Not established:** runtime compatibility, because nothing was imported or run.
  - **Docling's OCR backend is missing:** `rapidocr` resolved without one (such as `onnxruntime`).
  - The runtime smoke test is scheduled as the first step of the Phase 2 Docling spike (ADR-026), with owner approval.
- **Docs:** ADR-026 is now "Accepted — resolution verified", and Task 1.1 is marked complete in `docs/PROGRESS.md`.
- **Lessons:**
  - My first wheel check missed torchvision (`py3-none-win_amd64`) and opencv (`cp37-abi3-win_amd64`), because I searched only for `cp312` file names. Wheels for Python 3.12 can carry other tags.
  - In CMD, `%ERRORLEVEL%` on the same line as the command shows the *previous* exit code. That's why an earlier audit check misleadingly showed 0.

### Task 1.2: Project foundation

### Done
- **Environment.** The owner created `.venv` with uv 0.12.5 and Python 3.12.14. Then:
  - added `pyproject.toml` (`requires-python = ">=3.12,<3.13"`, `[tool.uv] package = false`, pytest and Ruff config);
  - added `.python-version` (`3.12`);
  - uv generated `uv.lock`.
- **Dependencies installed with uv:**
  - runtime: `pydantic-settings 2.16.2`, which brings `pydantic 2.14.3`, `pydantic-core 2.50.4`, `python-dotenv 1.2.4`, `typing-extensions`, `typing-inspection` and `annotated-types`;
  - dev: `pytest 9.2.0` and `ruff 0.17.5`, plus `pluggy`, `iniconfig`, `packaging`, `pygments` and `colorama`.
- **Code created:**
  - `app/__init__.py`, `app/__main__.py`;
  - `app/main.py`: entry point; exit codes 0 = ok, 1 = failure, 2 = invalid config;
  - `app/core/config.py`: `Settings` with the `MEDIMIND_` prefix, the `SAFE_TO_DISPLAY` allow-list, and a cached `get_settings()`;
  - `app/core/logging.py`: text and JSON formats; manages only its own tagged handler;
  - `app/core/errors.py`: `MediMindError`, `ConfigurationError`;
  - `app/core/health.py`: checks for Python 3.12, configuration, `.env`, the required `data_dir` (fail if missing) and the optional `data/synthetic` (warn if missing).
- **Config files:**
  - `.env.example` is committed as a template;
  - a local `.env` was created from it (git-ignored).
- **Tests:** `tests/conftest.py` (autouse isolation fixture), plus `test_config.py`, `test_logging.py`, `test_health.py` and `test_main.py`.
- **Removed:** `app/.gitkeep`, `tests/.gitkeep`.
- **Docs updated:** `README.md` (Getting started), `docs/IMPLEMENTATION_PLAN.md`, `docs/PROGRESS.md`, `docs/DECISIONS.md` (ADR-002, ADR-026).

### Verified (actually run)
- `uv run pytest`: **32 passed**. Also 32 passed in reverse order, and every test passed when run alone.
- The real `.env` and `data/` files had the same checksums before and after the test runs.
- `uv run ruff check .`: all checks passed. `uv run ruff format --check .`: 25 files already formatted. These are **13 Python files** (8 in `app/`, 5 in `tests/`) and **12 Markdown files**. Ruff 0.17.5 includes Markdown files when formatting and checks the Python code blocks inside them. Linting (`ruff check`) covers only the 13 Python files and `pyproject.toml`. Counts verified from Ruff's verbose file list during the audit.
- `uv run python -m app health`: all 5 checks ok, exit code 0.
- `MEDIMIND_LOG_LEVEL=LOUD`: exit code 2, and the message names the variable without echoing the value.
- `MEDIMIND_DATA_DIR=does_not_exist`: `data_dir` fails, `synthetic_data` warns, exit code 1.
- `MEDIMIND_LOG_FORMAT=json`: valid JSON log line.
- `git check-ignore`: `.env` is ignored, and `.env.example` is not.

### Decisions
- Approved plan with owner corrections:
  - Python 3.12 only;
  - required data dir fails / optional synthetic data warns;
  - secrets never displayed;
  - full test isolation;
  - logging never removes other handlers.
- ADR-002: `pydantic-settings`, `pytest` and `ruff` are now accepted.
- Deviations from `IMPLEMENTATION_PLAN.md`:
  - `DEMO_MODE` and the region profile are deferred until they are first used;
  - run IDs are deferred to the `pipeline_runs` task.

### Learned
- **Errors without echoed input.** Pydantic validation errors include the rejected input value, so `get_settings()` builds its own message without it. Otherwise a mistyped secret could be printed.
- **The VS Code terminal injects `.env` into the environment** (`python.terminal.useEnvFile`). Tests must remove `MEDIMIND_*` variables, or your local settings leak into them.
- **Output order.** stdout is buffered when piped and stderr isn't, so log lines can appear before printed output. The report now uses `print(..., flush=True)`.
- **Ruff 0.17** also checks Python code blocks inside Markdown files.

### Pre-commit audit and fixes (same day)
- **Read-only audit** of Task 1.2. Verdict: approved with minor fixes. It produced 12 findings: 0 critical, 0 high, 2 medium, the rest low or info.
- **Fixed #1 (Medium):** an uncaught `ConfigurationError` carried the original pydantic error, whose traceback showed the rejected input value (confirmed by a probe).
  - `get_settings()` now raises *outside* the `except` block, so there is no `__cause__` or `__context__` at all.
  - New regression test: `test_configuration_error_traceback_never_contains_the_invalid_value`.
  - The test was confirmed to **fail on the old code** (in a scratch copy) and to pass on the new code.
- **Fixed docs:**
  - #2: Ruff counts;
  - #3: `.vscode/` is local only;
  - README: CMD (`copy`) and PowerShell commands to create `.env`;
  - PROGRESS: git history;
  - DECISIONS: ADR-002 summary row;
  - CLAUDE.md: a permanent rule against logging sensitive data, and the reconciled SUMMARY update rule;
  - SUMMARY: the 38% estimate is labelled, and the git attribution is corrected.
- **Re-verified (actually run):**
  - `uv run pytest`: **33 passed**;
  - `ruff check`: passed; `ruff format --check`: 25 files formatted;
  - `python -m app health`: exit 0;
  - invalid config: exit 2, value echoed 0 times;
  - `uv lock --check`: ok;
  - `git diff --check`: no errors.
- **Deferred:**
  - #4: catch errors in each health check separately;
  - #5: separate exit codes for an invalid command vs invalid config;
  - #6: paths relative to the project root;
  - #7: accept lowercase log levels;
  - #8: test that the Python version matches `pyproject.toml`;
  - #9: test cleanup (importing private names);
  - #10: more CLI and logging tests;
  - #11: `.gitattributes` and line endings;
  - #12: keep `app_env` (no change needed).

### Issues / blockers
- None open for this task.
- Outstanding at the time; **all since resolved the same day:**
  - the ADR-026 Docling resolution check, done in Task 1.1;
  - ADRs 009, 016 and 020, now accepted;
  - ADR-025 (India), now accepted.
- **Git:**
  - Claude's commit `e9f708f` included a co-author trailer. At the owner's request, Claude removed it from GitHub with `reset --soft` and `push --force-with-lease`, keeping the files.
  - Separately, an accidental `git reset` and an unfinished rebase had hidden the Task 1.2 files. Claude recovered them with `git rebase --abort`, before that removal.
  - The owner then committed Task 1.2 as `58033ec`, "Foundation ok".
  - Claude no longer adds attribution lines to commits.
  - The owner committed Task 1.1's doc updates as `b732040`. The architecture-decision doc updates are uncommitted.

### Next
1. Task 1.6: SQLAlchemy connection and authenticated database health (plan first, owner approval).
2. Tasks 1.7–1.8: baseline migration (enables `vector`), patient-scoped repositories with cross-patient isolation tests.
4. Phase 2 starts with the Docling runtime smoke test (ADR-026).

---

## 2026-11-12: Project kickoff and Phase 0 design

**Phase:** 0, product planning and system design

### Done
- **Project structure.** Created a minimal structure: `README.md`, `.gitignore`, `docs/`, `app/`, `tests/`, `data/synthetic/`.
  - Real files can't be committed by accident: `.gitignore` excludes everything in `data/` except `data/synthetic/`, plus `.env` and `.venv/`.
  - Verified this with dummy files, which were then deleted.
- **Critical review of the master brief.** The main findings:
  - there was no question router, so structured questions like "last HbA1c?" would have gone through RAG instead of SQL;
  - evaluation and the database were planned too late;
  - the LangGraph scope was unclear;
  - there was no grounding check against hallucinated values;
  - the data model was missing raw vs normalized values, negation and date precision;
  - MIMIC and MedQuAD are unsuitable data sources.
- **All eight design docs.** Wrote `PROJECT_BRIEF`, `ROADMAP`, `ARCHITECTURE`, `USER_FLOWS`, `DATABASE_DESIGN`, `DECISIONS`, `IMPLEMENTATION_PLAN` and `PROGRESS`.
- **27 ADRs** recorded in `DECISIONS.md`, each with a status.
- **Git.**
  - Claude ran `git init`.
  - **The owner** created commit `d9dd20e` and connected the repo to GitHub, whose own "Initial commit" `f34a9f8` added a placeholder README.
  - The pull produced a README conflict. Claude resolved it, keeping the full project README, and staged it.
  - **The owner** created merge commit `1575603` and pushed it.

### Decisions
- **Accepted:**
  - single-user local app with multiple patient profiles;
  - synthetic-only public demo with no uploads;
  - lab reports first;
  - records-only copilot;
  - a status lifecycle where no fact becomes "verified" without a human action;
  - a pure-Python synthetic data generator;
  - LangGraph only for appointment preparation.
- **Provisional:** Python 3.12 + uv, kept only if the Docling compatibility test passes.
- **Deferred:**
  - the LLM provider: chosen in Phase 3 by measured comparison, **free preferred**, never by popularity;
  - the LangGraph record-analysis workflow;
  - Synthea.
- **Awaiting confirmation:** India region profile (English, DD/MM/YYYY, ambiguous dates flagged, original units and ranges kept).
- **Schedule:** full time. MVP in about 7–8 weeks, everything in about 14 weeks (estimates). A portfolio checkpoint after Phase 5 is proposed so job applications can start early.

### Learned
- **SQL vs RAG.** Structured questions (latest value, trends) belong in SQL. RAG is for unstructured text. Routing between them is a design decision in its own right.
- **Grounding check.** An extracted value must appear in the source text. This is the cheapest defence against LLM hallucination.
- **Class-table inheritance.** A shared `extracted_facts` table gives reviews and conflicts real foreign keys. The alternative, a loose "type + id" reference, can't be enforced by the database.
- **Ground truth by construction.** Generate the answers first, then render them into documents. The labels are then free and exact.

### Issues / blockers
- Strict flagging of ambiguous dates could flag roughly 38% of Indian lab dates, leaving gaps in trend charts until reviewed (open question Q12). **This is an estimate, not a measured result.** It assumes day-of-month values are spread evenly, so about 12 in 31 days are 12 or less. The real rate will be measured in Phase 3.
- Proposed ADRs 002, 009, 016 and 020 must be decided before Phase 1.

### Next
1. Confirm the India region profile (ADR-025) and decide the remaining Proposed ADRs.
2. Phase 1, task 1.1: Python 3.12 + uv + Docling compatibility test on Windows.
