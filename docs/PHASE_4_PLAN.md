# Phase 4 Plan: Database and Longitudinal Intelligence

> **Status:** written 2026-11-18 at the start of Phase 4, on the owner's instruction to implement the whole phase in one pass. The scope comes from [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) (Phase 4 outline) and [ROADMAP.md](ROADMAP.md), refined here with ADR-039. The targets in §10.4 were recorded **before** the first evaluation run. Results are in §12.
>
> **Phase 3 gate:** ROADMAP says phases are not started early and each needs the owner's approval. The owner's instruction to implement Phase 4 is recorded here as the reason Phase 4 started. No separate Phase 3 gate approval is claimed.

## 1. Scope

**In scope** (from the outline: timeline query, duplicate grouping, trend series with unit conversion, comparison queries, Timeline and Lab trends UI):
- a patient timeline of results and documents, ordered by clinical dates (§4);
- cross-document duplicate candidates, derived and explained (§5);
- lab trends per analyte and unit, with each document's own range, and exact differences (§6);
- comparison of two dates (§6.3);
- a read-only presentation of the existing Phase 3 conflicts (§8.2);
- the Overview, Timeline and Lab trends pages (§9; USER_FLOWS MVP pages);
- a synthetic longitudinal dataset and evaluation (§10).

**Out of scope:**
- document-to-document comparison (USER_FLOWS F8, Phase 6);
- new conflict rules or conflict types (Phase 6);
- medications, allergies and events;
- any LLM;
- any change to Phase 1–3 behaviour.

## 2. Decisions (ADR-039)

| # | Decision | Source |
|---|---|---|
| L-1 | Only usable facts appear: `validated` (labelled "auto-checked, not verified"), `verified`, `corrected`. `needs_review` facts are counted, never shown. `rejected` facts are never shown. | ADR-023 rule 4 |
| L-2 | Identity-`mismatch` documents are excluded and counted. `unknown` identity is shown with a visible label. | ADR-036, ADR-038 D-2 |
| L-3 | Display values = latest correction, otherwise machine-normalized (as on the document page). Printed values are always carried alongside, unchanged. | ADR-037 |
| L-4 | Clinical dates only: a result's display date with its recorded source (`collection` / `report`); a document's printed report date. Upload and processing dates are metadata, never a position. Undated items are listed separately. | prompt, USER_FLOWS F4 |
| L-5 | Duplicates are **derived at read time and not stored**. `extracted_facts.duplicate_group_id` stays unused (NULL). | §5 |
| L-6 | A trend series is one (analyte, display unit). Values were converted at extraction only where an exact conversion exists (`norm-1`). Incompatible units are never combined. | ARCHITECTURE §8 |
| L-7 | Range states avoid clinical words: within / below / above *this report's printed range* (derived), not compared, no printed range. MediMind applies no ranges of its own. | ARCHITECTURE §8 |
| L-8 | Stored conflicts are presented, never re-detected, changed or resolved by Phase 4. | ADR-038 |
| L-9 | No migration. Reads use the existing tables and indexes; measured in §12. | prompt §5 |
| L-10 | Pure logic in `app/longitudinal/`; SQL in `app/records/longitudinal.py`; orchestration in `app/services/longitudinal.py`; UI in `app/ui/longitudinal_views.py`. | ADR-016, ARCHITECTURE |

## 3. Data model (pure)

`app/longitudinal/model.py`:
- `LongitudinalObservation`: one usable fact, with:
  - its display values;
  - its printed values;
  - provenance (document, run, page, locator, character span);
  - review status, and whether the value comes from a human correction;
  - document type and identity.
- `LongitudinalDocument`: a document's report date, upload time and last processing time.

Version `longitudinal-1`.

## 4. Timeline and dates

- **Entries:** one per usable dated result, plus one per document with a printed report date.
- **Exact duplicate groups** (§5) are one entry carrying every source. **Possible duplicates** are separate entries that name each other.
- **Date labels:** a month-precision date is "March 2024"; a year-precision date is "2024". A partial date is never shown as a day.
- **Order:**
  1. by the first day of the date's period;
  2. on the same first day: day precision, then month, then year;
  3. entries with a time before those without;
  4. documents before results;
  5. then analyte, document, page, position and id.

  Upload order never decides.
