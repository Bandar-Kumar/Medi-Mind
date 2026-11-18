"""AppTest tests of the Phase 4 pages (Overview, Timeline, Lab trends). No database.

The longitudinal service is replaced by stand-ins that return real pure-layer results built from
fictional observations (tests/test_longitudinal.py helpers). docs/PHASE_4_PLAN.md §9.
"""

import datetime as dt
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import DocumentProcessingError
from app.extraction.reference import load_reference
from app.longitudinal.duplicates import find_duplicates
from app.longitudinal.timeline import build_timeline
from app.longitudinal.trends import build_trend, compare_dates
from app.services import longitudinal as service
from app.ui import texts
from tests.test_longitudinal import doc, obs
from tests.test_ui import SECRET, _app, _texts
from tests.test_ui import backend as ui_backend  # noqa: F401 (fixture)

REF = load_reference()


@pytest.fixture
def install(ui_backend):  # noqa: F811 (the shared UI backend fixture from tests/test_ui.py)
    return ui_backend


D = dt.date


def _history():
    first = obs(
        fact_id=str(uuid.uuid4()),
        document_id="d1",
        effective_date=D(2024, 1, 10),
        value=Decimal("14.2"),
    )
    copy = obs(
        fact_id=str(uuid.uuid4()),
        document_id="d2",
        effective_date=D(2024, 1, 10),
        value=Decimal("14.2"),
    )
    later = obs(
        fact_id=str(uuid.uuid4()),
        document_id="d3",
        effective_date=D(2024, 4, 10),
        value=Decimal("13.5"),
        range_comparison="below",
        identity_check="unknown",
    )
    other_unit = obs(document_id="d3", effective_date=D(2024, 4, 10), unit="mmol/L")
    undated = obs(document_id="d1", effective_date=None, date_precision=None)
    observations = [first, copy, later, other_unit, undated]
    documents = [
        doc("d1", document_date=D(2024, 1, 11), document_date_precision="day"),
        doc("d2"),
        doc("d3", identity_check="unknown"),
        doc("bad", identity_check="mismatch"),
    ]
    return observations, documents, find_duplicates(observations, REF)


FILES = {"d1": "lab-jan.pdf", "d2": "lab-jan-copy.pdf", "d3": "lab-apr.pdf"}


def _install(monkeypatch, *, fail: str | None = None) -> list:
    calls: list = []
    observations, documents, groups = _history()

    def maybe_fail(name: str) -> None:
        if name == fail:
            raise DocumentProcessingError(SECRET, kind="document_not_found")

    def timeline(patient_id, *, filters=None, page=1, page_size=50, newest_first=False):
        calls.append(("timeline", filters, page, page_size, newest_first))
        maybe_fail("timeline")
        built = build_timeline(
            observations, documents, groups, filters=filters, page=page, page_size=page_size,
            newest_first=newest_first,
        )  # fmt: skip
        return service.TimelineView(built, FILES, 2)

    def trend(patient_id, name):
        calls.append(("trend", name))
        maybe_fail("trend")
        return service.TrendView(build_trend(name, observations, groups), (), FILES, 1)

    def overview(patient_id):
        calls.append(("overview",))
        maybe_fail("overview")
        return service.PatientOverview(
            documents=4, documents_by_status={"processed": 4}, identity_mismatch_documents=1,
            identity_unknown_documents=1, results_shown=4, awaiting_review=2, rejected=1,
            analytes=1, first_result_date=D(2024, 1, 10), last_result_date=D(2024, 4, 10),
            current_conflicts=0, exact_duplicate_groups=1, possible_duplicate_groups=0,
            undated_results=1,
            latest=(service.LatestResult(observations[2], "below_printed_range"),),
        )  # fmt: skip

    def evidence(patient_id, fact_id):
        calls.append(("evidence", fact_id))
        fact = SimpleNamespace(
            page_number=1, source_locator="p1:t0:r1", source_excerpt="Haemoglobin\t14.2\tg/dL",
            date_raw="10/01/2024", date_page_number=1,
        )  # fmt: skip
        return service.Evidence(SimpleNamespace(fact=fact), "lab-jan.pdf", False)

    monkeypatch.setattr(service, "timeline", timeline)
    monkeypatch.setattr(service, "trend", trend)
    monkeypatch.setattr(service, "overview", overview)
    monkeypatch.setattr(service, "evidence", evidence)
    monkeypatch.setattr(
        service, "analytes",
        lambda p: (service.AnalyteSummary("Haemoglobin", 3, ("g/dL", "mmol/L"), D(2024, 1, 10),
                                          D(2024, 4, 10), 1),),
    )  # fmt: skip
    monkeypatch.setattr(service, "document_types", lambda p: ("lab_report",))
    monkeypatch.setattr(service, "result_dates", lambda p: (D(2024, 1, 10), D(2024, 4, 10)))
    monkeypatch.setattr(
        service, "compare_dates",
        lambda p, a, b: service.DateComparison(
            a, b, compare_dates(observations, groups, a, b), FILES
        ),
    )  # fmt: skip
    return calls


