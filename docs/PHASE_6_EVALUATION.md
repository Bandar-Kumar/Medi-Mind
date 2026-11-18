# Phase 6 Evaluation — `clinical_v1`

> **Status (2026-11-18):** dev evaluated, then the configuration was frozen and the held-out split was evaluated **once**. See §6 for the held-out run and §8 for the regression checks. Plan: [PHASE_6_PLAN.md](PHASE_6_PLAN.md) §10. Decisions: ADR-042.
>
> **Read this first.** All data is synthetic and fictional, including the medicine names. The generator, the extraction rules and the held-out-only label variants were written by the same author in the same session, and the dev split was used while developing the rules. These numbers show that the system does what it was designed to do on documents of this kind. They are **not** evidence of real-world accuracy or coverage, and **no clinical validation is claimed**. `validated` means only that automated checks passed.

## 1. Dataset

`data/synthetic/clinical_v1` (generator `evals/synthetic/clinical.py`, seed 20261118):

| Item | Value |
|---|---|
| Patients | 16 fictional (dev 11 / held-out 5, split by patient) |
| Documents | 136 PDFs (91 dev / 45 held-out) |
| Expected facts | 677: 207 medications, 68 allergies, 402 events |
| Expected conflicts | 39: 14 same-date medication mismatches, 20 allergy contradictions, 5 allergy status disagreements |
| Questions | 208 (13 per patient) |
| Held-out digest | `c3f4db6d64fe1ee6…` (frozen at build, before any extraction rule existed) |

- **Document mix per patient:**
  - two prescriptions (a first and a latest, derived from it: repeated, changed, new, missing or explicitly stopped medicines);
  - a discharge summary (one or two pages);
  - a consultation note;
  - an allergy record (table or list);
  - an imaging report;
  - a medical-history record (some undated).
- **Optional documents:**
  - a prescription dated on the discharge day with one discharge medicine at another strength (14 patients): a same-date conflict;
  - a noisy raster scan of the first prescription (6 patients): OCR, a duplicate;
  - an invoice (4 patients): unsupported.
- **Deliberate traps:**
  - "No known drug allergies" in a discharge summary against an allergy record;
  - a confirmed against a suspected allergy;
  - an ambiguous brand (`Combiflex`, two generics) and unknown names (`Zorbaxin`, `Quellora`);
  - ambiguous dates (03/04/…);
  - undated history records;
  - "Allergies: Not recorded" (unknown, never "none");
  - family history (must not become patient facts).
- **Held-out-only label variants** (declared in the manifest):
  - "Prescribed Medications:";
  - "Drug Allergy:";
  - "CLINIC VISIT NOTE";
  - "IMAGING REPORT";
  - the frequencies TID, QID, 1-0-0 and "once daily".
- **Reproducibility:** regenerating the dataset gives a byte-identical manifest. This was checked after formatting the generator.

## 2. What is measured, and how

The runner is `evals/run_clinical.py`. It needs no database and runs each PDF through the real system path:

1. the project's Docling parser (IRs cached);
2. the Phase 2 classifier v2;
3. identity v2 against the patient's own profile and a swapped profile;
4. the extraction gate;
5. `clinical-doctype-1`;
6. `clinical-rules-1`.

Then, per patient, `clinical-compare-1` and `clinical-qa-1` run over the extracted facts, with machine statuses and no human review.

- **Matching:** a prediction matches a gold fact of the same document and type with the same key:
  - medication: the printed name and context;
  - allergy: the printed substance;
  - event: the type and printed description.

  Matching is one to one, in page order.
- **Field exact match:** every normalized and raw field listed in the runner, over matched pairs.
- **Date accuracy:** date, precision and date source (document vs stated) all equal.
- **Review agreement:** the same set of review reasons.
- **Provenance:**
  - precision is predicted facts on the right page whose excerpt is an exact slice of the stored page text containing the gold printed text, over all predictions;
  - recall uses the same count over gold facts.
- **Conflicts:** precision and recall over (type, gold member pair).
- **Comparison:** first against latest prescription, per medicine (new / missing / discontinued / repeated / changed).
- **Questions:**
  - routing (the dispatcher takes the question and the intent is right; refusal probes stay refused);
  - correctness: the documented fact or document set equals gold, the uncertain set equals gold for list questions, statuses for "what changed", member pairs for conflicts, and abstention for unsupported topics.
- **Hard gates (must be 0):**
  - ungrounded facts (an excerpt or date that is not an exact slice);
  - facts for a swapped-profile `mismatch`;
  - refusal probes answered;
  - invalid citations.