- **Filters:** date window (a partial date matches when its period overlaps), analytes (documents are then hidden), document types and entry kinds. Pagination: 1–200 per page; newest first or oldest first.
- **Separate lists:** undated results and documents without a report date, each with its upload date labelled as such.

## 5. Duplicates

- **Candidate pair:** two usable results from **different documents**, with the same analyte, the same **day-precision** date, and values that Phase 3's `compare_pair` calls `equal`.
- **exact:** additionally the same printed name, value, unit and range text, the same date source and time, and no human correction.
- **possible:** any difference among those. The reasons are fixed codes, e.g. `different_printed_unit`.
- **Groups:** connected components of candidate pairs. A group is exact only if every pair in it is exact. The representative is human-reviewed first, then the lowest id.
- **Nothing is stored or merged.** This makes the result repeatable by construction, and it follows every review decision immediately.

## 6. Trends and comparisons

### 6.1 Series and points
- A series is one (analyte, display unit); qualitative values and numbers without a unit get their own series.
- An exact duplicate group is one point.
- A point is **plotted** only when it is numeric, has no comparator and has a day-precision date. Other points stay in the table, with the reason they are not drawn.

### 6.2 Changes
- Exact `Decimal` differences between consecutive plotted points of a series, with the number of days between them.
- A date with several results that no time of day separates gets no change in or out of it.

### 6.3 Comparing two dates
- Per analyte and series: changed / unchanged / only on one date / not computed (several results, a bound, a partial date).
- A note is added when the other date has the analyte in another unit.

## 7. Records layer (`app/records/longitudinal.py`, read-only)

| Function | Query |
|---|---|
| `list_usable_facts(patient_id, *, analyte_names, date_from, date_to)` | Current usable facts not from mismatch documents, with their decisions. Corrected and undated facts always pass the SQL pre-filter; the exact filter runs on display values. |
| `list_facts_by_ids(patient_id, fact_ids)` | Conflict members in one query. Other patients' ids are absent. |
| `list_timeline_documents(patient_id)` | Documents with their latest finished run (one grouped subquery). |
| `count_facts_by_status(patient_id)` | Counts per stored analyte and status (for "awaiting review"). |

- Each service call runs a fixed number of queries; an integration test asserts the statement count does not grow with the history.
- An architecture test asserts the module is patient-scoped and contains no writes.

## 8. Services (`app/services/longitudinal.py`)

### 8.1 Views
`overview`, `timeline`, `analytes`, `trend`, `compare_dates`, `result_dates`, `duplicates`, `conflicts`, `evidence` and `document_types`. All are deterministic; no model is loaded.

### 8.2 Conflicts
- Stored conflicts, with each member's role: `direct` (disagrees directly with another member) or `linked` (in the group only through other members).
- Each directly disagreeing pair carries an explanation code: `non_overlapping_values` or `opposite_qualitative_results`.
- Closed conflicts are history. Members that were later rejected or superseded are labelled.

## 9. UI

- **Overview:** counts, warnings (awaiting review, identity mismatch or unknown) and the latest result of each test.
- **Timeline:** filters and pagination, a table showing date source, shown and printed values, range state, status, sources and notes, an exact-evidence panel, and the undated lists.
- **Lab trends:** test picker, one chart and table per unit series, changes, undated results, conflicts and a two-date comparison.
- **Exactness:** values are displayed as exact text. Charts draw floats and say so.
- **Imports:** the UI imports only services (enforced by an existing test).

## 10. Evaluation (`longitudinal_v0`)

