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
  - Each is added when a task first needs it: SQLAlchemy 2.1.8 and psycopg[binary] 3.3.9 were added in Task 1.6 (2026-11-13). Alembic is added in Task 1.7 (decision D1).
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
- **Enforcement started (Task 1.6, 2026-11-13; owner decision D5):** `app/records/` is the only database access path. `session_scope()` exists for the records layer only. An architecture test (`tests/test_architecture.py`) fails if code outside `app/records/` imports SQLAlchemy or psycopg, or imports anything from `app.records.db` except the health probe and the error-sanitizing helpers.

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

## Open questions linked to ADRs
- **Q12** (from ADR-025): Should an ambiguous date be auto-accepted when the *same document* contains an unambiguous date confirming DD/MM order (for example `25/03/2024`)? This would reduce review work, but adds an inference rule. **Default: no. Always flag.**
