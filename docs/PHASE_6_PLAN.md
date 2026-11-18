# Phase 6 Plan — Multi-document healthcare intelligence

> **Status (2026-11-18):** written at the start of Phase 6 on the owner's instruction (Phase 6
> master execution prompt), after a repository audit. Design decisions are recorded as ADR-042 in
> [DECISIONS.md](DECISIONS.md). The owner's phase-gate approval is still required (ROADMAP "Phase
> gates"). Measured results are in [PHASE_6_EVALUATION.md](PHASE_6_EVALUATION.md).
>
> **Implementation (2026-11-18):** tasks 6.0–6.13 done and verified. Dev and held-out (once) met every pre-registered target, with all hard gates 0.

## 1. Objective

Extend MediMind from lab reports to the other documents a patient keeps: prescriptions, discharge
summaries, consultation notes, medical-history records, allergy records and imaging reports.
Extract documented medications, allergies and clinical events with exact provenance; compare them
across documents; detect conflicts without resolving them; answer record questions with citations;
measure it all on a new frozen synthetic dataset.

Out of scope (product boundaries, CLAUDE.md): diagnosis, treatment or medication advice, "current
medication" inference, automatic conflict resolution, any LLM or external service.

## 2. Audit findings that shaped the plan (Task 6.0)

| Finding | Consequence |
|---|---|
| `medication_mentions`, `allergy_mentions`, `clinical_events` exist since migration 0002 (class-table inheritance, ADR-021) and have never been written | No new fact tables. Migration 0005 only adds columns. |
| `DocType` allows `prescription` and `discharge_summary`, but the frozen Phase 2 classifier (rules v2, held-out evaluated) emits only `lab_report / other / unknown` | The Phase 2 classifier and `documents.doc_type` stay unchanged. A separate Phase 6 classifier writes a new column, `documents.clinical_doc_type`. |
| The lab `extract` stage records a `skipped` run for every non-lab document, and that completes the stage; the frozen lab evaluation imports `extraction_applies` | The lab stage is not changed. Phase 6 adds a separate stage, `clinical_extract`. |
| `_recompute_conflicts` closes every non-closed conflict whose key the observation detector does not report | Clinical conflicts are **not** stored in `conflicts`. They are derived at read time, as Phase 4 does for duplicates (ADR-039). |
| `record_review_decision` works on any fact type | It is reused for clinical facts unchanged. A clinical correction schema is added in a new service. |
| Docling merges several printed lines into one text line (seen in the cached `doctype_v0` IRs) | Extraction segments by field labels and item markers inside a line. The locators are `p<page>:l<line>:s<k>`, already allowed by the 0004 CHECK. |
| `copilot_v1` contains refusal probes such as "Should I stop taking my diabetes tablets?" | Clinical Q&A runs only **after** the existing input guardrails. A test proves that the clinical router claims none of the 592 `copilot_v1` questions. |
| Baseline (2026-11-18, before any change): default suite 1919 passed / 282 deselected | This is the regression reference. |

## 3. Supported document types (claimed only where tested and evaluated)

`clinical-doctype-1` (pure, rule-based, `app/extraction/clinical/classify.py`) runs only on documents
whose Phase 2 type is `other` or `unknown`. Lab reports stay with the lab extractor.

| `clinical_doc_type` | Extracted |
|---|---|
| `prescription` | Medications (`prescribed` / `discontinued`). A consultation event (the clinician line, dated by the prescription date). |
| `discharge_summary` | Admission and discharge events (facility line plus their own dates); documented diagnoses; procedures; discharge medications (`discharge_list`); follow-up instructions; allergies |
| `consultation_note` | Consultation event; documented symptoms (chief complaints); diagnoses; past history (`history` context); medications; follow-up; allergies |
| `history_record` | Past diagnoses and past procedures (`history` context, with their own year when printed); family history (`subject = family`, never a patient fact); allergies; medications (`history` context) |
| `allergy_record` | Allergies: substance, reaction, severity, status (`confirmed` / `suspected` / `none_known`) |
| `imaging_report` | An imaging event (study line plus study date). Findings and impression are not structured; they stay searchable through retrieval. |

Abstentions: no confident family → `unclassified`; two strong families → `ambiguous_type`; an
invoice, letter or booking slip → `unsupported_type`. Each gives a `skipped` run with that reason,
writes no facts, and is shown in the UI as needing review. A classification is never guessed.

## 4. Data model (migration 0005_clinical; additive and reversible)

The smallest change that supports the requirements:

- `documents.clinical_doc_type` (nullable; CHECK over the six values). Written only by the
  `clinical_extract` stage.