- **Separately:** native and raster (OCR) documents are reported separately, as in Phase 3.

**Pre-registered targets** were set in the runner before its first scored run. The extraction rules had already been developed against dev documents, which is disclosed here:

| Target | Threshold |
|---|---|
| Classification accuracy | ≥ 0.95 |
| Native entity precision and recall | ≥ 0.95 |
| Native field exact match | ≥ 0.95 |
| Native date accuracy | ≥ 0.95 |
| Native review agreement | ≥ 0.95 |
| Native provenance precision and recall | ≥ 0.95 |
| Conflict precision and recall | ≥ 0.90 |
| Comparison accuracy | ≥ 0.90 |
| Q&A routing | ≥ 0.95 |
| Q&A correctness | ≥ 0.90 |

## 3. Development history (dev split only)

These are the dev-only checks made while writing the rules, before the scored run:

1. **First check: 298 matched, 167 missed.** Two causes:
   - item markers never matched, because a regex `^` does not anchor at a `pos` given to `Pattern.match` (fixed, with a regression test);
   - allergy-record items sat inside a merged header line.
2. **Merged bullets.** Docling merged bullets onto one line (`- A since 2010 - B since 2013`) or dropped them entirely under a heading. Fixed with the bullet split and a conservative heading-block rule: a history item has no colon and is at most 80 characters.
3. **OCR spacing.** OCR produced `Tab.Mockcillin` with no space. It is now accepted, because real prescriptions print it that way too.

No held-out document or IR text was inspected at any point.

## 4. Dev results (scored run, `evals/reports/clinical_v1/clinical_dev.md`)

| Metric | Dev | Target |
|---|---|---|
| Classification accuracy (91 documents) | 1.000 | met |
| Native entity precision / recall (445 gold) | 1.000 / 1.000 | met |
| Native field exact match | 1.000 | met |
| Native date accuracy | 1.000 | met |
| Native review agreement | 1.000 | met |
| Native provenance precision / recall | 1.000 / 1.000 | met |
| Conflict precision / recall (27 gold) | 1.000 / 1.000 | met |
| Comparison accuracy | 1.000 | met |
| Q&A routing / correctness (143 questions) | 1.000 / 0.986 | met |
| Hard gates | all 0 | — |

- **OCR (raster) group**, 20 gold facts: entity precision and recall 0.90, provenance 0.80. Every OCR fact carries `ocr_unverified_source`, so none is usable until reviewed.
  - The two misses are clinician lines where OCR dropped spaces ("(Fictional),MBBS").
  - The two provenance misses are `20mg` and `Tab.Mockcillin`: the stored OCR text differs from the print, and the extractor correctly quotes the stored text.
- **The two Q&A errors** are both history summaries. They come from the same OCR clinician lines, which appear in the uncertain section under a key that cannot be matched to gold.
- **Identity:**
  - own profile: 88 `match` and 3 `unknown` (the raster copies);
  - swapped profile: 88 `mismatch` (all refused by the gate, 0 facts) and 3 `unknown`.
- **Abstentions:** 3 `allergy_status_unknown`, one for each "Not recorded".

## 5. Freeze

`evals/reports/clinical_v1/clinical_selection.json` records:
- the versions;
- the Phase 6 source digest (`app/clinical/**`, `app/services/clinical_records.py`, `app/services/extract_clinical.py`);
- the dataset and held-out digests;
- the dev report hash;
- the targets.

The held-out run refuses on any difference and writes its marker before evaluating.

## 6. Held-out results (run once)

Run once on 2026-11-18, after the freeze (report: `evals/reports/clinical_v1/clinical_heldout.md`; marker `clinical_heldout.done`). No rule was changed afterwards.

| Metric | Held-out | Target |
|---|---|---|
| Classification accuracy (45 documents, held-out-only titles included) | 1.000 | met |
| Native entity precision / recall (202 gold) | 1.000 / 1.000 | met |
| Native field exact match | 1.000 | met |
| Native date accuracy | 1.000 | met |
| Native review agreement | 1.000 | met |
| Native provenance precision / recall | 1.000 / 1.000 | met |
| Conflict precision / recall (12 gold) | 1.000 / 1.000 | met |
| Comparison accuracy | 1.000 | met |
| Q&A routing / correctness (65 questions) | 1.000 / 0.985 | met |
| Hard gates | all 0 | — |

- **OCR (raster) group**, 10 gold facts: entity precision and recall 0.90, provenance 0.90.
  - The one miss is `Tab. Sampletrex 500 mg0-0-1`: OCR dropped a space, so the name absorbed the strength and frequency.
  - That fact is stored as `needs_review` (unmapped name), so it is not usable until reviewed.
  - The one Q&A error (P10 "medications", uncertain section) is the same item.
