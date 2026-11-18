"""Fixed user-facing texts for the local UI (Task 2.9b). Presentation only.

Every text is chosen by a fixed code (a stage status, an error kind, an identity outcome), never
built from an exception message or document content. Nothing here decides an outcome: the
services and the indexing gate do; this module only explains their codes.
"""

from typing import Final

STAGE_NAMES: Final = {
    "parse": "Parse",
    "classify": "Classify",
    "identity": "Identity check",
    "index": "Index for search",
    "extract": "Extract lab results",
}

# (icon, label) per stage status (ADR-036 A4)
STATUS_BADGES: Final = {
    "succeeded": ("✅", "Succeeded"),
    "already_done": ("☑️", "Already done"),
    "blocked": ("⛔", "Blocked"),
    "failed": ("❌", "Failed"),
}

REASONS: Final = {
    # already done
    "already_parsed": "Parsed earlier; not repeated.",
    "already_classified": "Classified earlier; not repeated.",
    "already_checked": "Identity checked earlier; not repeated.",
    "already_indexed": "Indexed earlier; not repeated.",
    "already_extracted": "Extracted earlier; not repeated.",
    # blocked
    "not_parsed": "The document has no parsed pages yet.",
    "not_classified": (
        "No successful classification is recorded, so it is not indexed or extracted."
    ),
    "identity_not_checked": (
        "No successful identity check is recorded, so it is not indexed or extracted."
    ),
    "identity_mismatch": (
        "The identity check found a different name or date of birth: not indexed, no results "
        "extracted, flagged for review."
    ),
    "already_running": (
        "A parse of this document was started and has not finished. It may still be running in "
        "another process, or it was interrupted. Automatic recovery is not available."
    ),
    # failures
    "timeout": "Parsing took longer than the configured limit.",
    "parser_failed": "The parser could not read this document.",
    "partial_output": "The parser returned incomplete output.",
    "models_unavailable": "The parser's models are not available on this machine.",
    "parser_misconfigured": "The parser is not configured correctly.",
    "parser_environment": "The parser's environment is not set up correctly.",
    "unexpected_output": "The parser returned output that could not be used.",
    "integrity_mismatch": "Stored data did not match what was expected.",
    "text_not_storable": "The parsed text contains characters that cannot be stored.",
    "claim_lost": "Another process took over this document's parse.",
    "revision_not_configured": (
        "No approved embedding model revision is configured, so search indexing is not available."
    ),
    "invalid_query": "The question could not be used for search.",
    "database_error": "The database could not be reached or returned an error.",
    "unexpected": "An unexpected error occurred. Details are not shown.",
    # validation (upload)
    "not_a_file": "The upload could not be read as a file.",
    "empty": "The file is empty.",
    "too_large": "The file is larger than the upload limit.",
    "not_pdf": "The file is not a PDF.",
    "unreadable": "The PDF is damaged or cannot be read.",
    "encrypted": "Encrypted or password-protected PDFs are not supported.",
    "no_pages": "The PDF has no pages.",
    "too_many_pages": "The PDF has more pages than the configured limit.",
    "write_failed": "The file could not be stored.",
    "outside_data_dir": "The file could not be stored safely.",
    "invalid_key": "The file could not be stored safely.",
    # patients and documents
    "patient_not_found": "This patient does not exist (or is not available in demo mode).",
    "patient_not_empty": "A patient can be deleted only once all of their documents are deleted.",
    "invalid_display_name": "Enter a display name of 1 to 200 visible characters.",
    "invalid_date_of_birth": "The date of birth is not valid.",
    "document_not_found": "This document does not exist for the selected patient.",
    "document_referenced": "A newer version of this document refers to it; it cannot be deleted.",
    "invalid_filename": "Enter a plain file name (no folders or special characters).",
    "identity_failed": "The identity check could not be completed.",
    "classification_failed": "Classification could not be completed.",
    "invalid_input": "The stored pages could not be checked.",
    "invalid_pages": "The stored pages could not be classified.",
    # extraction and review (Phase 3)
    "extraction_failed": "Lab results could not be extracted.",
    "interrupted": "The operation was interrupted.",
    "reference_data_not_seeded": (
        "The test-name reference data is not in the database yet. Run "
        "`python -m app seed-reference` once."
    ),
    "not_reextractable": "There is nothing to re-extract with a newer version.",
    "has_review_decisions": (
        "Some results of this document were reviewed by you, so it is not re-extracted."
    ),
    "not_applicable": "Lab-result extraction applies to lab reports only.",
    "fact_not_found": "This result does not exist for the selected patient.",
    "conflict_not_found": "This conflict does not exist or is already closed.",
    "invalid_correction": (
        "The correction could not be used: enter a value, unit, range or full date "
        "that can be read."
    ),
}

