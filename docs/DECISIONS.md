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
| 002 | Core technology stack | Accepted (base; `pydantic-settings`, `pytest`, `ruff`; **SQLAlchemy, Alembic, psycopg**; **BGE-small, MiniLM cross-encoder** at pinned revisions) | CLAUDE.md, brief, Task 1.2, Owner 2026-11-13, Owner 2026-11-16 |
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
| 020 | Database foundation in Phase 1 | **Accepted** (+ D1, D2, Task 1.6) | Owner, 2026-11-13 |
| 021 | Class-table inheritance for extracted facts | Proposed | Review |
| 022 | Records-only copilot | **Accepted** | Q6 |
| 023 | Fact status lifecycle; review flagged extractions | **Accepted** | Q7 |
| 024 | LLM provider selected by evaluation, free preferred | **Deferred** to the start of Phase 3 | Q5 |
| 025 | India region profile and initial jurisdiction | **Accepted** | Q3, Owner 2026-11-13 |
| 026 | Python 3.12 + uv | **Accepted — dependency resolution verified; Docling runtime verified** (sentence-transformers runtime unverified) | Q11, Task 1.1, Task 2.1 |
| 027 | Full-time schedule; portfolio checkpoint at the MVP | Proposed | Q9 |
| 028 | Docling parser configuration | **Accepted** | Owner, 2026-11-14 (Tasks 2.1, 2.2, 2.4) |
| 029 | Versioned parse IR; the original PDF is authoritative | **Accepted** (IR schema version 1, **frozen 2026-11-15**) / Proposed (general schema-versioning policy) | Owner, 2026-11-14 (Tasks 2.3, 2.4); freeze 2026-11-15 (Task 2.5) |
| 030 | Parser text issues are flagged, never repaired | **Accepted** (amended 2026-11-15: `replacement_character`) | Owner, 2026-11-14 (Task 2.4); amendment 2026-11-15 (D7) |
| 031 | OCR provenance from Docling's OCR cells | **Accepted** (with a known limitation) | Owner, 2026-11-14 (Task 2.4) |
| 032 | Parse persistence and retry protocol | **Accepted** | Owner, 2026-11-15 (Task 2.5) |
| 033 | Document upload protocol and minimal patient repository | **Accepted** | Owner, 2026-11-15 (Task 2.6, decisions Q1–Q7) |
| 034 | Patient and document CRUD; deletion keeps files | **Accepted** | Owner, 2026-11-16 (Task 1.8, decisions D1–D4, O1–O5) |
| 035 | Evidence retrieval slice pulled forward (Milestone R1) | **Accepted** (model revisions approved and pinned; both models cached and verified, 2026-11-16) | Owner, 2026-11-16 (R1 plan, decisions P1–P4; model revisions) |
| 036 | Rule-based classification, identity outcomes and the identity gate on indexing | **Accepted** (D1–D10, R1–R3, A1–A4 with clarifications) | Owner, 2026-11-17 (Phase 2 plan, Tasks 2.7–2.9) |

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
  - **Accepted by the owner (2026-11-16):** the embedding model and the cross-encoder reranker, at exact pinned Hugging Face commits (must not be substituted or updated):
    - Embedding: `BAAI/bge-small-en-v1.5` @ `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (MIT).
    - Reranker: `cross-encoder/ms-marco-MiniLM-L6-v2` @ `233902d25c440f23af6f7d6e94d2946bac0bee0a` (Apache-2.0). `app/retrieval/models.py` requests the earlier repository name `cross-encoder/ms-marco-MiniLM-L-6-v2`, which the Hub redirects to this repository at the same commit.
  - *History:* SQLAlchemy, Alembic, psycopg and Docker were listed as Proposed additions from the brief until 2026-11-13. BGE-small-en-v1.5 and the MiniLM cross-encoder were "Proposed (still open)" until 2026-11-16.
- **Rationale for the database packages:**
  - **SQLAlchemy 2.x:** typed models and a session layer, so database access is plain, testable Python.
  - **Alembic:** versioned, reviewable schema migrations, so every database change is recorded in git.
  - **psycopg 3:** the PostgreSQL driver. Its binary build ships a ready-made Windows wheel (verified in Task 1.1).
  - Each is added when a task first needs it: SQLAlchemy 2.1.8 and psycopg[binary] 3.3.9 were added in Task 1.6 (2026-11-13). Alembic is added in Task 1.7 (decision D1).
- **Excluded unless a concrete need is shown:** FastAPI, Redis, Celery, Kubernetes, extra databases, hosted tracing.
- **Rule:** Each dependency is added in the phase that first needs it.
- **Update (2026-11-16, ADR-035):** BGE-small-en-v1.5 and the MiniLM cross-encoder are accepted for the R1 evidence-retrieval slice, run with the already-locked `transformers` (no new dependency). Their exact pinned revisions still need owner approval before any download.
- **Update (2026-11-16, later):** the owner approved the exact pinned revisions listed under Status; both models were downloaded once into the local Hugging Face cache and verified on CPU (see PROGRESS, R1.5).

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
- **Enforcement started (Task 1.6, 2026-11-13; owner decision D5):** `app/records/` is the only database access path. `session_scope()` exists for the records layer only. An architecture test (`tests/test_architecture.py`) fails if code outside `app/records/` imports SQLAlchemy or psycopg, or imports anything from `app.records.db` except the health probe and the error-sanitizing helpers.
- **Clarification (2026-11-16, ADR-034):** `create_patient` and `list_patients` are the only public repository functions without `patient_id`; both concern the patient scope itself. `tests/test_architecture.py` lists the complete public repository API and checks that every other function takes `patient_id` first.

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
- **Decision D1 (owner, 2026-11-13, Task 1.6):** Alembic initialization moves from Task 1.6 to Task 1.7, together with the baseline migration. Task 1.6 is the connection only: no `migrations/`, tables or extensions.
- **Decision D2 (owner, 2026-11-13, Task 1.6): severity of the authenticated `database_connection` health check.**
  - **Warn:** no password configured (check skipped), or the database is unreachable or timed out. A stopped container is the normal state on this laptop.
  - **Fail:** the server answered but rejected the login, or any other database error.
- **Refinement (owner approved "Option A", 2026-11-13), from a measured driver limit:** psycopg 3.3.9 with libpq 18.4 reports a wrong password and a missing database as a plain `OperationalError` with **no SQLSTATE**. Telling them apart would require reading the message text, which is never done (it is locale-dependent and may contain sensitive values). So:
  - SQLSTATE codes win when present (`28P01`/`28000` → authentication failed, `3D000` → database missing);
  - `ConnectionTimeout` → unreachable (warn);
  - a plain `OperationalError` with no SQLSTATE → one bounded TCP check (1 s): port open → **`connection_rejected`** (fail; "authentication failed, database missing or login refused"); port closed → unreachable (warn).
  - Rejected alternatives: matching message text; a second connection to another database to disambiguate.

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
- **Status:** **Accepted — dependency resolution verified; Docling runtime verified** (Q11; Task 1.1, approved 2026-11-13; Task 2.1, approved 2026-11-14).
  - *History:* "Accepted — resolution verified. Runtime compatibility has not been established yet; this ADR is revisited if the Phase 2 runtime smoke test fails" until 2026-11-14.
  - Loading sentence-transformers with transformers 5.x is **not yet verified** at runtime; it is checked when embeddings are first needed.
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
- **Task 2.1 result: step 2 (Docling runtime) passed (2026-11-14, owner approved).**
  - **What was run:** a Docling spike in a throwaway folder outside the repository, converting one native and one scanned synthetic lab report on the CPU.
  - **Outcome:**

    | | Native PDF | Scanned PDF (OCR) |
    |---|---|---|
    | Table cells | 28/28 exact | 20/28 exact; all 6 values correct |
    | Warm conversion, 1 page | 10.5–11.3 s | 20.2 s |
    | Peak memory | about 1.1 GB | 2.11 GB |

  - **The OCR backend limitation is resolved:** RapidOCR runs on its PyTorch backend, so no `onnxruntime` is needed. MediMind sets this backend explicitly (ADR-028).
  - **Installed in Task 2.2:** `docling==2.150.1` (Task 1.1 had resolved 2.150.0) with PyTorch 2.15.0+cpu and no CUDA packages; `.venv` grew from 79 MB to 1,181 MB.
  - No fallback to Python 3.11 was needed.

## ADR-027: Full-time schedule; portfolio checkpoint at the MVP
- **Status:** Proposed
- **Context:** The project is for a job search, and the owner can work on it full time.
- **Decision:** Add a **portfolio checkpoint at the end of Phase 5 (the MVP)**. It brings the demo mode, a README with an architecture diagram and *measured* evaluation results, and a short walkthrough video forward from Phase 9. That way job applications can start while Phases 6–9 continue.
- **Trade-off:** Some README and demo work is done twice. In return, the project becomes presentable about halfway through the timeline instead of at the end.
- **Consequence:** The calendar estimates in [ROADMAP.md](ROADMAP.md) assume full-time work.

## ADR-028: Docling parser configuration
- **Status:** **Accepted** (owner, 2026-11-14; Tasks 2.1, 2.2, 2.4)
- **Decision:** PDFs are parsed with Docling 2.150.1 (exact pin), configured explicitly in `app/ingestion/parsing.py`:
  - **OCR always on:** RapidOCR 3.10.0 with its PyTorch backend and default model (`lang=[]`). The backend is set explicitly because Docling's default would need `onnxruntime`, which is not installed.
  - **Table structure always on** (TableFormer, in Docling's default `accurate` mode).
  - **CPU only**, 4 threads; external plugins off.
  - **Limits from settings:** `MEDIMIND_MAX_UPLOAD_BYTES`, `MEDIMIND_MAX_PAGES`, `MEDIMIND_PARSE_TIMEOUT_SECONDS`.
  - **One converter per batch:** all models, RapidOCR's included, load at warm-up, so no download can happen mid-document.
- **Local processing vs model downloads:**
  - **Document processing is local.** Docling's remote services are turned off; documents, pages and extracted text are never sent to an external service.
  - **Model downloads are external, during setup or first use only.** Missing models are downloaded once (about 540 MB) by two separate mechanisms: Docling's models from Hugging Face into the Hugging Face cache, and RapidOCR's models from ModelScope into the installed environment (`.venv`). Only model files are downloaded; no document data is sent.
  - **Offline afterwards:** `HF_HUB_OFFLINE=1` stops Hugging Face requests but not RapidOCR's downloader. Fully offline operation therefore needs both the Hugging Face cache and RapidOCR's model files already present (see the README).
- **Reasoning:**
  - The Task 2.1 spike showed exact table cells on native PDFs and correct values on a scan, within the laptop's memory.
  - It is free and processes documents locally (ADR-013, ADR-025).
- **Trade-offs:**
  - About 1.2 GB of installed packages plus about 540 MB of models.
  - About 10 s per native page and 20 s per scanned page on the development laptop's CPU.
  - Docling's timeout is cooperative: it is checked between pages, so one slow page can overrun it. A hard limit would need a subprocess per document; it is not used unless measurements show it is needed.
  - Docling changes some characters before MediMind receives them, and this cannot be switched off (see ADR-030).
- **Consequences:**
  - Eager RapidOCR model loading uses private RapidOCR methods, so it is allowed only for the verified version (3.10.0); any other version is refused as `parser_misconfigured`.
  - Upgrading Docling or RapidOCR requires re-running the opt-in Docling tests.

## ADR-029: Versioned parse IR; the original PDF is authoritative
- **Status:**
  - **Accepted (owner, 2026-11-14; Tasks 2.3, 2.4):** IR schema version 1, as defined in `app/ingestion/ir.py`.
  - **Frozen (owner, 2026-11-15; Task 2.5):** version 1 is final, as described in "Version 1 contract" below. Task 2.5 writes the first IR files.
  - **Proposed (not yet adopted):** the general schema-versioning policy, i.e. when and how later IR versions are introduced, and how existing files are re-processed (decision D6, deferred; see ADR-032).
- **Decision:**
  - The parser's output is a Pydantic intermediate representation, **schema version 1** (`app/ingestion/ir.py`): per page, the text, parse method (`native`/`ocr`), visible native character count, OCR provenance, tables as cell grids and review flags; plus document-level review flags.
  - Models are frozen and reject unknown fields. Validation errors never contain input values.
  - The IR is stored as a versioned JSON file next to Docling's own full JSON output. Tables stay in these files: there is no database table for them (no migration 0004).
  - IR text is Docling's extracted, normalized text, not the raw PDF text. **The stored original PDF is the authoritative source.**
  - *(Superseded 2026-11-15: "The schema stays at version 1 until Task 2.5 writes the first file." Task 2.5 now writes IR files, and version 1 is frozen.)*
- **Version 1 contract (frozen 2026-11-15; `app/ingestion/ir.py`):**
  - **Structure:**
    - `ParsedDocument`: `schema_version` (always 1), `sha256` (of the original, lowercase hex), `page_count` (≥ 1), `parser` (`name`, `version`, `options`), `pages`, `review_flags` (document level).
    - `ParsedPage`: `page_number` (≥ 1), `text`, `parse_method` (`native` | `ocr`), `native_char_count` (≥ 0), `ocr` (provenance or null), `tables`, `review_flags`.
    - `OcrProvenance`: `engine`, `engine_version`, `backend`, `mean_confidence` and `min_confidence` (each null or 0–1).
    - `ParsedTable`: `table_index`, `num_rows`, `num_cols`, `cells`; `TableCell`: `row`, `col`, `row_span`, `col_span`, `text`, `column_header`, `row_header`.
  - **Serialization:** `dump_ir()` writes UTF-8 JSON (2-space indentation, field order fixed by the models). It is deterministic: dumping a loaded file gives the same bytes again. `load_ir()` validates and fails with a sanitized `ir_invalid` error.
  - **Validation:** models are frozen, reject unknown fields and never put input values into errors. Pages are numbered 1..`page_count` without gaps. Table cells fit their grid without overlap; table indexes run 0, 1, 2, … per page.
  - **OCR provenance (ADR-031):** present exactly on OCR pages; every OCR page carries `ocr_text_unverified`.
  - **Review flags:** 13 values: `docling_normalized_text`, `empty_page`, `invisible_text_layer`, `low_ocr_confidence`, `mixed_native_and_ocr`, `multi_page_item`, `multi_page_item_unplaced`, `ocr_confidence_unavailable`, `ocr_text_unverified`, `replacement_character`, `table_structure_invalid`, `unmapped_item`, `unplaced_item`. Each list is unique and sorted by value; any other form is rejected, never rewritten. Scope rules:
    - OCR-only flags (`ocr_text_unverified`, `low_ocr_confidence`, `ocr_confidence_unavailable`, `mixed_native_and_ocr`) only on OCR pages;
    - document level: only `unplaced_item` and `unmapped_item`; `unplaced_item` only there;
    - `empty_page` only on a page with no text and no tables;
    - `multi_page_item_unplaced` requires `multi_page_item`;
    - `replacement_character` exactly when the page text or a table cell contains U+FFFD (ADR-030, decision 6).
  - **Unicode:** a lone surrogate code point (U+D800–U+DFFF) is rejected in every text field, never repaired (Task 2.5d). U+FFFD and valid supplementary characters (e.g. U+1D707) are kept.
  - **Text preservation:** the characters of Docling's text are stored exactly as received: never stripped, normalized, corrected or replaced by MediMind. Serialization does encode them, as JSON in UTF-8 (with the escaping JSON requires), and loading restores exactly the same characters. Changes Docling made before MediMind received the text are flagged, not repaired (ADR-030). Text the database cannot store (NUL, lone surrogate) is rejected, never altered (ADR-032).
  - **Compatibility:** loaders accept only `schema_version` 1 and reject unknown fields, so a stored version 1 file is never edited or upgraded in place. **Schema version 2 is required for:**
    - adding, removing, renaming or retyping any field;
    - adding or removing a review flag value;
    - any validation change that alters which files are valid;
    - any change to the bytes `dump_ir()` writes for the same content;
    - any change to the meaning of stored text (e.g. normalizing it).

    **Not a new IR version:** a Docling upgrade that still produces valid version 1 (recorded in `parser`), and changes to the parse-output manifest (it has its own `manifest_version`; ADR-032). Re-processing existing documents after a parser upgrade is deferred (D6).
  - **Storage:** `data/parsed/<patient_id>/<document_id>/v1/` holding `ir.v1.json`, `docling.json` and `manifest.json`, published write-once (ADR-032).
- **Reasoning:** One validated contract between Docling and the rest of MediMind. Docling's output can change between versions, while the IR stays stable and testable without the models.
- **Trade-off:** Table contents can't be queried with SQL until a later phase extracts facts from them. In return, no schema is designed before its first use.
- **Consequences:**
  - Pages are matched by page number, never by list position. Missing or duplicate pages are refused.
  - An item spanning pages is split only when Docling's character spans check out exactly; otherwise it is placed on no page and flagged.

## ADR-030: Parser text issues are flagged, never repaired
- **Status:** **Accepted** (owner, 2026-11-14; Task 2.4: Option C, P1, visibility-aware signals, `invisible_text_layer`). **Amended** (owner, 2026-11-15; D7 step 0): decision 6, `replacement_character`.
- **Context (measured in Task 2.4):**
  - Docling's PDF text extractor turns `–` and `−` into `-`. Its text normalization rewrites quotes and ligatures and joins hyphenated lines (`4.0-`/`6.0` became `4.06.0`). Neither can be switched off.
  - OCR confidence is not accuracy: a scan with a mean confidence of 0.979 had only 25 of 40 cells exact.
  - Some PDFs contain text that is not drawn, for example the OCR layer of a "searchable scan".
- **Decision:**
  1. Docling's text is copied **verbatim**; MediMind never corrects it. Anything unusual becomes a review flag, stored in the IR with the text it describes (unique, sorted; any other form is rejected).
  2. **`docling_normalized_text`** (Option C): set when a watched character (such as `–`, `−`, `’`) occurs fewer times in Docling's page text than in the PDF's visible text layer read with PDFium, or when Docling's visible line cells contain characters it rewrites or a line-end hyphen it may drop.
  3. **`ocr_text_unverified`** (P1): on every OCR page, whatever its confidence. `low_ocr_confidence` (minimum below 0.80) is a separate diagnostic. Look-alike-character detection (P3) and per-page confidence counts (P4) are deferred.
  4. **Visibility:** PDFium associates each character with its text object. Render modes 3 (invisible) and 7 (clip only) are invisible; every other mode, including "unknown", counts as visible. Invisible text is excluded from `native_char_count`, `mixed_native_and_ocr` and both normalization checks.
  5. **`invisible_text_layer`:** a page-level flag, set when PDFium finds invisible non-whitespace characters, whether or not Docling kept that text. It means the PDF holds text a reader cannot see, not that the text is wrong. The text is kept.
  6. **`replacement_character`** (amendment, 2026-11-15; this is decision **D7** of the Task 2.5 review, listed in ADR-032): a page-level flag, set **exactly** when the page's final text or any of its table cells contains U+FFFD. The IR validator rejects the flag without the character and the character without the flag.
     - **Where U+FFFD comes from:**
       - Docling: by its source, docling-parse emits NUL for a glyph it cannot map, and Docling's default PDF backend replaces it with U+FFFD in both `text` and `orig`;
       - the PDF itself: its own Unicode mapping can store U+FFFD.
     - **The origin is not claimed:** Docling's output keeps no raw value, so the two cases look identical. PDFium does not resolve this either: in the D7 spike it reported unmappable Type3 glyphs as their raw character codes (1 and 2), not as U+FFFD, so the two libraries disagree on unmapped glyphs.
     - **OCR:** RapidOCR builds its text only from its recognition dictionary. The dictionary loaded at runtime (18,709 emittable single characters) contains no U+FFFD and no control characters, so a U+FFFD on an OCR page comes from native text on that page. The flag does not depend on the parse method: it is not an OCR-only flag.
     - **Scope:** page-level only, never a document flag. Text of an unplaced item is covered by `unplaced_item`; text Docling did not use for the page (e.g. a dropped invisible cell) does not set it.
     - The U+FFFD stays in the text; nothing is normalized, stripped or replaced. A U+FFFD genuinely present in the source is also flagged: the flag means "compare with the original", not "the parser failed".
     - Docling's `replace_broken_text_layer` (OCR instead of a broken text layer) stays off; ADR-028 is unchanged.
     - IR schema version stays 1: no IR file had been written yet (ADR-029), and the JSON layout is unchanged.
- **Trade-offs:**
  - More review work, and some false flags: counts are compared, not positions.
  - No unnormalized copy of the text is kept; the original PDF is the reference (ADR-029).
- **Consequences:** Flags never change text. Downstream extraction must treat flagged pages with care, and the review rules of ADR-023 apply to facts extracted from them.

## ADR-031: OCR provenance from Docling's OCR cells
- **Status:** **Accepted** (owner, 2026-11-14; Task 2.4), with the known limitation below.
- **Decision:**
  - A page is an OCR page (`parse_method = ocr`, with OCR provenance and `ocr_text_unverified`) **only** when Docling's line cells for that page include at least one non-blank cell marked `from_ocr`. Otherwise it is `native`.
  - "No visible native text" is **not** evidence of OCR. A blank page, or a page with only invisible text, is `native`.
  - A page with OCR cells and visible native text is also flagged `mixed_native_and_ocr`.
  - OCR confidences come only from those cells; missing or invalid values stay empty and are flagged `ocr_confidence_unavailable`.
- **Evidence:** only the OCR engine creates `from_ocr` cells (one per recognized line), and Docling's merge keeps them only where OCR text is used. Docling's own per-page `ocr_score` is computed from the same cells. On every test fixture the two agreed.
- **Known limitation (accepted):** Docling does not record whether OCR was *attempted* on a page. OCR that ran but recognized nothing therefore looks the same as no OCR: the page is `native`, and `empty_page` if it has no text. An image-presence signal from PDFium could tell these apart later; it is not designed, and would need a new decision.
- **Trade-off:** Provenance is never guessed, at the cost of not labelling unreadable scans as OCR pages.

## ADR-032: Parse persistence and retry protocol
- **Status:** **Accepted** (owner, 2026-11-15; Task 2.5, decisions D1–D8 and A1–A8 of the Task 2.5 review).
- **Context:** Task 2.4 produced a validated IR and Docling's JSON in memory only. Task 2.5 stores them, writes `pages` rows and must survive crashes, retries and two workers claiming the same document, without a migration.
- **Decision (D1–D8):**
  - **D1:** a minimal subset of Task 1.8 is built inside Task 2.5 (`app/records/repositories.py`: document creation and the parse-claim functions, all patient-scoped). The upload service is a separate task (2.6). Patient repositories and the rest of Task 1.8 stay open.
  - **D2:** parse outputs are found by a path derived from the IDs, `data/parsed/<patient_id>/<document_id>/v1/`. No database table or column records them.
  - **D3:** `pages.review_flags` is deferred to Phase 3; flags stay in the IR file.
  - **D4:** document status follows the existing architecture (see "Document lifecycle" below).
  - **D5:** page text containing NUL, or a lone surrogate that cannot be encoded as UTF-8, is rejected with `text_not_storable` before any database write. It is never modified.
  - **D6:** re-processing after a parser upgrade is deferred (ADR-029's versioning policy stays Proposed).
  - **D7:** the `replacement_character` flag. Recorded in ADR-030, decision 6; not repeated here.
  - **D8:** each output directory holds a `manifest.json` (its own `manifest_version` 1): document ID, source SHA-256, the producing parse run's ID, and the SHA-256 and size of `ir.v1.json` and `docling.json`. No timestamps, patient data, filenames or text.
- **Document lifecycle** (`documents.processing_status`; the state machine is unchanged):
  - `create_document` → `uploaded` (also the database default).
  - Claim for parsing → `processing`, and a `running` parse run is inserted. Its ID is the **claim token**.
  - Parse failure → the run becomes `failed` and the document `failed`. A `failed` document can be claimed again.
  - **Parse success → the run becomes `succeeded`; the document stays `processing`.** Parse completion is not pipeline completion: `processed` (after chunking and fact extraction) and `needs_review` (identity mismatch) are set by later stages (ARCHITECTURE §4.1). A parsed document cannot be claimed again (its pages exist: `already_parsed`).
- **Transactions:** Tx A claims (row lock with `SELECT … FOR UPDATE`); parsing and all file work run with **no** transaction open; Tx B re-checks that the claim token is still the active claim, inserts all pages or none and marks the run `succeeded`; on failure Tx C records it.
  - Tx C runs for ordinary exceptions and `KeyboardInterrupt` only and **never masks the original exception**, even if recording fails or a second interrupt arrives (A5). `SystemExit` and `GeneratorExit` are not handled (A4).
- **Stale claims:** a fixed threshold of **1,200 seconds** (`STALE_CLAIM_SECONDS`), chosen as the default parse timeout (600 s) plus an equal grace period because Docling's timeout is cooperative. It is a constant: it is **not** derived from the `parse_timeout_seconds` setting and does not change with it. A stale claim is recovered only when the caller explicitly asks (`recover_stale=True`); the stale run is marked `failed` with error kind `abandoned`. A worker whose claim was recovered can never write pages afterwards.
- **Parse outputs:** written to a temporary directory next to the final one (manifest last) and published with one directory rename, which never replaces an existing directory. Published outputs and page rows are never deleted or overwritten.
- **Reuse of earlier outputs (A1–A3):** before reuse, the manifest is checked (layout, validity, document ID, source SHA-256); then its producing run must be a parse run of the same patient and document, in any status; then the IR and Docling JSON are **verified against the manifest** (SHA-256 and size) and the IR is fully validated. The original PDF is verified on both the parse and reuse paths (A2): recomputed path, containment, readability, SHA-256, valid PDF, page count equal to the recorded one (the record is authoritative; a missing recorded count is `integrity_mismatch`, A6). The producer's provenance stays in the manifest and its own `pipeline_runs` row; the current run records only the `configured_*` parser versions (A3).
- **Error kinds:** `already_running`, `already_parsed`, `claim_lost`, `abandoned`, `text_not_storable`, `integrity_mismatch`; plus `document_not_found` (raised, never stored). Unreadable stored files use the existing `write_failed` kind (A7).
- **Trade-offs and known limitations:**
  - No database link from pages to the run that produced their outputs: that link is the manifest only.
  - A failure recording that itself fails (database down) leaves the document `processing` until the 1,200-second threshold passes and recovery is requested.
  - The fixed threshold does not follow a changed `parse_timeout_seconds`.
- **Consequences:** Upload (Task 2.6) creates documents with `create_document`; later Phase 2 stages move documents from `processing` to `processed` or `needs_review`.
- **Clarification (2026-11-16, ADR-034):** "Published outputs and page rows are never deleted or overwritten" applies to the parse and retry protocol. An explicit user deletion of a document (`delete_document`) removes its page and run rows by database cascade; the published output files still stay.

## ADR-033: Document upload protocol and minimal patient repository
- **Status:** **Accepted** (owner, 2026-11-15; Task 2.6, decisions Q1–Q7 of the Task 2.6 plan review).
- **Context:** Task 2.5 can parse a stored document, but nothing created patients or uploaded documents outside tests. Task 2.6 adds the upload service and the smallest patient repository needed for it. The rest of Task 1.8 (listing, updating and deleting patients and documents) stays open. No migration or schema change.
- **Patient repository** (`app/records/repositories.py`):
  - `create_patient(display_name, *, is_synthetic, date_of_birth=None) -> PatientRef`. It creates the patient scope, so it is the one repository function without a `patient_id` argument (the exception to ADR-016's "every method requires `patient_id`").
  - **Display name (Q4):**
    - surrounding whitespace is stripped; the result must be 1–200 characters;
    - rejected characters: Unicode category Cc (including NUL, tab and newline), lone surrogates, the bidirectional embedding/override/isolate controls U+202A–U+202E and U+2066–U+2069, zero width space U+200B, and the byte-order mark U+FEFF;
    - ZWJ (U+200D), ZWNJ (U+200C), LRM (U+200E) and RLM (U+200F) are allowed, because legitimate names in Indic and other scripts need them;
    - other format characters (category Cf) are not rejected as a class;
    - there is no Unicode normalization: an accepted name is stored exactly as given, minus surrounding whitespace.
  - **Date of birth:** optional; it must be a calendar `date` (a `datetime` is rejected) that is not in the future.
  - **`is_synthetic`** is required, with no default (ADR-005); anything other than a real `bool` raises `TypeError`.
  - Every input is checked before a database session exists. `PatientRef` holds only `id` and `is_synthetic`: the display name is personal data and is never returned, logged or included in an error.
  - `patient_exists(patient_id) -> bool` is one read-only `SELECT EXISTS`.
  - **Errors (Q3):** `PatientError` (`app/core/errors.py`) with `kind` `invalid_display_name`, `invalid_date_of_birth` or `patient_not_found`. Messages are fixed text that may name a limit or an identifier, never a value.
- **Unknown patient in `create_document` (Q2):** a violation of exactly the foreign key `fk_documents_patient_id_patients` (SQLSTATE 23503 **and** that constraint name) becomes `PatientError(patient_not_found)`, raised outside the `except` block so no driver error is chained. Every other integrity error propagates unchanged, including other foreign keys and CHECK constraints. The unique-violation race path is unchanged. This is the only change to Task 2.5 code; the parse protocol (ADR-032) is unchanged.
- **Upload service** (`app/services/upload_document.py`). `upload_document(settings, patient_id, data, *, original_filename) -> UploadResult` runs these steps in order:
  1. type checks (`UUID`, `bytes`, `str`) before any I/O;
  2. `validate_pdf_bytes` (in memory; PDF only for now);
  3. `patient_exists`, so an unknown patient stops before any upload directory is created;
  4. `store_original` (SHA-256 plus write-once, content-addressed publishing that never overwrites);
  5. `create_document` with the `safe_display_filename` result as metadata (the filename never builds a path).

  Each repository call is its own short transaction, and no transaction is open while files are written. The original is stored before its record, so a record never points to a file that was never stored.
- **Duplicates (Q1):** there is no separate duplicate pre-check. `store_original` writes nothing for identical bytes already stored, and `create_document` returns the patient's existing record. The database constraint `UNIQUE(patient_id, sha256)` is the race-safe authority. The same bytes uploaded for another patient are a separate file (`uploads/<patient_id>/<sha256>.pdf`) and a separate record.
- **Result flags.** `UploadResult(document, duplicate, original_stored)`:
  - `duplicate`: an existing document record was returned; no new record was created;
  - `original_stored`: this call created the original file;
  - **both can be true.** This happens when an existing record's file was missing and the identical, hash-verified bytes were published again (Q5; never an overwrite). It also happens when concurrent uploads of the same bytes race: one call writes the file, another creates the record. The service cannot tell these apart, so it logs a warning, "Original file published for an existing document record", with identifiers only, and does not claim which case occurred.
- **Concurrency and retries:**
  - concurrent identical uploads produce exactly one file and one record;
  - if the database fails after the file is stored, the file stays (read-only, content-addressed, harmless); a retry finds it (`original_stored=False`) and creates the record;
  - different bytes found at the content address raise `integrity_mismatch`; that file is left untouched and no record is created;
  - upload never deletes or rewrites a file and writes no `pipeline_runs` row.
- **Separation from processing:** upload stops after creating the record (status `uploaded`). It never claims or parses the document; callers run `process_document` (ADR-032) as a separate step.
- **Logging:** identifiers, flags, page count and size only; never the filename, a path, a hash, the display name or document content.
- **Trade-offs and known limitations:**
  - Orphan originals (stored, no record) can remain after a database failure until a retry; there is no cleanup.
  - The `duplicate` + `original_stored` warning cannot distinguish a restored original from a concurrent race.
  - `integrity_mismatch` from `create_document` is a `DocumentProcessingError` even during upload.
  - "Future" date of birth uses the local machine's date.
- **Consequences:** a UI (and `DEMO_MODE`, ADR-014) can build on `create_patient`, `upload_document` and `process_document`. Patient listing, update and delete, document listing, and their isolation tests remain in Task 1.8.
- **Clarification (2026-11-16, ADR-034):** "The display name is never returned" applies to `PatientRef` and to writes. `list_patients` returns it in `PatientSummary` (`repr=False`) for reading; it is still never logged or put in an error. The rest of Task 1.8 is done (ADR-034).

## ADR-034: Patient and document CRUD; deletion keeps files
- **Status:** **Accepted** (owner, 2026-11-16; Task 1.8, decisions D1–D4 and O1–O5 of the Task 1.8 plan review).
- **Context:** Tasks 2.5 and 2.6 built only the repository functions they needed. Task 1.8 completes the patient-scoped repositories: listing, updating and deleting patients, and listing, reading, renaming and deleting documents, with isolation tests (ADR-016). No migration, schema change, dependency or service was added.
- **Patients** (`app/records/repositories.py`):
  - `list_patients() -> tuple[PatientSummary, ...]`, oldest first. It is the **second unscoped exception** to ADR-016, after `create_patient`: both are about the patient scope itself. `PatientSummary` (`id`, `display_name`, `date_of_birth`, `is_synthetic`, `created_at`) holds personal data; the name and date of birth are `repr=False`. `PatientRef` is unchanged.
  - `update_patient(patient_id, *, display_name=…, date_of_birth=…) -> PatientRef`: only the given fields change (a sentinel tells "not given" from `None`, which clears the date of birth); the validation of `create_patient` is reused before any database access; `id`, `is_synthetic` and `created_at` are not parameters. An empty update is a `ValueError`.
  - **D2:** `delete_patient(patient_id)` deletes only an **empty** patient. The existing `ON DELETE RESTRICT` foreign keys are the race-safe authority: a violation of exactly `fk_documents_patient_id_patients`, `fk_pipeline_runs_patient_id_patients` or `fk_conflicts_patient_id_patients` (SQLSTATE 23503 and that name) becomes `PatientError(patient_not_empty)`; any other integrity error propagates.
- **Documents:**
  - `list_documents(patient_id) -> tuple[DocumentSummary, ...]`, newest first. An existing patient without documents gets `()`; an unknown patient is `patient_not_found`.
  - `get_document(patient_id, document_id) -> DocumentSummary`, filtered by both ids.
  - `DocumentSummary` carries no storage path or hash; `original_filename` is `repr=False`.
  - **Cross-patient ids:** another patient's document id gives exactly the error of a missing id (`document_not_found`, same message shape), so no operation reveals that the document exists.
  - **D4:** `rename_document(patient_id, document_id, original_filename)` changes only `original_filename`. Immutable: `id`, `patient_id`, `storage_path`, `sha256`, `mime_type`, `size_bytes`, `page_count`, `processing_status`, `created_at`. Owned by later stages, not editable here: `doc_type`, `doc_type_confidence`, `document_date*`, `identity_check`, `supersedes_document_id`. The row is locked like the parse protocol's, so a rename serializes with claims and changes no field the protocol reads.
  - **Filename boundary (O1, revised by the owner):** sanitizing stays at the ingestion/service boundary (`safe_display_filename`); the records layer does **not** import ingestion. The repository only checks that the name is canonical (exactly what the sanitizer would leave unchanged; lone surrogates are also refused, since they cannot be stored) and rejects anything else with `invalid_filename`, never rewriting it. A unit test ties the two rule sets together.
- **D1, document deletion:** `delete_document(patient_id, document_id) -> DeletedDocument(document_id, pages_deleted, runs_deleted)` hard-deletes the document row inside one transaction, with the row locked.
  - **Deleting a document removes its `pipeline_runs` history** (every parse run and its status, timings and error kinds) through the existing `fk_pipeline_runs_document_id_patient_id_documents ON DELETE CASCADE`, together with its pages, chunks, facts, review decisions and conflict memberships. **The original PDF (`uploads/<patient_id>/<sha256>.pdf`) and the parsed outputs (`parsed/<patient_id>/<document_id>/v1/`) are not deleted, renamed, chmod-ed or overwritten.** Keeping the run history would need a migration and is out of scope.
  - A **non-stale** running parse run means a live worker: the deletion is refused with `already_running`. Stale runs (older than `STALE_CLAIM_SECONDS`) do not block.
  - A newer version that supersedes the document blocks deletion: exactly `fk_documents_supersedes_document_id_patient_id_documents` becomes `DocumentProcessingError(document_referenced)`.
  - **Conflicts:** `conflicts` has no foreign key to documents or facts. Deleting a document cascades through `extracted_facts` to `conflict_members`, and the parent `conflicts` row **stays**, status unchanged, possibly with **zero members**. Such a row blocks `delete_patient` (`patient_not_empty`). No application code creates conflicts yet; closing them (USER_FLOWS F10) is deferred, with no schema change.
- **Stale worker race (analysed in the plan, covered by an integration test):** a worker whose stale claim's document was deleted can still publish outputs, but only under its own document id (`parsed/<patient>/<deleted id>/v1`; ids are never reused and publishing never replaces a directory), and it can write no row: its Tx B and Tx C find no document. Re-uploading the same bytes creates a new document id and reuses the content-addressed original without writing it. The new document's parse never sees the orphan outputs, which name another document and a run that no longer exists.
- **Clarifications of earlier ADRs:**
  - ADR-016: `list_patients` is the second documented unscoped repository function; every other public repository function takes `patient_id` first (enforced by `tests/test_architecture.py`, with the complete public API listed).
  - ADR-032: "published outputs and page rows are never deleted or overwritten" applies to the parse and retry protocol. An explicit user deletion removes the rows by cascade (files still stay).
  - ADR-033: "the display name is never returned" applies to `PatientRef` and to writes; `PatientSummary` returns it for reading, and it is still never logged.
- **Error kinds added:** `patient_not_empty` (`PatientError`); `document_referenced` and `invalid_filename` (`DocumentProcessingError`); `document_not_found` and `already_running` are reused.
- **Logging:** identifiers, counts and changed field names only; never a name, date of birth or filename.
- **Trade-offs and known limitations:**
  - Deleting a document loses its processing history.
  - Original files and parse outputs of deleted documents stay on disk as orphans until a separately approved purge task.
  - Leftover conflicts can make a patient undeletable until conflict handling exists.
  - A worker running longer than the stale threshold is treated as stale; its document can then be deleted and its outputs become orphans.
  - Moving a document to another patient (USER_FLOWS F2) is not supported.

## ADR-035: Evidence retrieval slice pulled forward (Milestone R1)
- **Status:** **Accepted** (owner, 2026-11-16; decisions P1–P4 of the R1 plan review). **Model revisions approved by the owner (2026-11-16):** `BAAI/bge-small-en-v1.5` @ `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (MIT) and `cross-encoder/ms-marco-MiniLM-L6-v2` @ `233902d25c440f23af6f7d6e94d2946bac0bee0a` (Apache-2.0); licences confirmed on the Hugging Face Hub at those commits. Both models are downloaded once into the local Hugging Face cache and verified on CPU with local-cache-only loading.
  - *History:* until 2026-11-16 this line read "**Pending owner approval:** the exact pinned model revisions and the first model download." See the update note at the end of this ADR.