def _cells(at, index: int = 0) -> str:
    return repr(at.dataframe[index].value.to_dict("records"))


def test_overview_counts_and_warnings(install, monkeypatch) -> None:
    install()
    _install(monkeypatch)
    at = _app("Overview")
    assert not at.exception
    page = _texts(at)
    assert "2 result(s) await your review" in page
    assert "do not match this profile" in page
    assert texts.IDENTITY_UNCERTAIN in page
    cells = _cells(at)
    assert "13.5 g/dL" in cells and "Below this report's printed range (derived)" in cells


def test_timeline_lists_clinical_dates_sources_and_evidence(install, monkeypatch) -> None:
    install()
    calls = _install(monkeypatch)
    at = _app("Timeline")
    assert not at.exception
    (call,) = [c for c in calls if c[0] == "timeline"]
    assert call[4] is True  # newest first by default
    cells = _cells(at)
    assert "lab-jan.pdf p1; lab-jan-copy.pdf p1" in cells  # exact duplicate shown once
    assert texts.DUPLICATE_KINDS["exact"] in cells
    assert "collection date" in cells and "report date of the document" in cells
    assert texts.IDENTITY_UNCERTAIN in cells
    page = _texts(at)
    assert "2 result(s) await review" in page
    assert "identity mismatch are not shown" in page
    assert "Haemoglobin\t14.2\tg/dL" in [t.value for t in at.text]  # exact source text
    assert any("Results without a printed date (1)" in e.label for e in at.expander)
    assert any("Documents without a report date (2)" in e.label for e in at.expander)


def test_trends_show_separate_unit_series_changes_and_comparison(install, monkeypatch) -> None:
    install()
    _install(monkeypatch)
    at = _app("Lab trends")
    assert not at.exception
    page = _texts(at)
    assert "never compared with the others" in page  # several units warning
    assert "1 result(s) of this test await review" in page
    subheaders = [s.value for s in at.subheader]
    assert "Haemoglobin — g/dL" in subheaders and "Haemoglobin — mmol/L" in subheaders
    everything = " ".join(_cells(at, i) for i in range(len(at.dataframe)))
    assert "-0.7 g/dL" in everything  # exact Decimal difference
    assert "Below this report's printed range (derived)" in everything
    assert "Only on the second date" in everything or "Changed" in everything
    lowered = page.lower() + everything.lower()
    assert "abnormal" not in lowered and " normal" not in lowered  # no clinical judgement words


def test_service_errors_show_fixed_text_only(install, monkeypatch) -> None:
    install()
    for page_name, failing in (("Timeline", "timeline"), ("Overview", "overview")):
        _install(monkeypatch, fail=failing)
        at = _app(page_name)
        assert not at.exception
        assert SECRET not in _texts(at)


def test_the_timeline_filter_reaches_the_service(install, monkeypatch) -> None:
    install()
    calls = _install(monkeypatch)
    at = _app("Timeline")
    at.multiselect(key="tl_tests").set_value(["Haemoglobin"]).run()
    assert not at.exception
    filters = [c for c in calls if c[0] == "timeline"][-1][1]
    assert filters.analytes == frozenset({"Haemoglobin"})