# Review status labels (ADR-023, docs/PHASE_3_PLAN.md §6.8). "validated" means only that the
# automated checks passed: it is never presented as verified, accurate or clinically validated.
REVIEW_STATUS_LABELS: Final = {
    "validated": "Auto-checked, not verified",
    "needs_review": "Needs review",
    "verified": "Verified by you",
    "corrected": "Corrected by you",
    "rejected": "Rejected (audit only)",
}
REVIEW_REASONS: Final = {
    "unmapped_analyte": "Test name not recognized",
    "unknown_unit": "Unit not recognized",
    "missing_unit": "No unit printed",
    "unparsed_value": "Value could not be read",
    "implausible_value": "Value outside the plausible range for this test (possible misread)",
    "ambiguous_date": "Ambiguous date (read as day/month)",
    "two_digit_year": "Two-digit year (not expanded)",
    "unparsed_date": "Date could not be read",
    "implausible_date": "Date is in the future or implausibly old",
    "conflicting_dates": "Two different collection or report dates on the page",
    "conflicting_value_in_document": "This test has different values in this document",
    "flag_range_disagreement": "Printed flag disagrees with the printed range",
    "ocr_unverified_source": "Read by OCR (text not verified)",
    "replacement_character_in_source": "Page has unreadable characters",
    "source_text_normalized": "Parser changed some characters on this page",
    "table_structure_invalid_source": "A table on this page could not be read structurally",
    "invisible_text_layer_source": "Page contains hidden text",
}
QUALITY_NOTES: Final = {
    "ref_range_unparsed": "Reference range kept as printed (not parsed)",
    "unit_not_convertible": "Value kept in its own unit (no conversion rule)",
    "effective_date_from_report": "Date is the report date (no collection date printed)",
    "no_effective_date": "No date found",
    "time_unparsed": "Time kept as printed (not parsed)",
}
DERIVED_COMPARISON: Final = {
    "below": "below this report's printed range",
    "within": "within this report's printed range",
    "above": "above this report's printed range",
}
DERIVED_NOTE: Final = (
    "Derived by MediMind: the value compared with the range printed on this same report. "
    "It is not a medical judgement, and MediMind applies no ranges of its own."
)
EXTRACTION_NOTE: Final = (
    "Results are copied from the document with their page and exact source text. "
    "'Auto-checked, not verified' means only that automated checks passed: no person has "
    "verified it, and it is not a clinical validation. Nothing is diagnosed or advised."
)
CONFLICT_NOTE: Final = (
    "These values for the same test and date disagree. MediMind never picks one: review the "
    "sources. Only the listed pairs disagree directly; other members are linked through them."
)
CLOSED_REASONS: Final = {
    "member_rejected": "a result was rejected",
    "member_corrected": "a result was corrected",
    "member_reviewed": "a result was reviewed",
    "member_superseded": "the document was re-extracted",
    "member_deleted": "a document was deleted",
    "member_added": "another result for the same test and date was added",
}

IDENTITY_TEXTS: Final = {
    "match": (
        "success",
        "Name (and date of birth, where printed) matched the profile. This is a consistency "
        "check of printed text, not proof of identity.",
    ),
    "unknown": (
        "warning",
        "Identity not confirmed: the document's name or date of birth could not be compared "
        "with certainty. The document is still searchable, with an 'identity not confirmed' label.",
    ),
    "mismatch": (
        "error",
        "Identity mismatch: the printed name or date of birth differs from this profile. The "
        "document is flagged for review and is not indexed. There is no override: if it belongs "
        "to another patient, delete it here and upload it to that patient.",
    ),
}
IDENTITY_NOT_CHECKED: Final = "Identity not checked yet."

TYPE_UNKNOWN: Final = "Document type unknown: the rules could not determine the type confidently."
TYPE_NOT_CLASSIFIED: Final = "Not classified yet."
RULE_SCORE_NOTE: Final = "Rule score, not a probability."

