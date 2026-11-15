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

### Issues / blockers
- **Test-count correction:** the Task 2.5d report said 10 new IR tests; the correct number is 8 (593 + 8 + 84 = 685).
- Still present and not changed: the stray file `tatus --short` in `f42f3f7`; the older `medimind-*` folders in `%TEMP%` (kept until the owner allows deletion).
- **Uncommitted:** all D7, Task 2.5 and Task 2.6 work.

### Next
1. Owner reviews and commits the D7–2.6 work.
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
