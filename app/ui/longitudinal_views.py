"""Overview, Timeline and Lab trends pages (Phase 4; docs/PHASE_4_PLAN.md §9; USER_FLOWS F4/F5).

Thin: every value comes from ``app.services.longitudinal``; this module only lays it out. Exact
values are shown as text (``Decimal``); charts receive floats for drawing only. Source excerpts
are shown with ``st.text`` (never interpreted as Markdown or HTML).
"""

import uuid
from typing import Any

import streamlit as st

from app.services import longitudinal
from app.services.longitudinal import (
    DOCUMENT,
    LAB_RESULT,
    TimelineFilter,
    date_label,
    range_state,
)
from app.ui import texts
from app.ui.views import attempt

ENTRY_KEY = "timeline_entry"
ANALYTE_KEY = "trend_analyte"


def _number(value: Any) -> str:
    return "" if value is None else format(value, "f")


def _shown(o: Any) -> str:
    """The display value with its source label (never merged with the printed value)."""
    if o is None:
        return ""
    if o.value is None:
        text = o.qualitative or ""
    else:
        text = f"{o.comparator or ''}{_number(o.value)} {o.unit or ''}".strip()
    return text + (" (corrected by you)" if o.value_source == "human_correction" else "")


def _printed(o: Any) -> str:
    return f"{o.value_raw} {o.unit_raw or ''}".strip() if o is not None else ""


def _sources(observations: Any, filenames: dict[str, str]) -> str:
    return "; ".join(
        f"{filenames.get(s.document_id, 'document')} p{s.page_number}" for s in observations
    )


# --- overview ------------------------------------------------------------------------------------


def overview_page(patient: Any) -> None:
    st.header("Overview")
    st.caption(texts.NO_ADVICE_NOTE)
    summary = attempt(lambda: longitudinal.overview(patient.id))
    if summary is None:
        return
    cols = st.columns(5)
    cols[0].metric("Documents", summary.documents)
    cols[1].metric("Results shown", summary.results_shown)
    cols[2].metric("Awaiting review", summary.awaiting_review)
    cols[3].metric("Current conflicts", summary.current_conflicts)
    cols[4].metric("Tests tracked", summary.analytes)
    if summary.first_result_date and summary.last_result_date:
        st.text(f"Results dated from {summary.first_result_date} to {summary.last_result_date}.")
    if summary.awaiting_review:
        st.info(
            f"{summary.awaiting_review} result(s) await your review on the Documents page; "
            "they are not used in the timeline or trends until reviewed."
        )
    if summary.identity_mismatch_documents:
        st.warning(
            f"{summary.identity_mismatch_documents} document(s) do not match this profile's name "
            "or date of birth; nothing from them is shown."
        )
    if summary.identity_unknown_documents:
        st.info(f"{summary.identity_unknown_documents} document(s): {texts.IDENTITY_UNCERTAIN}.")
    if summary.exact_duplicate_groups or summary.possible_duplicate_groups:
        st.caption(
            f"Repeated results: {summary.exact_duplicate_groups} shown once; "
            f"{summary.possible_duplicate_groups} possibly the same result (shown separately)."
        )
    if summary.undated_results:
        st.caption(
            f"{summary.undated_results} result(s) have no printed date (listed on Timeline)."
        )
    if summary.latest:
        st.subheader("Latest result of each test")
        st.dataframe(
            [
                {
                    "Test": item.observation.analyte_name,
                    "Date": date_label(
                        item.observation.effective_date, item.observation.date_precision
                    ),
                    "Shown value": _shown(item.observation),
                    "Printed": _printed(item.observation),
                    "Range": texts.range_state_text(item.range_state),
                    "Status": texts.review_status_label(item.observation.review_status),
                }
                for item in summary.latest
            ],
            hide_index=True,
        )
    else:
        st.write("No results to show yet.")


# --- timeline ------------------------------------------------------------------------------------