- **Context:** the roadmap places hybrid RAG in Phase 5, after extraction (Phase 3) and longitudinal work (Phase 4), and says future phases are not started early. The owner wants a demonstrable retrieval capability now. Document retrieval needs only parsed pages and the verified IR, not extracted facts.
- **Decision:**
  - **P1 (pull-forward):** the *document-retrieval subset* of Phase 5 is built now as Milestone R1, together with Tasks 1.9 (synthetic generator v0) and 1.10 (evaluation harness), which its metrics need. This deliberately amends the "phases are not started early" rule for this subset only. The query router and SQL tools (ADR-007) stay in Phase 5, because they need Phase 3 facts.
  - **P2 (evidence only):** R1 answers with ranked, page-cited evidence passages, or abstains, or refuses. No LLM, no generated text: citations are correct by construction. LLM answers and the deterministic citation validator follow in R2, after the ADR-024 provider evaluation.
  - **P3 (models on `transformers`):** `BAAI/bge-small-en-v1.5` (384 dimensions, CLS pooling, L2-normalized; BGE query instruction on queries) and `cross-encoder/ms-marco-MiniLM-L-6-v2` (raw logits) run on CPU with `transformers` and `torch`, already locked through Docling: **no new dependency** (no sentence-transformers, no LangChain). This accepts ADR-002's Proposed model choices for R1.
  - **P4 (CLI demo):** `python -m app index` and `python -m app ask`; Streamlit stays with Task 1.11.