### 10.1 Dataset
- **Generator:** `evals/synthetic/longitudinal.py` (`longitudinal-gen-1`, seed 20261118).
- **Patients:** 16 fictional scenario patients, each covering cases C1–C14: report vs collection dates; an exact re-upload; another lab's report of the same sample; a same-day conflict; convertible and non-convertible units; correction and rejection; undated, ambiguous and month-only dates; unknown and mismatched identity; a non-lab note. Documents are uploaded in a shuffled order.
- **Performance patient (L99):** dev only; 150 documents, 1,050 facts.
- **Splits:** patient-disjoint. Held-out: L03, L07, L10, L13, L16.
- **Freeze:** `manifest.json` records both SHA-256 digests at build time, before any evaluation. The runner refuses a file that differs.

### 10.2 Ground truth
- Derived from the scenario specification: the generator's own unit table, date-choice rule (collection date, else report date), DD/MM ambiguity rule, decisions, and the intended duplicate and conflict pairs.
- It never calls the evaluated code.

### 10.3 Loader and scoring
- **Loader:** stores facts through the production write path:
  - repositories;
  - `record_extraction` with the production normalizers;
  - the real identity gate for mismatch documents;
  - `review_fact` for decisions.
- **Not exercised:** the rule-based extractor itself (measured in Phase 3).
- **Scoring:** calls only the longitudinal services.

### 10.4 Pre-registered targets (recorded before the first run)

| Metric | Target |
|---|---|
| Timeline entry precision and recall; order accuracy (pairs with different dates); date-source accuracy | 1.0 |
| Trend point accuracy; trend change accuracy; unit-series accuracy | 1.0 |
| Duplicate precision and recall (pairs) | ≥ 0.95 |
| False duplicate rate | ≤ 0.05 |
| Conflict precision and recall (direct pairs) | 1.0 |
| Provenance completeness (exact span, page, locator, run); corrected-value accuracy; identity-uncertainty shown | 1.0 |
| Patient-isolation leaks; rejected, needs-review or mismatch items shown | 0 |

- **Runtime:** reported, not targeted (median, p95, max per service call; performance patient separately).
- **Held-out:** run once, after development (`longitudinal_heldout.done`).

### 10.5 Limitations (stated in every report)
- The data is synthetic, and the scenarios and rules were written by the same author: this is not a blind test, and no clinical accuracy is claimed.
- The ground truth checks the longitudinal layer, not extraction.
- The performance patient is one machine's measurement.

## 11. Tests

| Kind | File |
|---|---|
| Pure (hand-calculated) | `tests/test_longitudinal.py` |
| Dataset generator | `tests/test_longitudinal_dataset.py` |
| Runner (pure parts) | `tests/test_longitudinal_evals.py` |
| UI (`AppTest`) | `tests/test_longitudinal_ui.py` |
| Architecture | `tests/test_architecture.py`: patient scoping, read-only records, pure `app/longitudinal` |
| PostgreSQL integration | `tests/integration/test_longitudinal_integration.py` (corrections, rejection, review counts, duplicates, conflict presentation and lifecycle, mismatch gate, isolation, provenance, statement count) and `tests/integration/test_longitudinal_eval_integration.py` (runner end to end with a non-frozen seed, plus a negative control) |

## 12. Results (2026-11-18)

### 12.1 Dataset
- **Built and frozen** at 12:05 on 2026-11-18, before the first evaluation run.
- **Digests:** held-out `sha256 30fb83def86f6de5fdd7da33ad3cb6518a91d63f29cb303b4c270827ccb27196`; dev `08fe4a68590e5b799400178b78906b1afba0c1ad8bdefca5af9b801c0f8835ba`.
- **Splits:** dev L01, L02, L04, L05, L06, L08, L09, L11, L12, L14, L15 and L99 (performance); held-out L03, L07, L10, L13, L16.

### 12.2 Evaluation (no rule was changed between dev and held-out; held-out run once)