def _timeline_filters(patient: Any) -> tuple[TimelineFilter, bool, int, int]:
    names = [a.name for a in (attempt(lambda: longitudinal.analytes(patient.id)) or ())]
    types = list(attempt(lambda: longitudinal.document_types(patient.id)) or ())
    with st.expander("Filters", expanded=False):
        c1, c2 = st.columns(2)
        date_from = c1.date_input("From", value=None, key="tl_from")
        date_to = c2.date_input("To", value=None, key="tl_to")
        chosen = st.multiselect("Tests", names, key="tl_tests")
        chosen_types = st.multiselect("Document types", types, key="tl_types")
        show = st.multiselect(
            "Show",
            [LAB_RESULT, DOCUMENT],
            default=[LAB_RESULT, DOCUMENT],
            format_func={LAB_RESULT: "Lab results", DOCUMENT: "Documents"}.get,
            key="tl_kinds",
        )
        newest_first = st.checkbox("Newest first", value=True, key="tl_newest")
        page_size = st.selectbox("Per page", [25, 50, 100, 200], index=1, key="tl_size")
        page = st.number_input("Page", min_value=1, value=1, step=1, key="tl_page")
    if date_from and date_to and date_from > date_to:
        st.warning("'From' is after 'To'; the date filter is ignored.")
        date_from = date_to = None
    filters = TimelineFilter(
        date_from=date_from,
        date_to=date_to,
        analytes=frozenset(chosen) or None,
        doc_types=frozenset(chosen_types) or None,
        kinds=frozenset(show) or None,
    )
    return filters, newest_first, int(page), int(page_size)


def _entry_row(entry: Any, filenames: dict[str, str]) -> dict[str, Any]:
    o = entry.observation
    notes = []
    if entry.duplicate_kind:
        notes.append(texts.DUPLICATE_KINDS[entry.duplicate_kind])
    if entry.identity_uncertain:
        notes.append(texts.IDENTITY_UNCERTAIN)
    if entry.kind == DOCUMENT:
        d = entry.document
        return {
            "Date": entry.label,
            "Date from": texts.DATE_SOURCES.get(entry.date_source, entry.date_source),
            "Item": f"Document ({d.doc_type})",
            "Shown value": "",
            "Printed": "",
            "Range": "",
            "Status": d.processing_status,
            "Sources": filenames.get(d.document_id, "document"),
            "Notes": "; ".join(notes),
        }
    return {
        "Date": entry.label,
        "Date from": texts.DATE_SOURCES.get(entry.date_source, entry.date_source),
        "Item": o.analyte_name or o.analyte_raw,
        "Shown value": _shown(o),
        "Printed": _printed(o),
        "Range": texts.range_state_text(range_state(o)),
        "Status": texts.review_status_label(o.review_status),
        "Sources": _sources(entry.sources, filenames),
        "Notes": "; ".join(notes),
    }


def timeline_page(patient: Any) -> None:
    st.header("Timeline")
    st.caption(texts.TIMELINE_NOTE)
    filters, newest_first, page, page_size = _timeline_filters(patient)
    view = attempt(
        lambda: longitudinal.timeline(
            patient.id,
            filters=filters,
            page=page,
            page_size=page_size,
            newest_first=newest_first,
        )
    )
    if view is None:
        return
    result = view.page
    if view.awaiting_review:
        st.info(f"{view.awaiting_review} result(s) await review and are not shown.")
    if result.excluded_mismatch_documents:
        st.warning(
            f"{result.excluded_mismatch_documents} document(s) with an identity mismatch are "
            "not shown."
        )
    if not result.entries:
        st.write(
            "Nothing to show for these filters."
            if result.total == 0
            else "No entries on this page."
        )
    else:
        st.caption(f"Page {result.page} of {result.pages} ({result.total} entries).")
        st.dataframe([_entry_row(e, view.filenames) for e in result.entries], hide_index=True)
        _evidence_panel(patient, result.entries, view.filenames)
    if result.undated_observations:
        with st.expander(f"Results without a printed date ({len(result.undated_observations)})"):
            st.dataframe(
                [
                    {
                        "Test": o.analyte_name or o.analyte_raw,
                        "Shown value": _shown(o),
                        "Printed": _printed(o),
                        "Status": texts.review_status_label(o.review_status),
                        "Source": _sources((o,), view.filenames),
                    }
                    for o in result.undated_observations
                ],
                hide_index=True,
            )
    if result.undated_documents:
        with st.expander(f"Documents without a report date ({len(result.undated_documents)})"):
            st.caption(
                "Their results may still be dated (by collection date). The upload date is shown "
                "for reference only; it is not a clinical date."
            )
            st.dataframe(
                [
                    {
                        "File": view.filenames.get(d.document_id, "document"),
                        "Type": d.doc_type,
                        "Uploaded": d.uploaded_at.strftime("%Y-%m-%d"),
                        "Status": d.processing_status,
                        "Identity": d.identity_check,
                    }
                    for d in result.undated_documents
                ],
                hide_index=True,
            )


