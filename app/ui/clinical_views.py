"""Clinical records page (Phase 6; docs/PHASE_6_PLAN.md §9; ADR-042).

Thin: every value comes from ``app.services.clinical_records`` and every decision goes through
``app.services.review_clinical``; this module only lays them out. Source excerpts are shown as
plain text (data frames and ``st.text``), never interpreted as Markdown or HTML. Session state
holds identifiers only (Task 2.9b rule).

Tabs: documents (the Phase 6 type and stage outcome), medications, allergies (with the explicit
"unknown" state), clinical timeline, compare two documents, conflicts (never resolved), review.
Needs-review facts are always shown apart and marked; nothing here diagnoses or advises.
"""

import uuid
from typing import Any, Final

import streamlit as st

from app.services import clinical_records, review_clinical
from app.ui import texts
from app.ui.views import attempt

CLINICAL_NOTE: Final = (
    "What the documents say, with the page each item comes from. Items marked 'needs review' "
    "may be misread and are kept apart until you verify them. MediMind does not decide which "
    "record is right, and an empty list never means 'none'."
)
TYPE_LABELS: Final = {
    "prescription": "Prescription",
    "discharge_summary": "Discharge summary",
    "consultation_note": "Consultation note",
    "history_record": "Medical history record",
    "allergy_record": "Allergy record",
    "imaging_report": "Imaging report",
}
SKIP_LABELS: Final = {
    "lab_report": "Lab report (handled by lab extraction)",
    "unclassified": "Type not recognised: needs your review",
    "ambiguous_type": "Looks like more than one type: needs your review",
    "unsupported_type": "Not a supported document (e.g. an invoice)",
    "no_text": "No readable text",
}
STATUS_LABELS: Final = {
    "validated": "checks passed",
    "needs_review": "needs review",
    "verified": "verified by you",
    "corrected": "corrected by you",
    "rejected": "rejected by you",
}
REVIEW_FACT_KEY: Final = "clinical_fact_id"
COMPARE_KEYS: Final = ("clinical_compare_a", "clinical_compare_b")


def _when(fact: Any) -> str:
    if fact.date is None:
        return "not documented"
    if fact.date_precision == "year":
        text = str(fact.date.year)
    elif fact.date_precision == "month":
        text = fact.date.strftime("%m/%Y")
    else:
        text = fact.date.strftime("%d/%m/%Y")
    return text + (" (document date)" if fact.date_source == "document" else "")


def _identity(fact: Any) -> str:
    """The source document's identity label (acceptance fix: never hidden)."""
    return "Confirmed" if fact.identity_confirmed else "Identity not confirmed"


def _source(fact: Any) -> str:
    return f"{fact.document_name} p{fact.page_number}"


def _status(fact: Any) -> str:
    return STATUS_LABELS.get(fact.review_status, fact.review_status)


def clinical_page(patient: Any) -> None:
    st.header("Clinical records")
    st.caption(CLINICAL_NOTE)
    facts = attempt(lambda: clinical_records.clinical_facts(patient.id))
    documents = attempt(lambda: clinical_records.clinical_documents(patient.id))
    if facts is None or documents is None:
        return
    tabs = st.tabs(
        ["Documents", "Medications", "Allergies", "Timeline", "Compare", "Conflicts", "Review"]
    )
    with tabs[0]:
        _documents(documents)
    with tabs[1]:
        _medications(facts)
    with tabs[2]:
        _allergies(facts)
    with tabs[3]:
        _timeline(facts)
    with tabs[4]:
        _compare(patient, documents)
    with tabs[5]:
        _conflicts(patient)
    with tabs[6]:
        _review(patient, facts)