DELETE_DOCUMENT_WARNING: Final = (
    "This deletes the document's database records: its pages, processing history and search "
    "index entries. The original PDF and the parsed files stay on disk; they are not deleted."
)
PROCESSING_NOTE: Final = (
    "Processing runs in this browser session and waits until it finishes; there is no "
    "background processing. Parsing can take about 10-20 seconds per page."
)
NO_ADVICE_NOTE: Final = (
    "Search shows excerpts from this patient's stored documents, with their page. It does not "
    "generate answers, interpret results or give medical advice."
)
DEMO_BANNER: Final = (
    "Demo mode: only synthetic (fictional) patients are shown, new patients are created as "
    "synthetic, and upload is disabled. Nothing is loaded automatically."
)
DEMO_UPLOAD_DISABLED: Final = "Upload is disabled in demo mode (only synthetic data is used)."


def reason_text(kind: str | None) -> str:
    """The fixed explanation of a code, or a neutral line naming the code."""
    if kind is None:
        return ""
    return REASONS.get(kind, f"Reason code: {kind}")


def status_badge(status: str) -> str:
    icon, label = STATUS_BADGES.get(status, ("•", status))
    return f"{icon} {label}"


def stage_name(stage: str) -> str:
    return STAGE_NAMES.get(stage, stage)


def identity_badge(identity_check: str, *, checked: bool) -> str:
    if not checked:
        return "Not checked"
    return {"match": "Matched", "unknown": "Identity not confirmed", "mismatch": "MISMATCH"}.get(
        identity_check, identity_check
    )


def type_label(doc_type: str, confidence: object, *, classified: bool) -> str:
    if not classified:
        return "Not classified"
    if confidence is None:
        return doc_type
    return f"{doc_type} (rule score {float(confidence):.3f})"  # type: ignore[arg-type]


def review_status_label(status: str) -> str:
    return REVIEW_STATUS_LABELS.get(status, status)


def review_reasons_text(codes: tuple[str, ...] | list[str]) -> str:
    return "; ".join(REVIEW_REASONS.get(code, code) for code in codes)


def processing_label(
    processing_status: str, *, parsed: bool, indexed: bool, extracted: bool = False
) -> str:
    """The document's state in words. ``processing`` after parsing is not "in progress" (D7)."""
    if processing_status == "processed":
        return "Processed (every stage complete)"
    if processing_status == "needs_review":
        return "Needs review (identity mismatch)"
    if processing_status == "failed":
        return "Parse failed (can be run again)"
    if processing_status == "uploaded":
        return "Uploaded, not parsed"
    if processing_status == "processing" and not parsed:
        return "Parse not finished (see below)"
    if indexed and extracted:
        return "Parsed, indexed and extracted"
    if extracted:
        return "Parsed and extracted, not indexed"
    if indexed:
        return "Parsed and indexed, awaiting extraction"
    if parsed:
        return "Parsed, awaiting extraction (not indexed)"
    return processing_status


# --- Phase 4: longitudinal views (docs/PHASE_4_PLAN.md §9) ---------------------------------------