- **All three subtype tables** gain:
  - `date_source` (`document` = the document's own date; `stated` = a date printed for this event or item). This separates document date from event date.
  - The date's own span: `date_page_number`, `date_char_start`, `date_char_end`, all-or-none, as observations have had since 0004.
- `medication_mentions` gains the normalized fields. They are kept separate from the existing raw columns, which are never overwritten:
  - `name_match` (`generic_exact | brand_alias`);
  - `dosage_form`;
  - `strength_value` and `strength_unit` (paired);
  - `route_norm`;
  - `frequency_per_day`;
  - `as_needed`;
  - `duration_value` and `duration_unit` (paired).
- `allergy_mentions` gains `allergy_status` (NOT NULL: `confirmed | suspected | none_known`), `substance_norm` and `severity_norm`.
- `clinical_events` gains `event_context` (NOT NULL: `encounter | history`). The `event_type` CHECK widens to add `imaging` and `symptom_documented`. No existing value is removed.

Precondition: the three subtype tables must be empty. No code wrote them before Phase 6. The
upgrade raises inside its transaction otherwise, as 0004 does. The downgrade refuses while
any clinical fact exists, so no row is ever silently dropped. Downgrade also needs the existing
database-name confirmation guard.

No new conflict table: clinical conflicts are derived (§7).

## 5. Pipeline stage `clinical_extract`

`app/services/extract_clinical.py` and `app/records/clinical.py`:

1. Pre-check with the same gate as lab extraction (`extraction_gate_refusal`: pages, identity not `mismatch`, classify and identity runs succeeded).
2. Phase 2 type `lab_report` → `skipped` (`lab_report_handled_by_lab_extractor`).
3. Load the verified IR (the same path as lab extraction), then run `clinical-doctype-1`. On abstention → `skipped` with the reason.
4. `extract_clinical_facts` (pure) → drafts with exact spans.
5. One transaction:
   - lock the document;
   - re-check the gate;
   - refuse if a completed `clinical_extract` run exists (`already_extracted`: idempotent);
   - write the run and all facts, and set `clinical_doc_type`.

   It is all or nothing.
6. Failures are recorded as a `failed` run (best effort), and the original error is re-raised.

The document pipeline gains a sixth stage after `extract`. The completion rule (`processed` = index
plus lab extract) is **unchanged**, so Phase 2–5 status behaviour is backward compatible.
Re-extraction of clinical documents is not offered in Phase 6 (documented limitation).

## 6. Extraction strategy (`clinical-rules-1`, deterministic)

- **Segmentation.** Field labels (`Date of Admission:`, `Final Diagnosis:`, `Allergies:`, `Rx`, `Advice on Discharge:` …) are found anywhere in a line. A field's value runs to the next label or the end of the line. List sections continue onto the next lines only while those lines start with an item marker (`1.`, `-`, `Tab.`, …).
- **Medication items.** A form prefix (`Tab.`, `Cap.`, `Syp.`, `Inj.`, …) or a numbered item. Within an item:
  - name;
  - strength (`500 mg`);
  - frequency (`1-0-1`, `OD`, `BD`, `TDS`, `QID`, `HS`, `SOS`, `once daily`, …);
  - route;
  - duration (`for 5 days`, `x 2 weeks`);
  - instructions (`after food`).

  Each part is stored raw with its exact span and normalized separately. Never invented:
  - a missing part stays NULL, with quality note `<part>_not_documented`;
  - a duration is never converted to an end date;
  - `month` is never converted to days.
- **Medication names.** Matched against a versioned **fictional** lexicon (`medications.csv`, ADR-005: synthetic names only; a real deployment needs a licensed drug database).
  - generic → `generic_exact`;
  - brand → `brand_alias`;
  - a brand naming two generics → `ambiguous_medication_name` (review, `name_norm` NULL);
  - unknown → `unmapped_medication` (review).
- **Allergies.**
  - `No known (drug) allergies` / `NKDA` → `none_known`, `negated = true`;
  - `suspected` or `?` → `suspected`;
  - otherwise `confirmed`;
  - `Allergies: not recorded` → **no fact**. Unknown stays unknown: an empty list is never "no allergies".
- **Events.** Admission and discharge dates are `stated`. Consultation and imaging take the document date (`document`). History items carry their own printed year (`stated`, year precision) or no date.
- **Dates.** These reuse the Phase 3 policy (`dates-1`):
  - DD/MM;
  - `ambiguous_date` review;
  - no two-digit-year expansion;
  - `implausible_date`.
- **Guards.** These reuse Phase 3:
  - multiple patient names on a page → abstain;
  - OCR pages → `ocr_unverified_source` review;
  - every excerpt and part must be an exact slice of the stored page text (grounding).

No model is used. Model-assisted extraction is deferred (no approved provider, ADR-024). Any
future model path must implement the same draft contract and be compared with this baseline.

## 7. Comparison and conflicts (`clinical-compare-1`, pure, read time)

Inputs are only **usable** facts: `validated`, `verified` or `corrected`, current (not
superseded), and `subject = patient`. A correction's values replace the machine values for
comparison, and the originals stay visible.

- **Document comparison** (A, then B):
  - each medication, allergy or diagnosis is `new` (only in B), `missing` (only in A), `repeated` (equal), or `changed` (same item, different normalized strength, frequency, form or route; for allergies, a different status, reaction or severity);
  - an unmapped name is matched only by exact normalized text and is marked `uncertain_match`;
  - both sources are always cited.
- **Conflicts** (patient-wide; never resolved; the latest document is not assumed correct):
  - `medication_same_date_mismatch`: the same medication documented on the same day-precision date in two documents, with a different strength or frequency;
  - `allergy_contradiction`: a `none_known` statement and a `confirmed` or `suspected` allergy in different documents;
  - `allergy_status_disagreement`: the same substance `confirmed` in one document and `suspected` in another.

  Each conflict lists both facts, with document, page, excerpt and the differing fields.

## 8. Records Q&A and summaries

`app/answering/clinical.py` (pure) plus `app/services/clinical_records.py`:

- **Intents:**
  - medications documented (optionally in a year or "the last six months" relative to an explicit `as_of` date);
  - prescriptions in a period;
  - hospital admissions;
  - allergies;
  - records mentioning a named medication;
  - document changes ("what changed between …" → the latest two documents of a type, or the two named ones);
  - medication conflicts;
  - chronological history summary.
- Every answer item is a stored fact with document, page and exact excerpt.
- **Labels:**
  - **documented** (a fact);
  - **derived** (chronological order, period filters);
  - **uncertain** (needs review: shown separately, never mixed in);
  - **conflicting**;
  - **unavailable** (an explicit "nothing documented" that is never read as "none").
- **Order:**
  1. the existing input guardrails (advice, diagnosis, injection, general knowledge) run first;
  2. then the clinical router;
  3. then the unchanged Phase 5 lab copilot.

  Unrecognised clinical questions abstain.

## 9. UI (thin)

There is a new **Clinical records** page:

- medications;
- allergies (with the explicit "unknown" state);
- clinical timeline;
- compare two documents;
- conflicts;
- ask (records Q&A).

There is a review form for clinical facts: verify, correct or reject. Sources are shown as
document, page and excerpt, and uncertain items are highlighted. The library shows the clinical
type and the `clinical_extract` stage. The existing pages are unchanged.

## 10. Evaluation (`clinical_v1`; protocol as Phases 3–5)

- **Dataset.** `data/synthetic/clinical_v1`: fictional patients split by patient (dev / held-out). It is generated, hashed and **frozen before any extraction rule is written**, and the held-out digest is recorded at build time.
- **Documents.** Every type in §3, plus:
  - contradictory allergy records;
  - same-date medication conflicts;
  - missing dates;
  - ambiguous dates;
  - ambiguous and unknown medication names;
  - multi-page documents;
  - a raster (OCR) copy;
  - an exact re-scan duplicate (repeated, not a conflict);
  - unsupported documents.

  Held-out-only label variants test generalization honestly. They share an author with the rules (a limitation, stated).
- **Pipeline under test.** The real Docling parser (IRs cached), Phase 2 classifier v2, identity v2, the extraction gate, `clinical-doctype-1`, `clinical-rules-1`, then `clinical-compare-1` and the Q&A.
- **Metrics:**
  - classification accuracy and abstention;
  - field precision, recall and exact match per fact type;
  - date accuracy;
  - provenance precision and recall (page and exact excerpt);
  - conflict precision and recall;
  - comparison accuracy;
  - Q&A routing, correctness, citation validity and abstention;
  - unsupported facts (must be 0);
  - patient-isolation leaks (must be 0, swapped-profile mismatches extract nothing).
- **Protocol.** `dev` is repeatable; `freeze` records versions, code digest and pre-registered targets; `heldout` runs **once**.
- **Regression.** The full default and integration suites, and `copilot_v1` dev (repeatable) with results equal to the accepted ones.

## 11. Tasks

| Task | Content | Done when |
|---|---|---|
| 6.0 | Audit and baseline | Baseline recorded (§2) |
| 6.1 | This plan and ADR-042 | Written |
| 6.2 | `clinical_v1` generator, dataset, frozen digest, Docling IR cache | Dataset loads and verifies; IRs cached |
| 6.3 | Framework: classifier, segmentation, schemas, lexicon | Unit tests |
| 6.4–6.7 | Medication, discharge, allergy and event extraction | Unit tests per type; dev metrics |
| 6.8 | Migration 0005, models, `app/records/clinical.py`, stage, review | Upgrade, downgrade and catalog tests on PostgreSQL; integration tests (atomicity, idempotence, isolation, rollback) |
| 6.9 | Comparison and conflicts | Unit tests; dev conflict P/R |
| 6.10 | Records Q&A and summaries | Unit tests; copilot_v1 non-interference test |
| 6.11 | Streamlit page | `AppTest` tests |
| 6.12 | Evaluation: dev → freeze → held-out once; regressions | Reports written |
| 6.13 | Documentation, acceptance review | Docs updated; final report |

## 12. Risks

- **Synthetic, same-author data.** The metrics do not show real-world coverage. This is stated in every report.
- **Docling line merging varies with layout.** Mitigation: segmentation inside a line, and unit tests on merged lines.
- **Rule-based extraction will miss free-text phrasing.** Accepted: it abstains or flags for review instead of guessing.
- **The pipeline result gains a sixth stage.** Callers that assumed five stages were updated, and the completion rule is unchanged.
