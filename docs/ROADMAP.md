# Roadmap

> **Status:** The 10-phase sequence was set in the master project brief. Adjustments marked ⚙ reference their ADRs in [DECISIONS.md](DECISIONS.md). **Durations are estimates, assuming full-time work that includes learning time. They are not deadlines.** Task-level detail is in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## Strategy

- **Vertical slice first ⚙ (ADR-019).** Phases 2–5 deliver **lab reports end to end**: upload → extract → store → trend → cited answer. Phase 6 then broadens to other document types.
- **Evaluation from the start ⚙ (ADR-010).** Ground-truth synthetic data and the evaluation harness start in Phase 1. Every AI phase reports measured metrics. Phase 8 runs the final held-out evaluation.
- **The UI grows with each phase.** Every phase adds its own Streamlit page. Phase 9 polishes the UI, but the UI is not first built there.
- **Portfolio checkpoint at the MVP ⚙ (ADR-027, Proposed).** At the end of Phase 5, the project is presentable: demo mode, a README with measured results, and a walkthrough video. Job applications can start then.

## Estimated timeline (full-time)

| Phase | Estimate | Cumulative |
|---|---|---|
| 1 Foundation | 4–5 days | ~1 week |
| 2 Document intelligence | 5–7 days | ~2.5 weeks |
| 3 Clinical extraction | 8–10 days | ~4.5 weeks |
| 4 Longitudinal | 4–5 days | ~5.5 weeks |
| 5 Hybrid RAG + portfolio checkpoint | 10–14 days | **~7–8 weeks (MVP)** |
| 6 Healthcare features | 7–10 days | ~10 weeks |
| 7 LangGraph | 5–7 days | ~11.5 weeks |
| 8 Evaluation and security | 5–7 days | ~13 weeks |
| 9 Final polish and deployment | 4–5 days | **~14 weeks** |

These estimates will be revised with actual progress after Phase 1.

## Phases

| # | Phase | Objective | Depends on | Milestone | Effort | Introduces |
|---|---|---|---|---|---|---|
| 0 | Product planning and system design | Agree on scope, architecture, data model, evaluation | — | Eight docs approved | S | `docs/` |
| 1 | Development environment and project foundation | Reproducible environment, **database baseline ⚙ (ADR-020)**, config, logging, tests, synthetic generator v0, evaluation skeleton | 0 | `pytest` passes; database migrates; shell UI runs | M | `pyproject.toml`, `docker-compose.yml`, `app/core`, `app/records`, `app/ui`, `migrations/`, `evals/` |
| 2 | Medical document intelligence | Upload → parsed pages, classification, duplicates, identity check | 1 | Synthetic lab reports parsed with page provenance | M | `app/ingestion`, `app/services` |
| 3 | Structured clinical extraction | Grounded, normalized lab observations with review status | 2 | Extraction F1 reported on the dev set | L | `app/extraction`, `app/llm`, fact tables |
| 4 | Database and longitudinal intelligence | Timeline, trends, comparisons between dates (deterministic) | 3 | **First vertical slice complete** | M | `app/longitudinal` |
| 5 | Advanced hybrid RAG | Router, SQL tools, hybrid retrieval, reranking, citations, abstention | 4 | Ablation and retrieval metrics reported; **MVP** | L | `app/retrieval`, `app/answering`, `chunks` |
| 6 | Healthcare intelligence features | Prescriptions and discharge summaries; medications, allergies, conflicts, comparison | 5 | Conflict precision and recall reported | L | Conflict tables |
| 7 | LangGraph workflows | Appointment preparation with retries, human review and export | 6 | Graph tested path by path | M | `app/workflows` |
| 8 | Evaluation, security and reliability | Final held-out evaluation, security tests, hardening | 7 | Evaluation report published | M | — |
| 9 | Frontend integration, deployment and portfolio documentation | Polish, demo mode, hosting, write-up | 8 | Public synthetic-only demo | M | — |

## Phase gates

A phase is complete only when:
1. its acceptance criteria are met;
2. its tests pass;
3. its docs and `PROGRESS.md` are updated;
4. the owner approves.

Future phases are not started early.