def _documents(documents: Any) -> None:
    if not documents:
        st.info("No documents yet. Upload them on the Documents page.")
        return
    rows = []
    for d in documents:
        if d.clinical_doc_type:
            outcome = TYPE_LABELS.get(d.clinical_doc_type, d.clinical_doc_type)
        elif d.run_status == "skipped":
            outcome = SKIP_LABELS.get(d.skip_reason or "", "Skipped")
        elif d.run_status == "failed":
            outcome = f"Extraction failed ({d.error_kind}); run processing again"
        else:
            outcome = "Not processed yet"
        rows.append(
            {
                "Document": d.document_name,
                "Clinical type / outcome": outcome,
                "Facts": d.facts,
                "Needing review": d.needs_review,
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")


def _rows(facts: list[Any], columns) -> list[dict]:
    return [
        {
            **columns(f),
            "Date": _when(f),
            "Status": _status(f),
            "Source": _source(f),
            "Identity": _identity(f),
            "Exact text": f.source_excerpt,
        }
        for f in sorted(facts, key=lambda f: (f.date is None, f.date or "", f.document_name))
    ]


def _split(facts: Any, fact_type: str) -> tuple[list[Any], list[Any]]:
    mine = [f for f in facts if f.fact_type == fact_type and f.subject == "patient"]
    return [f for f in mine if f.usable], [f for f in mine if f.uncertain]


def _medication_columns(f: Any) -> dict:
    v = f.values
    strength = (
        f"{v.get('strength_value')} {v.get('strength_unit')}" if v.get("strength_unit") else ""
    )
    return {
        "Name as printed": v.get("name_raw"),
        "Generic (lexicon)": v.get("name_norm") or "not matched",
        "Strength": strength or v.get("dose_raw") or "not documented",
        "Frequency": v.get("frequency_raw") or "not documented",
        "Duration": v.get("duration_raw") or "not documented",
        "Context": str(v.get("mention_context", "")).replace("_", " "),
    }


def _medications(facts: Any) -> None:
    st.caption(
        "Medications as documented on a date. This is not a current medication list: an old "
        "prescription does not mean a medicine is still taken."
    )
    usable, uncertain = _split(facts, "medication")
    if usable:
        st.dataframe(_rows(usable, _medication_columns), hide_index=True, width="stretch")
    else:
        st.info("No verified or validated medication is documented.")
    if uncertain:
        st.warning(f"{len(uncertain)} medication item(s) need review (may be misread).")
        st.dataframe(_rows(uncertain, _medication_columns), hide_index=True, width="stretch")


def _allergy_columns(f: Any) -> dict:
    v = f.values
    return {
        "Substance as printed": v.get("substance_raw"),
        "Status": str(v.get("allergy_status", "")).replace("_", " "),
        "Reaction": v.get("reaction_raw") or "not documented",
        "Severity": v.get("severity_norm") or "not documented",
    }


def _allergies(facts: Any) -> None:
    usable, uncertain = _split(facts, "allergy")
    if not usable and not uncertain:
        st.info(
            "Allergy status unknown: no document records an allergy or 'no known allergies'. "
            "This does not mean there are none."
        )
        return
    if usable:
        st.dataframe(_rows(usable, _allergy_columns), hide_index=True, width="stretch")
    if uncertain:
        st.warning(f"{len(uncertain)} allergy statement(s) need review.")
        st.dataframe(_rows(uncertain, _allergy_columns), hide_index=True, width="stretch")


def _event_columns(f: Any) -> dict:
    v = f.values
    return {
        "Event": str(v.get("event_type", "")).replace("_", " "),
        "As written": v.get("description_raw"),
        "Context": "past history" if v.get("event_context") == "history" else "this encounter",
    }


def _timeline(facts: Any) -> None:
    st.caption(
        "Documented events in date order; the order is MediMind's. 'Document date' means the "
        "date of the document, not a date printed for the event. Family history is not shown."
    )
    usable, uncertain = _split(facts, "event")
    if usable:
        st.dataframe(_rows(usable, _event_columns), hide_index=True, width="stretch")
    else:
        st.info("No verified or validated events are documented.")
    if uncertain:
        st.warning(f"{len(uncertain)} event(s) need review.")
        st.dataframe(_rows(uncertain, _event_columns), hide_index=True, width="stretch")


def _compare(patient: Any, documents: Any) -> None:
    typed = [d for d in documents if d.clinical_doc_type]
    if len(typed) < 2:
        st.info("At least two processed clinical documents are needed to compare.")
        return
    names = {str(d.document_id): d.document_name for d in typed}
    ids = list(names)
    first = st.selectbox("Older document (A)", ids, format_func=names.get, key=COMPARE_KEYS[0])
    second = st.selectbox(
        "Newer document (B)", ids, index=1, format_func=names.get, key=COMPARE_KEYS[1]
    )
    if first == second:
        st.info("Choose two different documents.")
        return
    result = attempt(
        lambda: clinical_records.document_comparison(
            patient.id, uuid.UUID(first), uuid.UUID(second)
        )
    )
    if result is None:
        return
    st.caption(
        "'missing' means not listed in B, which is not the same as stopped; 'discontinued' "
        "means B explicitly documents stopping it. Needs-review items are not compared."
    )
    rows = []
    for item in result.items:
        evidence = item.a_facts + item.b_facts
        label = (
            evidence[0].values.get("name_raw")
            or evidence[0].values.get("substance_raw")
            or (evidence[0].values.get("description_raw"))
        )
        rows.append(
            {
                "Kind": item.fact_type,
                "Item": label,
                "Status": item.status
                + (" (matched by exact name only)" if item.uncertain_match else ""),
                "Changed": ", ".join(item.changed_fields),
                "In A": "; ".join(f.source_excerpt for f in item.a_facts),
                "In B": "; ".join(f.source_excerpt for f in item.b_facts),
                "Identity": "Identity not confirmed"
                if any(not f.identity_confirmed for f in evidence)
                else "Confirmed",
            }
        )
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("Neither document has verified or validated items to compare.")
    if result.uncertain:
        st.warning(f"{len(result.uncertain)} item(s) need review and were not compared.")


def _conflicts(patient: Any) -> None:
    found = attempt(lambda: clinical_records.clinical_conflicts(patient.id))
    if found is None:
        return
    if not found:
        st.success(
            "No conflicts detected among verified or validated records. Items needing review "
            "are not checked."
        )
        return
    st.warning(
        f"{len(found)} conflict(s). Both records are kept; MediMind does not decide which is "
        "right and does not assume the newest is correct."
    )
    for conflict in found:
        with st.expander(conflict.conflict_type.replace("_", " ")):
            st.write("Differs in: " + ", ".join(conflict.differing_fields))
            for member in conflict.members:
                st.write(f"{_source(member)} — {_when(member)} — {_status(member)}")
                if not member.identity_confirmed:
                    st.warning(texts.IDENTITY_UNCERTAIN)
                st.text(member.source_excerpt)


def _review(patient: Any, facts: Any) -> None:
    candidates = sorted(
        (f for f in facts if f.subject == "patient"),
        key=lambda f: (f.review_status != "needs_review", f.document_name, f.page_number),
    )
    if not candidates:
        st.info("No clinical facts to review.")
        return
    labels = {
        f.id: f"[{_status(f)}] {f.fact_type}: {f.source_excerpt[:60]} ({_source(f)})"
        for f in candidates
    }
    chosen = st.selectbox("Fact", list(labels), format_func=labels.get, key=REVIEW_FACT_KEY)
    fact = next(f for f in candidates if f.id == chosen)
    st.text(fact.source_excerpt)
    st.write(f"Source: {_source(fact)}; date: {_when(fact)}; status: {_status(fact)}")
    if not fact.identity_confirmed:
        st.warning(texts.IDENTITY_UNCERTAIN)
    if fact.review_reasons:
        st.write("Why it needs review: " + ", ".join(fact.review_reasons))
    with st.form("clinical_review"):
        decision = st.radio("Your review", ["verify", "correct", "reject"], horizontal=True)
        fields = review_clinical.FIELDS[fact.fact_type]
        entered = {name: st.text_input(f"Corrected {name} (only for 'correct')") for name in fields}
        note = st.text_input("Note (optional)", max_chars=200)
        if not st.form_submit_button("Save review"):
            return
    correction = entered if decision == "correct" else None
    done = attempt(
        lambda: review_clinical.review_clinical_fact(
            patient.id, uuid.UUID(fact.id), decision, correction=correction, note=note
        )
    )
    if done is not None:
        st.success("Review saved. The original extracted text is kept.")
