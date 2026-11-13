# Decisions

This is a lightweight Architecture Decision Record (ADR) log.

**Statuses:**
- **Accepted:** the owner has agreed to it.
- **Accepted (provisional):** agreed, but must be revisited if a named test fails.
- **Proposed:** a recommendation waiting for the owner's decision.
- **Deferred:** deliberately postponed until a stated trigger.
- **Superseded:** replaced by a later ADR.

A Proposed or Deferred ADR is not a final decision.

## Summary

| ADR | Title | Status | Source |
|---|---|---|---|
| 001 | Modular monolith | Accepted | CLAUDE.md |
| 002 | Core technology stack | Accepted (base; `pydantic-settings`, `pytest`, `ruff`; **SQLAlchemy, Alembic, psycopg**) / Proposed (BGE-small, MiniLM cross-encoder) | CLAUDE.md, brief, Task 1.2, Owner 2026-11-13 |
| 003 | Deterministic numerical computation | Accepted | CLAUDE.md |
| 004 | Incremental project structure | Accepted (amended by 018) | Owner, 2026-11-12 |
| 005 | Synthetic data only | Accepted | CLAUDE.md |
| 006 | Phase-scoped documentation | Superseded by 018 | — |
| 007 | Query router + parameterized SQL tools | Proposed | Review |
| 008 | Deterministic grounding check | Proposed | Review |
| 009 | Docker for the database only | **Accepted** | Owner, 2026-11-13 |
| 010 | Synthetic ground truth; pure-Python generator first | **Accepted** (Synthea **Deferred**) | Q8 |
| 011 | Narrow use of LangChain | Proposed | Review |
| 012 | LangGraph scope | **Accepted** (appointment prep) / **Deferred** (record analysis) | Brief, Q10 |
| 013 | Single-user local deployment | **Accepted** | Q1 |
| 014 | Synthetic-only public demo | **Accepted** | Q2 |
| 015 | Optional local-only LLM provider | Superseded by 024 | — |
| 016 | Patient-scoped repositories | **Accepted** | Owner, 2026-11-13 |
| 017 | Custom evaluation primary, RAGAS optional | Proposed | Review |
| 018 | All eight design docs in Phase 0 | Accepted | Owner, 2026-11-12 |
| 019 | Lab reports first | **Accepted** | Q4 |
| 020 | Database foundation in Phase 1 | **Accepted** | Owner, 2026-11-13 |
| 021 | Class-table inheritance for extracted facts | Proposed | Review |
| 022 | Records-only copilot | **Accepted** | Q6 |
| 023 | Fact status lifecycle; review flagged extractions | **Accepted** | Q7 |
| 024 | LLM provider selected by evaluation, free preferred | **Deferred** to the start of Phase 3 | Q5 |
| 025 | India region profile and initial jurisdiction | **Accepted** | Q3, Owner 2026-11-13 |
| 026 | Python 3.12 + uv | **Accepted — resolution verified** (runtime not yet tested) | Q11, Task 1.1 |
| 027 | Full-time schedule; portfolio checkpoint at the MVP | Proposed | Q9 |

---

## ADR-001: Modular monolith
- **Status:** Accepted
- **Decision:** One deployable Python application with strict layers: UI → services → domain → repositories (see [ARCHITECTURE.md](ARCHITECTURE.md) §2).
- **Trade-off:** It is simpler to build, test and run than microservices. The cost is that layer discipline must be enforced by convention and review, not by process boundaries.

## ADR-002: Core technology stack
- **Status:**
  - **Accepted (base, from CLAUDE.md):** Python, Streamlit, PostgreSQL + pgvector, Docling, Pydantic, LangChain, LangGraph.
  - **Accepted with the Task 1.2 plan (2026-11-13):** `pydantic-settings` (configuration), `pytest` and `ruff` (development only).
  - **Accepted by the owner (2026-11-13):** SQLAlchemy 2.x, Alembic, psycopg 3. Docker (database only) is accepted separately in ADR-009.
  - **Proposed (still open):** BGE-small-en-v1.5, MiniLM cross-encoder.
  - *History:* SQLAlchemy, Alembic, psycopg and Docker were listed as Proposed additions from the brief until 2026-11-13.
