# PHASE 3 PLAN — Structured Clinical Extraction

> **Status: Approved (owner, 2026-11-17, revision 3), including the decisions in §16.** Batches A–F implemented 2026-11-17 (see the implementation note at the end); ~~database acceptance pending (Docker unavailable)~~ database acceptance passed 2026-11-18 (see the PostgreSQL acceptance note at the end). The coding agent then executes Batches A–F in order and reports once at the end. Every metric is a target or a procedure, never a result.

## 1. Executive summary

- **Architecture:** deterministic-first lab extraction. Facts come from the verified IR tables and labelled lines of `lab_report` documents, by versioned rules. Every fact has an **exact, contiguous span of the stored page text**, computed by construction, never searched approximately or invented. The extractor abstains whenever exactness cannot be proven. Grounding, table-driven normalization, plausibility checks and review statuses follow, and everything is written atomically with a versioned `extract` run.
- **No LLM in the core.** ADR-024's provider evaluation becomes optional Batch G, which is not required for the phase gate (D-1).
- **Scope:** lab observations end to end. Medications, diagnoses and procedures are designed here (§3.3) and implemented in Phase 6 (ADR-019).
- **Schema:** migration `0004_extraction` extends the existing `0002` fact tables. It adds:
  - the run link and supersession, both as composite foreign keys with patient and document;
  - the source locator;
  - normalizer and reference versions;
  - date provenance;
  - a derived range comparison;
  - a conflict lifecycle.
- **Human corrections** live in the existing `review_decisions.corrected_values`. Original and normalized fact columns are never updated.
- **Identity `mismatch` blocks extraction.** `unknown` proceeds, labelled at display time.
- **Pipeline:** `extract` becomes a fifth stage, independent of `index`. A document becomes `processed` only when parse, classify, identity, index and extract have all completed.
- **Evaluation:** a new patient-split dataset `extract_v0`, with its held-out digest frozen before any rule is written. End-to-end metrics penalize missed facts. The system evaluation includes the classifier and identity gates. Targets are pre-registered in this plan, so held-out can run once without a further approval stop.
- **Docker:** the database is checked once. If it is unavailable, database-dependent acceptance is reported as **pending**, and Docker is not retried.

## 2. Current architecture assessment (verified against the repository)

### 2.1 Reusable without modification

| Component | Where | Use in Phase 3 |
|---|---|---|
| IR v1: `ParsedPage.text`, `ParsedTable` (grid validated, no overlaps), `TableCell` (`row`, `col`, spans, verbatim `text`, header flags), page `review_flags` | `app/ingestion/ir.py` | Extraction input, read only |
| Table-to-page-text rendering: each structurally valid table becomes one page-text segment. Its rows are sorted by start row and joined with `\n`; within a row, cells are sorted by start column and joined with `\t`; cell text is verbatim. Page text is the segments joined with `\n`. | `app/ingestion/docling_mapping.py` (`_place_table`, `_render_table_text`, `_build_page`) | The basis of exact table spans (§6.2). Not modified. |
| `pages.text` is exactly the IR page text: no strip and no normalization (`_page_record`) | `app/services/parsed_pages.py` | The offsets target |
| Verified IR loading: `read_published_manifest`, `parse_run_belongs_to_document`, `load_verified_parse_outputs` | `app/ingestion/parse_outputs.py`, repositories | Same three steps as `index_document` |
| Run rows: `pipeline_runs` with statuses `running`, `succeeded`, `failed` and **`skipped`** (CHECK exists), `component_versions` JSONB, `error_kind`, `error_class` | 0001 / `PipelineRun` | `extract` runs |
| Gate pattern: a pure predicate, a pre-check before work, and a re-check under the document lock in the write transaction | `app/records/chunks.py` (`_gate_refusal`, `get_index_target`, `insert_document_chunks`) | The extraction gate |
| `_has_succeeded_run`, `_lock_document` | `app/records/repositories.py` | Reused inside the records layer |
| Service failure pattern: `NOT_RECORDED_KINDS`, `_failure_kind`, `_record_failure` | `app/services/classify_document.py` | Copied by `extract_document.py` |
| Stage mapping: `_run_stage`, `error_kind` | `app/services/document_pipeline.py` | Unchanged logic, one more stage |
| `review_decisions`: `id`, `patient_id`, `fact_id`, `decision` (`verify`, `correct`, `reject`), `corrected_values` JSONB (CHECK: non-NULL exactly when `decision = 'correct'`), `note`, `decided_at`; FK `(fact_id, patient_id)` ON DELETE CASCADE. The ORM documents it as append-only at the application level; the database does not block UPDATE or DELETE. | 0002 / `ReviewDecision` | Corrections (§6.5), **no schema change** |
| Eval harness and the freeze / held-out protocol | `evals/dataset.py`, `metrics.py`, `report.py`, `run_classification.py` | `evals/run_extraction.py` |
| Generator and renderer (ReportLab, deterministic) | `evals/synthetic/{generator,render,catalog}.py` | Base for `extract_v0` |
| Pure identity and classifier rules: `check_identity`, `classify_pages` | `app/ingestion/identity.py`, `classification.py` | Imported read-only by the system evaluation. **Never edited:** their source SHA-256 is frozen. |

### 2.2 Verified current status behaviour
- Parse claim → `processing`; parse failure → `failed`; identity `mismatch` → `needs_review`, in the same transaction as the run.
- **No code sets `processed` today.**
- The parse claim already refuses a document in `processed` or `needs_review` (`_PARSED_STATUSES`), so a completed document can never be re-parsed.
- Indexing never changes `processing_status` (the `chunks.py` docstring).
- No code writes `extracted_facts`, `review_decisions` or `conflicts` today, and no review service exists.

### 2.3 Requires extension
- `app/records/models.py`: mirror migration 0004 exactly. The existing catalog-comparison integration test enforces this.
- `app/records/repositories.py`: `delete_document` closes conflicts that lose members (§5.6); documented as an ADR-034 follow-up.
- `app/services/document_pipeline.py`: a fifth stage.
- `app/services/index_document.py`: calls the completion rule after a successful insert.
- `app/services/library.py`, `app/main.py`, `app/ui/views.py`, `app/ui/texts.py`, `tests/test_architecture.py`.

### 2.4 New modules
- **`app/extraction/`** is a pure domain package: no database, services, Docling or torch. It contains:
  - `schemas.py`, `reasons.py`, `versions.py`, `region.py`, `date_policy.py`;
  - `spans.py` (exact span construction), `grounding.py`;
  - `normalize/{numbers,dates,units,ranges,analytes}.py`;
  - `reference.py` and `reference/*.csv`;
  - `lab/{headers,tables,lines,context,extract}.py`;
  - `checks.py` (plausibility, flags, duplicates inside a document);
  - `compare.py` (observation comparability, §6.6).
- `app/records/facts.py`: all extraction, review and conflict SQL, patient-scoped.
- `app/services/extract_document.py`, `app/services/review_fact.py`, `app/services/facts.py` (read models for the UI).
- `migrations/versions/0004_extraction.py`.
- `evals/synthetic/extraction.py`, `evals/run_extraction.py`, `data/synthetic/extract_v0/`.

### 2.5 Gaps in the existing fact schema
1. No run link.
2. No idempotency key.
3. No supersession.
4. No normalizer or reference version.
5. One date per observation, with no source span, no role and no time.
6. No non-blocking notes.
7. Conflicts have no idempotency key, no closed state and no history columns.
8. Character offsets are optional.

### 2.6 Constraints that must not break
- **ADR-016:** `patient_id` comes first in every function; another patient's id behaves exactly like a missing one.
- **ADR-029, 030, 032:** the IR, parse outputs and page text are read only.
- **ADR-036 freezes:** `identity.py` and `classification.py` are never edited.
- **ADR-023:** human statuses are set only through the review service, together with a decision row.
- **R1:** chunking, models, ranking and thresholds are unchanged; `CHUNKER_VERSION` stays `1`.

### 2.7 Frozen artifacts that stay untouched
- IR schema v1 and all published `data/parsed/**/v1/`.
- Migrations 0001–0003.
- `data/synthetic/{lab_v0,doctype_v0,identity_v0,identity_v1}`.
- Everything under `evals/reports/`.
- Classifier rules v2 and identity rules v2.
- The R1.8 runner's frozen fields.
- `app/ingestion/docling_mapping.py`. It is not frozen by a digest, but changing it would change parsing; Phase 3 only depends on its documented rendering.

### 2.8 Discrepancies found

| # | Finding | Handling |
|---|---|---|
| X1 | ADR-019 says the other fact tables are created by migration in Phase 6, and DATABASE_DESIGN says "Phases 3–4". Migration 0002 already created them in Phase 1. | Doc correction in Batch F |
| X2 | The Phase 3 outline and ADR-024 start with LLM provider evaluation. LangChain is not a dependency. | D-1 |
| X3 | ADR-029's "no migration 0004" refers to IR tables; 0004 becomes the extraction migration. | Doc note in Batch F |
| X4 | ADR-008 sends an ungrounded fact to `needs_review`. That would store unlocated text. | D-4 refinement |
| X5 | A tracked file named `tatus --short` exists at the repository root. | **Outside Phase 3; not touched** |
| X6 | Phase 2 PostgreSQL integration suites and R1.8 have never run (Docker unavailable). | Batch 0 checks once; otherwise pending |
| X7 | ARCHITECTURE §11 describes `app/llm/`; it does not exist. | Design only, unless Batch G runs |
| X8 | DATABASE_DESIGN lists `pipeline_runs.error`; the actual columns are `error_kind` and `error_class`. | Doc fix in Batch F |
| X9 | The ARCHITECTURE §4.1 diagram sets `processed` after indexing and fact storage; no code implements this. | Implemented by the completion rule (§9.3) |

