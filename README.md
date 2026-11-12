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

**Phase 0: Product planning and system design.** There is no application code yet. The design docs are waiting for review. See [docs/PROGRESS.md](docs/PROGRESS.md).

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

## Repository layout

| Path | Purpose |
|---|---|
| `docs/` | Project brief, roadmap, decisions and progress log |
| `app/` | Application code (added from Phase 1 onwards) |
| `tests/` | Automated tests |
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