- **Rationale for the database packages:**
  - **SQLAlchemy 2.x:** typed models and a session layer, so database access is plain, testable Python.
  - **Alembic:** versioned, reviewable schema migrations, so every database change is recorded in git.
  - **psycopg 3:** the PostgreSQL driver. Its binary build ships a ready-made Windows wheel (verified in Task 1.1).
  - All three are added when Task 1.6 first needs them.
- **Excluded unless a concrete need is shown:** FastAPI, Redis, Celery, Kubernetes, extra databases, hosted tracing.
- **Rule:** Each dependency is added in the phase that first needs it.

## ADR-003: Deterministic numerical computation
- **Status:** Accepted
- **Decision:** Trends, range comparisons, unit conversions and differences are computed in SQL and Python, never by an LLM.

## ADR-004: Incremental project structure
- **Status:** Accepted (amended by ADR-018)
- **Decision:** Code directories are created only when their phase begins. `app/` was chosen over `src/medimind/`.

## ADR-005: Synthetic data only
- **Status:** Accepted
- **Decision:** Only fictional synthetic records go in the repository or are sent to any external LLM.
- **Consequence:** This is what makes a free LLM tier acceptable even if its terms allow training on inputs (ADR-024). The guard in [ARCHITECTURE.md](ARCHITECTURE.md) §12 enforces it.

## ADR-006: Phase-scoped documentation
- **Status:** Superseded by ADR-018.

## ADR-007: Query router + parameterized SQL tools
- **Status:** Proposed
- **Decision:** A router classifies each question as `structured`, `document` or `out_of_scope`. Structured questions use parameterized, patient-scoped SQL tools, and the LLM only chooses the tool and its arguments. **No free-form text-to-SQL.**
- **Trade-off:** It covers fewer kinds of question than text-to-SQL, but it is safe, scoped and testable. New question types need new tools.

## ADR-008: Deterministic grounding check
- **Status:** Proposed
- **Decision:** An extracted fact passes only if its source excerpt is found in the page text and its value appears in that excerpt. Otherwise its status is `needs_review`.
- **Trade-off:** OCR noise can cause false failures, which means extra review work. That is preferable to silently accepting hallucinated values.

## ADR-009: Docker for the database only
- **Status:** **Accepted** (owner, 2026-11-13; previously Proposed)
- **Decision:** One Postgres + pgvector container. The app runs natively.
- **Trade-off:** Docker Desktop and WSL2 use memory, but in exchange we get a reproducible database without a native Postgres install on Windows.
- **Decision (2026-11-13):**
  - Docker Compose is used for PostgreSQL infrastructure only, at first.
  - The application, Streamlit and workers are **not** containerized.
  - Containerizing more needs a new, concrete reason and a new ADR.
  - Implemented in Task 1.5.

## ADR-010: Synthetic ground truth; pure-Python generator first
- **Status:** **Accepted** (Q8). Synthea is **Deferred**.
- **Decision:** Generate the structured truth first, then render it into documents, including degraded "scanned" variants. The labels are exact by construction. Generator v0 is pure Python and runs in Phase 1. The data is split into development and held-out sets by patient, and the held-out set is frozen before any tuning.
- **Reasoning:**
  - Pure Python means no Java dependency and full control over Indian-style layouts (ADR-025).
  - It is fast to iterate on.
- **Trade-offs:** Custom histories are less clinically realistic than Synthea's, and synthetic layouts are cleaner than real scans. Mitigations: degraded variants, varied layouts, and a small hand-annotated realism set.
- **Synthea trigger:** Re-evaluate at the start of Phase 6, when the broader document types need richer histories. Synthea needs Java, and its licence must be verified first.
- **Consequences:**
  - Phase 1 task 1.9 builds the generator.
  - Evaluation metrics exist from Phase 2 onwards.

## ADR-011: Narrow use of LangChain
- **Status:** Proposed
- **Decision:** Use LangChain only for the chat-model abstraction and structured output, behind our own `app/llm/provider.py`.
- **Trade-off:** We write a little more glue code ourselves. In return, the provider stays swappable (important for ADR-024) and we are insulated from LangChain's API changes.

