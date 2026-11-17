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
    # blocked
    "not_parsed": "The document has no parsed pages yet.",
    "not_classified": "No successful classification is recorded, so it is not indexed.",
    "identity_not_checked": "No successful identity check is recorded, so it is not indexed.",
    "identity_mismatch": (
        "The identity check found a different name or date of birth: not indexed, "
        "flagged for review."
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


def processing_label(processing_status: str, *, parsed: bool, indexed: bool) -> str:
    """The document's state in words. ``processing`` after parsing is not "in progress" (D7)."""
    if processing_status == "needs_review":
        return "Needs review (identity mismatch)"
    if processing_status == "failed":
        return "Parse failed (can be run again)"
    if processing_status == "uploaded":
        return "Uploaded, not parsed"
    if processing_status == "processing" and not parsed:
        return "Parse not finished (see below)"
    if indexed:
        return "Parsed and indexed, awaiting extraction"
    if parsed:
        return "Parsed, awaiting extraction (not indexed)"
    return processing_status