- **Identity:** own profile 44 `match` and 1 `unknown` (the scan); swapped profile 44 `mismatch` (0 facts) and 1 `unknown`.
- **Classification:** all 3 invoices were `unsupported_type`, and every held-out-only title was recognised.
- **Interpretation:** the held-out-only label variants were all handled. Because they were written in the same session as the rules, this shows the rules generalise across the anticipated phrasing, not to unseen real documents.

## 7. Limitations

- **Same-author, synthetic, template-generated data.** Real documents vary far more: handwriting, free narrative, unusual abbreviations, tables split across pages. These rules only cover printed labels and conventional abbreviations.
- **The medication lexicon is fictional.** A real deployment needs a licensed, reviewed drug database. There is no fuzzy matching, so misspellings stay "unmapped" (review).
- **Free narrative is not structured.** Examples are "course in hospital", imaging findings and impressions, and advice text. It remains searchable through retrieval.
- **Facility and prescriber** are kept as event descriptions, not as separate fields.
- **OCR is measured on six noisy scans only.** All OCR facts need review.
- **Q&A is rule-routed** and claims only phrasings with a known clinical topic. Unrecognised phrasings go to the Phase 5 copilot, which abstains for clinical topics it does not know.
- **No LLM** was evaluated (ADR-024 is still deferred).

## 7a. Acceptance fixes after the freeze (2026-11-18)

The read-only acceptance audit recommended HOLD. The fixes are in ADR-042's amendment:
- a full-question allow-list for records questions;
- identity labels;
- the answer-time citation check;
- the stage label.

**They changed `app/clinical/` and `app/services/clinical_records.py` after the freeze**, so `clinical_selection.json` no longer matches the current source digest.
- The frozen dev and held-out reports describe the code as it was frozen. They were not rewritten, and the held-out split was **not** re-run.
- Dev was re-evaluated **in memory** on the fixed code, with no report written. Every metric, the hard gates, the Q&A results by kind and the extraction groups are **identical** to the frozen dev report.
- Safety evidence is in `evals/reports/clinical_safety_v1/safety_regression.md` (pure, reproducible with `uv run python -m evals.run_clinical_safety`):
  - 57 of 57 probes give the hand-written expected decision;
  - 0 of the 12 audit bypass examples are answered;
  - all 926 frozen Phase 5 questions are dispatched to Phase 5;
  - the identity labels and the citation checks behave as specified.

## 8. Regression checks

All of these were run on 2026-11-18 after the Phase 6 code was final. The numbers are as observed.

- **Default suite:** 2032 passed, 0 failed. The baseline before Phase 6 was 1919 passed.
  - One stale expectation was updated: `tests/test_health.py` expected head `0004`.
- **PostgreSQL integration suite:** 232 passed, 0 failed (baseline 221, plus 11 new Phase 6 tests).
- **Migrations on `medimind_test`:**
  - a fresh initialization reaches 0005;
  - a stepwise downgrade to base and upgrade back gives an identical catalog;
  - `alembic check` finds no differences;
  - the model and migration catalogs are identical;
  - the downgrade is refused while clinical data exists.
- **Application database `medimind` upgraded 0004 → 0005.** Every table's row count and content hash is identical before and after; only the revision changed.
- **Phase 5 non-interference:**
  - none of the 926 `copilot_v0` and `copilot_v1` questions is routed to the clinical path (a unit test);
  - `copilot_v1` dev was re-evaluated in memory on the final code (425 questions). The metrics, per-kind results and failures are identical to the frozen dev report;
  - the frozen `copilot_v1` report files are byte-identical. The held-out split was **not** re-run, because it runs once.
- **Frozen artifacts:** `copilot_v0`, `copilot_v1`, `extract_v0`, `longitudinal_v*` and `lab_v*` datasets and reports are unchanged. Migrations 0001–0004 are unchanged, and so are `app/extraction/` and the Phase 5 copilot code files.
- **Real end to end** (`medimind`, real Docling parser and real embedder): fictional dev patient P01 was run through all six stages.
  - 8 documents, 26 s;
  - types: 3 prescriptions (one is the OCR copy), plus a discharge summary, a consultation note, an allergy record, an imaging report and a history record;
  - 41 facts, all `validated`;
  - derived conflicts: 3 allergy contradictions and 1 same-date medication mismatch, exactly the gold set;
  - the records question "What medications are documented in my records?" was answered with 12 cited items.
- **Ruff:** check and format check are clean.