## ADR-012: LangGraph scope
- **Status:**
  - **Accepted:** appointment preparation uses LangGraph (Phase 7).
  - **Deferred:** the record-analysis workflow (Q10).
- **Decision:** The ingestion and extraction pipeline is plain Python. Record analysis is reconsidered only when a concrete use case needs branching, retries or human review that the plain pipeline cannot express cleanly.
- **Trade-off:** It shows one well-justified agentic workflow instead of two. That is a stronger engineering signal than wrapping a linear pipeline in a graph.
- **Consequence:** `app/workflows/` contains only the appointment-preparation graph unless this ADR is revisited.

## ADR-013: Single-user local deployment
- **Status:** **Accepted** (Q1)
- **Decision:** For the MVP, the app runs locally for one user with no authentication. That user can manage multiple patient profiles.
- **Reasoning:** Authentication adds work that doesn't demonstrate the project's AI engineering.
- **Trade-off:** It is not production-ready for multiple users. That is documented as a limitation.
- **Consequences:**
  - The patient is the isolation boundary (ADR-016).
  - There is no `users` or `workspaces` table.
  - Isolation tests are still required, because caregivers mix patients.

## ADR-014: Synthetic-only public demo
- **Status:** **Accepted** (Q2)
- **Decision:** The public demo serves preloaded synthetic data only, with uploads disabled. Ingestion runs locally.
- **Reasoning:** A public upload box will receive real health records. Docling and its models are also probably too heavy for free hosting.
- **Trade-off:** Visitors can't try ingestion live. A recorded walkthrough of ingestion (a GIF or video) fills that gap.
- **Consequences:**
  - A `DEMO_MODE` setting hides the upload controls.
  - The demo database is seeded from `data/synthetic/`.
  - The demo needs precomputed facts and embeddings.

## ADR-015: Optional local-only LLM provider
- **Status:** Superseded by ADR-024. A local model is now one of the candidates evaluated there.

## ADR-016: Patient-scoped repositories
- **Status:** **Accepted** (owner, 2026-11-13; previously Proposed)
- **Decision:** All database and retrieval access goes through repositories that require `patient_id`. Child rows use composite foreign keys (`document_id`, `patient_id`). Isolation tests are mandatory. Postgres row-level security is optional, in Phase 8.
- **Decision (2026-11-13):** patient isolation is enforced in **every database query and every application service**.
  - **Repositories:** every method requires `patient_id`. There are no unscoped query methods.
  - **Services:** they pass the patient scope through and never bypass it.
  - **Retrieval:** every search is filtered by `patient_id`.
  - **Tests:** mandatory cross-patient tests for read, update and delete. Patient A's scope must never read, change or delete patient B's rows.
- **Rationale:** caregivers manage several patients, so leaking data between profiles is the main privacy risk in a single-user app.

## ADR-017: Custom evaluation primary, RAGAS optional
- **Status:** Proposed
- **Decision:** Deterministic, task-specific metrics in `evals/` are the primary measure. RAGAS is added only if it measures something the harness cannot.
- **Consequence:** This fits a zero-cost budget. LLM-judge metrics would consume free-tier quota.

## ADR-018: All eight design docs in Phase 0
- **Status:** Accepted (2026-11-12)
- **Decision:** All eight design docs are written in Phase 0 and refined as each phase starts. This supersedes ADR-006.

## ADR-019: Lab reports first
- **Status:** **Accepted** (Q4)
- **Decision:** Phases 2–5 support lab reports end to end. Prescriptions and discharge summaries follow in Phase 6. Handwriting is out of scope.
- **Reasoning:**
  - Lab reports are tabular and numeric, and they are the most common document type.
  - They exercise extraction, normalization, trends and RAG all at once.
  - Their correctness can be measured against exact ground truth.
- **Trade-off:** Medication and allergy features arrive later. Until then the demo is narrower but deeper.
- **Consequences:**
  - The classifier only needs to distinguish `lab_report` from `other` until Phase 6.
  - The fact tables for other types are designed now and created through migrations in Phase 6.

