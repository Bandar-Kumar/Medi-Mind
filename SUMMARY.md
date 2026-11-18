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

## 2026-11-18 (evening): Phase 5 remediation (ADR-041): citation precision 0.605 → 1.000 on a new held-out set; rules-based routing confirmed

**Phase:** 5, focused remediation (owner's instruction)

### Done
- Diagnosed `copilot_v0` precision loss: unknown everyday names widened to "all tests".
- Added patient vocabulary, unrecognised-name abstention (never "all tests"), partial answers for known + unknown, passage relevance gate.
- Calibrated a lexical threshold on dev only and rejected it (overlapping scores).
- Built new sources `lab_v1`, `longitudinal_v1` and the `copilot_v1` set (frozen before remediation code); dev, freeze, held-out once.
- Held-out (167 questions, new patients): every target met; citation precision 1.000; 0 unsupported claims, leaks, exposures; 0/320 attacks accepted.
- Tests: default 1919 passed, 282 deselected; integration 221; real-model 3; Ruff clean. Docs: ADR-041, ADR-007/024 notes, PHASE_5_PLAN §12, ARCHITECTURE, USER_FLOWS, IMPLEMENTATION_PLAN, ROADMAP, README, PROGRESS.

### Decisions
- Routing stays rules-based (recorded decision). No lexical score threshold. LLM provider evaluation stays deferred (no model, no credentials).

### Learned
- The precision problem was a fallback design choice, not retrieval: abstaining on unknown names fixed it.
- A ts_rank threshold cannot separate answerable from unanswerable here; term-based relevance can.

### Issues
- Same-author vocabulary and held-out phrasings: coverage of real patient language is not measured.
- Two held-out invocations aborted in a harness bug before any metric existed (recorded).

### Next
- Owner review of ADR-040/041; real or independently written patient phrasings to measure vocabulary coverage; LLM provider evaluation when access is approved.

## 2026-11-18 (later): Phase 5 evidence-grounded copilot implemented (ADR-040); R1.8 retrieval evaluation completed; LLM provider evaluation blocked

**Phase:** 5, advanced hybrid RAG (one-pass implementation on the owner's instruction; awaiting review)

### Done
- **R1.8 completed** with Docker running: dev ingest, ablation, `lexical` frozen, held-out ingest, one held-out run (MRR 0.891, Recall@5 0.971, leak 0, abstention recall 0.375).
- Built the copilot: `app/llm/` (interface, mock, OpenAI-compatible adapter), `app/answering/` (router, guardrails, composer, LLM generator, citation validator), `copilot_tools.py`, `copilot.py`, `stored_chunks`, Copilot page, settings.
- Built and froze `copilot_v0` (231 dev / 103 held-out questions) and `evals/run_copilot.py` with an attack suite; dev runs, freeze, held-out once.
- Tests: 177 new unit/UI tests, 7 integration tests; default suite 1894 passed, 282 deselected (0 failed, 0 skipped); integration 221 passed; real-model 3 passed; Ruff clean.
- Docs: PHASE_5_PLAN.md (new), DECISIONS (ADR-040; notes on ADR-007, 011, 024), ARCHITECTURE, IMPLEMENTATION_PLAN, ROADMAP, USER_FLOWS, DATABASE_DESIGN, README, PROGRESS.

### Decisions
- Retrieval: lexical over hybrid/rerank (no measured ranking gain; ~57× latency). Abstention trade-off accepted pending owner review.
- Deterministic router and rule-chosen tools (deviation from ARCHITECTURE §9.1 / ADR-007, flagged); no LangChain; extractive generator as default until ADR-024 is resolved; no conversation history.
- Fail-closed validation of every supplied source, added after the eval exposed a display leak path.

### Learned
- On exact-code lab questions, full-text search beat dense embeddings by a wide margin and matched a cross-encoder.
- An adversarial eval found a bug unit tests had not: validating only *cited* sources still let an uncited foreign source reach the screen.
- Router vocabulary, not grounding, limits answer quality: held-out citation precision 0.605 because "A1c"/"fasting sugar" are not reference aliases.

### Issues
- LLM provider evaluation blocked (no credentials, no local model server).
- Citation-precision target missed on dev (0.827) and held-out (0.605).
- `copilot_v0` held-out is spent; further copilot work needs `copilot_v1`.

### Next
- Owner review of Phase 5 and its deviations; provide LLM access or approve a local model; patient-vocabulary table + abstain-on-unknown-test with `copilot_v1`; portfolio checkpoint (demo seed, video).

## 2026-11-18: Q12 closed (ambiguous-date review retained); Phase 3 PostgreSQL acceptance passed (205 integration tests, end-to-end UI run, 6 defects fixed); Phase 4 longitudinal intelligence implemented (ADR-039; all targets met on dev and held-out)

*The entry starts with the Phase 3 acceptance work, then Phase 4.*

**Phase:** 3, structured lab extraction (acceptance); then 4, longitudinal intelligence

### Done
- Closed Q12 with the owner's decision: keep `ambiguous_date` review. The dev rate of 129 of 394 facts (32.7%) was checked against the frozen `extraction_dev_lab-rules-2` report. Recorded in DECISIONS.md (Q12, ADR-025, ADR-037), PROGRESS.md, ARCHITECTURE.md and PHASE_3_PLAN.md.
- First session: Docker checked once, unavailable; database acceptance stopped there, as instructed.
- Later, with Docker running:
  - **Migration:** 0001 → 0004 applied to `medimind`; the schema matches the models (0 autogenerate differences, 173/173 columns).
  - **Seeding:** reference seeding is idempotent (31/89/9 rows, then 0 added, identical hashes).
  - **Integration tests:** 205 passed, 0 failed, 0 skipped (first run: 41 failed).
- **End to end** through the Streamlit script (`AppTest`) with fictional dev documents, persisted state checked by SQL:
  - five stages to `processed`;
  - exact spans and run links for all 41 facts;
  - mismatch blocked, `unknown` labelled;
  - review history and the full conflict lifecycle;
  - idempotent repeats; cross-patient refusal.
- **Regression:** default suite 1666 passed; Ruff and `git diff --check` clean; frozen artifacts byte-identical.
- **Phase 4** (on the owner's instruction; [docs/PHASE_4_PLAN.md](docs/PHASE_4_PLAN.md), ADR-039):
  - **Implemented:** timeline, lab trends, comparison of two dates, cross-document duplicates (derived, exact or possible, with reasons) and the presentation of stored conflicts; Overview, Timeline and Lab trends pages. No migration; no LLM.
  - **Dataset** `longitudinal_v0`: 16 fictional scenario patients covering 14 cases, plus a 1,050-result performance patient. Held-out digest frozen at build.
  - **Measured** (synthetic; not blind; no clinical accuracy claimed): every pre-registered target met on dev and on held-out (run once):
    - timeline, ordering, trend values and differences, conflicts and provenance at 1.0;
    - duplicates 1.0 / 1.0;
    - 0 leaks.

    Timeline page median: 291 ms at 1,050 results.
  - **Tests:** default suite 1717 passed; integration 214 passed, 0 failed, 0 skipped; Docling and models 64 passed.
  - **Fixed:** two Windows concurrency defects in upload storage, found by an intermittently failing existing test (ADR-033 update).

### Decisions
- Q12: no same-document auto-accept rule. A confirming date elsewhere is an inference; a silent day/month swap is worse than one review action.
- Fixes stayed inside the approved design: no schema shape, rule or evaluation changed.
- Phase 4: only usable facts appear (ADR-023); duplicates are derived, never stored; one trend series per unit; no migration (ADR-039).

### Learned
- The measured rate (32.7%) is below the pre-measurement estimate (about 38%).
- Code that only ever ran against stand-ins hid six defects that a real PostgreSQL found in minutes:
  - Alembic naming conventions re-apply to plain names in `drop_constraint`.
  - `text()` treats `:r` inside a regex as a bind parameter.
  - SQLAlchemy writes `None` to JSONB as JSON `'null'`.
  - RESTRICT deletes raise `23001`, not `23503`.
  - `mkstemp` returns absolute paths, which broke the check against a relative data dir.
  - A catalog comparison between models and migrations cannot catch a bug both share.
- Phase 4:
  - Pre-registering targets and freezing held-out before the first run made a first-run 1.0 a statement about the specification, not tuning. A negative control (duplicates disabled) proved the scoring can fail.
  - On Windows, `Path.resolve()` can return `\\?\C:\...` while a file is busy, and a read-only attribute on one hard link protects them all.

### Issues / blockers
- None blocking. Not seen in the UI run: an `unknown`-identity document with extracted facts. The UI was driven by `AppTest`, not a browser; a manual browser pass is still worthwhile.
- R1.8 (retrieval evaluation) is still deferred.
- Phase 3 and Phase 4 gates both await the owner's approval. Phase 4 started on the owner's instruction, without a recorded Phase 3 gate approval.

### Next
- Owner: review Phase 3 and Phase 4 (with today's fixes), decide both phase gates and commit.
- R1.8 now that the database runs; Phase 5 only after approval.

---

## 2026-11-17: Phase 2 completed (Tasks 2.7–2.9b, committed as `f989b98`); Phase 3 planned, approved and implemented (structured lab extraction); R1.8 deferred (Docker unavailable)

*The entry starts with the day's Phase 2 work (its original headline: "R1.8 deferred; Phase 2 remaining-work planning"), then the Phase 3 work, then the end-of-day wrap-up.*

**Phase:** 2, document intelligence

### Done
- **R1.8 dev evaluation attempted, blocked at step 1.** `docker info` failed: `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine ... The system cannot find the file specified.` Per the run protocol, the run stopped there. `docker compose up -d`, `app health`, `ingest --split dev` and `ablate` were **not run**.
- **R1.8 recorded as Deferred — Docker unavailable (2026-11-17)** in `docs/PROGRESS.md`, at the owner's request. R1.8 is **not complete**.
- **Phase 2 remaining work planned and approved:** Tasks 2.7 (rule-based classification), 2.8 (identity verification and the indexing gate) and 2.9 (2.9a pipeline orchestration and service wrappers; 2.9b thin Streamlit UI, including Task 1.11). Recorded in ADR-036 (Accepted) and IMPLEMENTATION_PLAN; none of them started.
- **Documentation consistency pass:**
  - ARCHITECTURE: §5 document-type list corrected (`consultation_note` and `diagnostic_report` never existed in the schema); §4.1 diagram now draws classification and identity as independent branches of parsing, both required before indexing.
  - USER_FLOWS F2: "move to another profile" and "keep anyway" removed (kept as a dated note); identity `unknown` flow added.
  - IMPLEMENTATION_PLAN: stale R1.5 row corrected (revisions were approved and verified on 2026-11-16); R1.8 row marks the deferral and the planned runner change.
  - ADR-035 gained a dated note about the planned indexing gate.
- Planning and documentation only: no application code, tests, dependencies or migrations changed; nothing run.
- **Task 2.7 implemented (later the same day): rule-based document classification.**
  - Pure rules (`app/ingestion/classification.py`, version `2`), patient-scoped persistence (`list_document_pages`, `record_classification`, `record_classification_failure`), `classify_document` service, `python -m app classify`. No migration, no new dependency.
  - New dataset `data/synthetic/doctype_v0`: 84 fictional documents, 12 patients, 8 dev / 4 held-out by patient; held-out digest `8d71ffb5…` frozen at build time, before any rule was written. `lab_v0` untouched.
  - **Measured (dev, 56 documents):** rules v1 (untuned) accuracy 0.9107, lab precision 1.0000, lab recall 0.7500, unknown rate 0.0893; rules v2 accuracy 0.9464, lab precision 1.0000, lab recall 0.8750, unknown rate 0.0536. No document was given the wrong type; every error is an `unknown`.
  - **Tests:** 122 new or changed unit tests passed; default suite 1128 passed, 230 deselected; 8 PostgreSQL integration tests written, **skipped** (Docker unavailable). ~~Not frozen; held-out not parsed or evaluated.~~
  - **Owner review, freeze and held-out (later the same day):** targets set (accuracy >= 0.90, lab precision >= 0.95, lab recall >= 0.85, unknown rate <= 0.10), all met on dev. The current rules re-scored dev identically from cache; dataset and digest verified; frozen (rules v2, dev report SHA-256 `133b8d72…`). **Held-out, run once (28 documents):** accuracy 0.9643, lab precision 1.0000, lab recall 1.0000, unknown rate 0.0357; the one `unknown` is a discharge summary with a lab table (conflict). Rules unchanged afterwards. Optimistic for real documents: same templates as dev, written by the same developer.
- **Split check:** the "other" dev/held-out assignment seen earlier was `lab_v0`'s (seed 20261116), printed by a Docling check during Task 2.7; every `doctype_v0` source agrees. No inconsistency.
- **Task 2.8 implemented: patient identity verification and the indexing gate.**
  - Pure rules (`app/ingestion/identity.py`, version `1`), `get_patient`, `record_identity_check` (mismatch → `needs_review` in the same transaction), `record_identity_failure`, `check_document_identity` service, `python -m app identity`.
  - Gate: a succeeded `classify` run, a succeeded `identity` run, and no `mismatch`; pre-checked in `index_document`, re-checked under the row lock in `insert_document_chunks`; `index_patient` reports every document (indexed / blocked with reason / already done); "identity not confirmed" label on evidence, never in scores.
  - R1.8 runner: classify and identity stages before indexing, blocked documents stop ingestion, both rule versions frozen and validated.
  - Dataset `identity_v0` (120 documents, families and namesakes; held-out digest `1b4ec4a5…` frozen at build time). **Measured (dev, 640 pairs):** 0 false matches on distinguishable swapped profiles; own profile 29 match / 51 unknown / 0 mismatch; the 4 swapped matches are namesake pairs with no printed DOB (indistinguishable, documented). Held-out not evaluated.
  - **Tests:** default suite 1272 passed, 242 deselected; 32 PostgreSQL integration tests (12 new identity, 12 updated chunk, 8 classification) **skipped**: Docker unavailable.
- **Task 2.8 identity rules v2 (after a review of v1).**
  - **Review findings (v1):** the `identity_v0` dev numbers were correct; the 4 swapped matches were the namesake pair without a DOB. The checker, however, had untested false-match and false-mismatch paths: "Baby" was stripped as an honorific, a single-token profile could match, the "no initials" rule was checked on the document only, DOB labels had no word boundary, other people's DOBs were read as the patient's, Cf characters split tokens, and a curly apostrophe or a generic `Name:` was mishandled.
  - **Rules v2:** in `app/ingestion/identity.py` only. Two new reason codes (`name_generic_label`, `profile_name_incomplete`); no gate, repository, schema or dependency change.
  - **New dataset `identity_v1`:** 28 patients, 48 documents. Hand-written truth-based expectations; held-out digest `12ea2976…` frozen at build time. Generator `evals/synthetic/identity_v1.py`, runner `evals/run_identity_v1.py`. `identity_v0` and its v1 reports are unchanged (hash-verified).
  - **Measured (dev, rules v2, Docling):**
    - `identity_v1` (336 pairs): 0 false matches, 0 own false mismatches, plus 2 documented-limit mismatches (spelling, spacing); 3 indistinguishable swapped matches; unknown rate 0.345.
    - `identity_v0` (640 pairs): identical to v1, 0 / 0.
    - Rules v1 on the same `identity_v1` dev cases (printed-lines stand-in, not Docling): 1 false match and 3 own false mismatches.
  - **Dev-found defect:** the first v2 draft read any date in an `Age/DOB` field. Docling had merged `Report Date: …` into that field, giving 2 own false mismatches. The rule was narrowed before v2 was settled; the draft reports were moved out of the repository and the draft is recorded in ADR-036.
  - **Tests:** identity unit tests 126 (60 before), `identity_v1` dataset tests 9 (new); default suite 1347 passed, 242 deselected; Ruff and format clean. The identity and chunk PostgreSQL integration tests were collected once: 24 skipped (no database; Docker not retried).
  - ~~Not frozen; neither held-out split parsed or evaluated.~~ Frozen and held-out evaluated later the same day (next item).
- **Task 2.8 identity rules v2 frozen; held-out evaluated once each.**
  - **Protocol:** `freeze` and a one-time `heldout` were added to the identity runners.
    - `freeze` re-scored dev and matched both dev reports pair-for-pair.
    - The selection records the rules version, the rules source SHA-256 `48c58bd9…`, the dataset, the digest and the dev report hash.
    - The `.done` marker is written before evaluating. Both guards now refuse a second run.
  - **Measured (held-out, not blind):**
    - `identity_v1` (336 pairs): 0 false matches, 3/3 indistinguishable pairs matched, 0 own false mismatches, 2 documented-limit mismatches; own 13 / 9 / 2, swapped 3 / 99 / 210.
    - `identity_v0` (160 pairs): 0 false matches, 4/4 namesake pairs without DOB matched, 0 own false mismatches; own 15 / 25 / 0, swapped 4 / 73 / 43; 160/160 by construction.
  - **Unchanged (hash-verified):** identity code, datasets, generators, earlier reports, and classification v2 with its artifacts. No rule changed after held-out.
  - **Tests:** 7 new protocol test functions (11 cases) passed. The focused identity, classification, evaluation and retrieval-service run gave 337 passed. Ruff and format are clean, and `git diff --check` is clean. PostgreSQL integration is still pending (Docker not retried).

- **Task 2.9a implemented: pipeline orchestration and service wrappers.**
  - **Pipeline:** `app/services/document_pipeline.py` runs parse → classify → identity → index through the existing services, for one document or every document of a patient (oldest first).
    - Each document gets four stage results: `succeeded`, `already_done`, `blocked` or `failed`, with a fixed reason kind.
    - Errors are reported per stage; the remaining stages and documents still run.
  - **Library:** `app/services/library.py` holds the patient-scoped wrappers for the UI.
  - **`DocumentSummary`** gains read-only `doc_type_confidence` and `identity_check`. No migration or dependency.
  - **Tests:**
    - new and updated: 132 passed;
    - regressions: 448 + 71 passed;
    - repository integration: 76 skipped (no database).
- ~~Task 2.9b not started.~~ Implemented later the same day (next item).
- **Task 2.9b implemented: local Streamlit UI.**
  - **What it covers:** Patients, Documents (with upload, processing and per-stage results) and Evidence search pages. Identity and type warnings, the unfinished-parse notice, and delete confirmations that say files stay on disk.
  - **Thin by design:** the UI imports `app.services` only, and a test enforces this.
  - **Additions:**
    - `streamlit` 1.67.0 (the lock only gained packages);
    - the `demo_mode` setting;
    - `.streamlit/config.toml` (localhost only, no usage statistics, no error details);
    - `ui_runtime` and `library.get_document_state`.
  - **Tests and checks:**
    - 40 new UI tests passed, and the regression run (166 in total) passed;
    - against the real services without a database, the UI shows the fixed database message only;
    - the server started on 127.0.0.1 and answered its health check.
  - **Pending (Docker unavailable):** PostgreSQL verification and the end-to-end acceptance run.

### Decisions
- **Task 2.9b:**
  - Demo mode means synthetic patients only, patients created synthetic and no upload. There is no automatic loading, and ADR-014's seeded demo database is deferred.
  - The entry file is `streamlit_app.py`, not the planned `app.py`, to avoid shadowing the `app` package.
  - The UI explains a stale parse but offers no recovery or retry.
  - Without model revisions, the index stage reports `revision_not_configured`, and search offers `lexical` only.
- **Task 2.9a:**
  - The pipeline calls every stage and lets each service and the single indexing gate decide blocked or already done. It does not pre-check, so no gate logic is duplicated.
  - Stage errors are reported with kind and class only, never the message.
  - No automatic retry and no stale-claim recovery.
  - `index_patient` and the R1.8 runner's resumable ingest stay separate (different contracts).
- **Task 2.8 freeze (owner):**
  - Identity rules v2 is frozen unchanged.
  - Accepted prototype limitations:
    - spelling and spacing variants may give `mismatch`;
    - token-order variations and namesakes without a DOB may give `match`;
    - single-token and initials-only profiles stay `unknown`;
    - there is no override and no recheck.
  - No fuzzy matching, phonetic matching or accent folding.
  - Both held-out runs are labelled not blind.
- **Task 2.8 rules v2:**
  - A full name is needed on both sides for `match` or `mismatch`; an incomplete profile name gives `unknown`.
  - "Baby" is a name token.
  - A generic `Name:` is never identity evidence (it may be a doctor or a guardian).
  - Other people's DOB labels are skipped.
  - Strict matching is kept, so spelling and spacing variants of the same person are still `mismatch` (a documented limit; no override, D6).
  - `identity_v1` is scored against the truth (whose document it is), not against reason codes.
- **Task 2.8:** Kumari, Kum and Sri are not stripped as honorifics (they are also names; stripping made a different person match); any symbol or U+FFFD in a name is unreadable → `unknown` (Docling prints `■` for unmapped glyphs); a clearly different DOB is a mismatch even for the same name; identity reason codes are stored in the run's `component_versions`.
- **Task 2.7:** version 1 was measured once; version 2 changed two patterns after reviewing version-1 dev errors only (unit occurrences instead of lines; inline `value unit (low - high)` ranges). Tuning stopped at v2 so the dev set is not fitted further. A conflict between strong lab and strong other-document evidence stays `unknown`.
- **R1.8 deferred (owner):** move on to Phase 2 planning instead of waiting for Docker. What this leaves open:
  - The evaluation runner and its offline tests are implemented, but dev ingestion and the ablation were not executed.
  - Recall@k, MRR, nDCG, leakage, abstention, refusal rates and latency remain **unmeasured**.
  - No calibrated threshold or frozen configuration (`dev_selection.json`) exists, so the held-out evaluation remains blocked.
  - The PostgreSQL chunk and isolation integration tests remain unrun.
  - No measured evidence supports choosing dense-only, hybrid or hybrid plus reranking. The architecture rule still applies (ARCHITECTURE §9.4): hybrid and reranking stay only with a measured improvement.
- **ADR-036 (owner-approved):**
  - **D1–D10:** keep the existing `DocType` values; the Phase 2 classifier outputs `lab_report`, `other` or `unknown`; new `doctype_v0` dataset with a frozen held-out split (`lab_v0` unchanged); `doc_type_confidence` is a rule score, not a probability; identity compares the name, and the date of birth only when printed; strict name matching (partial → `unknown`); no override or recheck; documents stay `processing` until Phase 3; `streamlit` and `demo_mode` arrive only with Task 2.9b; the accuracy target is set after the first dev measurement.
  - **R1–R3:** `mismatch` → `needs_review` and not indexed, `unknown` indexed with the uncertainty visible; classification and identity are independent services; the UI calls services only.
  - **A1, indexing gate:** needs a succeeded `classify` run and a succeeded `identity` run, and `identity_check <> 'mismatch'`; enforced inside `index_document`, so every indexing path (command, bulk, pipeline, R1.8 runner) uses it; blocked documents are reported, never dropped.
  - **A2:** successful stages are not repeated, failed ones can be retried; no reprocessing after a rule-version change.
  - **A3:** the R1.8 runner will run classify and identity before indexing, stop on a blocked document and record both rule versions. Not run now.
  - **A4:** every stage reports `succeeded`, `already_done`, `failed` or `blocked`, with a table of which stages still run after each failure.
- **No migration:** both "not yet checked" ambiguities (`identity_check` and `doc_type` default to `unknown`) are resolved through `pipeline_runs` rows.

### Learned
- A column default that is also a valid result (`unknown`) cannot record whether a stage ran; here the stage's run row carries that fact, which avoids a migration.
- A gate on a bulk operation must report what it refused: filtering gated documents out of `list_unindexed_documents` would have shrunk the retrieval corpus silently.
- **(2.7)** Docling merges several printed lines into one text line (`Haemoglobin : 14.7 gm/dL (13.0 - 17.0) Total Leucocyte Count : …`), so line-based counting under-counts; rules should count occurrences.
- **(2.8 v2)** A printed-lines stand-in parser hid a defect that the real Docling output exposed (merged lines put a report date inside an `Age/DOB` field). Dev runs must go through the real parser before a rule version is settled.
- **(2.8 v2)** A dataset whose variants each map to one reason code mostly confirms its own construction. Truth labels plus hand-flagged "indistinguishable" pairs measure safety more honestly.
- **(2.7)** A repository test that checks every statement for `patient_id` caught a reused helper (`_has_pages`) that filters by document only; the new code uses a patient-scoped check.

### Issues / blockers
- Docker Desktop is still not running; every database-dependent verification from 2026-11-16 is still outstanding.

### Next
1. ~~Owner reviews the Phase 2 task breakdown (classifier, identity check, document library UI).~~ Done: approved (ADR-036).
2. When Docker Desktop is running: resume R1.8 (`docker compose up -d`, `app health`, `ingest --split dev`, `ablate`) and the integration tests. *Once Task 2.8 lands, R1.8 ingest also runs classify and identity before indexing (ADR-036 A3).*
3. ~~Implement Task 2.7 (rule-based classification)~~ Done (rules v2, dev measured). Owner: review the dev report, set the accuracy target, then `freeze` and the single `heldout` run.
4. ~~Run the classification integration tests when Docker is running; then Task 2.8~~ → 2.9a → 2.9b. Task 2.8 done (below); integration tests still pending.
5. When Docker runs: classification, identity and chunk integration tests; then R1.8. Owner reviews the identity dev report before Task 2.9a.

### Correction (2026-11-17, documentation consistency at the end of the day)
- **The headline above covers only the start of the day.** The same day also implemented:
  - Task 2.7 (frozen, held-out run once);
  - Task 2.8 (rules v1, then v2; frozen; both identity held-out splits run once, not blind);
  - Task 2.9a;
  - Task 2.9b.

  All of these are recorded under *Done* above.
- **The *Next* list above is partly outdated:**
  - Item 2: Task 2.8 has landed, and the R1.8 runner already runs classify and identity before indexing. This is unit-tested and not run.
  - Item 3: done (targets set, frozen, held-out run once).
  - Item 4: Tasks 2.8, 2.9a and 2.9b are done.
  - Item 5: the identity review is done (v2 frozen, limitations accepted). The integration tests are still pending.
- **Issues / blockers, made precise:** Docker is still unavailable. So the PostgreSQL integration verification for Task 1.8, R1, 2.7, 2.8 and 2.9a, and the 2.9b end-to-end acceptance run, are still outstanding. They are not just the 2026-11-16 ones.
- **Current open items (nothing else is implied complete):**
  1. PostgreSQL integration verification and the UI acceptance run, when Docker is available.
  2. R1.8, still deferred: **no retrieval metric has been measured.**
  3. Owner review and commit of the uncommitted work (Task 1.8 onward).
- **README model setup corrected:** the BGE and MiniLM revisions were already approved, pinned, cached and CPU-verified on 2026-11-16 (R1.5). The README no longer describes them as awaiting approval.

### Phase 3 (later the same day): structured lab extraction


#### Done
- **Planning:** `docs/PHASE_3_PLAN.md` written after three owner review rounds and approved once (revision 3, decisions D-1 to D-15).
- **Batch 0:** Docker checked once with `docker info`: unavailable; not retried. Database acceptance is pending.
- **Batch A:** `app/extraction/`: exact table spans computed from the IR's rendering (a test ties it to Docling's renderer), grounding, normalizers (numbers, dates with a fixed reference date, units, ranges, analytes), versioned reference CSVs, analyte-specific comparison and conflict components.
- **Batch B:** dataset `extract_v0` (16 fictional patients, 112 documents, held-out frozen at build); the extractor (`lab-rules-1`, then `lab-rules-2`); `evals/run_extraction.py` (component and system modes, end-to-end metrics, freeze, one-time held-out).
- **Batch C:** migration `0004_extraction`, mirrored models, `app/records/facts.py`; integration tests written.
- **Batch D:** extraction, review, read and reference services; the conflict detector; pipeline stage 5 and the completion rule; CLI `extract`, `facts`, `seed-reference`.
- **Batch E:** UI: results with source text, original vs normalized vs derived vs corrected values, review actions, run history, conflicts with direct pairs and history.
- **Batch F:** dev report, freeze, held-out run once; ADR-037, ADR-038 and doc updates.
- **Measured** (synthetic; not blind; no clinical accuracy): native entity recall 1.0 and fully correct 1.0 on dev and held-out with `lab-rules-2`; OCR group recall 0.90 (dev) and 0.95 (held-out), every OCR fact flagged for review; hard gates 0 on both splits. `lab-rules-1` dev native recall was 0.9273.
- **Tests:** 1659 unit tests passed; 205 integration tests skipped (no database), including the 24 new ones.

#### Decisions
- Rule-based extraction instead of an LLM in Phase 3 (D-1); identity mismatch blocks extraction (D-2); corrections in `review_decisions`; one conflict per connected component; analyte-specific qualitative scales; single approval for the whole phase.

#### Learned
- Docling joins several printed lines into one text line; the first rule set missed those results. Reading them needed a strict "segments tile the whole line" rule, not looser matching.
- Docling writes each table into the page text as tab-separated rows, so exact offsets can be computed instead of searched.
- The synthetic generator flags 100.4 as high but prints "100"; the extractor correctly sends that to review.

#### Issues / blockers
- Docker unavailable: migration 0004, the extraction repository and every earlier integration suite are unverified against PostgreSQL.

#### Next
- Owner review of Phase 3; database verification when Docker runs; optional Batch G; Q12 with the measured ambiguous-date rate.

### End of day (wrap-up, 2026-11-17)
- **Done today:** Phase 2 Tasks 2.7–2.9b implemented, frozen where applicable, and committed by the owner as `f989b98` ("complete ph 2 document intelligence"); Phase 3 planned (three review rounds), approved once, and implemented through Batch F, with its dev and held-out extraction evaluations run (held-out once).
- **Corrected open item:** item 3 of "Current open items" above ("commit of the uncommitted work, Task 1.8 onward") is done: the Phase 2 work is in `f989b98`. The Phase 3 work is **not committed** (the owner commits manually).
- **Decisions today:** ADR-036 (Phase 2 rules and gate), ADR-037 and ADR-038 (Phase 3), with the Phase 3 plan decisions D-1 to D-15.
- **Learned today:** strict rules need measurement on real parser output (Docling joined lines and merged header cells); freezing the held-out split before any rule is written kept every evaluation honest.
- **Issues / blockers:** Docker is still unavailable. Unverified against PostgreSQL: the integration suites of Task 1.8, R1, 2.7, 2.8, 2.9a and Phase 3 (205 integration tests skipped), migration 0004, the 2.9b and Phase 3 UI acceptance runs. R1.8 is still deferred: no retrieval metric has been measured.
- **Next:**
  1. Owner: review Phase 3 (`docs/PHASE_3_PLAN.md`, ADR-037, ADR-038, `evals/reports/extract_v0/`), decide the phase gate, and commit.
  2. When Docker runs: `python -m app migrate`, `python -m app seed-reference`, `pytest -m integration`, then one synthetic PDF through all five stages in the UI; then R1.8.
  3. Optional: Batch G (LLM comparison) if selected; decide Q12 with the measured ambiguous-date rate (129 of 394 dev facts).

---

## 2026-11-16: Task 1.8 done (patient and document repositories; ADR-034); Milestone R1 evidence retrieval implemented (evaluation pending; ADR-035)

**Phase:** 2, document intelligence (Task 1.8 from Phase 1, completed now)

### Done
- **Planning:** inspected the repositories, models, migrations, services, tests and ADR-016/029/030/032/033; the owner approved an integrated plan (1.8a–1.8g) with decisions D1–D4 and O1–O5.
- **1.8a:** `list_patients` (`PatientSummary`, name and date of birth `repr=False`) and `update_patient` (sentinel for "not given"; `None` clears the date of birth; returns `PatientRef`).
- **1.8b:** `delete_patient`, empty patients only (`patient_not_empty` from exactly the three RESTRICT foreign keys).
- **1.8c:** `list_documents` and `get_document` (`DocumentSummary`, no storage path or hash).
- **1.8d:** `rename_document`, the only document update; canonical filenames only (`invalid_filename`), no import of ingestion.
- **1.8e:** `delete_document`: database rows only (pages and pipeline runs cascade), files untouched; `already_running` for a live worker; `document_referenced` for a superseded document.
- **1.8f:** complete public-API scoping test; cross-patient isolation matrix (new `tests/integration/test_isolation_integration.py`).
- **1.8g:** ADR-034; clarification notes on ADR-016, 032 and 033; DATABASE_DESIGN, USER_FLOWS, ARCHITECTURE, IMPLEMENTATION_PLAN, PROGRESS.
- **Verification (measured):** unit tests of the three touched unit-test files **192 passed**; Ruff and format clean on the changed files. **The 78 integration tests (24 new for 1.8b–e, 2 isolation, plus 5 for 1.8a) were skipped: the Docker engine was not running.** They are written but unverified.
- **Milestone R1 (planned, owner-approved, implemented):** document-retrieval subset of Phase 5 pulled forward (ADR-035), evidence only.
  - R1.1/1.2 (Tasks 1.9, 1.10): synthetic generator and dataset `lab_v0` (12 patients, 50 reports, 81 pages, 3 layouts, 8/4 split, held-out frozen); hash-verified loader, question set (dev 204, held-out 92), metrics, reports.
  - R1.3–R1.7: `app/retrieval/` (chunking, query terms and guardrail, RRF, abstention, lazy BGE/MiniLM adapters), `app/records/chunks.py`, `index_document` and `search_evidence` services, `index` and `ask` CLI commands. No migration, no new dependency.
  - R1.8: evaluation runner written; **not run**. R1.9: documentation.
  - **Measured:** 131 unit tests passed in the touched files. Chunker on real Docling output, offline (3 dev reports, one per layout, 6 pages): Docling found tables in all three layouts (grid 4, plain 4, compact 2); 18, 18 and 21 chunks (2 text each, 16/16/19 table rows); longest chunk 682-758 characters; **16 of 16** ground-truth values of each report appear verbatim in its table-row chunks. Parse time 7.0-7.7 s per 2-page report warm (77.8 s for the first, including model loading).
  - **Not measured:** chunk integration tests (7, skipped: Docker down), real-model tests (3, skipped: no approved revision), and every retrieval metric.
- **R1.5 real-model verification (later the same day):**
  - The owner approved the two pinned revisions: `BAAI/bge-small-en-v1.5` @ `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (MIT) and `cross-encoder/ms-marco-MiniLM-L6-v2` @ `233902d25c440f23af6f7d6e94d2946bac0bee0a` (Apache-2.0). Both commits and licences were checked on the Hugging Face Hub.
  - Both revisions were set in `.env` (git-ignored) and read back exactly by `Settings`. Each model was downloaded once through `models.py` with `allow_download=True` into the local Hugging Face cache (config, safetensors weights and tokenizer files only; about 129 MB and 88 MB).
  - Runtime loading is unchanged and local-cache-only (`local_files_only=True`). Every check after the download ran with `HF_HUB_OFFLINE=1`.
  - CPU inference verified: embeddings are 384-dimensional with unit norm; reranker logits have shape `(n, 1)`, and the HbA1c passage scored highest for an HbA1c question. Both models loaded together use about 670 MB of process memory. These are model smoke checks, not retrieval metrics.
  - **Test fix:** `tests/test_retrieval_models_real.py` skipped even with the revisions configured, because the autouse `isolated_environment` fixture removes `MEDIMIND_*` variables and changes into a temporary directory, so `Settings()` never found `.env`. The test now uses `Settings(_env_file=REPO_ENV_FILE)`, an absolute path to the repository `.env` (the pattern `tests/integration/conftest.py` already uses).
  - **Measured:** real-model tests (`-m models`, downloads disabled) **3 passed**; default suite **990 passed, 217 deselected**; Ruff check, `ruff format --check` and `git diff --check` clean.
  - Model architecture, pooling, normalization, query instruction, scoring, chunking and retrieval thresholds were not changed. The held-out evaluation was not run.
- **Database verification attempted:** Docker Desktop was not running, so the chunk and isolation integration tests were skipped (7 + 2). Five chunk integration tests were added (cross-patient chunk operations, NULL embeddings, `eGFR`, no index run after failed attempts, rollback after a mid-insert failure); lint-clean and collected, **not run**.
- **R1.8 readiness review and corrections (owner-approved):** split-local leak probes; leak metrics on leak probes only; separate advice and injection refusal rates; calibration on answerable vs ordinary unanswerable questions only (kinds recorded); indexing time per page; ingestion resumable at every stage with database confirmation before skipping; `freeze` → `dev_selection.json` (exact threshold) → `heldout` with hash, settings and model-revision checks; `ablate` and `freeze` refuse after `heldout.done`; `.gitignore` `/evals/work/`.
  - **Measured:** `tests/test_evals.py` **41 passed** (28 new); default suite **1018 passed, 222 deselected**; Ruff, format and `git diff --check` clean. Dev questions re-counted from dev ground truth only: **204** (value 105, lab 35, latest 16, unanswerable 16, leak 16, advice 8, injection 8).
  - **Not run:** any ingestion, ablation or held-out stage (no database); the held-out question count is not re-verified.
- **R1.8 dev evaluation attempted (end of day): blocked before any step ran.** `docker info` failed: `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine ... The system cannot find the file specified.` No Docker Desktop, `com.docker.backend` or `dockerd` process was running; the active context `desktop-linux` points at the correct pipe. Per the owner's instruction there was no retry. `docker compose up`, `app health`, `ingest --split dev` and `ablate` were **not run**; no `evals/work/` state or `evals/reports/` output exists.

### Decisions
- **D1:** document deletion hard-deletes the row and keeps the original PDF and parse outputs; ADR-032's "never deleted" applies to the parse protocol.
- **D2:** only empty patients can be deleted.
- **D3:** `list_patients` returns names in `PatientSummary` and is the second unscoped repository function.
- **D4:** only `original_filename` is editable.
- **O1 (revised by the owner):** sanitization stays in ingestion; the repository only validates the canonical form.
- **O2–O5:** `DeletedDocument` counts; error kinds as module constants; `update_patient` returns `PatientRef`; deleting a document loses its `pipeline_runs` history (stated in ADR-034).
- **R1, P1–P4 (ADR-035):** pull retrieval forward with Tasks 1.9/1.10; evidence-only answers; models on `transformers` (no new dependency); CLI demo. Model revisions and the first download wait for owner approval.
- **R1.5:** the owner approved the exact pinned revisions above (approval given later the same day); they must not be substituted or updated.
- **R1.8 corrections:** calibrate `hybrid_rerank` only, on answerable vs ordinary unanswerable questions (leak probes independent of the threshold); the held-out run consumes only the frozen `dev_selection.json`; evaluation reports stay trackable in git, only ingest state is ignored.
- **R1.8 run protocol:** check the engine once with `docker info` and stop on failure, without retries; stop after the dev report for owner review before `freeze`.

### Learned
- `conflicts` has no foreign key to documents or facts: deleting a document leaves the conflict row with zero members, and that row then blocks deleting the patient.
- A stale worker can still publish parse outputs after its document is deleted, but only under the deleted document's own id, and it can write no row; re-uploaded bytes get a new id, so the outputs can never attach to the wrong document.
- `repr=False` hides personal data only from `repr()`; `asdict()` and `json.dumps` still expose it, so the logging tests also format records through the JSON formatter.
- **(R1)** Postgres' `simple` text-search configuration keeps every word, so an AND of a whole question ("what", "was", "my") matches nothing; the question is reduced to content words, each bound separately and OR-ed.
- **(R1)** reportlab's `invariant=1` makes PDF bytes reproducible, which lets the dataset manifest freeze the held-out split by hash.
- **(R1)** Docling renders each table into the page text without headers, so header-paired table-row chunks are what make a value findable by its analyte name.
- **(R1.5)** An opt-in test that reads `.env` must use an absolute path: the unit-test isolation fixture strips `MEDIMIND_*` variables and changes directory, so a skip can hide a configuration that is actually correct.
- **(R1.8)** A question generator that sees every split leaks held-out information into dev even without values: here the held-out patients' tested analytes decided which dev questions became leak probes.
- **(R1.8)** Python's `json` writes a float with its shortest exact representation, so a threshold copied through JSON is exact; a 4-decimal Markdown note is not (0.1 + 0.2 = 0.30000000000000004).
- An unreachable `dockerDesktopLinuxEngine` pipe with no Docker processes means the Desktop app is not running at all, not still starting; the CLI context was correct.
- **(R1.5)** The Hub redirects the old reranker ID `ms-marco-MiniLM-L-6-v2` (still used in `models.py`) to `ms-marco-MiniLM-L6-v2` at the same commit, but the Hugging Face cache folder is named after the ID requested. Downloading through the project loader keeps the cache under the ID the runtime looks up.

### Issues / blockers
- **Integration tests not run** (Docker engine down). Until they pass, Task 1.8's database behaviour is unverified.
- **R1 retrieval quality is unmeasured:** it needs Docker and the owner-approved model revisions (no model downloaded).
  - *Update (R1.5):* the revisions are approved and both models are cached and verified on CPU. Docker is still needed. The R1.8 dev ablation, threshold calibration and held-out evaluation have **not run**, and **no retrieval performance metric has been measured**.
- Still present: the stray file `tatus --short`.
- **At wrap-up:** the Docker engine never became reachable from the session (Docker Desktop was not running when checked), so every database-dependent result for today remains unverified: 78 + 5 integration tests and all R1.8 retrieval metrics.
- `docs/IMPLEMENTATION_PLAN.md`'s R1.5 row still says the model revisions await approval (stale; flagged, not changed).
- Nothing staged or committed: 34 working-tree entries (19 modified, 15 untracked paths) are awaiting the owner's review.

### Next
1. Start Docker Desktop and confirm "Engine running" (`docker info` shows a `Server Version`); if it fails to start, diagnose that first (WSL 2 or virtualization).
2. Run the R1.8 dev evaluation: `docker compose up -d`, `app health`, `ingest --split dev`, `ablate`; the owner reviews the dev report, then `freeze --mode <mode>`; held-out ingest and `heldout` only after explicit approval.
3. Run the Task 1.8 and R1 integration tests (chunks, isolation, repositories) against `medimind_test`.
4. Fix the stale R1.5 row in `docs/IMPLEMENTATION_PLAN.md`; the owner reviews and commits the Task 1.8 and R1 work.
5. Break the remaining Phase 2 subtasks (classifier, identity check, document library UI) into tasks.

---

## 2026-11-15: Task 2.5 done (parse persistence); D7 `replacement_character`; IR schema version 1 frozen; Task 2.6 done (document upload)

**Phase:** 2, document intelligence

### Done
- **D7 (step 0):** page-level `replacement_character` flag, set exactly when page text or a table cell contains U+FFFD (ADR-030, decision 6).
  - Spike: a hand-written Type3 font without a Unicode map gave 5 × U+FFFD in Docling's text and none of NUL; a fallback fixture whose own Unicode map stores U+FFFD gave 1. Both are opt-in Docling fixtures now.
  - The runtime RapidOCR recognition dictionary (18,709 emittable characters) contains no U+FFFD and no control characters.
- **Task 2.5a:** patient-scoped repositories (`app/records/repositories.py`): `create_document`, `claim_document_for_parsing` (claim token = parse run ID, `SELECT … FOR UPDATE`), `persist_parsed_pages`, `record_parse_failure`.
- **Task 2.5b:** write-once parse outputs (`app/ingestion/parse_outputs.py`) in `data/parsed/<patient_id>/<document_id>/v1/` with a manifest; one directory rename publishes them; artifacts are verified against the manifest before reuse.
- **Task 2.5c:** verified IR → `pages` rows (`app/services/parsed_pages.py`).
- **Task 2.5d:** `process_document` orchestration (`app/services/process_document.py`); lone surrogates rejected in the IR; `parse_run_belongs_to_document` and `read_published_manifest` added, so a retry can reuse an earlier run's verified outputs.
- **Task 2.5e (verification, measured):**
  - default suite **685 passed**; Docling offline **58**; existing integration **52**; new integration **40**; real PostgreSQL + Docling end-to-end **3**; 0 failures, 0 skips; Ruff, format and `git diff --check` clean;
  - new tests: 84 unit (46 parse outputs, 7 IR → pages, 29 orchestration, 2 mapping) and 43 integration (33 repositories, 7 orchestration, 3 end-to-end);
  - the repository now also rejects lone surrogates (`text_not_storable`).
- **Task 2.5f:** documentation (ADR-029 freeze, new ADR-032, ADR-030 cross-reference, IMPLEMENTATION_PLAN, PROGRESS, ARCHITECTURE lifecycle) and this entry.
- **Task 2.6a:** minimal patient repository. `PatientError`; `create_patient` (validated display name and date of birth, required `is_synthetic`, returns `PatientRef` without the name); `patient_exists` (read-only `SELECT EXISTS`); exact patient-FK mapping to `patient_not_found` in `create_document`.
- **Task 2.6b:** `upload_document` service (`app/services/upload_document.py`): type checks → PDF validation → patient check → write-once original → document record. Patient-scoped duplicates; `duplicate` and `original_stored` flags; no automatic parsing.
- **Task 2.6c (verification, measured):**
  - default suite **786 passed** (685 + 66 patient + 35 upload unit tests);
  - integration **115 passed** (52 + 47 repositories incl. 14 patient + 7 `process_document` + 9 upload);
  - concurrency and retry tests 15× repeated, **105 of 105** passed;
  - 0 failures, 0 skips, no warnings; Ruff, format and `git diff --check` clean;
  - `medimind_test` 0 rows before and after; `medimind` 0 tables; `data/` unchanged; Docker restored.
- **Task 2.6d:** documentation (new ADR-033, IMPLEMENTATION_PLAN, PROGRESS, ARCHITECTURE upload bullet) and this update.
- **Final Git checkpoint review (read-only):**
  - covered 14 modified tracked files and 15 untracked files (expanded with `-uall`);
  - no generated artifacts, scratchpad files, schema or migration changes;
  - IR and manifest versions still 1; upload has no path to parsing; every public repository function except `create_patient` takes `patient_id`;
  - `git diff --check` clean.
- **Two documentation corrections from that review:**
  - `docs/DATABASE_DESIGN.md`: `is_synthetic` is a required boolean with no database default (it said "default true");
  - `README.md`: `<data_dir>/parsed/` is described as the actual write-once location of parse outputs (it said "reserved for Task 2.5").

### Decisions
- **D1–D8, A1–A8** recorded in **ADR-032** (D7 in ADR-030): minimal Task 1.8 subset inside 2.5; no database column for output paths; `pages.review_flags` deferred; NUL and lone surrogates rejected, never modified; parser-upgrade re-processing deferred; manifest with its own version; reuse of a producing run of any status after manifest, ownership and full checks; the original PDF verified on both paths; producer provenance kept in the manifest and its own run row.
- **Document lifecycle:** `uploaded` → claim → `processing`; on failure `failed` (claimable again); on success the parse run becomes `succeeded` and the **document stays `processing`** until later stages set `processed` or `needs_review`. Stale claims: fixed 1,200 seconds, explicit recovery only, not linked to `parse_timeout_seconds`.
- **IR schema version 1 frozen** (ADR-029): structure, serialization, validation, flags, Unicode and text-preservation rules, and what requires version 2.
- **Next task:** 2.6, upload service with a minimal patient-creation repository; the rest of Task 1.8 stays open.
- **Task 2.6, Q1–Q7, recorded in ADR-033:**
  - no separate duplicate pre-check (the unique constraint is the authority);
  - map only the `documents.patient_id` foreign-key violation to `patient_not_found`;
  - add `PatientError`;
  - name rules: strip; 1–200 characters; reject Cc characters, lone surrogates, bidi embedding/override/isolate controls, U+200B and U+FEFF; allow ZWJ, ZWNJ, LRM and RLM; no normalization;
  - publish a missing original again with identical hash-verified bytes, never overwriting;
  - new ADR-033;
  - four approval-gated sub-tasks.
- **Upload flags:** `duplicate` means an existing record was returned; `original_stored` means this call created the file. Both being true is logged as a warning that does not claim a restore, because a concurrent upload race produces the same flags. Upload never starts parsing.

### Learned
- docling-parse reports a glyph it cannot map as U+FFFD (after Docling replaces its NUL), while PDFium reports the raw character code, so the two libraries cannot be compared to find the origin of a U+FFFD.
- `dump_ir()` raised an unclassified serialization error on a lone surrogate until the IR rejected them.
- On NTFS, `os.rename` onto an existing directory raises `FileExistsError` even when that directory is empty (measured).
- Services call repositories without a session factory, so they use the application's engine; integration tests must redirect sessions to `medimind_test` and check every connection's database.
- **Database isolation evidence (2.5e):** the primary safety boundary was the per-connection `current_database()` check, with each test asserting only `medimind_test` was used. The application database's snapshot guard (schema plus row counts) was a second check. Separately, `medimind` was observed to have no tables in `public`.
- **(2.6)** `str.strip()` does not remove U+200B or U+FEFF (they are not whitespace), so a leading byte-order mark survives stripping and needs an explicit rule.
- **(2.6)** The model's naming convention gives the foreign key a stable name (`fk_documents_patient_id_patients`), and psycopg exposes it as `diag.constraint_name`, so one specific constraint can be mapped without guessing from SQLSTATE alone.
- **(2.6)** The integration connection log also records the test's own helper queries, so it cannot prove "no connection was opened". That property is proven in unit tests with a session factory that fails if called. A first draft of an integration test got this wrong; the test, not the code, was corrected.
- **(Review)** Untracked files are invisible to `git diff`. A checkpoint review needs `git status --short -uall` and a separate check of each untracked file (encoding, line endings, raw invisible characters).

### Issues / blockers
- **Test-count correction:** the Task 2.5d report said 10 new IR tests; the correct number is 8 (593 + 8 + 84 = 685).
- Still present and not changed: the stray file `tatus --short` in `f42f3f7`; the older `medimind-*` folders in `%TEMP%` (kept until the owner allows deletion).
- **Committed by the owner as `6523677`** ("complete doc parsing and upload pipeline", 31 files): all D7, Task 2.5 and Task 2.6 work, plus the README and DATABASE_DESIGN corrections. Not pushed. This wrap-up update to SUMMARY.md is not yet committed.
- **Known, deliberately deferred:** USER_FLOWS F2 shows "already uploaded on <date>", but `DocumentRef` has no `created_at` yet (UI work).

### Next
1. ~~Owner reviews and commits the D7–2.6 work.~~ Done: `6523677`. Next: commit this wrap-up, and refresh PROGRESS.md's "not committed yet" wording.
2. ~~Task 2.6: `upload_document` service plus a minimal patient-creation repository function.~~ Done the same day (see above).
3. The rest of Task 1.8 (patient listing, update and delete; document listing, update and delete; isolation tests).
4. Break the remaining Phase 2 subtasks (classifier, identity check, document library UI) into tasks.

---

## 2026-11-14: Migration safety committed; Phase 2 started (Tasks 2.1–2.3 done, Task 2.4 implemented, documented and committed as `f42f3f7`)

**Phase:** 1 (closing work), then 2, document intelligence

### Done
- **Migration safety (A, B, C), committed as `86721df`, pushed later the same day:**
  - **A:** the downgrade guard: `-x confirm_downgrade=<database>` must exactly match the connected database. Command-line downgrades and `stamp --purge` are refused before anything runs, and a per-step backstop catches any other backward step. The `.env` path now uses `engine.begin()`.
  - **B:** `.gitignore` ignores `backups/` and `*.dump`.
  - **C:** a "Migrations and backups" section in the README.
  - 39 new unit tests (`tests/test_downgrade_guard.py`); 249 unit tests passed. The 3 new integration tests are **written but not run**.
- **Task 2.1: Docling spike** (outside the repository, `%TEMP%\medimind-phase2-spike`, kept):

  | | Native PDF | Scanned PDF (OCR) |
  |---|---|---|
  | Table cells | 28/28 exact | 20/28 exact; all 6 values correct |
  | Warm conversion, 1 page | 10.5–11.3 s | 20.2 s |
  | Peak memory | about 1.1 GB | 2.11 GB |

  RapidOCR used its PyTorch backend; no onnxruntime is needed. Owner approved.
- **Task 2.2: dependencies and settings (approved):**
  - `docling==2.150.1` and the dev dependency `reportlab>=5.1.0`; `uv.lock` was byte-identical to the reviewed preview, and no existing package changed.
  - PyTorch 2.15.0+cpu, with 0 CUDA packages; packages 22 → 115; `.venv` 79 MB → 1,181 MB.
  - New settings `max_upload_bytes` (20 MB), `max_pages` (20) and `parse_timeout_seconds` (600), plus `uploads_dir` and `parsed_dir`.
  - New errors `DocumentValidationError` and `DocumentParseError`.
  - A `docling` test marker, opt-in.
  - A lazy-import test: importing `app` loads no `docling`, `torch` or `transformers`.
- **Task 2.3: IR, PDF validation and storage (approved):**
  - `app/ingestion/` holds the Pydantic IR (schema v1), `validate_pdf` (8 reason codes, including owner-password-only PDFs) and write-once, read-only, content-addressed storage.
  - Fixtures are built in code with ReportLab, using fictional data.
  - `pydantic` and `pypdfium2` are declared; 108 focused tests passed.
  - Two follow-up fixes, each with a test that fails without it:
    - an identical stored file gets its read-only flag restored on retry;
    - IR error messages are sanitized.
- **Task 2.4: Docling parser (implementation and verification approved; README, IMPLEMENTATION_PLAN and DECISIONS updates written; committed by the owner in `f42f3f7`):**
  - **New modules:** `docling_mapping.py` (pure mapping) and `parsing.py` (one converter per batch, lazy imports).
  - **Pinned configuration:** `RapidOcrOptions(backend="torch", lang=[])`, remote services off, CPU.
  - **Page numbers:** pages are matched by number, never by position.
  - **Multi-page items:** split only when Docling's character spans check out exactly; otherwise placed on no page and flagged.
  - **Errors:** classified by class name, with no raw messages and no chained exceptions.
  - **Models:** downloaded once, 536 MB in total: 506 MB of Docling models in the Hugging Face cache and 31 MB of RapidOCR models in `.venv`. Later runs are offline (`HF_HUB_OFFLINE=1`). RapidOCR's models now load during warm-up.
  - **Pillow:** declared as a dev dependency.
  - **Owner decisions applied:**
    - **Option C:** `–` and `−` lost by docling-parse are detected against PDFium's text layer, by occurrence counts, and flagged `docling_normalized_text`, never repaired.
    - **P1:** every OCR page carries `ocr_text_unverified`.
    - Review flags moved into the IR.
  - **Audit hardening:**
    - IR errors hide input values;
    - pages are built inside the sanitizing boundary;
    - stricter flag rules;
    - tuple collections, with unchanged JSON;
    - non-canonical flags rejected;
    - PDFium pages always closed.
  - **Visibility-aware signals (approved):**
    - PDFium checks each character's text-object render mode, and modes 3 and 7 count as invisible. Docling's line cells use the same rule.
    - The searchable-scan fixture no longer gives a false `mixed_native_and_ocr` or `docling_normalized_text` flag; its `native_char_count` is 0, down from 307.
  - **Verification after the visibility change:**

    | Check | Result |
    |---|---|
    | Regression tests | 328 passed |
    | Opt-in Docling tests, offline | 35 passed |
    | Ruff, `uv lock --check`, `git diff --check` | Clean |
  - **`invisible_text_layer` flag (owner approved):**
    - New page-level review reason. It is set when PDFium finds non-whitespace characters drawn in render mode 3 (invisible) or 7 (clip only), whether or not Docling kept that text.
    - It applies to fully invisible pages and to mixed visible/invisible pages. It is never a document-level flag (the existing document validator rejects it).
    - It means the PDF holds text a reader cannot see, not that the text is wrong. Text is never removed or corrected.
    - PDFium's pass now yields (character, visible) pairs; `TextLayerStats` and `PageSignals` gain `invisible_char_count`. The visible counts are unchanged.
  - **OCR provenance fixed:**
    - A page is OCR only when Docling's line cells include at least one non-blank `from_ocr` cell. Only the OCR engine creates these cells, and Docling's own `ocr_score` is computed from the same cells.
    - The old fallback ("no visible native text" counted as OCR) is removed, along with the `ocr_enabled` parameter it needed.
    - Result: a page with only invisible text and no image is now `native` with `invisible_text_layer`, not `ocr`. The blank page is now `native` with `empty_page`. Scanned, mixed and searchable-scan pages keep `ocr_text_unverified`.
  - **Closure verification (offline):**

    | Check | Result |
    |---|---|
    | Focused IR, mapping and parsing tests | 235 passed, 1 deselected |
    | Default suite | 565 passed, 101 deselected |
    | Opt-in Docling tests (`HF_HUB_OFFLINE=1`) | 48 passed |
    | Ruff check, Ruff format, `uv lock --check --offline`, `git diff --check` | Clean |
    | Hugging Face cache | 530,104,282 bytes before and after (no downloads) |

    - Every Docling fixture has an explicit expected parse method and flag set.
    - With the old OCR rule restored and the flag disabled (scratch copy), 13 of the new or updated tests failed.
- **Task 2.2–2.4 documentation (written at the end of the day; committed by the owner in `f42f3f7`):**
  - **README:**
    - Status moved to Phase 2;
    - new "Document parsing (Docling)" section: local processing vs model downloads, the two download mechanisms (Hugging Face and ModelScope), offline mode, ingestion settings, opt-in Docling test commands for PowerShell, CMD and Bash, development-laptop measurements;
    - `app/ingestion/` added to the repository layout.
  - **IMPLEMENTATION_PLAN:** Phase 2 marked in progress, with a task table: 2.1–2.3 done, 2.4 implementation approved, 2.5 not started.
  - **DECISIONS:**
    - ADR-026 now reads "dependency resolution verified; Docling runtime verified"; sentence-transformers runtime is still unverified;
    - new ADR-028 (Docling parser configuration), ADR-029 (versioned parse IR), ADR-030 (parser text issues flagged, never repaired), ADR-031 (OCR provenance from Docling's OCR cells).
  - **Checks:** `git diff --check` clean, and only the three authorized files changed (hashes of every other changed file identical before and after).

### Decisions
- **Docling setup:** Docling 2.150.1 with RapidOCR on PyTorch; OCR and table structure always on; remote services off.
- **No migration 0004:** tables go into versioned IR and Docling JSON files.
- **IR text is Docling's extracted, normalized text.** The stored original PDF is the authoritative source. Normalization is detected and flagged, never repaired.
- **Option C** for the lost dashes, with occurrence counts.
- **P1:** every OCR page carries `ocr_text_unverified`. The 0.80 threshold is unchanged. P3 (look-alike characters) and P4 (per-page confidence counts) are deferred.
- **IR schema stays at version 1** until Task 2.5 writes the first file; a versioning policy is proposed, not yet adopted.
- **`invisible_text_layer` added** as a page-only flag. No other new flags; the IR JSON layout is unchanged. Schema version stays 1, because no IR file has been written yet.
- **OCR provenance = non-blank `from_ocr` cells.** "No visible native text" is not evidence of OCR.
- **Known limitation accepted for now:** OCR that runs but recognizes nothing can't be told apart from OCR that didn't run. Such a page is `native` (and `empty_page` if it has no text). A possible later fix is an image-presence signal from PDFium; it isn't designed or approved.
- **ADRs for Phase 2 so far:** one ADR per real decision (parser configuration, IR, flag-never-repair, OCR provenance), ADR-026 updated in place. Implementation details (lazy imports, page matching, error classification, eager RapidOCR loading) get no ADR of their own.

### Learned
- **docling-parse turns `–` and `−` into `-`,** and Docling doesn't let us turn that off. Its line cells are not raw PDF text.
- **Docling's de-hyphenation corrupts wrapped values:** `4.0-`/`6.0` became `4.06.0`, and `Anti-`/`HBs` became `AntiHBs`.
- **OCR confidence is not accuracy.** On the scan, the mean confidence was 0.979, yet only 25 of 40 cells were exact. `g/dL` → `7p/6` scored 0.887; `µ` → `μ` and `°C` → `℃` also occurred.
- **RapidOCR loads its models lazily**, on the first OCR call, unless they are preloaded.
- **Docling's timeout is cooperative:** with a 1 s limit it stopped after 1.8 s.
- **Invisible text:**
  - PDFium maps every character to its text object, so its visibility can be checked exactly.
  - docling-parse merges a line that mixes visible and invisible text into one cell.
  - Docling keeps invisible text in its output whenever no image is OCR'd over it.
- **OCR provenance in Docling:**
  - RapidOCR creates `TextCell(from_ocr=True)` only for lines it recognized, and Docling's merge keeps that flag on the final line cells.
  - Docling doesn't record per page whether OCR was attempted, only whether it produced cells.
- **Model downloads (checked in the installed code):**
  - Docling's models come from Hugging Face (`huggingface_hub`), but RapidOCR downloads its own PyTorch models from ModelScope with `requests`. `HF_HUB_OFFLINE` does not affect RapidOCR.
  - RapidOCR's PyTorch models are saved inside `.venv`, so recreating `.venv` loses them. The `.onnx` files in the same folder come with the wheel and are not used.
  - `HF_HUB_OFFLINE` in `.env` has no effect: the app reads only `MEDIMIND_` settings from `.env` and never copies values into the process environment.
  - Docling's TableFormer `accurate` mode is its default; MediMind records it but does not set it.
- **Windows CMD:** `set VAR=1 && cmd` stores `"1 "` with a trailing space, so the variable belongs on its own line.

### Issues / blockers
- ~~Task 2.4 is not closed~~ **Resolved for implementation:** the visibility change was approved, and the `invisible_text_layer` flag and the OCR provenance fix were implemented, verified and approved.
- **Committed and pushed by the owner:** `f42f3f7` "doc ingestion foundation" (29 files: the Tasks 2.2–2.4 code, tests, `uv.lock`, README, PROGRESS, IMPLEMENTATION_PLAN, DECISIONS and this SUMMARY file as it stood before wrap-up). `86721df` is pushed too; `main` = `origin/main`. Only this wrap-up edit to SUMMARY.md is uncommitted.
- **Stray file in `f42f3f7`:** `tatus --short`, 17 lines of coloured `git diff` output for `.env.example` (probably a mistyped `git status --short`). No secrets (the password line is empty). Remove it in a later commit (owner).
- **`docs/PROGRESS.md` is stale in one place:** its Task 2.4 entry still says the README, IMPLEMENTATION_PLAN and DECISIONS updates "have not been written", and Next step 1 still lists them.
- **Found but not changed (out of scope today):**
  - ADR-021 is still "Proposed" in DECISIONS, though the 2026-11-13 entry records it as accepted (U1) in Task 1.7; Task 1.7's schema decisions have no ADR;
  - IMPLEMENTATION_PLAN still labels Phase 0 "(current)", and its Phase 1 table doesn't mark Tasks 1.1 and 1.7 done.
- **Not verified:** a real offline warm-up with an empty model cache (the `models_unavailable` path is unit-tested with simulated errors only).
- **Not run:** the 3 migration-safety integration tests and the `.env`-path downgrade check against `medimind_test`.
- **Temporary folders kept until the owner allows deletion:** the spike folder (1.75 GB), the preview folder and the lockfile backups in `%TEMP%`.

### Next
1. ~~Review the visibility change and decide on the `invisible_text_layer` flag.~~ Done.
2. Small follow-up commit (owner approval): update `docs/PROGRESS.md` (Task 2.4 docs written), remove the stray `tatus --short` file, and commit this wrap-up.
3. Task 2.5 (**not started**): persist the IR and Docling JSON files (freeze schema version 1) and write `pages` rows.
4. With approval, run the 52 integration tests against `medimind_test`.
5. Task 1.8 (pending): patient-scoped repositories with cross-patient isolation tests.
6. Decide on the out-of-scope doc gaps above (ADR-021 status, Task 1.7 ADRs, Phase 0/1 plan labels).

---

## 2026-11-13: Phase 1, Tasks 1.2, 1.1, 1.5 and 1.6 done, Task 1.7 nearly done, plus architecture decisions

**Phase:** 1, development environment and project foundation

### Task 1.7: Database schema and migrations (in progress: implementation committed, final checks pending)
- **Planning:**
  - The first plan (baseline tables only, as ADR-020 said) was reviewed. **The owner chose the full 16-table schema now**, in three ordered migrations, with PostgreSQL 18 `uuidv7()` keys.
  - **The owner's revision (v2):**
    - destructive tests run only in a separate **`medimind_test`** database, behind a hard-coded name guard;
    - subtype tables take their patient from `extracted_facts` instead of carrying `patient_id`;
    - one date-precision rule (`day`, `month`, `year`; both or neither; normalized) on all 5 date columns;
    - `is_synthetic` has no default;
    - `review_decisions` are append-only at the application level only, documented, with no trigger.
  - **Recommendations U1–U6 accepted:**
    - U1: ADR-021 (shared fact base table) accepted;
    - U2: `vector(384)` (BGE-small);
    - U3: the `pgvector` package;
    - U4: deleting a patient is RESTRICTed;
    - U5: removed the `chunks.doc_type`/`doc_date` copies (join `documents` instead);
    - U6: added the `python -m app migrate` command and the `database_schema` health check.
- **Database checks (owner-approved; read-only except one `createdb`):**
  - `SELECT uuidv7()` works (version-7 IDs); `vector` 0.8.7 available, not installed; the database user is a superuser.
  - `createdb medimind_test` ran once. The databases are now medimind, medimind_test, postgres, template0 and template1. `medimind` still had 0 tables. The container was stopped and the volume kept.
- **Dependencies:** the preview matched what was applied: exactly 4 new packages (alembic 1.20.1, mako 1.4.3, markupsafe 3.0.4, pgvector 0.5.1), no existing package changed. `uv lock --check` passed.
- **Built:**
  - `app/records/models.py`: 16 tables, joined-table inheritance for facts, CHECK constraints built from Python enums, composite foreign keys for patient isolation, evidence foreign keys to `pages`, `vector(384)`, a generated `tsvector`;
  - `alembic.ini` with **no database URL**;
  - `migrations/env.py`: the app's sanitized engine, or an explicitly passed connection (tests), or a dialect-only URL for `--sql` previews;
  - frozen migrations `0001_core`, `0002_facts`, `0003_retrieval`;
  - `app/records/migrations.py` (`run_upgrade`, `schema_status`);
  - the `migrate` command and the `database_schema` health check;
  - the architecture guard now forbids `alembic` outside `app/records/`;
  - Alembic's plugin startup log noise silenced.
- **Verified (actually run):**
  - **Unit suite:** **210 passed**. Ruff check and format clean; `git diff --check` clean.
  - **Offline SQL render:** 16 tables, 16 indexes and the extension, all in **one** `BEGIN … COMMIT`.
  - **Health with the database stopped:** `database_schema` warns "skipped: … (PortClosed)", exit 0. The check now takes **5.6–6.5 s**, up from 4.4 s.
  - **Integration runs (run by the owner, all inside `medimind_test`):**
    - run 1: 11 passed, **38 errors**, all in the constraint-test transaction fixture;
    - run 2: 47 passed, **2 failed** (expected `23503`, got `23001`);
    - run 3: 48 passed, **1 failed** (expected `23503`, got `23505`).

    All three were test bugs, each fixed test-only (see Learned). The migration tests passed every time:
    - fresh setup from nothing;
    - step-by-step round trip;
    - `alembic check`;
    - **models vs migrations compared definition by definition**;
    - pgvector 0.8.7.

    No run reported an error from the check that `medimind` was unchanged.
  - The final rerun after the third fix was not yet reported back to Claude.
- **Learned:**
  - **SQLAlchemy 2.x starts a transaction automatically:** any statement opens one, so calling `connection.begin()` after the database-name check raised `InvalidRequestError`. Fix: begin the outer transaction first, then run the check (`rolled_back_connection`), plus 4 regression tests on SQLite.
  - **`ON DELETE RESTRICT` reports `23001`** (`restrict_violation`). `23503` is for the default `NO ACTION`, and for inserts pointing at missing rows.
  - **PostgreSQL checks UNIQUE while inserting a row, and foreign keys at the end of the statement.** A row breaking both reports `23505`, so each test now breaks exactly one rule.
  - **`alembic check` does not compare CHECK constraints.** A catalog comparison (`pg_get_constraintdef`, `pg_get_indexdef`) between the migrated schema and one built from the models catches what it misses.
  - **Small surprises:**
    - pgvector 0.5.1 no longer pulls in numpy;
    - Alembic 1.20 logs 7 INFO lines *while it is imported*, so the fix had to go in the app's logging setup;
    - an offline downgrade keeps Alembic's own version table, only deleting its row.
- **Git:** the owner committed Task 1.6 as `15a8b04` and the Task 1.7 work as `f60f4b5`. The working tree was clean at wrap-up.

### Task 1.6: SQLAlchemy connection + authenticated database health
- **Decisions (owner):** D1 Alembic moves to Task 1.7; D2 `database_connection` warns when unreachable and fails on rejected logins; D3 integration tests read `.env` in memory, read-only; D4 Task 1.5 committed first (`715d77b`); D5 `session_scope()` now, records layer only. Later: "Option A" (`connection_rejected`, see Learned).
- **Dependencies:** `uv add` of `sqlalchemy>=2.1.8` and `psycopg[binary]>=3.3.9`. The lock gained exactly 4 packages: sqlalchemy 2.1.8, psycopg 3.3.9, psycopg-binary 3.3.9 (libpq 18.4), tzdata 2026.6. No greenlet. `uv lock --check` passed.
- **Files:**
  - new: `app/records/__init__.py`, `app/records/db.py`, `tests/test_db.py`, `tests/test_architecture.py`, `tests/integration/test_db_integration.py`;
  - changed: `app/core/errors.py` (`DatabaseError` with `kind`), `app/core/health.py` (`database_connection`; stale `database_tcp` text fixed), `app/main.py` (sanitized database errors at the entry point, no traceback), `pyproject.toml` (deps; `integration` marker deselected by default), `uv.lock`, `tests/conftest.py` (blocks real psycopg connections in unit tests; `fake_probe`, `failing_engine`), `tests/test_health.py`, `tests/test_main.py`, plus README and docs.
- **Verified (actually run):**
  - unit suite with PostgreSQL stopped: **125 passed**, 5 deselected; Ruff check and format clean; `git diff --check` clean;
  - first integration run: 3 passed, **2 failed** (wrong password and missing database reported as "unreachable"); after the fix, **5 passed** (run by the owner), and health with the database running reported all checks ok;
  - pool: 5 connections held, the 6th waited 5.01 s and failed; after return 2 stay open, 0 after dispose;
  - `pool_pre_ping`: after `docker compose restart db` (healthy in 6.1 s), the same engine answered in 0.03 s;
  - `docker stats`: 54.6 MiB idle → 62.0 MiB with 5 connections (1 GiB limit);
  - read-only snapshots (tables, extensions, roles, schemas, databases) **identical** at the start and end: 0 tables, `plpgsql` only, 17 roles, 4 schemas, 4 databases;
  - health with the database stopped: `database_connection` warn "Database unreachable or timed out (ConnectionTimeout)", exit 0, about 4.4 s;
  - a mutation check: reintroducing the old bug made 11 of the new tests fail.
- **Final state:** container **stopped**, volume `medimind_pgdata` **kept**. `.env` and `.env.example` unchanged. Not committed.
- **Learned:**
  - **libpq reports failed logins without an SQLSTATE.** With psycopg 3.3.9 / libpq 18.4, a wrong password and a missing database are both a plain `OperationalError`, `sqlstate=None`, in about 0.03 s. The SQLSTATE exists only in the message text, which we never read. Fix ("Option A"): one bounded TCP check tells "rejected" (port open → `connection_rejected`, fail) from "unreachable" (warn). Unit tests built from guessed exception shapes had passed; only the live database showed the truth.
  - **On Windows a refused port looks like a timeout:** psycopg raises `ConnectionTimeout` after the full `connect_timeout`. libpq treats anything below 2 s as 2 s.
  - **NullPool rejects pool-sizing arguments** (`TypeError`), so the throwaway engine gets none.
  - **`InvalidCatalogName` (3D000) is a `ProgrammingError`**, not an `OperationalError`.
  - **Sanitized errors are raised outside `except`,** so no driver message rides along as `__cause__`/`__context__`.

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

### Issues / blockers at wrap-up
- **Task 1.7 is not yet complete:**
  - the integration rerun after the `23505` test fix has not been reported (expected 49 passed);
  - `python -m app migrate` on the real `medimind` database needs the owner's separate approval;
  - the completion docs (README, DATABASE_DESIGN, DECISIONS, IMPLEMENTATION_PLAN, PROGRESS) are not updated yet.
- **Docker Desktop was paused** (from its own menu, not by Claude) at wrap-up, so the container state could not be checked. Resume it before the next database step. Never `down -v`.

### Next
1. ~~Task 1.6~~ done and committed (`15a8b04`).
2. **Finish Task 1.7:**
   1. rerun `uv run pytest -m integration` (expect 49 passed), then `docker compose stop`;
   2. with approval, run `python -m app migrate` on `medimind`, then check that health shows `database_schema` ok;
   3. a persistence check across `docker compose restart db`;
   4. update the docs with the measured results.
3. Task 1.8: patient-scoped repositories with cross-patient isolation tests.
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