def _evidence_panel(patient: Any, entries: Any, filenames: dict[str, str]) -> None:
    labs = [e for e in entries if e.kind == LAB_RESULT]
    if not labs:
        return
    choices = {
        s.fact_id: f"{e.label}: {s.analyte_name or s.analyte_raw} ({_sources((s,), filenames)})"
        for e in labs
        for s in e.sources
    }  # noqa: E501
    if st.session_state.get(ENTRY_KEY) not in choices:
        st.session_state[ENTRY_KEY] = next(iter(choices))
    st.selectbox("Source of a result", list(choices), format_func=choices.get, key=ENTRY_KEY)
    shown = attempt(
        lambda: longitudinal.evidence(patient.id, uuid.UUID(st.session_state[ENTRY_KEY]))
    )
    if shown is None:
        return
    f = shown.view.fact
    st.text(f"File: {shown.filename}   Page: {f.page_number}   Location: {f.source_locator}")
    st.text("Exact source text:")
    st.text(f.source_excerpt)
    if f.date_raw:
        st.text(f"Date as printed: {f.date_raw} (page {f.date_page_number})")
    if shown.is_correction:
        st.caption("The shown value is your correction; the printed value above is kept.")


# --- trends --------------------------------------------------------------------------------------


def _point_row(point: Any, filenames: dict[str, str]) -> dict[str, Any]:
    o = point.observation
    notes = []
    if len(point.sources) > 1:
        notes.append(texts.DUPLICATE_KINDS["exact"])
    if point.possible_duplicates:
        notes.append(texts.DUPLICATE_KINDS["possible"])
    if not point.plotted and point.not_plotted_reason:
        notes.append(texts.NOT_PLOTTED.get(point.not_plotted_reason, point.not_plotted_reason))
    if o.identity_check == "unknown":
        notes.append(texts.IDENTITY_UNCERTAIN)
    return {
        "Date": point.label,
        "Date from": texts.DATE_SOURCES.get(o.date_source or "", o.date_source or ""),
        "Shown value": _shown(o),
        "Printed": _printed(o),
        "Printed range": o.ref_range_raw or "",
        "Printed flag": o.flag_as_printed or "",
        "Range": texts.range_state_text(point.range_state),
        "Status": texts.review_status_label(o.review_status),
        "Sources": _sources(point.sources, filenames),
        "Notes": "; ".join(notes),
    }