## ADR-020: Database foundation in Phase 1
- **Status:** **Accepted** (owner, 2026-11-13; previously Proposed)
- **Decision:** Postgres, SQLAlchemy and an Alembic baseline (`patients`, `documents`, `pipeline_runs`) come in Phase 1.
- **Trade-off:** Docker and Postgres are set up earlier. This avoids throwaway JSON storage in Phases 2–3.
- **Decision (2026-11-13):** the PostgreSQL infrastructure is set up in Phase 1, in this order:
  - **Task 1.5:** the Docker Compose database;
  - **Task 1.6:** the SQLAlchemy connection;
  - **Task 1.7:** the baseline migration;
  - **Task 1.8:** the patient-scoped repositories.

## ADR-021: Class-table inheritance for extracted facts
- **Status:** Proposed
- **Decision:** An `extracted_facts` base table holds provenance, status and versions, with one subtype table per fact type.
- **Trade-off:** One join per subtype query. In return, reviews and conflicts get real foreign keys (see [DATABASE_DESIGN.md](DATABASE_DESIGN.md) §3).

## ADR-022: Records-only copilot
- **Status:** **Accepted** (Q6)
- **Decision:** Every copilot answer is grounded in the selected patient's records: it uses SQL-tool results or cited document chunks. MediMind gives no general medical explanations. Advice-seeking questions get a fixed out-of-scope response.
- **Reasoning:**
  - It keeps every answer citable and evaluable.
  - It removes the biggest safety risk.
- **Trade-off:** It is less helpful ("What is HbA1c?" is declined, or answered only from what the documents say).
- **Consequences:**
  - The router's `out_of_scope` route covers general medical knowledge questions as well as advice.
  - The safety evaluation set includes general-knowledge questions that should be refused.

## ADR-023: Fact status lifecycle; review flagged extractions
- **Status:** **Accepted** (Q7)
- **Decision:** `review_status` keeps machine-extracted, machine-validated and human-verified states separate:

  | Status | Meaning | Set by | Shown in UI as |
  |---|---|---|---|
  | `extracted` | Output of the extractor; checks not yet run (transient) | Pipeline | — |
  | `validated` | Passed every automated check: grounding, plausibility, date, unit, analyte mapping | Pipeline | **Unverified** (auto-checked) |
  | `needs_review` | One or more checks failed; reasons are listed | Pipeline | **Needs review** |
  | `verified` | A human confirmed it | **Review action only** | Verified |
  | `corrected` | A human supplied corrected values; the original is kept | **Review action only** | Verified (corrected) |
  | `rejected` | A human rejected it; kept for audit | **Review action only** | Hidden (audit only) |

- **Rules:**
  1. Only flagged facts (`needs_review`) *require* review. `validated` facts may be verified, but are never auto-promoted.
  2. **No silent verification.** `verified`, `corrected` and `rejected` can be set only by the `record_review_decision` service. That service writes a `review_decisions` row in the same transaction. The repository rejects any other path, and a test enforces this.
  3. There is no bulk "verify all" in the MVP. Each verification is an explicit decision on one fact.
  4. Use downstream:
     - timeline, trends and copilot use `validated` (always labelled "unverified"), `verified` and `corrected`;
     - `needs_review` facts appear only in the review queue and the document detail page;
     - `rejected` facts are excluded everywhere.
- **Trade-off:** It takes more clicks to verify. The machine checks reduce how much review is needed, and human verification keeps its meaning.
- **Consequences:** The status enum in [DATABASE_DESIGN.md](DATABASE_DESIGN.md), the UI badges in [USER_FLOWS.md](USER_FLOWS.md), and the query filters in the longitudinal and answering modules all follow this table.

## ADR-024: LLM provider selected by evaluation, free preferred
- **Status:** **Deferred** to the start of Phase 3, the first phase that needs an LLM. Phase 2 classification is rule-based.
- **Decision:** No provider is chosen for popularity. At the start of Phase 3, candidates are compared on a fixed sample of about 20 dev-set pages:

  | Criterion | How it is measured |
  |---|---|
  | Cost | **A free tier is strongly preferred.** Target: zero spend. |
  | Structured-output reliability | Rate of schema-valid outputs; field accuracy against ground truth |
  | Privacy terms | Whether inputs are used for training, how long they are retained, and the date the terms were checked. Free tiers that train on inputs are acceptable **only** because the data is synthetic (ADR-005). |
  | Latency | p50 and p95 seconds per page |
  | Rate limits | Requests per minute and per day. These are critical for evaluation runs over hundreds of pages. |
  | Context window | Tokens per call |