- **Model approval gate:**
  - models load only at an exact 40-hex commit revision (`MEDIMIND_EMBEDDING_MODEL_REVISION`, `MEDIMIND_RERANKER_MODEL_REVISION`; no default, branch names are rejected) and only from the local Hugging Face cache (`local_files_only=True`); nothing downloads unless `allow_download=True` is passed explicitly;
  - neither model is in the local cache (checked 2026-11-16); no revision has been chosen. The owner looks up and approves the commits, downloads them once, and sets the two settings;
  - licences to confirm on the model cards at approval: BGE-small-en-v1.5 is published under MIT, ms-marco-MiniLM-L-6-v2 under Apache-2.0 (as last known; verify the cards at the approved revisions).
- **Design (as built):**
  - `app/retrieval/` is pure (no database, enforced by `tests/test_architecture.py`): `chunking.py` (`CHUNKER_VERSION = "1"`), `query.py` (lexical terms, advice/injection guardrail), `fusion.py` (RRF), `evidence.py` (page grouping, abstention, calibration), `models.py` (lazy adapters).
  - `app/records/chunks.py` holds every SQL statement; every public function takes `patient_id` first (architecture test). Indexing inserts all chunks and one `pipeline_runs` row (`stage = 'index'`) in one transaction after locking the document; concurrent indexers serialize and one wins (`already_indexed`). `processing_status` is never changed. **No migration:** the `chunks` table of migration 0003 is used as designed.
  - Chunks never cross pages. Text chunks are exact spans of the page text (about 1,000 characters, 1-line overlap). Table-row chunks pair verbatim headers and cells (`Header: cell | ...`), `section = "table <i>, row <r>"`.
  - Search: dense (pgvector cosine, exact scan) and lexical (Postgres full text, `'simple'` configuration, each content word a bound `plainto_tsquery`, OR-ed), RRF (k = 60), cross-encoder on the top 20, one passage per (document, page), top 5 pages. Abstains when nothing is found or the top score is below a dev-calibrated threshold (`MEDIMIND_RETRIEVAL_ABSTAIN_THRESHOLD`; empty = not calibrated, never abstains on score). Advice-seeking and injection-style questions get a fixed out-of-scope response before any retrieval (ADR-022).
  - Evidence shows the filename, page, section, date and score; OCR pages carry "OCR text - unverified". Questions and excerpts are never logged.