## 3. Phase 3 scope

### 3.1 In scope (phase gate)
- Lab observations from `lab_report` documents:
  - analyte, value (numeric, comparator or qualitative), unit, reference range and printed flag;
  - specimen, when labelled;
  - collection date and time, and report date;
  - page and exact span.
- Deterministic normalization.
- A derived comparison against the document's own printed range.
- The review lifecycle with corrections.
- Same-date value conflicts with a full lifecycle.
- The pipeline stage, CLI, UI and offline evaluation.

### 3.2 Out of scope
- An LLM in the core (optional Batch G), ML or NER models, LOINC or SNOMED codes.
- Trends, timeline, cross-document duplicate grouping (Phase 4).
- Medications, allergies, diagnoses and procedures (Phase 6).
- Handwriting, PNG or JPEG upload, re-processing after a parser upgrade, identity override.
- The stray `tatus --short` file.

### 3.3 Later categories (designed now, implemented in Phase 6)

| Category | Existing table | Columns to add in Phase 6 | Rules (raw values always kept) |
|---|---|---|---|
| Medication | `medication_mentions` | `brand_name_raw`, `generic_name_raw` (only when both are printed), `dosage_form_raw`, `prescriber_raw`, `start_date*`, `end_date*` | `1-0-1` becomes three counts only when printed in that form. `OD`/`BD`/`TDS`/`QID`/`HS`/`SOS` come from a versioned table. A dose is never inferred from strength. `mention_context` comes only from a section label; without one it is `other`. |
| Diagnosis | `clinical_events` (`diagnosis_documented`) | `clinical_status_raw`, `code_system`, `code`, `code_source_version` | Labelled sections only. A code is stored only if printed. Never derived from lab values. |
| Procedure or event | `clinical_events` | `outcome_raw` | Labelled sections only. An outcome is stored only if printed. |
| Allergy | `allergy_mentions` | — | "NKDA" is stored as `negated = true`. |

## 4. Recommended extraction architecture

### 4.1 Comparison (unchanged from revision 1)

| Approach | Strength | Weakness for this project |
|---|---|---|
| A: rules | Exact, free, offline, explainable | Coverage limited to recognized layouts |
| B: local NER | Local | New dependencies; not built for Indian lab tables; no column semantics |
| C: LLM | Flexible on narrative text | Hallucination and alignment risk, cost or rate limits, slow on a CPU, hard to reproduce |
| D: hybrid | Best ceiling | Its value must be measured, not assumed |

R1.3 already measured on real Docling output that Docling found tables in all three `lab_v0` layouts, with 16 of 16 values verbatim in the table rows. **Recommendation:** rules in the core, with a model slot reserved behind the same contract: `prompt_version = 'none'` and `model_id = 'none'` until Batch G.

### 4.2 Flow (pure, no I/O)
```
verified IR  +  pages.text (must equal the IR page text, else the document is abstained)
  → lab/context.py  subject guard, labelled dates and times, specimen, page flags (each with an exact span)
  → lab/tables.py   header roles → row candidates, with offsets computed exactly (§6.2)
  → lab/lines.py    labelled lines, alias-mapped analytes only (§6.3)
  → grounding.py    excerpt == page_text[start:end]; analyte and value cells at their computed offsets
  → normalize/*     additive, versioned
  → checks.py       plausibility, flag vs range, duplicates and conflicts inside the document
  → ExtractionOutput  (candidates with status and reasons; abstention counts; no text in counts)
```

### 4.3 Extractor rules (`lab-rules-1`)
- **Header roles:** exact, case- and space-insensitive vocabulary.
  - `name`: Test, Test Name, Investigation, Parameter.
  - `value`: Result, Observed Value, Value.
  - `unit`: Unit, Units.
  - `range`: Reference Range, Biological Reference Interval, Normal Range, Ref. Range.
  - `flag`: Flag.
  - `method`, `specimen`.
  - **`historical`**: Previous, Previous Result, Last Value, Prev. These cells are never extracted.
- **Header rows:** rows whose cells carry `column_header`. If none are marked, the first row counts as the header only if it is non-empty and every cell matches the role vocabulary.
- **Table accepted** only with exactly one `name` and exactly one `value` column. Otherwise it is skipped as `table_unrecognized`.
- **Value cell with a trailing flag:** a cell like `14.2 H` splits into value and flag only when the trailing token is in the flag vocabulary (`H`, `L`, `High`, `Low`, `*`, `HH`, `LL`, `Critical`). `value_raw` keeps the whole cell.
- **Subject guard (`lab/context.py`, own version):** a page that labels more than one distinct patient name yields no facts (`multiple_subjects`). Every fact has `subject = 'patient'`, `assertion_type = 'direct_fact'` and `negated = false`. A "Negative" result is a qualitative value, not a negation.
- **Dates:** labels decide the role.
  - Collected, Collection Date, Sample Collected On → collection.
  - Report Date, Reported On → report.
  - Registered, Received → ignored.
  - Two different collection dates on one page → `conflicting_dates`; neither is chosen.

### 4.4 Abstention: no fact is written; a reason is counted
- **Document level:** `page_text_mismatch` (a `pages.text` row differs from the IR page text).
- **Table and page level:**
  - `table_unrecognized`;
  - `table_block_not_found`;
  - `table_block_not_unique`;
  - `table_block_not_rendered`;
  - `historical_column`;
  - `multiple_subjects`;
  - unplaced or multi-page tables.
- **Row level:**
  - `row_spans_rows` (a row covered by a multi-row span);
  - `cell_contains_separator` (a cell containing a tab or newline);
  - `merged_value_cell` (the value cell spans several columns);
  - empty, `--`, `NA`, `Pending` or `See note` values;
  - an empty analyte cell.
- **Line level:**
  - `line_analyte_unmapped` (lines require a mapped alias);
  - narrative text.

### 4.5 Extracted but flagged `needs_review`
- `unmapped_analyte`, `unknown_unit`, `missing_unit`, `unparsed_value`, `implausible_value`.
- `ambiguous_date`, `two_digit_year`, `unparsed_date`, `implausible_date`, `conflicting_dates`.
- `conflicting_value_in_document`, `flag_range_disagreement`.
- Page-flag reasons (D-5): `ocr_unverified_source`, `replacement_character_in_source`, `source_text_normalized`, `table_structure_invalid_source`, `invisible_text_layer_source`.

Anything else is `validated`. That means **only that every automated check passed**. It is not human verification and not clinical validation (§6.8). The UI shows it as "Auto-checked, not verified".

## 5. Database design (migration `0004_extraction`)

The migration follows the frozen style: literal names, no application imports. It is mirrored in `models.py` and checked by the existing catalog-comparison test.
- **Precondition:** `upgrade()` raises if `extracted_facts`, `review_decisions` or `conflicts` holds any row (no code writes them today, and new NOT NULL columns have no truthful backfill).
- **Downgrade:** drops only what 0004 adds, behind the existing downgrade guard.

### 5.1 `pipeline_runs`
- Add `UNIQUE (id, document_id, patient_id)` (`uq_pipeline_runs_id_document_id_patient_id`) as the target for composite foreign keys.
- New stage value `extract`. The stage is free text, so no CHECK change.
- `component_versions` of an `extract` run holds:
  - `extractor_version`, `normalizer_version`, `reference_data_version`, `date_policy_version`, `reference_date`, `region_profile`;
  - `facts_written`, `facts_needs_review`;
  - `abstentions` and `reasons` (reason → count);
  - `skip_reason` (skipped runs only).

  It never holds text, names or values.

### 5.2 `extracted_facts`

| Column | Type | Null | Rule |
|---|---|---|---|
| `extraction_run_id` | uuid | NOT NULL | FK `(extraction_run_id, document_id, patient_id)` → `pipeline_runs(id, document_id, patient_id)` ON DELETE CASCADE |
| `source_locator` | text | NOT NULL | CHECK `^p[0-9]+:(t[0-9]+:r[0-9]+\|l[0-9]+)$` (page, table, row) or (page, line) |
| `normalizer_version` | text | NOT NULL | |
| `reference_data_version` | text | NOT NULL | |
| `quality_notes` | text[] | NOT NULL, default `'{}'` | Non-blocking notes |
| `superseded_at` | timestamptz | NULL | |
| `superseded_by_run_id` | uuid | NULL | **FK `(superseded_by_run_id, document_id, patient_id)` → `pipeline_runs(id, document_id, patient_id)` ON DELETE CASCADE** |

**Constraints:**
- `UNIQUE (extraction_run_id, fact_type, source_locator)`.
- CHECK `char_start IS NOT NULL` (the existing pair CHECK then requires both offsets).
- CHECK `length(source_excerpt) <= 1000`.
- CHECK `(superseded_at IS NULL) = (superseded_by_run_id IS NULL)`.
- CHECK `superseded_by_run_id IS NULL OR superseded_by_run_id <> extraction_run_id`.