- **Candidates:**
  - free API tiers from several providers;
  - a local model through Ollama, which is free and private but runs on CPU in 16 GB RAM, so it is slower and likely less reliable at structured output.

  Different models may be chosen for extraction and for answering.
- **Trade-off:** Free tiers change often and have rate limits. This is why the provider interface (ADR-011) and the caching described below matter.
- **Consequences:**
  - An **LLM response cache** keyed by (prompt version, model, input hash), so evaluation reruns are free and repeatable.
  - Retry with backoff when rate-limited.
  - An evaluation run budget.
  - Cost is still logged (0 for free tiers).
  - The comparison results are recorded in this ADR when it is decided.

## ADR-025: India region profile and initial jurisdiction
- **Status:** **Accepted** (owner, 2026-11-13). *History:* "Proposed, awaiting the owner's final confirmation" (Q3) until 2026-11-13.
- **Decision (2026-11-13):**
  - **Jurisdiction:** India is MediMind's **initial intended privacy and data-protection jurisdiction**.
  - **Adaptable:** the architecture stays adaptable for future international expansion. Region and jurisdiction are configuration (the region profile), not hard-coded logic.
  - **No compliance claim:** MediMind **does not claim legal or regulatory compliance** with any law.
    - India's Digital Personal Data Protection Act 2023 is used only as a design *reference*: consent, purpose limitation, deletion, keeping data to a minimum.
    - It is not a certification or a compliance claim.
  - **Region profile:** the profile below (English documents, DD/MM/YYYY, ambiguous dates flagged, original units and ranges kept) is accepted as written.
  - **Q12** (relaxing ambiguous-date flagging) stays open.
- *Original proposal (kept for history):*
- **Decision:**
  - The initial region is **India**, with **English-only** documents.
  - The expected date format is **DD/MM/YYYY**. `12-Mar-2024` and `12.03.2024` are also accepted.
  - Region settings live in a **region profile**: a configuration object, not hard-coded logic, so other regions can be added later.
- **The India profile covers:**
  - date order DMY;
  - Indian digit grouping (`1,50,000`) and lakh notation (`1.5 lakhs/cumm` → 150000, keeping the original);
  - common unit spellings (`cumm`, `/cu.mm`, `gm/dL`, `gm%`, `mill/cumm`);
  - British spellings in analyte aliases (`Haemoglobin`, `Glycosylated Haemoglobin`);
  - the typical report table layout: `Investigation | Result | Unit | Biological Reference Interval`.
- **Rules:**
  - A numeric date where both day and month are 12 or less (for example `03/04/2024`) is **ambiguous**. It is interpreted as DD/MM but **always flagged** with `ambiguous_date`.
  - Original laboratory units and reference ranges are **always kept as printed**. Normalization is an additional field, never a replacement.
- **Trade-offs:**
  - Strict flagging of ambiguous dates means many lab-report dates will need review.
  - A possible relaxation is open question Q12 (below).
  - Regional configuration adds a little indirection now, but avoids hard-coded assumptions.
- **Consequences:**
  - The synthetic generator renders Indian-style layouts and units.
  - The analyte alias table includes Indian and British naming.
  - The date and number normalizers read from the region profile.
  - If real data were ever used, India's Digital Personal Data Protection Act 2023 would apply. This is out of scope for the synthetic-only prototype.

## ADR-026: Python 3.12 + uv
- **Status:** **Accepted — resolution verified** (Q11; Task 1.1, approved 2026-11-13).
  - **Runtime compatibility has not been established yet.** This ADR is revisited if the Phase 2 runtime smoke test fails.
- **Reasoning:**
  - Python 3.12 is mature, and ML libraries (torch, Docling, sentence-transformers) publish wheels for it. Some ML wheels lag behind for 3.13.
  - uv is fast, uses a lockfile, reads `pyproject.toml` natively and installs Python versions on Windows.