- **Evaluation (ADR-010, ARCHITECTURE §13):** synthetic dataset `lab_v0` (seed 20261116): 12 fictional patients, 50 reports, 81 pages, 3 layouts, split by patient 8 dev / 4 held-out; `heldout_sha256 = 5dcdc7dfa3ff4c082585281ad3716e6c79164d90382e5fbb8e04a39156c8a56a`, frozen before any tuning and re-checked on every load. Question set (seed 7): dev 204, held-out 92. Metrics: Recall@k and Hit@k (1/3/5/10), MRR, nDCG@10, abstention precision/recall/F1, refusal rate, cross-patient leak rate, latency p50/p95. Ablation dense / lexical / hybrid / hybrid_rerank on dev; the threshold is calibrated on dev; held-out runs once (`evals/run_retrieval.py` refuses a second run).
- **Measured so far (2026-11-16):** Chunker on real Docling output, offline (3 dev reports, one per layout, 6 pages): Docling found tables in all three layouts (grid 4, plain 4, compact 2); 18, 18 and 21 chunks (2 text each, 16/16/19 table rows); longest chunk 682-758 characters; **16 of 16** ground-truth values of each report appear verbatim in its table-row chunks. Parse time 7.0-7.7 s per 2-page report warm (77.8 s for the first, including model loading). **Not yet measured:** every retrieval, abstention, leak and latency metric, the ablation and the held-out run. They need the running database and the approved models.
- **Trade-offs and limitations:** synthetic layouts are cleaner than real reports; the corpus is small, so reranker gains may not show (the ablation decides); only the OCR flag is shown, not the IR's other review flags (`pages.review_flags` is deferred, ADR-032 D3); no date or document-type filters yet; no re-indexing after a chunker or model change (deferred like D6); unanswerable and leak probes can coincide when every catalogue analyte is measured by some patient.
- **Update (2026-11-16, model approval and local-cache verification):** the statements above that the model revisions are pending owner approval, that "neither model is in the local cache … no revision has been chosen", and that the licences are still to be confirmed are **historical and superseded**. They are kept as the original record.
  - The owner approved the two exact revisions listed under Status; the commits and licences (MIT, Apache-2.0) were checked on the Hugging Face Hub.
  - Both revisions are set in `.env`; each model was downloaded once through `models.py` (`allow_download=True`). Runtime loading is unchanged: local cache only (`local_files_only=True`).
  - With downloads disabled (`HF_HUB_OFFLINE=1`), the three real-model tests (`-m models`) pass; CPU inference gives 384-dimensional unit-norm embeddings and one reranker logit per pair.
  - `app/retrieval/models.py` requests the earlier reranker name `cross-encoder/ms-marco-MiniLM-L-6-v2`, which the Hub redirects to `cross-encoder/ms-marco-MiniLM-L6-v2` at the same commit.
  - No model, pooling, normalization, query instruction, scoring, chunking or threshold setting was changed. The retrieval metrics, ablation, calibration and held-out run remain **not measured** (see PROGRESS, R1.5).