**How the composite FK works:**
- PostgreSQL's default `MATCH SIMPLE` skips the check when `superseded_by_run_id` is NULL, which means "not superseded".
- When it is set, the replacement run must belong to the **same document and patient**; the database refuses anything else.
- `ON DELETE CASCADE` is safe: a run is deleted only by deleting its document, which deletes the fact as well. `SET NULL` would be wrong, because it would null `document_id` and break the pair CHECK.
- The replacement run must be an `extract` run newer than the original. The repository enforces this under the document lock, and an integration test covers it.

**Indexes:** `ix_extracted_facts_current (patient_id, document_id) WHERE superseded_at IS NULL`, `ix_extracted_facts_extraction_run_id`.

### 5.2a Review status: existing schema and the 0004 changes

**As verified in migration 0002 and `models.py`:**
- `review_status` is `text NOT NULL`, with a server default of `'extracted'`.
- `ck_extracted_facts_review_status_values` allows `extracted`, `validated`, `needs_review`, `verified`, `corrected` and `rejected`. These are the `ReviewStatus` enum values.
- `ck_extracted_facts_needs_review_has_reasons` requires `needs_review` to have at least one reason.
- `review_reasons` is `text[] NOT NULL`, default `'{}'`.
- Nothing else constrains status. `review_decisions.decision` allows `verify`, `correct` and `reject`.

**Allowed persisted values after 0004:** `validated`, `needs_review`, `verified`, `corrected`, `rejected`.
- `extracted` stays in the values CHECK and the enum, because ADR-023 defines it as the extractor's transient in-memory state.
- A new CHECK stops `extracted` from ever being stored.

**0004 changes to `extracted_facts`:**
- `ALTER COLUMN review_status DROP DEFAULT`. Every insert must state its status explicitly, so a forgotten field fails loudly instead of storing `extracted`.
- Add CHECK `ck_extracted_facts_review_status_not_transient`: `review_status <> 'extracted'`.
- Add CHECK `ck_extracted_facts_validated_has_no_reasons`: `review_status <> 'validated' OR cardinality(review_reasons) = 0`.
- Keep `ck_extracted_facts_needs_review_has_reasons` and the values CHECK unchanged.
- `models.py` mirrors this: `server_default` is removed, and the two CHECKs are added.

**Initial status of a new fact** is decided in memory by the checks:
- `needs_review` with at least one reason when any check fails;
- otherwise `validated` with no reasons.

No other initial value is possible: the repository refuses anything else with a `ValueError`.

**Human statuses** (`verified`, `corrected`, `rejected`) are set only by `record_review_decision`. That function appends the `review_decisions` row and updates `review_status` in the same transaction. `review_reasons` are never cleared; after a review they remain as history, which is allowed because the reasons CHECKs apply only to `needs_review` and `validated`. The database cannot express "a human status has a decision row" without a trigger. ADR-023 rule 2 is therefore enforced by the repository and by an integration test that inserts facts through every public function and asserts that no human status exists without a decision.

**Supersession** never changes `review_status`.

### 5.3 `observations`

| Column | Type | Rule |
|---|---|---|
| `effective_date_source` | text NULL | CHECK IN (`collection`, `report`); NULL exactly when `effective_date` is NULL |
| `effective_time`, `time_raw` | time without time zone NULL, text NULL | Printed local time; no zone is invented |
| `date_page_number`, `date_char_start`, `date_char_end` | int NULL | All NULL or all set; `start <= end`; page existence is checked by the repository (the subtype has no `document_id`) and by an integration test |
| `ref_range_comparison` | text NULL | **Derived.** CHECK IN (`below`, `within`, `above`); NULL when not comparable; CHECK it is NULL when `value_num` is NULL |
| `analyte_match` | text NULL | CHECK IN (`alias_exact`); NULL when unmapped |

### 5.4 `documents`
No schema change. A report date that is valid and unambiguous fills `document_date`, `document_date_precision` and `document_date_raw` in the extraction transaction, only while they are NULL.

### 5.5 `review_decisions`
**No schema change.** The existing columns support corrections (§6.5). The append-only rule stays at the application level: `app/records/facts.py` offers no update or delete, and a test asserts that.

### 5.6 `conflicts` (lifecycle)

**Status and closure columns:**
- Status CHECK becomes `open | acknowledged | closed`. The existing constraint is dropped and recreated in 0004.
- Add `closed_at timestamptz NULL` and `closed_reason text NULL`. `closed_reason` takes one of: `member_rejected`, `member_corrected`, `member_reviewed`, `member_superseded`, `member_deleted`.
- CHECK `(status = 'closed') = (closed_at IS NOT NULL)`, and the same rule for `closed_reason`.

**History and idempotency columns** (so a closed conflict stays readable after members are gone):
- `analyte_id` int NULL (FK `analyte_catalog` ON DELETE RESTRICT);
- `effective_date` date NULL;
- `member_count_at_detection` int NULL;
- `detection_key` text NULL;
- `rule_version` text NULL.

**Constraints and index:**
- `UNIQUE (patient_id, detection_key)`.
- CHECK `(detection_key IS NULL) = (rule_version IS NULL)`.
- Partial index `(patient_id) WHERE status <> 'closed'`.

**Description and detection key:**
- `description` holds a fixed template code (`value_mismatch_same_date`), never values or names.
- The detection key is built from the conflict group and its members:
  ```
  sha256( rule_version | analyte_id | effective_date | effective_date_source |
          sorted over members: fact_id ":" display_fingerprint ":" state_token )
  ```
  - `display_fingerprint` is a canonical string of the compared display values (§6.6): the `Decimal` normalized to a string, the comparator, `unit_norm` and the qualitative class token.
  - `state_token` is the id of the member's latest decision that **changed its usability or its display values** (§6.7), or `machine` when no decision has done so.
  - The same facts with the same values and review state always give the same key, so re-detection is idempotent. A correction, a reversed rejection or a re-extraction gives a new key, so a genuinely new conflict can be opened even when the same facts still disagree.

**When `delete_document` (existing) runs**, in its transaction it first closes every non-closed conflict that has a member fact from that document (`member_deleted`), then deletes. The conflict row stays as history; its members cascade away as today.

### 5.7 Reference data (no schema change)
- `python -m app seed-reference` upserts `app/extraction/reference/{analytes,aliases,unit_conversions}_v1.csv` in one transaction. `qualitative_scales_v1.csv` is read by code only and versioned with the rest (§7.5). It refuses to change the meaning of an existing row.
- The pure normalizer reads the same CSVs.
- The extraction repository resolves `canonical_name → analyte_id` in the write transaction, and fails with `reference_data_not_seeded` if a name is missing.
- `loinc_code` stays NULL.

### 5.8 Isolation, idempotency, retries, re-extraction
- **Isolation:** a fact's patient equals its document's patient, its run's patient and its replacement run's patient, all enforced by composite foreign keys. Decisions and conflict members use the existing `(fact_id, patient_id)` foreign keys.
- **Atomic run:** facts are inserted only in the transaction that marks the run `succeeded`. A failed run has no facts.
- **One completed run:** an `extract` run that `succeeded` or was `skipped` makes a repeat refuse with `already_extracted`. The check runs under the document lock, following the ADR-036 A2 pattern.
- **Retry:** after a failure, a retry writes a new run; the failed run stays as history.
- **Re-extraction** is explicit only (`--reextract`), allowed only when the stored `extractor_version`, `normalizer_version` or `reference_data_version` differs from the current code. It is refused with `has_review_decisions` when any current fact has a decision (D-7). It runs in one transaction:
  1. write the new run and its new facts;
  2. supersede the old current facts;
  3. close the conflicts of those facts (`member_superseded`);
  4. run conflict detection on the new current facts.

## 6. Provenance model

### 6.1 Columns that answer "where did this come from"

| Question | Column(s) |
|---|---|
| Patient, document, page | `patient_id`, `document_id`, `page_number` (composite foreign keys) |
| Run and code versions | `extraction_run_id` → `component_versions`; `extractor_version`, `normalizer_version`, `reference_data_version`, `prompt_version`, `model_id` |
| Exact text | `source_excerpt == pages.text[char_start:char_end]` |
| Structural position | `source_locator` |
| Original values | `analyte_raw`, `value_raw`, `unit_raw`, `ref_range_raw`, `flag_as_printed`, `date_raw`, `time_raw` |
| Machine-normalized values | `analyte_id`, `value_num`, `comparator`, `value_qualitative`, `unit_norm`, `value_num_norm`, `ref_low`, `ref_high`, `effective_date`, `date_precision`, `effective_time` |
| Derived values | `ref_range_comparison` |
| Human-corrected values | `review_decisions.corrected_values` (§6.5) |
| Date provenance | `effective_date_source`, `date_page_number`, `date_char_start`, `date_char_end` |

There are four categories, never merged: extracted, normalized, derived and human-corrected. There is no AI-generated content in Phase 3.

### 6.2 Exact table spans (`spans.py`)

**Verified basis.** `docling_mapping._place_table` appends a structurally valid table's rendering to the page text as one segment. `_render_table_text` sorts rows by start row and cells by start column, then joins cells with `\t` and rows with `\n`, using verbatim cell text. `_structured_table` builds the IR cells from the same cell list, with `row` and `col` as start offsets. The page text is the segments joined with `\n`, and `pages.text` stores it unchanged.

**Steps:**
1. **Re-render.** `render_table_block(table)` reproduces that rendering from the IR cells: rows that have cells, in start-row order; cells by start column; tab and newline separators. A unit test feeds the same synthetic cell lists to the private Docling renderer (through simple adapter objects) and to ours, and asserts identical strings, including merged cells, empty cells and gaps. If the two ever diverge after a Docling or mapping change, the test fails and every table abstains as `table_block_not_found`; offsets are never guessed.
2. **Locate once.** The extractor counts exact occurrences of the block in `page.text`:
   - 0 occurrences → `table_block_not_found` (for example reordered text or OCR differences);
   - more than 1 → `table_block_not_unique`;
   - exactly 1 → that is the block's offset.