- **Trade-offs:**
  - uv is newer and less familiar than pip. The fallback is pip + venv using the same `pyproject.toml`.
  - Python 3.12 is not the newest version, but its library support is broader.
- **Compatibility test (Phase 1, task 1.1):**
  1. Resolve the planned dependency set on Windows *without installing it*: Docling, sentence-transformers, CPU torch, LangChain, LangGraph, SQLAlchemy, psycopg, Streamlit.
  2. With your approval, run a smoke test that imports Docling in a throwaway environment.
  3. If it fails, fall back to Python 3.11.
- **Progress (2026-11-13):**
  - The owner created `.venv` with uv 0.12.5 and Python 3.12.14.
  - `pyproject.toml` pins `requires-python = ">=3.12,<3.13"`.
  - Task 1.2 dependencies installed and tests pass.
- **Task 1.1 result: step 1 (resolution) passed (2026-11-13).**
  - **What was run:** `uv pip compile` for docling, sentence-transformers, langchain, langgraph, sqlalchemy, psycopg[binary], streamlit and pydantic-settings.
    - Targets: `--python-version 3.12 --python-platform x86_64-pc-windows-msvc`.
    - `--only-binary :all:`, so only ready-made wheels were used, no package was built, and no package code ran.
    - It ran in a temporary folder outside the repo, so nothing was installed.
  - **Outcome:**
    - **146 packages resolved** with no conflicts. Every package has a Windows x86_64 wheel that works on Python 3.12, so no C compiler is needed.
    - Key versions: `docling 2.150.0`, `torch 2.15.0` (CPU build: its NVIDIA/CUDA requirements apply to Linux only), `transformers 5.22.2`, `sentence-transformers 6.3.0`, `numpy 2.5.4`, `langchain 1.5.0`, `langgraph 1.2.21`, `langchain-core 1.8.0`, `sqlalchemy 2.1.8`, `psycopg 3.3.9` + `psycopg-binary 3.3.9`, `streamlit 1.67.0`.
    - **Compared with `uv.lock`:** every locked package resolves to the same version (pydantic 2.14.3, pydantic-core 2.50.4, typing-extensions 4.16.0 and the others). The stack only adds packages; nothing would be upgraded, downgraded or replaced.
  - **What this does *not* show:** resolution proves only that compatible versions *exist*. Nothing has been imported or run. **Runtime compatibility is unverified.**
  - **Known limitation:** Docling's OCR engine resolved to `rapidocr 3.10.0`, but **no OCR backend** (such as `onnxruntime`) was pulled in. OCR on scanned documents may fail at runtime, or need an extra package.
- **Step 2, the runtime smoke test, is scheduled as the first step of the Phase 2 Docling spike.** It needs owner approval, and runs in a throwaway environment.
  - **What it checks:**
    - `import torch` and `import docling` on Windows;
    - converting one native-text PDF and one scanned PDF, including a working OCR backend;
    - loading sentence-transformers with transformers 5.x;
    - first-run model downloads;
    - peak memory.
  - **If it fails:** first fix it by adjusting packages or extras; fall back to Python 3.11 only if needed.

## ADR-027: Full-time schedule; portfolio checkpoint at the MVP
- **Status:** Proposed
- **Context:** The project is for a job search, and the owner can work on it full time.
- **Decision:** Add a **portfolio checkpoint at the end of Phase 5 (the MVP)**. It brings the demo mode, a README with an architecture diagram and *measured* evaluation results, and a short walkthrough video forward from Phase 9. That way job applications can start while Phases 6–9 continue.
- **Trade-off:** Some README and demo work is done twice. In return, the project becomes presentable about halfway through the timeline instead of at the end.
- **Consequence:** The calendar estimates in [ROADMAP.md](ROADMAP.md) assume full-time work.

## Open questions linked to ADRs
- **Q12** (from ADR-025): Should an ambiguous date be auto-accepted when the *same document* contains an unambiguous date confirming DD/MM order (for example `25/03/2024`)? This would reduce review work, but adds an inference rule. **Default: no. Always flag.**