- **Update (2026-11-16, R1.8 readiness corrections, owner-approved):** the evaluation design above changes as follows. Retrieval, ranking, chunking and the models are unchanged. Nothing has been measured yet.
  - **Split-local questions:** `generate_questions(patients, seed=...)` reads only the given split's ground truth. Leak probes ask about an analyte that another patient *of the same split* has, so every probe is a genuine cross-patient test against ingested documents. (Previously the held-out patients' tested analytes decided the dev leak probes.)
  - **Metrics:** `leak_rate` and `leak_probe_abstain_rate` use leak probes only as the denominator; `questions_with_foreign_pages` counts a foreign page on any question type (must be 0). Abstention precision/recall/F1 cover answerable and ordinary unanswerable questions only. `advice_refusal_rate` and `injection_refusal_rate` replace the combined refusal rate. The dev report adds the recorded indexing time per page (wall-clock `index_document`, including the chunk insert).
  - **Calibration:** `hybrid_rerank` only; answerable vs ordinary `unanswerable` questions. Leak, advice and injection questions are excluded, so the leak metrics stay independent of the threshold. The included and excluded question kinds are recorded in the calibrated run's `config.extra`.
  - **Freeze and held-out:** `freeze --mode <mode>` writes `evals/reports/<dataset>/dev_selection.json` once: the mode, the exact threshold (JSON number and `float.hex()`), the full run configuration with model revisions, and the dev report's SHA-256. `heldout` takes no mode or threshold; it refuses unless the dev report hash, dataset version, held-out digest, settings and model revisions match the frozen file, and records the selection's hash in `heldout.done`. `ablate` and `freeze` refuse once `heldout.done` exists.
  - **Resumable ingestion:** patient ids, document ids and each completed stage are saved atomically as they happen. `already_parsed` / `already_indexed` are accepted only when the database confirms the stage (all page rows / chunks present). An unrecorded synthetic patient with the same name and date of birth stops ingestion rather than being duplicated.
  - **Question counts:** dev re-counted from dev ground truth only: **204** (value 105, lab 35, latest 16, unanswerable 16, leak 16, advice 8, injection 8). The held-out count (**92** above) was produced by the previous generator and is **not re-verified**.
  - `.gitignore` ignores `/evals/work/` (ingest state); `evals/reports/` stays trackable.
