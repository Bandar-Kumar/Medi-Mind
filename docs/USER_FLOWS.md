# User Flows

> **Status: Design (Phase 0).** Flows marked **[MVP]** are part of the first usable version. Flows marked **[Later]** depend on later phases.

**Primary users:**
- **Patients**, who manage their own records.
- **Caregivers**, who manage records for one or more family members.

The prototype is single-user and runs locally (ADR-013).

## MVP pages

| Page | Purpose |
|---|---|
| Overview | Patient selector, recent documents, items needing review, open conflicts |
| Documents | Library with filters; upload |
| Document detail | Page viewer, extracted facts with source excerpts, review actions |
| Timeline | Chronological facts with sources |
| Lab trends | Analyte charts and tables, with document reference ranges |
| Copilot | Questions with cited answers |

**Later:** Comparison, Appointment preparation.

---

## F1. Select or create a patient profile [MVP]
1. The user opens the app and sees the patient selector on the Overview page.
2. They select an existing profile or create one: display name, plus an optional date of birth.
3. Everything else in the app is scoped to the selected patient.

**Edge case:** There are no profiles yet. A prompt asks the user to create one. Demo mode shows preloaded synthetic patients.

## F2. Upload a document [MVP]
1. On the Documents page, the user selects Upload and picks a PDF, PNG or JPEG.
2. The file is validated: type, size, page count and encryption.
3. The user starts processing, and a progress indicator shows each Phase 2 stage with its result: parsing, then classification and identity verification (independent stages), then indexing for search. Indexing runs only after both have succeeded and never for an identity mismatch (ADR-036).
   - *Changed 2026-11-17:* this step previously listed "parsing, then classifying, then extracting". Fact extraction belongs to Phase 3 and is not part of the current pipeline; it will be added as a later stage then.
4. The result is a document card showing its filename, document type, processing status, indexing result (indexed, or blocked with its reason) and identity result, with any applicable review flags highlighted (for example `needs_review` after an identity mismatch, or "identity not confirmed").
   - *Changed 2026-11-17:* this step previously said the card shows the document's date and the number of facts extracted. Both come from Phase 3 extraction and are not available in the Phase 2 pipeline; they will be added to the card in Phase 3.

**Edge flows:**
- **Invalid file.** The file is rejected with a specific reason, e.g. "Encrypted PDFs are not supported" or "File exceeds 20 MB".
- **Exact duplicate.** "This document was already uploaded on <date>", with a link to it. No reprocessing.
- **Possible revised version.** "This looks like a revised version of <document>. Link them?" The user chooses yes or no.
- **Identity mismatch.** "The name or date of birth in this document does not match <profile>." The document is flagged `needs_review` and is **not indexed**, so it never appears in search results (ADR-036). The user can see the flag and its reason, or delete the document.
  - *Changed 2026-11-17 (ADR-036 D6):* the original design also let the user move the document to another profile or keep it anyway. Neither action is supported: there is no override and no automatic re-check. To file it under the right profile, the user deletes it and uploads it to that profile.
- **Identity unknown.** No name was found, or the match is partial or ambiguous. The document is processed and indexed normally, and an "identity not confirmed" badge stays visible on the document and on evidence from it.
- **Parsing or OCR failure.** The document is marked `failed`, the reason is shown, and the user can retry.
- **Demo mode.** Upload is disabled, with an explanation.

## F3. Review extracted information [MVP]
1. The user opens Document detail. The page image or text is on the left, and the extracted facts are on the right.
2. Each fact shows its original value, its normalized value (if any), the source excerpt with the page, and a status badge: **Unverified (auto-checked)**, **Needs review** or **Verified** (ADR-023).
3. Facts flagged for review list their reasons, e.g. "Value not found in source text", "Implausible value", "Ambiguous date 03/04/2024 (read as 3 April)", "Unknown analyte".
4. For each fact, the user can **verify**, **correct** or **reject** it, one fact at a time. A correction records the new value, and the original is kept.

**Rules:**
- No fact becomes Verified without an explicit human action. There is no "verify all" button in the MVP.
- A fact that is not verified is never shown as a plain fact anywhere in the app. It always carries an "unverified" label.
- "Needs review" facts are kept out of the timeline, trends and copilot until they are reviewed.

## F4. View the timeline [MVP]
1. The Timeline page lists dated facts in order: lab results, documented medications, events and documents.
2. The user can filter by fact type, document type and date range.
3. Each entry links to its source document and page.
4. A result that appears in several documents shows once, with a "Also in: …" link.
5. Dates with month or year precision are shown as "March 2021" or "2021", never as a made-up exact day.

## F5. View lab trends [MVP]
1. The user picks an analyte, e.g. HbA1c.
2. The page shows a chart and table of values over time, each linked to its source. Each point shows *that document's* reference range and printed flag.
3. If units differ and a conversion is defined, values are converted, and the original is shown on hover. If no conversion is defined, the series are shown separately with an explanation.
4. Changes between consecutive values are computed in SQL or Python and shown as numbers. There is no interpretive language.

## F6. Ask the copilot [MVP]
1. The user types a question.
2. The router sends it to one of three paths:
   - **Structured** ("What was my last HbA1c?"): an SQL tool answers, citing the source document and page.
   - **Document** ("What did the discharge summary say about follow-up?"): hybrid RAG gives an answer with inline citations that open the source page.
   - **Out of scope** ("Should I stop taking metformin?", or general knowledge like "What is HbA1c?"): a fixed response says MediMind only answers from the user's records and cannot give medical advice, suggests discussing it with a clinician, and offers to show what the records document (ADR-022).
3. If the evidence is insufficient: "I couldn't find this in your documents." The system does not guess.
4. Answers that rely on unverified facts say so.

## F7. Resolve flags and conflicts [MVP: view and acknowledge]
1. The Overview page lists open conflicts. For example: "Metformin 500 mg (Doc A, 2024-01-10) vs 1000 mg (Doc B, 2024-01-10)."
2. The user opens the conflict to see both sources side by side.
3. The user can **acknowledge** it, optionally with a note. MediMind never picks a winner.

## F8. Compare documents [Later, Phase 6]
1. The user selects two documents, or two dates.
2. The page shows observations that changed, are missing or are new. Each row cites both sources. Differences are calculated deterministically.

## F9. Prepare for an appointment [Later, Phase 7]
1. The user selects a date window and optionally adds free-text topics.
2. The workflow gathers lab changes, documented medications, open conflicts and missing or unverified information.
3. A cited draft summary is generated. Any statement that fails citation validation is regenerated or removed.
4. **Human review:** the user edits or approves the draft.
5. The user exports it to PDF or Markdown, and every factual line keeps its citation.

## F10. Delete data [MVP]
1. The user deletes a document. A confirmation lists what will be removed: pages, chunks, facts and reviews.
2. The deletion cascades. Any conflicts that depended on the document are closed.

> **Implemented so far (Task 1.8, ADR-034):** `delete_document` removes the document row and, by cascade, its pages, processing history (pipeline runs), chunks, facts, reviews and conflict memberships, and returns page and run counts for the confirmation. The original PDF and parse outputs stay on disk. Closing the affected conflicts is not implemented yet: they stay, possibly with no members. A document being parsed by a live worker cannot be deleted. A patient profile can be deleted only once it has no documents, runs or conflicts.
