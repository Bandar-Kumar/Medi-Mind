# MediMind

MediMind is a prototype platform for evidence-linked personal medical records. It takes scattered medical documents (lab reports, prescriptions, discharge summaries) and turns them into health records that are structured, searchable and in date order. Every fact in a record links back to the source document it came from.

**Intended users:** patients and caregivers.

## What MediMind is not

MediMind manages health information. It is not a clinical tool. It does **not**:

- diagnose conditions,
- prescribe medicines or recommend medication changes,
- make clinical decisions on its own,
- fill in missing medical information, or settle conflicting records without telling you.

All demo data is **fictional and synthetic**.

## Status

**Phase 1: Development environment and project foundation.** The project foundation is in place: configuration, logging, error handling, a health check and tests. There are no AI features yet. See [docs/PROGRESS.md](docs/PROGRESS.md).

### Roadmap
| Phase | Focus |
|---|---|
| 0 | Product planning and system design |
| 1 | Development environment and project foundation |
| 2 | Medical document intelligence |
| 3 | Structured clinical extraction |
| 4 | Database and longitudinal intelligence |
| 5 | Advanced hybrid RAG |
| 6 | Healthcare intelligence features |
| 7 | LangGraph workflows |
| 8 | Evaluation, security and reliability |
| 9 | Frontend integration, deployment and portfolio documentation |

For details, see [docs/ROADMAP.md](docs/ROADMAP.md).

## Getting started

Requirements: Windows, macOS or Linux, with [uv](https://docs.astral.sh/uv/) installed. uv provides Python 3.12.

```bash
uv sync                          # create .venv and install the locked dependencies
uv run python -m app health      # check the environment: exit code 0 = healthy
uv run pytest                    # run the tests
uv run ruff check .              # lint
```

Create your local settings file (git-ignored) from the template:

| Shell | Command |
|---|---|
| Windows CMD | `copy .env.example .env` |
| PowerShell | `Copy-Item .env.example .env` |
| macOS / Linux / Git Bash | `cp .env.example .env` |

- **Settings** use the `MEDIMIND_` prefix. A real environment variable overrides `.env`, and `.env` overrides the defaults in code.
- **VS Code (optional, local only):** `.vscode/` is git-ignored, so it isn't included when you clone the repo. To have VS Code load `.env` into new terminals, add `"python.terminal.useEnvFile": true` to your own `.vscode/settings.json`. If you change `.env`, open a new terminal so the change is picked up.
- **Health-check exit codes:** `0` healthy, `1` a check failed, `2` invalid configuration.

## Repository layout

| Path | Purpose |
|---|---|
| `docs/` | Project brief, roadmap, decisions and progress log |
| `app/` | Application code. `app/main.py` is the entry point, and `app/core/` holds configuration, logging, errors and health checks |
| `tests/` | Automated tests (pytest) |
| `data/synthetic/` | Fictional demo records only. Never put real patient data here. |

## Documentation

- [Project brief](docs/PROJECT_BRIEF.md): vision, scope, safety boundaries
- [Roadmap](docs/ROADMAP.md): phases and milestones
- [Architecture](docs/ARCHITECTURE.md): layers, data flows, RAG design, security, evaluation
- [User flows](docs/USER_FLOWS.md)
- [Database design](docs/DATABASE_DESIGN.md)
- [Decisions](docs/DECISIONS.md): architecture decision records
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Progress](docs/PROGRESS.md)
- [Daily summary](SUMMARY.md): day-by-day log of work and decisions