- **Update (2026-11-17, planned, ADR-036 A1/A3):** indexing will be gated. `index_document` will refuse a document without a succeeded `classify` run and a succeeded `identity` run, or whose `identity_check` is `mismatch`. The R1.8 runner's ingest gains classify and identity stages before indexing, records both rule versions, and stops on a blocked document. Retrieval, ranking, chunking, models and thresholds are unchanged. Not implemented yet; R1.8 is **Deferred — Docker unavailable** and nothing has been measured.

## ADR-036: Rule-based classification, identity outcomes and the identity gate on indexing
- **Status:** **Accepted** (owner, 2026-11-17: D1–D10, requirements R1–R3, and A1–A4 with the owner's clarifications). Planning only: nothing is implemented yet (Tasks 2.7–2.9).
  - *History:* A1–A4 were first recorded as Proposed earlier on 2026-11-17 and accepted later the same day with clarifications. The proposed A1 gated indexing on the identity run only, and the proposed A4 let a classification failure proceed to indexing; both are superseded by the accepted text below.
- **Context:** Phase 2 still lacks the rule-based classifier, the identity check and the document library UI. The `documents` table already has every column they need (`doc_type`, `doc_type_confidence`, `identity_check`, `processing_status` with `needs_review`), so **no migration** is needed. The synthetic lab reports print `Patient Name:` and `Age/Sex:` but no date of birth, and `lab_v0` holds lab reports only, with its held-out split frozen.
- **Decisions (owner-approved D1–D10):**
  - **D1, document types:** the `DocType` values stay as they are (`lab_report | prescription | discharge_summary | other | unknown`). ARCHITECTURE §5 listed `consultation_note` and `diagnostic_report`, which never existed in the schema; it is corrected. Per ADR-019, the Phase 2 classifier outputs only `lab_report`, `other` or `unknown`.
  - **D2, classification dataset:** a new synthetic dataset `data/synthetic/doctype_v0` (fictional prescriptions, discharge summaries, letters, invoices and harder lab-report layouts), split by patient, its held-out digest frozen before any rule is tuned. `lab_v0` is not changed.
  - **D3, rule score:** `doc_type_confidence` stores a deterministic **rule score** in [0, 1], not a calibrated probability. It is documented and displayed as such.
  - **D4, identity fields:** Phase 2 compares the name, and the date of birth only when the document prints it. No age-based check (document dates are not extracted until Phase 3).
  - **D5, strict name matching:** `match` only when the normalized full name-token sets are equal. A partial or initials-only match, several different names, no labelled name, a U+FFFD in the name field or an ambiguous date of birth gives `unknown`. No fuzzy or phonetic matching.
  - **D6, no override or recheck:** no "keep anyway" action, no move to another profile and no automatic re-check after a profile edit in this phase. The flag is shown only.
  - **D7, status:** a document stays `processing` until Phase 3 extraction; `processed` still needs chunking **and** extraction (ADR-032 unchanged). The UI labels it "parsed, awaiting extraction".
  - **D8, UI dependency:** `streamlit` is added, and the `demo_mode` setting introduced, only when Task 2.9b begins.
  - **D9, accuracy target:** classifier accuracy is reported first; the owner sets the phase-gate target after the first dev measurement. No target is assumed before then.
  - **D10:** this ADR.
- **Requirements (owner, 2026-11-17):**
  - **R1, identity gates indexing:**
    - `match`: indexing can proceed.
    - `unknown`: indexing can proceed; the uncertainty stays visible (badge in the UI, recorded outcome).
    - `mismatch`: the document is set to `needs_review` and is **not indexed**.
  - **R2, independent stages:** classification and identity verification are separate services. Both read the parsed pages; neither depends on the other's successful result. Orchestration handles every failure explicitly and never skips a stage silently. All database access stays in the patient-scoped repositories (ADR-016).
  - **R3, thin UI:** Streamlit files call service functions only: no SQL, classification rules, identity logic or orchestration in UI files.
- **Rule versioning:**
  - Each rule set has a version constant (`CLASSIFIER_RULES_VERSION`, `IDENTITY_RULES_VERSION`, starting at `"1"`). Any change to a pattern, weight or threshold bumps it.
  - Each run records its version in `pipeline_runs.component_versions` (stages `classify` and `identity`). The run row stores no document text, name or date of birth; outcomes are recorded as fixed reason codes.
  - Rules are tuned on dev data only; the held-out split is evaluated once, after the version is frozen.
- **Identity outcomes:** `match | mismatch | unknown`, stored in `documents.identity_check`. Only `mismatch` changes `processing_status` (to `needs_review`, in the same transaction as the run row). Classification never changes `processing_status`.
- **Accepted details (A1–A4, owner, 2026-11-17):**
  - **A1, indexing gate:** `identity_check` and `doc_type` both default to `unknown`, so the columns alone cannot tell "checked, uncertain" from "never checked". Without a migration, the gate uses the `pipeline_runs` rows. A document may be indexed only when **all** of these hold:
    - a **succeeded `classify` run** exists for it (so `doc_type = 'unknown'` is accepted only as a recorded classification result, never as the column default);
    - a **succeeded `identity` run** exists for it;
    - `identity_check <> 'mismatch'`. `identity_check = 'unknown'` is accepted, and the uncertainty is preserved: the column keeps `unknown`, the UI shows it, and evidence returned from such a document is labelled "identity not confirmed".
    - **Enforcement:** inside `index_document` (checked before chunking and embedding, and re-checked under the document lock in the same transaction that inserts the chunks), not only in a command. Refusal kinds: `not_classified`, `identity_not_checked`, `identity_mismatch`.
    - **Every indexing entry point uses this same gate:** `index_document` is the only caller of `insert_document_chunks`; the `python -m app index` command, `index_patient`, the pipeline service and the R1.8 runner all go through `index_document`.
    - **No silent exclusion:** `list_unindexed_documents` keeps returning gated documents; `index_patient` returns one explicit result per document (indexed or blocked with its kind) instead of stopping at, or hiding, a blocked one.
  - **A2, idempotency (explicit in the contracts):**
    - A stage with a succeeded run is not repeated automatically: the repository refuses with `already_classified` / `already_checked` (as `already_parsed` and `already_indexed` already do), and the services and the pipeline report it as `already_done`.
    - A failed run can be retried: a new run row is written; earlier failed runs stay as history.
    - Re-running a stage after a rule-version change is out of scope (like ADR-032 D6). A stored result keeps the rule version that produced it.
  - **A3, R1 compatibility:** the R1.8 runner's ingest becomes upload → parse → **classify → identity** → index, each a recorded, resumable stage. A gated (blocked) document **stops ingestion with an error** instead of being dropped, so the retrieval corpus is never silently reduced. The runner records the classifier and identity rule versions in the run configuration, and `heldout` validates them against `dev_selection.json` like the model revisions. Retrieval, ranking, chunking, models and thresholds are unchanged. R1.8 stays **Deferred — Docker unavailable** and is not run now.
  - **A4, stage results:** each stage of the document pipeline reports exactly one of `succeeded`, `already_done`, `failed (<kind>)` or `blocked (<reason>)`. No stage is skipped silently: every stage appears in the result.

    | Earlier outcome | classify | identity | index |
    |---|---|---|---|
    | parse failed | blocked (`not_parsed`) | blocked (`not_parsed`) | blocked (`not_parsed`) |
    | classify failed | — | runs | blocked (`not_classified`) |
    | identity failed | runs | — | blocked (`identity_not_checked`) |
    | identity = `mismatch` | runs | — | blocked (`identity_mismatch`) |
    | identity = `unknown` | runs | — | runs (uncertainty kept) |
    | classify result `unknown` or `other` | — | runs | runs |

    Classification and identity are independent of each other (R2): each runs whenever parsing succeeded. Only indexing depends on both. A stage `failed` or `blocked` never changes `processing_status`, except identity `mismatch` → `needs_review`.
- **Contract changes accepted (owner, 2026-11-17):**
  - **Bulk indexing:** `index_patient` returns an explicit result for every document, indexed or blocked with its reason. It never stops at the first blocked document and never omits one.
  - **Identity-unknown label:** evidence returned from a document with `identity_check = 'unknown'` carries an "identity not confirmed" label. The label is presentation only: it is kept separate from retrieval and ranking scores and never changes them. The document library shows the same uncertainty.
  - **R1.8 frozen configuration:** `dev_selection.json` includes the classifier and identity rule versions, and the held-out run validates both, refusing on any difference (as for model revisions).
- **Trade-offs:**
  - Strict name matching gives more `unknown` results (more user attention), but a false `match` is the costlier error for a caregiver managing family members with shared surnames.
  - Rule scores are explainable and free, but brittle on unseen layouts; the frozen held-out split measures this.
  - Gating on a run row rather than a new column keeps the schema unchanged, at the cost of two `EXISTS` subqueries (classify and identity runs) in the gate.
- **Out of scope:** LLM classification, Phase 6 document types, document-date extraction, user overrides of type or identity, fuzzy name matching, moving documents between patients, and behaviour of Phase 3 extraction on a `mismatch` document (decided in Phase 3).
- **Update (2026-11-17, Task 2.8 implemented):**
  - **Identity rules version `1`** (`app/ingestion/identity.py`), as built: labelled name fields only; Unicode NFKC and case folding for comparison only; punctuation splits tokens, apostrophes are dropped, honorifics (Mr, Mrs, Ms, Miss, Dr, Smt, Shri, Baby, Master, Mstr, Prof) are removed, token order is ignored. **Kumari, Kum and Sri are not treated as honorifics**: they are also given or middle names, and removing them made `Asha Kumari Fictional` match `Asha Fictional` during development. No accent folding.
  - **Unreadable names:** U+FFFD or any symbol, private-use or unassigned character in the name field is `name_unreadable` → `unknown`. Found before any evaluation by a parser experiment: Docling prints `■` for glyphs the PDF font cannot map, which would otherwise split a name into wrong tokens and give a false `mismatch`.
  - **Date of birth:** a clearly different DOB is `mismatch` even when the name matches (a namesake); a matching DOB alone never makes a `match`; age is never used.
  - **Stored per run:** `component_versions` holds `identity_rules_version` and `identity_reason_codes` (fixed lowercase codes, validated; never names or dates).
  - **Gate as built:** one rule (`_gate_refusal` in `app/records/chunks.py`) used by `get_index_target` (`IndexTarget.blocked_reason`, the pre-check before chunking and embedding) and by `insert_document_chunks` under the row lock in the insert transaction. Precedence: `identity_mismatch`, then `identity_not_checked`, then `not_classified`. `index_patient` returns an `IndexOutcome` per document (`indexed`, `blocked` with the reason, or `already_done`); other errors propagate. Evidence carries `identity_check`; `Evidence.identity_warning` is "identity not confirmed" unless the outcome is `match`, attached after ranking.
  - **R1.8 runner:** ingest stages `parsed → classified → identity_checked → indexed`, each confirmed in the database on resume; a blocked document stops ingestion. `RunConfig` gains `classifier_rules_version` and `identity_rules_version`; both are in `FROZEN_FIELDS`, so `dev_selection.json` records them and `heldout` refuses any difference. Not run (Docker unavailable).
  - **Known limit:** namesakes with no printed date of birth cannot be told apart and are `match`.
- **Update (2026-11-17, identity rules v2; supersedes the v1 details above where they differ):**
  - **Why:** a review of v1 found false-match and false-mismatch defects that `identity_v0` did not exercise. Examples: a newborn "Baby Priya Sharma" matched the mother "Priya Sharma"; a single-token profile matched "Mr. Sharma"; a profile "R. Lakshmi" gave `mismatch` against "Ramesh Lakshmi"; "Baby DOB" / "Father's DOB" were read as the patient's; "Dobriyal", "dobutamine" and "Age/DOB: 33 Y" gave `dob_unreadable`.
  - **Rules v2** (`IDENTITY_RULES_VERSION = "2"`). The policy is unchanged: strict matching, no fuzzy or phonetic matching, no accent folding, no age, a DOB alone never matches.
    - **Full names on both sides:** `match` and `mismatch` need a full name (at least two tokens, no single-letter initial) on both the document and the profile.
    - **Incomplete profile name:** a profile name that is not a full name, or that holds an unreadable character, gives the new code `profile_name_incomplete` → `unknown`. A clear DOB mismatch still gives `mismatch`.
    - **"Baby" is no longer an honorific;** it stays a name token.
    - **DOB labels must be whole words.**
    - **Another person's DOB is never the patient's.** A DOB label preceded by baby, father, mother, guardian, spouse and similar words (optionally possessive), or followed by `of <person>` / `(<person>)`, is skipped.
    - **Combined `Age/DOB` fields:** the date is read only at the start of the field or right after an age (`39 Y / 25-03-1985`). Otherwise the field is ignored, never read as a date.
    - **Format characters:** Unicode format characters (Cf: soft hyphen, ZWJ, ZWNJ, ZWSP, BOM) in a name field are `name_unreadable`.
    - **Patient's Name:** the label is read with a straight or curly apostrophe.
    - **Generic `Name:` is never identity evidence.** A bare `Name:` at the start of a line may name a doctor or a guardian, so it is ignored next to a patient label. Alone, it gives the new code `name_generic_label` → `unknown`.
  - **Reason codes added:** `name_generic_label`, `profile_name_incomplete`. Both fit the stored-code pattern (`[a-z][a-z_]{0,31}`), so there are no repository or schema changes.
  - **Dev-found defect, fixed before v2 was settled:** the first v2 draft read "a date anywhere" in an `Age/DOB` field. On the real Docling output of `identity_v1` dev this gave 2 own-profile false mismatches: Docling merged `Age/DOB: 38 Y` with the next printed line, `Report Date: 15/06/2024`. The rule was narrowed as described above and regression tests were added. The draft reports were superseded and are not in the repository; this note records them.
  - **New dataset `identity_v1`** (`data/synthetic/identity_v1`). It is a dataset version, not a rules version; `identity_v0`, its digest and its v1 reports are unchanged.
    - **Size and split:** 28 fictional patients, 48 documents. Split by patient: E01–E14 dev, H01–H14 held-out. Held-out digest `12ea2976…`, frozen at build time.
    - **Categories:** newborn/mother, other-person DOB, generic `Name:`, combined `Age/DOB`, initials in the profile, single-token names, near-miss spellings, token order, namesakes, spacing variants, curly apostrophes and format characters (stored as page text).
    - **Truth-based scoring:** each document records its true subject; own-profile outcomes are hand-written; swapped profiles must not be `match`. Pairs a strict policy cannot resolve are flagged by hand and reported apart. These are a misspelling equal to another patient's name, the same tokens in another order, and namesakes without a DOB.
  - **Measured (dev, rules v2, real Docling parse):**

    | Dataset | Pairs | False matches (distinguishable) | Own false mismatches | Agreement | Own profile m / u / x | Swapped m / u / x |
    |---|---|---|---|---|---|---|
    | `identity_v1` | 336 | **0** | **0** | 24/24 (hand-written) | 13 / 9 / 2 | 3 / 107 / 202 |
    | `identity_v0` | 640 | **0** | **0** | 640/640 (by construction) | 29 / 51 / 0 | 4 / 337 / 219 |

    - On `identity_v1`, the 2 own-profile mismatches are documented limits (a spelling variant and a spacing variant). The 3 swapped matches are the indistinguishable pairs flagged by hand. The unknown rate is 0.345.
    - Rules v1 on the same `identity_v1` dev cases, using a printed-lines stand-in rather than Docling, gave 1 false match (the mother's report against the newborn's profile) and 3 own false mismatches.
    - Held-out (both datasets) not parsed or evaluated.
  - **Remaining limits:** strict matching gives `mismatch` for a different spelling or spacing of the same person's name (`Aarthi`/`Aarthy`, `Sai Krishna`/`Saikrishna`). With no override (D6), such a document stays out of the index.
    - Namesakes without a printed DOB, and the same tokens in another order (`Mohan Arjun`/`Arjun Mohan`), are `match`.
    - A profile with an initial or a single token can never `match`, so that patient's documents are always `unknown`.
    - Only the word immediately before a DOB label is checked for another person. A generic `Name:` is recognized only at the start of a line, and Docling may merge it into the previous line (the result stays `unknown`).
    - A date of birth labelled `Child DOB` on a child's own record is skipped. This is safe, but it loses that DOB's evidence.
  - **Evaluation caveat:** the rules, the `identity_v1` cases (both splits) and their expectations were written by the same developer in the same task, after the defects were known. Perfect dev agreement therefore does not show generalization, and a held-out run on `identity_v1` would not be a blind test.
- **Update (2026-11-17, identity rules v2 frozen; held-out evaluated once; owner decisions):**
  - **Decisions (owner):**
    - Rules v2 is kept unchanged and frozen.
    - Fuzzy matching, phonetic matching, accent folding and an override mechanism are **not** introduced.
  - **Accepted limitations** of the local prototype, which must stay explicit:
    - Spelling and spacing variants of the same person's name may give `mismatch`; such a document is not indexed.
    - Token-order variations may give `match` (`Mohan Arjun` = `Arjun Mohan`).
    - Indistinguishable namesakes without a printed DOB may give `match`.
    - Single-token and initials-only profiles always give `unknown`.
    - There is no manual override and no automatic recheck (D6).
  - **Freeze:** `identity_selection.json` per dataset holds:
    - rules version `2`;
    - rules source SHA-256 `48c58bd9…`;
    - the dataset version and held-out digest;
    - the dev report SHA-256.

    `freeze` first re-scored dev with the current code and confirmed every pair equals the stored v2 dev report.
  - **One-time guard:** `heldout` refuses after `identity_heldout.done` exists, or when the rules version, rules source, dataset version, held-out digest or dev report differs from the selection. The marker is written **before** the evaluation, so a failed run is never silently retried (`evals/run_identity.py`, shared by `evals/run_identity_v1.py`).
  - **Held-out, run once each** (`identity_v1` first, then `identity_v0`; rules v2; Docling):

    | Dataset | Pairs | False matches (distinguishable) | Indistinguishable pairs matched | Own false mismatches | Documented-limit mismatches | Own m / u / x | Swapped m / u / x |
    |---|---|---|---|---|---|---|---|
    | `identity_v1` | 336 | **0** | 3 of 3 | **0** | 2 | 13 / 9 / 2 | 3 / 99 / 210 |
    | `identity_v0` | 160 | **0** | 4 of 4 | **0** | — | 15 / 25 / 0 | 4 / 73 / 43 |

    - On `identity_v1`, own-profile agreement with the hand-written expectation was 24/24. The 2 documented-limit mismatches are a spelling variant and a spacing variant. The 3 indistinguishable matches are a misspelling equal to another patient's name, the same tokens in another order, and namesakes without a DOB.
    - On `identity_v0`, agreement with the by-construction expectation was 160/160. The 4 swapped matches are the I09/I10 namesake pair without a printed DOB.
    - No rule was changed after either held-out run.
  - **Not a blind or independent validation:** both held-out reports state this.
- **Update (2026-11-17, Task 2.9a implemented: orchestration contract and service boundaries):**
  - **`app/services/document_pipeline.py`:**
    - `run_document_pipeline(settings, parser, embedder, patient_id, document_id, *, on_stage=None)` → `DocumentPipelineResult`.
    - `run_patient_pipeline(...)` → one `DocumentPipelineResult` per document of the patient, oldest first.
    - Each `DocumentPipelineResult` holds four `StageResult`s, always in the order parse, classify, identity, index. A `StageResult` has a status (`succeeded`, `already_done`, `blocked` or `failed`), a fixed `reason` kind, an `error_class` (failed only), the stage service's result as `detail` (succeeded only), and `duration_ms`.
  - **Each stage runs through its existing service:** `process_document`, `classify_document`, `check_document_identity`, `index_document`.
    - The pipeline decides no outcome itself. It attempts every stage once per call and maps each service's result or error kind to a status.
    - "Blocked" comes from the services themselves: `not_parsed` from classify, identity and index; `already_running` from the parse claim; the gate refusals from `index_document`.
    - So the single indexing gate, including its re-check under the document row lock in the insert transaction, stays the only rule. Classification and identity never gate each other (R2), and a recorded `unknown` type or identity is indexed while a column default without a run is refused (A1).
  - **Failures are reported, not raised.** A stage error becomes `failed` with the error's fixed kind (`database_error` or `unexpected` when it has none) and class name, never its message. The remaining stages and documents still run. `KeyboardInterrupt` and `SystemExit` propagate.
  - **Raised before any stage:** `document_not_found` for a document that is not the patient's, and `patient_not_found`.
  - **No retries:** a stage is attempted at most once per call. A failed stage is attempted again only when the caller runs the pipeline again (A2). Stale parse claims are not recovered by the pipeline.
  - **No new status semantics:** `processing_status` changes only where the stage services already change it. A parsed or indexed document stays `processing` (ADR-032, D7).
  - **Progress:** `on_stage(document_id, stage_result)` is called after each stage, so Task 2.9b can show progress without the service knowing about Streamlit.
  - **`app/services/library.py`:** thin, patient-scoped wrappers, so `app/ui/` imports services only (R3):
    - patients: create, list, get, update (with `KEEP`), delete;
    - documents: list, get, rename, delete.

    They add no behaviour; deleting a document still leaves its files, as before (Task 1.8e).
  - **`DocumentSummary` gains read-only `doc_type_confidence` and `identity_check`,** mapped from existing columns. No new SQL, migration or dependency.
  - **Unchanged:** `index_patient` (the index-only bulk path of `python -m app index`) and the R1.8 runner's own resumable ingest, which stops on a blocked document (A3).
- **Update (2026-11-17, Task 2.9b implemented: local Streamlit UI; D8 applied):**
  - **Dependency (D8):** `streamlit` (1.67.0 locked). The lock gained only `streamlit` and its 10 dependencies; no existing locked version changed.
  - **Setting (ADR-014):** `demo_mode` (`MEDIMIND_DEMO_MODE`, default `false`, safe to display).
  - **`.streamlit/config.toml`:**
    - the server listens on `127.0.0.1` only;
    - usage statistics are off;
    - uploads are limited to 20 MB;
    - `client.showErrorDetails = "none"`, so no exception text ever reaches the browser.
  - **UI (`app/ui/`):**
    - `streamlit_app.py` is the entry point, `views.py` holds the pages (Patients, Documents, Evidence search), and `texts.py` maps fixed codes to plain language.
    - The UI imports `app.services` (and Streamlit and the standard library) only; a test enforces this (R3).
    - Every action is one service call. Errors are shown by fixed kind (`document_pipeline.error_kind`) and logged as kind and class name only.
    - `st.session_state` holds identifiers and the last pipeline results (identifiers, kinds, counts) only.
  - **Service additions (no new SQL):**
    - `library.list_patients` and `library.get_patient` take `synthetic_only`. In demo mode a real patient is `patient_not_found`.
    - `library.get_document_state` reads the indexing gate's own `blocked_reason` through `get_index_target`, plus page and chunk counts.
    - `app/services/ui_runtime.py` builds the settings, logging, the Docling parser, the models and the retrieval configuration (as `python -m app ask` does), and offers only configured retrieval modes. Without an approved embedding revision, its embedder stand-in fails the index stage with `revision_not_configured`, while parse, classify and identity still run.
    - `document_pipeline.error_kind` became public.
  - **Demo mode as built:**
    - synthetic patients only;
    - patients created synthetic;
    - upload disabled;
    - a banner.

    Nothing is loaded or created automatically in either mode. ADR-014's seeded demo database (precomputed facts and embeddings) is **not implemented**; it stays planned for the portfolio phase.
  - **Stale parse:** a document claimed for parsing with no stored pages is shown as "parse not finished", explaining that it may be running elsewhere or was interrupted and that automatic recovery is not available. The UI offers no recovery and no retry (A2).
  - **Not offered:** identity override, a "keep anyway" action, moving documents, automatic re-check, background processing, authentication, and LLM answers. Rules, generators, documents and expectations come from one developer, and each held-out split shares templates and categories with its dev split. The numbers are a regression check of the frozen rules on unseen synthetic patients, not evidence of accuracy on real documents.
- **Update (2026-11-17, Task 2.7 implemented):**
  - **Rule versions:** version `1` (untuned) was measured once on dev. Version `2` changed two patterns after reviewing the version-1 dev errors only: units are counted per occurrence (Docling merges printed lines), and an inline range such as `14.7 gm/dL (13.0 - 17.0)` counts as a reference range. Both dev reports are kept in `evals/reports/doctype_v0/`. Version `2` is current, **not frozen**; the held-out split has not been parsed or evaluated.
  - **Decision precedence (as built):** no text → `unknown`; strong lab evidence (a results-table header or reference range, plus enough lab cues) without strong other-document evidence → `lab_report`; the reverse → `other`; strong on both sides → `unknown` (`doc.conflict`, never silently resolved); neither → `unknown`.
  - **Failed stage:** an unexpected classification failure is recorded as a `failed` `classify` run (fixed error kind and class name only) and the original error is re-raised; `not_parsed`, `already_classified` and `document_not_found` write no run. Rule ids are returned to the caller but not stored; the run stores the rules version only.

## Open questions linked to ADRs
- **Q12** (from ADR-025): Should an ambiguous date be auto-accepted when the *same document* contains an unambiguous date confirming DD/MM order (for example `25/03/2024`)? This would reduce review work, but adds an inference rule. **Default: no. Always flag.**