| Metric | Dev (11 patients) | Held-out (5 patients) |
|---|---|---|
| Timeline entries (truth) | 335 | 153 |
| Timeline entry precision / recall | 1.0 / 1.0 | 1.0 / 1.0 |
| Order accuracy (pairs with different dates) | 1.0 (4,286 pairs) | 1.0 (1,970 pairs) |
| Date-source accuracy | 1.0 | 1.0 |
| Trend point accuracy (truth points) | 1.0 (269) | 1.0 (123) |
| Trend change accuracy (truth changes) | 1.0 (115) | 1.0 (53) |
| Unit-series accuracy | 1.0 | 1.0 |
| Duplicate precision / recall (pairs) | 1.0 / 1.0 (66) | 1.0 / 1.0 (30) |
| False duplicate rate; kind accuracy | 0.0; 1.0 | 0.0; 1.0 |
| Conflict precision / recall (direct pairs) | 1.0 / 1.0 (11) | 1.0 / 1.0 (5) |
| Provenance completeness (sources checked) | 1.0 (324) | 1.0 (148) |
| Corrected-value accuracy | 1.0 (11) | 1.0 (5) |
| Identity uncertainty shown | 1.0 (22 entries) | 1.0 (10) |
| Isolation leaks; rejected / needs-review / mismatch shown | 0; 0 / 0 / 0 | 0; 0 / 0 / 0 |
| Mismatch extractions refused by the gate | 11 | 5 |

**Timings (one Windows laptop, PostgreSQL in Docker), median ms per call:**

| Call | Dev scenario patients | Held-out | Performance patient (150 documents, 1,050 results) |
|---|---|---|---|
| Timeline page | 44 | 25 | 291 (p95 304) |
| Trend | 62 | 35 | 93 |
| Duplicates | 32 | 16 | 287 |
| Overview | 52 | 28 | 342 |

The performance patient's own metrics were also 1.0 for timeline, ordering, trends and provenance. Reports: `evals/reports/longitudinal_v0/longitudinal_{dev,heldout}.{json,md}`; held-out marker `longitudinal_heldout.done`.

### 12.3 Tests (final runs)

| Suite | Result |
|---|---|
| Default (`uv run pytest`) | **1717 passed**, 0 failed, 0 skipped; 275 deselected (opt-in markers); 1992 collected |
| Integration (`-m integration`) | **214 passed**, 0 failed, 0 skipped; 1778 deselected |
| Docling and models (`-m "docling or models"`) | **64 passed**, 0 failed; 1928 deselected (some overlap with integration) |

New in Phase 4: 30 pure, 5 dataset, 3 runner, 5 UI and 2 architecture tests; 7 + 2 PostgreSQL integration tests.

### 12.4 Defects found and fixed during Phase 4 (outside Phase 4 code; ADR-033 update)

The existing concurrent-upload integration test failed intermittently (2 of 8 runs). A stress test of 200 runs reproduced it, including with today's earlier fix removed, so it predates that fix. Two Windows causes:
1. `resolve()` returned the extended-length form while another writer held the file, which led to a false `outside_data_dir`.
2. A concurrent caller's read-only safeguard blocked removal of this caller's temporary hard link, which led to a false `write_failed`.

Both were fixed in `app/ingestion/storage.py`. Each has a regression test that fails without its fix. After the fixes, the stress tests were clean over 400 and 300 runs, and the integration test passed 10 of 10 runs.

### 12.5 Deviations and limitations
- **Phase 3 gate:** no separate owner approval of the Phase 3 gate was recorded before Phase 4 started; the owner's instruction is the recorded reason (see the status note).
- **Documents without a report date:** Phase 3 dates a document only from a printed report date, so lab reports that print only a collection date, and every non-lab document, appear under "documents without a report date" (their results are still dated).
- **Timeline paging:** each timeline call loads the patient's usable history and pages in Python (corrections can change dates). This was measured at 1,050 results; a much larger history would need SQL paging over display values.
- **Charts:** they draw floats; tables show the exact decimals.
- **Unknown identity with facts:** shown in the PostgreSQL tests and the evaluation (22 + 10 labelled entries), not in a manual browser session; no browser pass was made.
- **Not a blind test:** the evaluation isolates the longitudinal layer; extraction was measured in Phase 3. The data is synthetic, written by the same author as the rules; no clinical accuracy is claimed.