3. **Row and cell offsets are computed, not searched.** A row's start is the block start plus the lengths of the preceding rendered rows, plus one per newline. A cell's start inside the row is the sum of the preceding cells' lengths plus one per tab. Repeated values and repeated analytes therefore always resolve to their own row.
4. **Conservative row abstention:**
   - any cell of the row contains `\t` or `\n` → `cell_contains_separator` (a multiline cell makes row boundaries unprovable);
   - the row is covered by a cell with `row_span > 1` → `row_spans_rows`;
   - the value cell has `col_span > 1` → `merged_value_cell`;
   - the analyte or value cell is missing from the row → abstain.
5. **Excerpt and verification.** The excerpt is the whole rendered row line, `page_text[row_start:row_end]`. Before a fact is emitted, grounding re-checks four things and abstains on any failure:
   - `page_text[row_start:row_end] == row_line`;
   - the analyte cell text equals `page_text[name_start:name_end]`;
   - the same for the value cell;
   - `pages.text == ir_page.text`.

### 6.3 Exact line spans (`lab/lines.py`, `lab/context.py`)
A line is a maximal substring of `page.text` without `\n`, taken by index. The extractor matches its regular expression against that substring and records the match offsets plus the line start. Dates, times and specimens get their spans the same way. The excerpt is always a slice of `page.text`, never text that was rebuilt.

### 6.4 Required tests for spans

| Test | Expectation |
|---|---|
| Repeated value (two rows with "5.0") | Each fact points to its own row offsets |
| Repeated analyte (the same test in two tables or two rows) | Distinct locators and offsets; flagged `conflicting_value_in_document` if the values differ |
| Multiline cell (text containing `\n`) | Row abstained (`cell_contains_separator`) |
| Reordered table text (the page text contains the cells in another order) | `table_block_not_found`; no facts from that table |
| Merged cells (row span, column span on the value) | `row_spans_rows` or `merged_value_cell` |
| Same table rendered twice on a page | `table_block_not_unique` |
| `pages.text` differs from the IR | `page_text_mismatch`; no facts |
| Renderer equivalence with Docling's `_render_table_text` | Identical strings for the fixture grids |
| Property test over generated grids | `text[start:end] == excerpt` for every emitted fact |
| Real Docling output (`-m docling`, `extract_v0` dev) | Share of rows abstained by each span reason, reported |

### 6.5 Human corrections

**Storage.** A correction is one appended `review_decisions` row: `decision = 'correct'` and `corrected_values` JSONB (required by the existing CHECK). The JSON has a versioned shape, validated by a Pydantic model before the insert:
```json
{"schema": "observation-correction-1",
 "entered": {"value": "13.2", "unit": "g/dL", "ref_range": "13.0 - 17.0", "effective_date": "2024-03-25"},
 "normalized": {"value_num": "13.2", "comparator": null, "value_qualitative": null,
                "unit_norm": "g/dL", "value_num_norm": "13.2", "ref_low": "13.0", "ref_high": "17.0",
                "effective_date": "2024-03-25", "date_precision": "day", "ref_range_comparison": "within"},
 "normalizer_version": "norm-1", "reference_data_version": "<prefix>"}
```
- `entered` holds only the fields the user changed. Allowed fields: value, unit, reference range, effective date, analyte (`analyte_id`).
- `normalized` is the complete normalized view after applying the entered values, computed by the same normalizers. Numbers are stored as strings, so no precision is lost.

**Immutability:**
- On a correction, the only change to `extracted_facts` is `review_status`, set to `corrected` in the same transaction. `review_reasons` are kept as history.
- Raw, normalized and derived columns are never updated by any repository function. A test asserts that the only `UPDATE` statements on `extracted_facts` and `observations` in `facts.py` touch `review_status`, `superseded_at` and `superseded_by_run_id`.

**More than one decision.** Decisions are appended, never edited. They are allowed only on current (not superseded) facts, from any current status. The fact's status and display always follow the **latest** decision:

| Latest decision | Resulting `review_status` | Display values |
|---|---|---|
| (none) | `validated` or `needs_review` (machine) | Machine-normalized |
| `verify` | `verified` | Machine-normalized. "Verify" always means "confirm the original extracted values", so a verify after an earlier correction withdraws that correction. The UI words the action that way. |
| `correct` | `corrected` | This decision's `corrected_values.normalized` |
| `reject` | `rejected` | None (excluded everywhere except audit) |

**A rejected fact can be reviewed again.** A later `verify` or `correct` on a `rejected` fact is allowed: it is an explicit human reversal, not a silent one.
- The status becomes `verified` or `corrected` as in the table.
- The rejection decision stays in the history.
- Conflict recomputation (§6.7) runs in the same transaction.