RANGE_STATES: Final = {
    "within_printed_range": "Within this report's printed range (derived)",
    "below_printed_range": "Below this report's printed range (derived)",
    "above_printed_range": "Above this report's printed range (derived)",
    "not_compared": "Not compared with the printed range",
    "no_printed_range": "No range printed",
}
DATE_SOURCES: Final = {
    "collection": "collection date",
    "report": "report date (no collection date printed)",
    "document_report_date": "report date of the document",
}
DUPLICATE_KINDS: Final = {
    "exact": "Same printed result in several documents (shown once)",
    "possible": "Possibly the same result (shown separately)",
}
DUPLICATE_REASONS: Final = {
    "same_test_date_and_value": "same test, date and value",
    "same_printed_text": "same printed value, unit and range",
    "different_printed_name": "test name printed differently",
    "different_printed_value": "printed value written differently",
    "different_printed_unit": "different printed unit (converted to the same value)",
    "different_printed_range": "different printed range",
    "different_date_source": "one dated by collection, the other by report",
    "different_time_information": "only one prints a time",
    "corrected_value": "one value was corrected by you",
}
NOT_PLOTTED: Final = {
    "partial_date": "not drawn: the date is only a month or year",
    "bound_value": "not drawn: a bound (for example '< 5'), not a value",
    "not_numeric": "not drawn: not a number",
}
CHANGE_SKIPPED: Final = {
    "several_results_on_date": "several results on this date with no time to order them",
    "different_units": "the other date has this test in a different unit; not compared",
    "bound_value": "a bound value; no difference computed",
    "partial_date": "a partial date; no difference computed",
    "not_numeric": "not a number; no difference computed",
}
COMPARISON_STATUS: Final = {
    "changed": "Changed",
    "unchanged": "Same value",
    "only_on_first_date": "Only on the first date",
    "only_on_second_date": "Only on the second date",
    "not_computed": "Not computed",
}
CONFLICT_EXPLANATIONS: Final = {
    "non_overlapping_values": "same test, same date, values that cannot both be right",
    "opposite_qualitative_results": "same test, same date, opposite results",
}
CONFLICT_ROLES: Final = {
    "direct": "disagrees directly",
    "linked": "linked through another result",
}
TIMELINE_NOTE: Final = (
    "Ordered by the date printed on the documents: the collection date when printed, otherwise "
    "the report date. Upload and processing dates never place an entry. Results awaiting review, "
    "rejected results and documents whose identity does not match are not shown."
)
TRENDS_NOTE: Final = (
    "Each value is shown with the range printed on its own report. Units are combined only where "
    "an exact conversion is defined; otherwise each unit is a separate series. Differences are "
    "arithmetic only. MediMind does not interpret results."
)
CHART_NOTE: Final = "The chart draws values as decimals; the table shows them exactly as stored."
IDENTITY_UNCERTAIN: Final = "Identity not confirmed for this document"


def range_state_text(state: str) -> str:
    return RANGE_STATES.get(state, state)


def duplicate_reasons_text(codes: tuple[str, ...]) -> str:
    return "; ".join(DUPLICATE_REASONS.get(c, c) for c in codes)


# --- copilot (Phase 5, ADR-040) ------------------------------------------------------------------

COPILOT_NOTE: Final = (
    "The copilot answers only from this patient's stored records. Every statement cites its "
    "source and passed MediMind's citation checks; the exact source text is shown below it. It "
    "does not diagnose, interpret results or give medical advice."
)
COPILOT_STATUS: Final = {
    "answered": ("success", "Answered from the cited records."),
    "partial": ("warning", "Partly answered: some of what you asked for is not in the records."),
    "insufficient_evidence": ("warning", "Not enough evidence in the records. Nothing is guessed."),
    "refused": ("error", "Refused."),
    "unsupported": ("info", "Outside what MediMind answers."),
    "validation_failed": ("error", "The generated answer failed citation checks and is not shown."),
    "generation_failed": ("error", "No answer could be generated."),
}
ROUTE_LABELS: Final = {
    "document_search": "Document search",
    "structured_facts": "Stored lab results",
    "longitudinal": "Lab history",
    "combined": "Stored lab results + document search",
    "unsupported": "Not supported",
    "refusal": "Safety refusal",
}
CITATION_ISSUES: Final = {
    "unknown_source": "cited a source that was not provided (fabricated)",
    "source_not_found": "cited a document page that is not in this patient's records",
    "source_location_mismatch": "cited a passage under the wrong document or page",
    "excerpt_mismatch": "the source text differs from the stored text",
    "fact_not_found": "cited a result that is not stored for this patient",
    "fact_location_mismatch": "cited a result under the wrong document or page",
    "unusable_fact": "cited a rejected, unreviewed or superseded result",
    "uncited_claim": "made a statement without a citation",
    "unsupported_number": "stated a number that its sources do not contain",
    "unsupported_date": "stated a date that its sources do not contain",
    "page_mismatch": "named a page that its sources are not on",
    "irrelevant_citation": "cited a source about a different test",
    "prohibited_content": "contained advice, a diagnosis or an interpretation",
    "empty_answer": "returned an answer with no statements",
    "claims_with_insufficient_status": "said 'insufficient evidence' but made statements",
}
REASONS.update(
    {
        "not_configured": "The language model is not configured (see MEDIMIND_LLM_* settings).",
        "external_not_allowed": (
            "External language-model endpoints are disabled (MEDIMIND_LLM_ALLOW_EXTERNAL)."
        ),
    }
)


def copilot_status(status: str) -> tuple[str, str]:
    return COPILOT_STATUS.get(status, ("info", status))


def citation_issue_text(code: str) -> str:
    return CITATION_ISSUES.get(code, f"check {code}")