def trends_page(patient: Any) -> None:
    st.header("Lab trends")
    st.caption(texts.TRENDS_NOTE)
    summaries = attempt(lambda: longitudinal.analytes(patient.id))
    if summaries is None:
        return
    if not summaries:
        st.write("No lab results yet.")
        return
    labels = {
        s.name: f"{s.name} ({s.results} result(s)"
        + (f", {s.awaiting_review} awaiting review" if s.awaiting_review else "")
        + ")"
        for s in summaries
    }
    if st.session_state.get(ANALYTE_KEY) not in labels:
        st.session_state[ANALYTE_KEY] = next(iter(labels))
    st.selectbox("Test", list(labels), format_func=labels.get, key=ANALYTE_KEY)
    view = attempt(lambda: longitudinal.trend(patient.id, st.session_state[ANALYTE_KEY]))
    if view is None:
        return
    if view.awaiting_review:
        st.info(f"{view.awaiting_review} result(s) of this test await review and are not shown.")
    if view.trend.several_units:
        st.warning(
            "This test appears in units with no defined conversion between them; each unit is "
            "shown as its own series and never compared with the others."
        )
    for series in view.trend.series:
        st.subheader(f"{view.trend.analyte_name} — {series.unit or series.kind}")
        drawn = [p for p in series.points if p.plotted]
        if len(drawn) >= 2:
            st.line_chart(
                {
                    "date": [p.date for p in drawn],
                    series.unit or "value": [float(p.observation.value) for p in drawn],
                },
                x="date",
            )
            st.caption(texts.CHART_NOTE)
        st.dataframe([_point_row(p, view.filenames) for p in series.points], hide_index=True)
        if series.changes:
            st.dataframe(
                [
                    {
                        "From": date_label(c.from_date, "day"),
                        "To": date_label(c.to_date, "day"),
                        "Difference": f"{'+' if c.delta > 0 else ''}{_number(c.delta)} {series.unit or ''}",  # noqa: E501
                        "Days between": c.days,
                    }
                    for c in series.changes
                ],
                hide_index=True,
            )
        for skipped in series.skipped_change_dates:
            st.caption(
                f"{date_label(skipped, 'day')}: {texts.CHANGE_SKIPPED['several_results_on_date']}."
            )
    if view.trend.undated:
        with st.expander(f"Results without a printed date ({len(view.trend.undated)})"):
            st.dataframe(
                [
                    {"Shown value": _shown(o), "Printed": _printed(o),
                     "Source": _sources((o,), view.filenames)}
                    for o in view.trend.undated
                ],
                hide_index=True,
            )  # fmt: skip
    _conflicts_section(view)
    _compare_section(patient)


def _conflicts_section(view: Any) -> None:
    if not view.conflicts:
        return
    st.subheader(f"Conflicting results ({len(view.conflicts)})")
    st.caption(texts.CONFLICT_NOTE)
    for conflict in view.conflicts:
        record = conflict.record
        with st.expander(
            f"{conflict.analyte_name or 'Test'} on {record.effective_date} ({record.status})"
        ):
            st.dataframe(
                [
                    {
                        "Source": f"{view.filenames.get(str(m.view.fact.document_id), 'document')} p{m.view.fact.page_number}",  # noqa: E501
                        "Printed": f"{m.view.fact.value_raw} {m.view.fact.unit_raw or ''}".strip(),
                        "Status": texts.review_status_label(m.view.fact.review_status),
                        "Role": texts.CONFLICT_ROLES[m.role],
                    }
                    for m in conflict.members
                ],
                hide_index=True,
            )
            for _a, _b, code in conflict.direct_pairs:
                st.caption(texts.CONFLICT_EXPLANATIONS.get(code, code))


def _compare_section(patient: Any) -> None:
    dates = attempt(lambda: longitudinal.result_dates(patient.id)) or ()
    if len(dates) < 2:
        return
    st.subheader("Compare two dates")
    options = list(dates)
    c1, c2 = st.columns(2)
    first = c1.selectbox(
        "First date", options, index=0, format_func=lambda d: date_label(d, "day"), key="cmp_first"
    )
    second = c2.selectbox(
        "Second date",
        options,
        index=len(options) - 1,
        format_func=lambda d: date_label(d, "day"),
        key="cmp_second",
    )  # noqa: E501
    if first == second:
        st.caption("Choose two different dates.")
        return
    result = attempt(lambda: longitudinal.compare_dates(patient.id, first, second))
    if result is None:
        return

    def values(points: Any) -> str:
        return "; ".join(_shown(p.observation) for p in points)

    st.dataframe(
        [
            {
                "Test": row.analyte_name,
                "Unit": row.unit or row.kind,
                date_label(first, "day"): values(row.first),
                date_label(second, "day"): values(row.second),
                "Difference": "" if row.delta is None else _number(row.delta),
                "Result": texts.COMPARISON_STATUS.get(row.status, row.status),
                "Note": texts.CHANGE_SKIPPED.get(row.reason or "", ""),
            }
            for row in result.rows
        ],
        hide_index=True,
    )