**Read model** (`app/services/facts.py`, `FactView`):
- `original` (raw columns);
- `machine_normalized` (normalized columns plus `ref_range_comparison`, labelled derived);
- `correction` (the latest `correct` decision's `entered` and `normalized`, its time and its note, or None);
- `display`: the correction's normalized values when the status is `corrected`, otherwise the machine-normalized values. It always carries a `source` label (`machine` or `human_correction`).
- Every decision, listed as history.

Downstream code (Phase 4) reads `display` together with its label.

### 6.6 Conflict comparison (`app/extraction/compare.py`, pure, versioned `compare-1`)

**Candidates** are pairs of observations of the same patient with:
- the same non-NULL `analyte_id`;
- both current and in status `validated`, `verified` or `corrected` (needs-review and rejected facts excluded);
- equal `effective_date`, both with `day` precision;
- equal `effective_date_source`;
- if both have `effective_time` and the times differ, they are separate collections: no conflict.

**Values compared** are each fact's `display` values (§6.5).

**Comparability, deciding `equal`, `consistent`, `conflict` or `incomparable`:**

| Pair | Rule |
|---|---|
| Numeric vs numeric | Comparable only when both have `value_num_norm` and the same `unit_norm`, or when both lack a normalized value but their canonical `unit_norm` spellings are equal. Then exact `Decimal` comparison (`14.2 == 14.20`). Different → conflict. Missing or different units → **incomparable**. |
| Comparator vs numeric, or comparator vs comparator | Each value becomes an interval: `x` → [x, x]; `<a` → (−∞, a); `<=a` → (−∞, a]; `>a`, `>=a` likewise. Same unit rule as above. Disjoint intervals → conflict; overlapping → consistent (`<5` vs `3` is not a conflict). |
| Qualitative vs qualitative | **Analyte-specific, from reference data only** (`qualitative_scales_v1.csv`, §7.5). For the shared `analyte_id`, each canonical token maps to a declared class (for example `detected` and `not_detected`), and the analyte declares which class pairs are opposite. Same class → equal. A declared opposite pair → conflict. Anything else → **incomparable**: an analyte with no scale, a token not mapped for that analyte, graded tokens such as Trace or 1+ unless the analyte's scale maps them, and class pairs not declared opposite. No word is treated as a universal opposite. |
| Numeric vs qualitative | Incomparable |
| Unmapped analyte or missing value | Not a candidate |

Incomparable and consistent pairs create **nothing**.

**Grouping into conflicts: one conflict per connected component.**
- Within one group (patient, `analyte_id`, `effective_date`, `effective_date_source`), the usable facts are the nodes of a **conflict graph**. An edge joins two facts exactly when their pair is `conflict`. Pairs that are equal, consistent or incomparable get no edge.
- Each connected component with two or more facts becomes **one** conflict, and every fact in the component is a member.
- **Members of one conflict need not all conflict directly with each other.** They are only linked through a chain of direct conflicts. Example, all in the same unit: A = `< 5`, B = `7`, C = `< 4`. A conflicts with B and B conflicts with C (disjoint intervals), but A and C overlap, so A–C is `consistent`. All three are members of one conflict.
- The members and the conflict are inserted in one transaction.
- `conflict_members` stores membership only. The specific conflicting pairs are recomputed from `compare.py` when the conflict is displayed, so no schema change is needed.
- The detection key (§5.6) is computed over the component's sorted members. It therefore depends only on the component, not on the order in which pairs were found, and stays stable while the component is unchanged.

**UI rule.** A conflict is shown as "These values for the same test and date disagree", followed by the list of members and then **the specific pairs that directly disagree** (for example "A vs B"). The UI never states or implies that every pair of members disagrees.

**Required test** (Batch A, pure `compare.py` grouping; repeated in the Batch C integration tests):
- Three usable facts in one group, same unit: A = `< 5`, B = `7`, C = `< 4`. A conflicts with B, B conflicts with C, and A–C is `consistent`.
- Expected: exactly **one** component {A, B, C}, giving one conflict with three members; the direct pairs reported are exactly A–B and B–C, never A–C.
- Key stability:
  - the same inputs in any order give the same detection key, and re-detection creates no second conflict;
  - correcting C to `< 8` makes it consistent with both A and B, so C has no edge and the component becomes {A, B}. That gives a new key: the old three-member conflict closes as `member_corrected`, and a new two-member conflict {A, B} opens (§6.7).

### 6.7 Conflict lifecycle

**States:** `open` → `acknowledged` (user action) → `closed` (system, with a reason). A closed conflict is never reopened; it stays as history.

**Usable facts** are current and in status `validated`, `verified` or `corrected`.

**When a decision changes a member's state.** A decision changes a member's **usability** when the status before and after differ in whether they are usable. It changes the member's **display** when its display values differ:
- every `correct` changes the display;
- a `verify` that follows a correction changes the display back to the machine values;
- a `verify` on a `validated` fact changes neither usability nor display.

The id of the latest decision that changed either one is that member's `state_token`.

**One recomputation procedure** runs in the same transaction as each trigger. Triggers are:
- a review decision;
- a successful extraction or re-extraction;
- a document deletion (before the cascade).

The procedure works on each affected group, meaning (patient, `analyte_id`, `effective_date`, `effective_date_source`):
1. Lock the group's non-closed conflicts (`SELECT … FOR UPDATE`).
2. Compute the conflicting components over the group's usable facts with `compare.py`, and their detection keys.
3. A key that matches an existing non-closed conflict is kept unchanged, including its `acknowledged` status. This is the idempotent path: unchanged facts never churn.
4. Every other non-closed conflict in the group is closed with the trigger's reason: `member_rejected`, `member_corrected`, `member_reviewed` (a verify that changed usability or display, including the reversal of a rejection), `member_superseded` or `member_deleted`.
5. A key with no existing row is inserted as a new `open` conflict, with its members, `analyte_id`, `effective_date` and `member_count_at_detection`.
6. A key that matches only a **closed** conflict is not reopened and not duplicated. The unique constraint enforces this, and it is counted as `closed_key_recurrence`. The state token makes this impossible after any real change, so a non-zero count means a bug, and a test asserts it stays zero.

**Worked cases:**

| Case | Effect |
|---|---|
| Reject member A of conflict C1 | A becomes unusable, so C1's key disappears; C1 closes as `member_rejected`. A new conflict opens only if other usable members still conflict among themselves. |
| Later verify (or correct) of the rejected A | A becomes usable with a new state token, giving a new key. A new conflict C2 opens if A still conflicts. C1 stays closed as history. |
| Correct A, and the values still differ | New display fingerprint and token, so a new key. C1 closes as `member_corrected` and C2 opens. The acknowledgment on C1 stays in history. |
| Correct A so the values now agree | No component, so C1 closes as `member_corrected` and nothing opens. |
| Verify a `validated` member | No change to usability or display, so the same key: C1 stays as it is, including `acknowledged`. |
| Re-extraction | Old facts are superseded and the new facts have new ids. The old conflicts close as `member_superseded`, and the new facts are detected afresh. |
| Document deletion | Conflicts with a member in the document close as `member_deleted`, then the cascade removes the member rows. History keeps `analyte_id`, `effective_date` and the member count. |

**The current conflict view is a query, not a stored state:** status in (`open`, `acknowledged`) and every member current and usable. It needs no separate rebuild, because every trigger has already run the recomputation in its own transaction. **The history view** lists closed conflicts with their reason, time, analyte, date, original member count and any members that still exist. No rule ever picks a winner. Acknowledging changes only the status.

### 6.8 What `validated` means (terminology rule)
- **Database:** `validated` means only that the fact passed every automated check (grounding, span, plausibility, unit, date, analyte mapping). Migration 0004 adds `COMMENT ON COLUMN extracted_facts.review_status` saying exactly this. The catalog comparison ignores comments, so the ORM needs no change for it.
- **Never implied:** clinical validation, human verification, or correctness of the source document.
- **UI wording:**

  | Status | UI label |
  |---|---|
  | `validated` | "Auto-checked, not verified" |
  | `needs_review` | "Needs review" |
  | `verified` | "Verified by you" |
  | `corrected` | "Corrected by you" |

  The words "validated", "clinically", "accurate" and "normal" never appear as a status. A test over `app/ui/texts.py` asserts that.
- **Evaluation reports** use "auto-checked (status `validated`)" and carry a fixed footnote: automated checks only, synthetic data, no clinical validation.
- **Docs:** ADR-037 and DATABASE_DESIGN repeat the definition. ADR-023's "Unverified (auto-checked)" label becomes "Auto-checked, not verified" (wording only; the meaning is unchanged).

## 7. Normalization strategy

Pure functions, configured by the region profile (India, ADR-025), versioned as `norm-1`. **Each operation keeps the raw value in its own column. On failure, the normalized field stays NULL and a reason or note is added.**

### 7.1 Dates (`dates.py` with `date_policy.py`)

| Input | Output | Failure or flag |
|---|---|---|
| `25/03/2024`, `25-03-2024`, `25.03.2024` | 2024-03-25, `day` | — |
| `03/04/2024` | 2024-04-03 (DD/MM) | `ambiguous_date` (Q12 default: always flag) |
| `12-Mar-2024`, `12 March 2024` | 2024-03-12 | — |
| `2024-03-12` | ISO, only with a 4-digit year first | — |
| `Mar 2024`, `03/2024` | 2024-03-01, `month` | — |
| `12/03/24` | NULL | `two_digit_year` |
| `12 Mar` | NULL | `unparsed_date` |
| `31/02/2024` | NULL | `unparsed_date` |
| Before 1900-01-01, or after the reference date | Value kept | `implausible_date` |
| `10:30 AM`, `22:15` | time | Unparsed: `time_raw` kept, plus a note |

**Fixed date reference.** The extractor never reads the machine clock. `date_policy.py` (`DATE_POLICY_VERSION = "dates-1"`) defines:
- a fixed lower bound of 1900-01-01;
- an upper bound equal to an explicit `reference_date` argument.

The service passes the document's `created_at` (upload time) converted to a date in UTC. The column is immutable, so re-runs give the same result. The evaluation passes the fixed `reference_date` from the dataset manifest. Both values are recorded in the run's `component_versions`, and unit tests pin them.

### 7.2 Numbers and values

| Input | Output | Rule or failure |
|---|---|---|
| `14.2` | 14.2 | The `numeric` type keeps the scale |
| `1,50,000` | 150000 | Validated Indian grouping; malformed → `unparsed_value` |
| `1,5` | — | `unparsed_value` (no decimal comma) |
| `< 0.01`, `>= 90` | Comparator plus number | — |
| Positive, Negative, Nil, Trace, Reactive, Non-reactive, Absent, Present | Canonical qualitative token | Exact vocabulary only; the sign is never flipped; anything else → `unparsed_value` |
| `14.2 H` | 14.2 with flag H | Trailing flag vocabulary |

### 7.3 Units
1. **Spelling** comes from a synonym table:
   - `gm/dL`, `g/dl`, `gm%` → `g/dL`;
   - `/cumm`, `/cu.mm` → `/µL`;
   - the micro sign (`µ`, `μ` or `u`) is unified;
   - million per cumm → `10^6/µL`; lakhs per cumm → `10^5/µL`.
2. **Generic power-of-ten scales** are applied with exact `Decimal` arithmetic.
3. **Analyte-specific conversions** are applied only from CSV rows that cite a source, and only toward the catalog's `default_unit`. Otherwise the value is not converted (note `unit_not_convertible`).

An unknown unit gives `unknown_unit`. A missing unit for an analyte that is not unitless gives `missing_unit`.

### 7.4 Reference ranges
- `a - b`, `< b` / `Up to b`, and `> a` are parsed.
- These are left unparsed (both bounds NULL, note `ref_range_unparsed`): a range printed in a different unit, a multi-tier range, a sex- or age-specific range, or a text range.
- A missing range stays NULL and is never taken from the catalog.
- `ref_range_comparison` uses only this row's parsed bounds and is NULL when a comparator value straddles a bound.

### 7.5 Analytes and terminology
- The lookup key is NFKC, casefolded, with collapsed whitespace and a trailing `:` or `.` stripped. Parentheses are kept.
- Matching is **exact alias only**: no fuzzy and no substring matching.
- An unmapped name gives `analyte_id` NULL and `unmapped_analyte`.
- The catalog has the 25 `catalog.py` analytes plus the additions `extract_v0` needs. Every plausibility bound has a source note.
- **Qualitative scales** (`qualitative_scales_v1.csv`) are part of the reference data and the `reference_data_version`. Each row has: canonical analyte name, canonical token, class, and the analyte's declared opposite class pair, plus a source note. The table covers only the qualitative analytes in `extract_v0` (for example urine glucose, urine protein and a serology marker). Graded tokens are mapped only where the scale is explicit. Any analyte or token without a row cannot be compared. The pure `compare.py` and the seed command read the same file; it is held in code and in the run version, and no new database table is needed.
- No terminology codes (D-6).

## 8. Identity mismatch decision (unchanged)

| Option | Isolation | Provenance | Downstream | Verdict |
|---|---|---|---|---|
| Block extraction | No facts exist for the wrong person | The refusal is reported | No filter needed | **Recommended** |
| Quarantine | Breaks the composite-FK invariant; facts for an unknown person | — | Every query needs an exclusion; one missed filter leaks data | Rejected |
| Extract after manual review | Needs an override, which ADR-036 D6 excludes | — | — | Not available |

**Gate** (pre-check plus re-check under the lock; the precedence is the index gate's):
1. Pages exist (`not_parsed`).
2. `identity_check <> 'mismatch'` (`identity_mismatch`).
3. A succeeded `identity` run exists (`identity_not_checked`).
4. A succeeded `classify` run exists (`not_classified`).

A refusal writes no run, as indexing does. The document stays `needs_review` on a mismatch. Phase 2 identity behaviour is unchanged.

**Identity `unknown`:** extraction proceeds. The "identity not confirmed" label is read from `documents.identity_check` when facts are displayed.

The gate predicate `extraction_gate_refusal(...)` is a public pure function in `app/records/facts.py`, the same pattern as `chunks._gate_refusal`. The system evaluation calls this exact function.

## 9. Pipeline integration design

### 9.1 Placement
```
parse → classify ┐
      → identity ┴→ index    (unchanged gate)
                  → extract  (§8 gate; independent of index)
```

### 9.2 Skipped extraction, made consistent end to end

| Layer | `lab_report` | `other` or `unknown` doc type |
|---|---|---|
| Database run | `extract` run with `status = 'succeeded'` and facts | `extract` run with `status = 'skipped'`, `error_kind` NULL, `component_versions.skip_reason = 'unsupported_doc_type'`; no facts |
| Service result (`ExtractResult.outcome`) | `extracted` | `not_applicable` |
| Pipeline `StageResult` | `succeeded`, detail outcome `extracted` | `succeeded`, detail outcome `not_applicable`, `reason` None |
| Next pipeline run | `already_done` (`already_extracted`) | `already_done` (`already_extracted`: a skipped run counts as completed) |
| Document completion | Counts as complete | Counts as complete |
| UI | "N facts" | "Extraction not applicable (document type)" |

`error_kind` stays reserved for failures. `skipped` is used only for this "not applicable" case.

### 9.3 Stage results (extends ADR-036 A4)

| Earlier outcome | extract |
|---|---|
| Parse failed | blocked (`not_parsed`) |
| Classify failed | blocked (`not_classified`) |
| Identity failed | blocked (`identity_not_checked`) |
| Identity `mismatch` | blocked (`identity_mismatch`) |
| Identity `unknown` | runs |
| Index failed or blocked | runs (independent) |
| Doc type `other` or `unknown` | succeeded (`not_applicable`) |
| Extraction error | failed (`<kind>`); run failed; document stays `processing`; retried on the next pipeline run |
| Completed run exists | already_done |

### 9.4 Completion rule: `complete_document_if_ready(patient_id, document_id)`

The rule runs in its own short transaction with the document row locked. When **all** of the following hold, it moves `processing` to `processed`; it is idempotent:
- the document is `processing`;
- pages exist;
- succeeded `classify` and `identity` runs exist;
- `identity_check <> 'mismatch'`;
- a succeeded `index` run exists;
- an `extract` run that succeeded or was skipped exists.

It never touches `uploaded`, `failed`, `needs_review` or `processed`.

It is called at the end of a successful `index_document` and a successful or not-applicable `extract_document`, so every entry point completes the document: CLI, pipeline and UI.

Reviews do not change document status. A blocked, failed or missing stage leaves the document `processing`, and the UI shows why.

The parse claim already refuses a `processed` document (`_PARSED_STATUSES`).

### 9.5 Effects per file
- `document_pipeline.py`: `STAGES` becomes five stages. Add `_ALREADY_DONE_KIND[EXTRACT]`, `_BLOCKED_KINDS[EXTRACT]` and the property `extracted`. Update the docstring and ADR.
- `index_document.py`: call the completion rule after a successful insert. The gate is unchanged.
- `library.py`: `DocumentState` gains `facts_current`, `facts_needs_review` and `extract_blocked_reason`.
- CLI:
  - `extract --patient --document | --all [--reextract]`;
  - `facts --patient --document` (identifiers, analyte names, statuses and reason codes on the local terminal; nothing logged);
  - `seed-reference`.
- R1.8 runner: **no change**. A test asserts that its frozen fields are unchanged.

## 10. UI design (thin, service-driven)
- **Documents list:**
  - fact counts and needs-review counts;
  - five-stage progress;
  - "processed" can now appear;
  - "extraction not applicable" and "extraction blocked (identity mismatch)" texts.
- **Document detail:**
  - Header: type, identity badge, status, report date (or "not found").
  - Facts table: the printed values (analyte, value, unit, range, flag); the machine-normalized value; the "compared with this report's range" result, labelled derived; the human correction, if any, labelled "corrected by you" next to the original; the status badge and plain-language reasons.
  - Expanding a fact shows the page, the excerpt (rendered with `st.text`, never Markdown or HTML), the date source and its excerpt, the versions and the decision history.
- **Review actions**, one fact at a time:
  - verify;
  - correct (a form; the original stays visible);
  - reject.
- **Run history:** statuses, versions, counts and abstention reasons. No text.
- **Conflicts panel:** current conflicts side by side, with "acknowledge" as the only action; closed conflicts in a history expander with their reason.
- Session state holds identifiers only. There are no charts.

## 11. Security and safety
- **Logs, run rows, errors and results** carry identifiers, counts, reason codes, versions and durations only. JSON-formatter tests assert this.
- **Exceptions** carry fixed messages with no chained values.
- **User-entered data:** corrected values and notes are stored but never logged.
- **Conflict descriptions** are template codes.
- **Isolation of the core:** no new dependency, no network access, no model download.
- **Clinical boundary:**
  - no diagnosis or advice;
  - no wording such as "abnormal";
  - the only comparison is against the document's own range, and it is labelled derived;
  - missing information stays missing.
- **Demo mode:** synthetic patients only (`synthetic_only`).

## 12. Evaluation design

### 12.1 Dataset `extract_v0`
- **Construction:** fictional data; the disclaimer on every page; a fixed seed; a fixed `reference_date` in the manifest.
- **Split by patient** into dev and held-out (for example 11 and 5 patients). The held-out digest is frozen at build time, **before any extraction rule is written**. There is no train split: the rules are tuned on dev only.
- **Variants, in both splits:**
  - clean grid, plain and compact layouts;
  - difficult tables: multiline names, merged headers, merged cells, units inside the value cell;
  - OCR-like scans (rasterized with `pypdfium2`, already a dependency);
  - missing units, ranges or values;
  - qualitative results and comparators;
  - Indian grouping and lakh notation;
  - similar names (Total vs Direct Bilirubin; HDL vs LDL);
  - unit variants;
  - ambiguous dates and two-digit years;
  - collection vs report dates, with times;
  - repeated values and repeated analytes;
  - conflicting duplicate rows within a report;
  - family-member blocks on a page.
- **Traps:**
  - a Previous Result column;
  - footnote numbers;
  - a table of normal values with no results;
  - side-by-side tables.
- **Non-lab documents**, which must yield zero facts.
- **Source verification, kept separate by rendering mode:**
  - **Native PDFs** (`render_mode: native`): at build time, every `value_raw`, `analyte_raw`, `unit_raw` and `ref_range_raw` of the ground truth must appear in the PDF's visible text layer, read with PDFium. The build fails otherwise.
  - **Rasterized OCR-like PDFs** (`render_mode: raster`, with the DPI and noise seed recorded). Each one is generated from a **native source fixture** (`source_fixture`, its SHA-256 in the manifest).
    - The fixture passes the native check above.
    - The rasterized PDF must have **no** text layer (PDFium finds zero characters), so no hidden text can leak the answer.
    - Its ground truth is the fixture's ground truth: the printed truth, not whatever OCR returns.
  - **At evaluation time, rasterized pages:**
    - the verified Docling IR must show `parse_method = ocr` with the `ocr_text_unverified` flag; if not, the document is counted as `raster_not_ocr` and reported;
    - every extracted fact from such a page must carry `ocr_unverified_source` (D-5), so it is `needs_review`;
    - OCR misreads count as field errors against the printed truth;
    - provenance accuracy is checked against the stored IR page text (offsets and excerpt), never against the PDF text layer.

    Metrics are reported separately for native and rasterized documents.
- **Ground truth** per document:
  - `doc_type`, `true_subject`, `render_mode` and `source_fixture`;
  - `expected_facts[]`, with every raw, normalized and date field and `expected_reasons[]`;
  - `traps[]`;
  - `expected_abstentions[]`.

### 12.2 Runner `evals/run_extraction.py` (offline, no database)
- **Parsing:** Docling, with an IR cache.
- **Component mode:** the extractor on gold `lab_report` documents.
- **System mode:**
  1. classifier v2 on the pages (`classify_pages`);
  2. identity v2 (`check_identity`), run on each document twice: against the patient's **own** profile, and against a **swapped** profile from the same split;
  3. the gate (`extraction_gate_refusal`, the real function);
  4. the extractor.

  Expected results: own profile → extraction according to the doc type; a swapped profile that gives `mismatch` → **zero facts**; a swapped profile that gives `unknown` or `match` (the documented identity limits) → reported separately as `identity_limit_extractions`.
- **Freeze and held-out:**
  - `freeze` writes `extraction_selection.json`: all versions, the extractor source SHA-256, the dataset version, the held-out digest, the dev report SHA-256 and the pre-registered targets;
  - `heldout` refuses on any difference, writes `extraction_heldout.done` **before** it evaluates, and never runs twice.
- `lab_v0` dev may be scored as a secondary clean set. `lab_v0` held-out is not used.

### 12.3 Metrics
- **Matching:** predicted to gold, one-to-one, keyed on (document, page, `source_locator` row, analyte key). Unmatched predictions are false positives; unmatched gold facts are misses.
- **Entity level:** precision, recall, F1.
- **End-to-end field metrics** (misses count as errors). For each field (`value_num`, comparator, qualitative, `unit_raw`, `unit_norm`, `value_num_norm`, `ref_low`, `ref_high`, flag, `effective_date` with precision, date source, time):
  - field recall = correctly extracted field instances / gold field instances (every gold fact counts, including missed ones);
  - field precision = correct / predicted field instances;
  - field F1.
- **Fully-correct fact recall:** gold facts whose every key field is correct / all gold facts.
- **Matched-pair field accuracy:** reported as a diagnostic only, never as the headline.
- **Provenance accuracy:** the page is correct, and `excerpt == page_text[start:end]`, and the analyte and value cells sit at their recorded offsets.
- **Safety:**
  - unsupported fact rate;
  - trap extractions;
  - wrong-subject facts;
  - **facts written for mismatch-blocked documents** (system mode);
  - **silent error rate** (validated facts with any wrong key field / validated facts; the most important);
  - false extraction rate;
  - duplicate rate.
- **Review economics:**
  - needs-review rate by reason;
  - ambiguous-date flag rate (input to Q12);
  - abstention rate by reason, including span abstentions (§6.4);
  - abstention correctness against `expected_abstentions`.
- **Gate behaviour (system mode):**
  - mismatch block rate on swapped pairs;
  - extraction rate on own-profile `unknown` documents;
  - classifier-induced misses (gold lab reports classified `unknown` or `other`).
- **Cost:** seconds per page for the extractor alone.
- **Reports** state that the data is synthetic, that rules and data share one author, that the test is not blind, and that no clinical accuracy is claimed. They also state that "validated" means auto-checked only (§6.8).

### 12.4 Pre-registered targets (approved with this plan, D-12)

**Hard gates, on dev and held-out, system mode:**
- unsupported facts = 0;
- trap extractions = 0;
- wrong-subject facts = 0;
- facts for mismatch-blocked documents = 0.

**Dev targets:**

| Metric | Target |
|---|---|
| Silent error rate | ≤ 0.01 |
| Entity precision | ≥ 0.97 |
| Native-layout entity recall (component mode) | ≥ 0.95 |
| Native-layout fully-correct fact recall | ≥ 0.90 |

OCR and hard-variant recall are reported without a target.

**Protocol:**
1. Tune on dev only.
2. Freeze when the hard gates and the dev targets are met.
3. If the hard gates cannot be met on dev, do **not** run held-out; report it as pending with the failing cases.
4. If only dev targets are missed, freeze anyway, run held-out once, and report the shortfall honestly.

## 13. Dependency-ordered implementation tasks

There is no owner approval between batches. Each batch ends with focused tests and moves on. One regression run happens in Batch F, and one final report goes to the owner.

### Batch 0 — Preconditions (S)
- Check Docker once (`docker info`).
- If it is available, start the database and run the outstanding integration suites of Tasks 1.8, 2.7, 2.8 and 2.9 and R1.4 once. Record the counts.
- If it is unavailable, record "Docker unavailable" once and do not retry. Every database-dependent item later in this plan is then reported **pending**.
- Excluded: fixing unrelated Phase 2 failures (they are reported).

### Batch A — Extraction core, spans, normalization (pure)
- **3.1** `schemas.py`, `reasons.py`, `versions.py`, `spans.py`, `grounding.py`. Pydantic models are frozen, with `extra='forbid'`, `hide_input_in_errors` and `repr=False` on text fields. Tests: §6.4, including Docling renderer equivalence and property tests.
- **3.2** `region.py`, `date_policy.py`, `normalize/*`, `reference.py` and CSVs. Tests: every row of the §7 tables; `Decimal` exactness; a reference-date test that proves no clock is read (the extractor gets the date as a parameter, and a test patches `datetime` to fail if called).
- **3.3** `compare.py` and the detection-key function. Tests:
  - every row of the §6.6 table, including interval edges and unit mismatch;
  - analyte-specific qualitative scales: the same token pair conflicts for one analyte and is incomparable for an analyte without a scale; Trace is unmapped;
  - numeric vs qualitative;
  - key stability: same inputs give the same key; changed values or a changed state token give a new key.
- Architecture test: `app/extraction` imports no `sqlalchemy`, `psycopg`, `app.records`, `app.services`, `docling` or `torch`.

### Batch B — Dataset, lab extractor, dev evaluation (pure)
- **3.4** Build `extract_v0` first, with the digest frozen. Tests:
  - determinism, split disjointness and the digest;
  - native verification: the raw values are in the text layer;
  - raster verification: no text layer, a valid fixture link, and the fixture passes the native check (§12.1).
- **3.5** `lab/*` and `checks.py` (`lab-rules-1`). Rule tests use IR fixtures built in code.
- **3.6** `evals/run_extraction.py`, component and system modes, with scoring unit tests (the misses penalty and the identity cases). Dev measurement runs on real Docling output, and the dev report goes to `evals/reports/extract_v0/`.
- **Acceptance:** the dev report has every §12.3 metric. Hard gates met on dev, or the plan's stop rule applies.

### Batch C — Schema and persistence (needs PostgreSQL)
- **3.7** Migration 0004 and the models (§5). Tests:
  - upgrade and downgrade on an empty database;
  - refusal when data exists;
  - the catalog comparison;
  - every new CHECK, FK and unique constraint violated once, **including a cross-document or cross-patient `superseded_by_run_id`** and a self-supersession;
  - storing `extracted` is refused, and a missing status is refused (no default);
  - `validated` with reasons is refused;
  - `needs_review` without reasons is still refused.
- **3.8** `app/records/facts.py`:
  - `extraction_gate_refusal`, `get_extraction_target`;
  - `record_extraction`, `record_extraction_skipped`, `record_extraction_failure`;
  - `reextract_and_supersede`, `complete_document_if_ready`;
  - `list_facts`, `get_fact`, `list_fact_decisions`, `list_extraction_runs`;
  - `record_review_decision`;
  - `detect_conflicts_for`, `list_conflicts` (current or history), `acknowledge_conflict`;
  - `seed_reference_data` (an explicit unscoped exception, reference data only).

  Also update `delete_document` to close conflicts first.
- **Integration tests:**
  - an isolation matrix for every function;
  - atomicity (an injected failure leaves no facts and a failed run);
  - concurrency (two extractors; one wins, the other gets `already_extracted`);
  - skipped run → `already_extracted`;
  - the completion matrix;
  - the decision appended together with its status change;
  - no UPDATE of fact value columns (a statement audit test);
  - every conflict lifecycle transition in the §6.7 worked-case table, including a rejection followed by a verify, a correction that still conflicts (old conflict closed, new one opened), an unchanged re-detection (no churn, acknowledgment kept), and `closed_key_recurrence = 0`;
  - qualitative comparisons: an unmapped analyte or token creates no conflict;
  - every decision transition in the §6.5 table, each in one transaction with its conflict recomputation (an injected failure rolls back both);
  - deletion closes conflicts and keeps the history row.
- **If Docker is unavailable:** the code and tests are written, the unit tests use stand-ins, and integration tests are reported as **not run**.

### Batch D — Services, pipeline, CLI, review, conflicts
- **3.9** `extract_document`: load and verify the IR, check `pages.text` equals the IR text, extract with `reference_date = created_at` (UTC date), persist, detect conflicts, run the completion rule.
- **3.10** The five-stage pipeline, the completion call in `index_document`, the library and facts read services, and the CLI commands.
- **3.11** `review_fact.py`: verify, correct (validated `corrected_values`, normalization re-run, conflict closure and re-detection) and reject.
- **Tests:**
  - every stage combination with stand-ins, including the skipped mapping (§9.2);
  - redaction in logs;
  - CLI exit codes;
  - one end-to-end synthetic PDF reaching `processed` (integration; pending without Docker);
  - a mismatch document gets no facts.

### Batch E — UI
- **3.12** Everything in §10.
- **AppTest with stand-ins:**
  - the original, normalized, derived and corrected labels;
  - status wording per §6.8 (no "validated", "clinically", "accurate" or "normal" status text);
  - the "Verify (confirm original values)" wording, and verify or correct offered on a rejected fact in the audit view;
  - the badges;
  - `not_applicable` and blocked texts;
  - excerpts never rendered as Markdown;
  - corrections keep the original visible;
  - current vs history conflicts;
  - demo-mode restrictions.
- A manual run against PostgreSQL if available, else pending.

### Batch F — Freeze, held-out, documentation, final report
- **3.13** Apply the §12.4 protocol: freeze, then run held-out once (or report it as pending).
- **3.14** Documentation:
  - ADR-037: extraction approach, spans and provenance, corrections, the date policy.
  - ADR-038: the gate and mismatch policy, the skipped semantics, the completion rule, the five-stage contract, the conflict lifecycle.
  - The proposed ADR-008 refinement (D-4) and ADR-021 status.
  - DATABASE_DESIGN, ARCHITECTURE §4.1 and §6, USER_FLOWS F2, F3 and F7, IMPLEMENTATION_PLAN, ROADMAP note, README (measured results only), PROGRESS, SUMMARY.
  - Fixes X1, X3, X8 and X9.
- **3.15** One regression run: the default suite, plus integration (if the database is available) and `-m docling` extraction tests.
- **3.16** One final report to the owner: what ran, measured results, pending items (Docker), deviations from this plan. Nothing is staged or committed.

### Batch G — Optional LLM comparison (D-1; not part of the phase gate)
- Run the ADR-024 evaluation on about 20 `extract_v0` dev pages.
- Candidates, revisions, licences and RAM are verified at that time.
- Add `app/llm/` (provider interface, prompt registry, response cache).
- LLM candidates pass through the same span check. Text that cannot be located is discarded and counted.
- Report it as an ablation.
- Runs only if the owner selects it in D-1.

## 14. Risks and trade-offs

| Risk | Mitigation |
|---|---|
| Docker unavailable | Checked once; pure batches complete; database acceptance reported as pending |
| A Docling or mapping change breaks the rendering equivalence | The equivalence test fails, and tables abstain (`table_block_not_found`); offsets are never guessed |
| Conservative span rules lower recall (multiline and merged cells) | Measured per reason; a safety-first trade-off |
| Rules overfit to self-made templates | Held-out frozen before any rule; traps; caveats stated |
| Review fatigue from strict flags | Flag rates measured; Q12 decided with data (closed 2026-11-18: flagging retained at 129/394 = 32.7% of dev facts) |
| Docling's text normalization damages ranges | `source_text_normalized`; range parsing fails closed |
| The five-stage contract change | Documented in ADR-038; pipeline and UI tests updated in the same batch |
| Re-extraction would lose reviews | Refused when decisions exist |
| Conflict noise | Strict comparability; needs-review facts excluded; incomparable pairs never conflict |
| Unsourced catalog bounds | A source note per row; flags never change values |
| Accidental edits to frozen identity or classifier files | Explicit rule (§2.6); import only |

## 15. Acceptance criteria for the complete phase
1. **Provenance:** every stored fact has patient, document, page, run, locator, exact excerpt and offsets, raw and normalized values kept separate, and versions. The offsets are verified by constraints and tests.
2. **Span exactness:** every emitted excerpt equals `pages.text[start:end]`; the §6.4 tests pass; span abstentions are measured.
3. **Supersession:** the composite FK rejects a replacement run of another document or patient (integration).
4. **Identity:** no facts for any `mismatch` document, in both the integration tests and the system evaluation.
5. **Hard gates:** all zero on dev and held-out; dev targets met or the shortfall reported; held-out run exactly once, or pending under the stop rule.
6. **Status and stages:** `processed` only through the completion rule; skipped runs consistent across the database, service, pipeline and UI; failures leave `processing` and can be retried.
7. **Corrections and review status:**
   - corrections are stored in `review_decisions`;
   - fact value columns are never updated;
   - the read model separates original, normalized, derived and corrected values;
   - only `validated` or `needs_review` are ever stored by extraction (`extracted` is refused by the database);
   - human statuses always have a decision row;
   - the latest decision decides status and display, including after a rejection.
8. **Conflicts:**
   - created only for comparable values; qualitative comparisons only through analyte-specific scales;
   - the detection key is idempotent for unchanged facts and new after a value or review-state change;
   - current vs history behaves as in §6.7;
   - nothing is auto-resolved.
8a. **Terminology:** `validated` is never presented as human or clinical validation (§6.8), in the database comment, the UI or the reports.
8b. **Evaluation provenance:** the native and rasterized verification paths are kept separate (§12.1); rasterized facts carry their OCR provenance.
9. **Isolation:** the matrix passes for every new function.
10. **UI:** AppTest passes.
11. **Hygiene:** no content in logs; no new core dependency.
12. **Reporting:** one final report; Docker-dependent items are explicitly marked pending if they were not run.

## 16. Decisions requiring owner approval (once, with this plan)
- **D-1:** Deterministic core. Batch G (LLM comparison) is optional and not run unless selected.
- **D-2:** Block extraction on `mismatch`. For `unknown`, label the facts at display time.
- **D-3:** Migration 0004 as in §5, including:
  - the review-status changes of §5.2a: no default, `extracted` never stored, `validated` without reasons;
  - the conflict status value `closed` and the closure columns;
  - the value-and-state detection key;
  - the refusal when fact data exists.
- **D-4:** ADR-008 refinement. Text that cannot be located is never stored; located but inconsistent facts go to `needs_review`.
- **D-5:** Facts from pages carrying OCR, replacement-character, Docling-normalization, invalid-table-structure or invisible-text-layer flags are `needs_review`.
- **D-6:** No terminology codes in Phase 3.
- **D-7:** Re-extraction is explicit only, and refused when decisions exist.
- **D-8:** Medications, diagnoses and procedures stay in Phase 6.
- **D-9:** The lab extractor runs on `lab_report` only. Other types get a skipped run, reported as `not_applicable`.
- **D-10:** Store the derived range comparison against the document's own range.
- **D-11:** The date policy uses the document's upload date (UTC) as the future-date reference.
- **D-12:** The pre-registered evaluation targets and stop rule in §12.4.
- **D-13:** Review semantics:
  - the latest decision decides status and display;
  - "verify" always confirms the original extracted values, so it withdraws an earlier correction;
  - a rejected fact may later be verified or corrected, with conflicts recomputed in the same transaction.
- **D-14:** Qualitative values are compared only through analyte-specific scales in the reference data. Anything unmapped is incomparable.
- **D-15:** The UI label for `validated` changes from ADR-023's "Unverified (auto-checked)" to "Auto-checked, not verified". This is wording only.

## 17. Recommended implementation order and workflow
1. Batch 0 (Docker checked once).
2. Batch A, then B, then C, then D, then E, then F.
3. Batch G only if selected.

The owner approves this plan once, and the agent executes all batches in coordinated order without per-batch approvals. Database-dependent work is written and unit-tested either way. Its integration acceptance is reported **pending** if Docker was unavailable at the single check. Nothing is staged, committed or pushed; the owner reviews the single final report.


---

## Implementation note (2026-11-17)

- **Batch 0:** Docker was checked once (`docker info` failed: the Docker Desktop engine pipe was not found) and not retried. Every database-dependent acceptance item below is **pending**.
- **Deviations from this plan, each recorded in ADR-037 or ADR-038:**
  - the extractor version is `lab-rules-2`: `lab-rules-1` was measured once on dev, then the labelled-line rule was extended to read several `name: value` segments that Docling had joined onto one line (only when the segments tile the whole line and every name maps). Both dev reports are kept;
  - the source-locator format gained an optional segment: `p<page>:l<line>:s<k>`;
  - the closed-conflict reasons gained `member_added` (a first extraction can join an existing conflict group).
- **Measured on `extract_v0`** (synthetic; not blind; no clinical accuracy claimed): see ADR-037 and `evals/reports/extract_v0/`. Hard gates 0 on dev and held-out; pre-registered dev targets met; held-out run once.
- **Not run:** PostgreSQL integration tests (`tests/integration/test_facts_integration.py`, 24 tests, skipped), the live Streamlit run against a database, migration 0004 against a database. Batch G (LLM comparison) not started; it runs only if the owner selects it.

## Verification note (2026-11-18)

- **Q12 closed:** `ambiguous_date` review retained (129 of 394 dev facts, 32.7%; see DECISIONS.md, Q12). No rule, dataset or report changed.
- **PostgreSQL acceptance attempted once, blocked:** `docker info` failed (Docker Desktop engine pipe `dockerDesktopLinuxEngine` not found) and nothing was listening on `127.0.0.1:5432`. As instructed, it was not retried.
- **Therefore still not run** (none passed, none failed): migration 0004 against PostgreSQL and the schema-versus-ORM check; `seed-reference` and its repeatability; the integration suites (the 24 tests in `test_facts_integration.py` and the earlier ones, 205 in total as last counted on 2026-11-17); the Streamlit end-to-end run (parse, classify, identity, index, extract); and the database-level checks of persisted facts, provenance, identity-mismatch blocking, review and correction, the conflict lifecycle and the processed-state gate. These remain the open acceptance items of this phase. *(Completed later the same day; see below.)*

## PostgreSQL acceptance note (2026-11-18)

- **Database:** container healthy, `pg_isready` accepting connections.
- **Migration 0004:** applied to `medimind` after one fix (check-constraint drops by `op.f()`). The live schema matches the models: `compare_metadata` reports 0 differences; 173/173 columns; constraint and index names agree. The full-definition catalog comparison passed as an integration test.
- **Reference seeding:** 31 / 89 / 9 rows on the first run, 0 added on the second, identical table hashes.
- **Integration tests:** **205 passed, 0 failed, 0 skipped** (24 extraction + 181 earlier). The first run had 41 failures; the causes and fixes are in PROGRESS.md (2026-11-18) and ADR-038: the 0004 drop names, the locator check's bind-parameter colons, JSON `null` in nullable JSONB, SQLSTATE `23001` for RESTRICT, the relative data-dir upload check, and tests not yet updated for 0004.
- **End to end** (Streamlit script via `AppTest`, fictional `extract_v0` dev documents, persisted state checked by SQL), all passed as listed in PROGRESS.md:
  - five-stage processing to `processed`; `not_applicable` extraction for an `other` document;
  - exact spans and run links for all 41 facts;
  - native facts equal to gold on every field;
  - `ambiguous_date` on 28/28 expected facts;
  - mismatch blocking (0 chunks, 0 facts);
  - `unknown` identity labelled and extracted;
  - verify, correct and reject with append-only history;
  - the conflict lifecycle: open, acknowledged, closed `member_rejected`, a new conflict, closed `member_corrected`;
  - idempotent repeats; cross-patient refusal.
- **Not seen in the UI run:** an `unknown`-identity document with extracted facts (both `unknown` documents had none). Atomicity, locking, re-extraction refusal and deletion are verified by integration tests only.
- **Unchanged:** extraction rules, `extract_v0` data, all evaluation reports, migrations 0001–0003. Held-out was not re-run; Batch G not started.
