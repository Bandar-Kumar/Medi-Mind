"""Phase 3 services with stand-ins (no database): extraction, review, conflict detector.

Covers docs/PHASE_3_PLAN.md §6.5-§6.7, §9.2 and §9.4: what is recorded or not, the fixed date
reference, skipped runs, failure recording, corrections stored with their normalized view, and
conflict grouping, keys and state tokens. All data is fictional.
"""

import datetime as dt
import logging
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import DocumentProcessingError
from app.extraction.reference import load_reference
from app.records.facts import GroupFact
from app.services import extract_document as svc
from app.services import fact_conflicts, review_fact
from tests.fixtures.extraction_ir import document, lab_page

PATIENT, DOC = uuid.uuid4(), uuid.uuid4()
SECRET = "Asha Fictional 14.2"


def _target(**kw):
    base = dict(
        document_id=DOC, sha256="a" * 64, page_count=1, doc_type="lab_report",
        identity_check="match", reference_date=dt.date(2026, 11, 17), blocked_reason=None,
        extracted=False,
    )  # fmt: skip
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def stubs(monkeypatch):
    ir = document([lab_page(rows=[("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")])])
    state = SimpleNamespace(target=_target(), calls=[], fail=None, ir=ir)
    monkeypatch.setattr(svc, "get_extraction_target", lambda p, d: state.target)
    monkeypatch.setattr(
        svc, "read_published_manifest",
        lambda settings, **kw: SimpleNamespace(parse_run_id=uuid.uuid4()),
    )  # fmt: skip
    monkeypatch.setattr(svc, "parse_run_belongs_to_document", lambda *a: True)
    monkeypatch.setattr(
        svc, "load_verified_parse_outputs", lambda s, **kw: SimpleNamespace(document=state.ir)
    )
    monkeypatch.setattr(
        svc,
        "list_document_pages",
        lambda p, d: [SimpleNamespace(page_number=pg.page_number, text=pg.text) for pg in ir.pages],
    )

    def record(name):
        def call(patient_id, document_id, **kw):
            state.calls.append((name, patient_id, document_id, kw))
            if state.fail is not None:
                raise state.fail
            facts = len(kw.get("observations", ()))
            return SimpleNamespace(
                run_id=uuid.uuid4(), facts=facts, needs_review=0, superseded=0,
                conflicts_opened=0, conflicts_closed=0,
            )  # fmt: skip

        return call

    for name in ("record_extraction", "record_extraction_skipped", "reextract_and_supersede"):
        monkeypatch.setattr(svc, name, record(name))
    monkeypatch.setattr(
        svc,
        "record_extraction_failure",
        lambda p, d, **kw: state.calls.append(("failure", p, d, kw)),
    )
    monkeypatch.setattr(
        svc, "complete_document_if_ready", lambda p, d: state.calls.append(("complete",)) or True
    )
    return state


def test_extracts_with_the_documents_fixed_reference_date(stubs) -> None:
    result = svc.extract_document(SimpleNamespace(), PATIENT, DOC)
    (name, patient_id, document_id, kw) = stubs.calls[0]
    assert (name, patient_id, document_id) == ("record_extraction", PATIENT, DOC)
    assert kw["reference_date"] == dt.date(2026, 11, 17)
    assert kw["versions"]["extractor_version"] == svc.EXTRACTOR_VERSION
    assert set(kw["versions"]) == {
        "extractor_version",
        "normalizer_version",
        "reference_data_version",
        "date_policy_version",
    }
    observation = kw["observations"][0]
    assert observation.analyte_name == "Haemoglobin" and observation.review_status == "validated"
    assert kw["report_date"].value == dt.date(2024, 3, 25)
    assert callable(kw["detector"])
    assert stubs.calls[-1] == ("complete",)
    assert (result.outcome, result.facts, result.completed) == ("extracted", 1, True)


@pytest.mark.parametrize("reason", ["identity_mismatch", "identity_not_checked", "not_parsed"])
def test_a_gate_refusal_writes_no_run(stubs, reason) -> None:
    stubs.target = _target(blocked_reason=reason)
    with pytest.raises(DocumentProcessingError) as caught:
        svc.extract_document(SimpleNamespace(), PATIENT, DOC)
    assert caught.value.kind == reason and stubs.calls == []


def test_a_completed_stage_is_not_repeated(stubs) -> None:
    stubs.target = _target(extracted=True)
    with pytest.raises(DocumentProcessingError) as caught:
        svc.extract_document(SimpleNamespace(), PATIENT, DOC)
    assert caught.value.kind == "already_extracted" and stubs.calls == []


@pytest.mark.parametrize("doc_type", ["other", "unknown"])
def test_other_types_get_a_skipped_run_and_count_as_complete(stubs, doc_type) -> None:
    stubs.target = _target(doc_type=doc_type)
    result = svc.extract_document(SimpleNamespace(), PATIENT, DOC)
    assert stubs.calls[0][0] == "record_extraction_skipped"
    assert (result.outcome, result.facts) == ("not_applicable", 0)
    assert stubs.calls[-1] == ("complete",)


def test_reextraction_uses_the_superseding_write(stubs) -> None:
    stubs.target = _target(extracted=True)
    svc.extract_document(SimpleNamespace(), PATIENT, DOC, reextract=True)
    assert stubs.calls[0][0] == "reextract_and_supersede"


def test_a_failure_is_recorded_with_its_kind_and_reraised(stubs, caplog) -> None:
    stubs.fail = DocumentProcessingError(SECRET, kind="reference_data_not_seeded")
    with caplog.at_level(logging.DEBUG), pytest.raises(DocumentProcessingError):
        svc.extract_document(SimpleNamespace(), PATIENT, DOC)
    failure = next(c for c in stubs.calls if c[0] == "failure")
    assert failure[3]["error_kind"] == "reference_data_not_seeded"
    assert failure[3]["error_class"] == "DocumentProcessingError"
    assert ("complete",) not in stubs.calls
    assert all(SECRET not in r.getMessage() for r in caplog.records)


def test_an_unexpected_failure_is_recorded_as_extraction_failed(stubs) -> None:
    stubs.fail = RuntimeError(SECRET)
    with pytest.raises(RuntimeError):
        svc.extract_document(SimpleNamespace(), PATIENT, DOC)
    failure = next(c for c in stubs.calls if c[0] == "failure")
    assert failure[3]["error_kind"] == "extraction_failed"


def test_a_missing_parse_output_is_an_integrity_mismatch(stubs, monkeypatch) -> None:
    monkeypatch.setattr(svc, "read_published_manifest", lambda settings, **kw: None)
    with pytest.raises(DocumentProcessingError) as caught:
        svc.extract_document(SimpleNamespace(), PATIENT, DOC)
    assert caught.value.kind == "integrity_mismatch"


# --- the conflict detector (§6.6, §6.7) ----------------------------------------------------------

REF = load_reference()
DATE = dt.date(2024, 3, 25)


def _gf(fact_id, value, *, status="validated", decisions=(), comparator=None, reasons=False):
    return GroupFact(
        fact_id=fact_id, review_status=status,
        machine_status="needs_review" if reasons else "validated", analyte_id=7,
        analyte_name="Fasting Blood Sugar", effective_date=DATE, date_precision="day",
        effective_date_source="collection", effective_time=None,
        value_num_norm=Decimal(value) if value is not None else None, comparator=comparator,
        unit_norm="mg/dL", value_qualitative=None, decisions=tuple(decisions),
    )  # fmt: skip


def _detect(*facts):
    return fact_conflicts.detect(list(facts), REF)


def test_detector_groups_by_display_values_and_is_idempotent() -> None:
    first = _detect(_gf("a", "100"), _gf("b", "120"))
    assert len(first) == 1 and first[0].members == ("a", "b") and first[0].analyte_id == 7
    assert _detect(_gf("b", "120"), _gf("a", "100"))[0].detection_key == first[0].detection_key


def test_a_correction_changes_display_values_and_the_key() -> None:
    corrected = {"normalized": {"value_num_norm": "120", "unit_norm": "mg/dL", "analyte_id": 7,
                                "analyte_name": "Fasting Blood Sugar"}}  # fmt: skip
    equal = _detect(_gf("a", "100", status="corrected", decisions=[("d1", "correct", corrected)]),
                    _gf("b", "120"))  # fmt: skip
    assert equal == []  # the corrected value now agrees: no conflict
    still = {"normalized": {**corrected["normalized"], "value_num_norm": "105"}}
    after = _detect(_gf("a", "100", status="corrected", decisions=[("d1", "correct", still)]),
                    _gf("b", "120"))  # fmt: skip
    before = _detect(_gf("a", "100"), _gf("b", "120"))
    assert after and after[0].detection_key != before[0].detection_key


def test_a_reversed_rejection_gives_a_new_key() -> None:
    before = _detect(_gf("a", "100"), _gf("b", "120"))
    history = [("d1", "reject", None), ("d2", "verify", None)]
    reversed_ = _detect(_gf("a", "100", status="verified", decisions=history), _gf("b", "120"))
    assert reversed_[0].members == before[0].members
    assert reversed_[0].detection_key != before[0].detection_key


def test_a_plain_verify_keeps_the_key() -> None:
    before = _detect(_gf("a", "100"), _gf("b", "120"))
    verified = _detect(_gf("a", "100", status="verified", decisions=[("d1", "verify", None)]),
                       _gf("b", "120"))  # fmt: skip
    assert verified[0].detection_key == before[0].detection_key


def test_facts_without_a_day_date_or_analyte_are_not_compared() -> None:
    no_date = GroupFact(**{**_gf("a", "100").__dict__, "effective_date": None})
    no_analyte = GroupFact(**{**_gf("c", "300").__dict__, "analyte_id": None})
    assert _detect(no_date, _gf("b", "120")) == []
    assert _detect(no_analyte, _gf("b", "120")) == []


def test_the_three_member_chain_is_one_conflict() -> None:
    found = _detect(_gf("A", "5", comparator="<"), _gf("B", "7"), _gf("C", "4", comparator="<"))
    assert len(found) == 1 and found[0].members == ("A", "B", "C")


# --- review service (§6.5) ----------------------------------------------------------------------


def _fact_record(**kw):
    base = dict(
        id=uuid.uuid4(), document_id=DOC, analyte_name="Haemoglobin", analyte_id=3,
        value_raw="14.2", unit_raw="gm/dL", ref_range_raw="13.0 - 17.0",
        effective_date=DATE, date_precision="day",
    )  # fmt: skip
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def review_stubs(monkeypatch):
    state = SimpleNamespace(fact=_fact_record(), recorded=None)
    monkeypatch.setattr(review_fact, "get_fact", lambda p, f: state.fact)
    monkeypatch.setattr(
        review_fact,
        "get_extraction_target",
        lambda p, d: _target(reference_date=dt.date(2026, 1, 1)),
    )
    monkeypatch.setattr(review_fact, "reference_analyte_ids", lambda names: {n: 9 for n in names})

    def record(patient_id, fact_id, **kw):
        state.recorded = (patient_id, fact_id, kw)
        return SimpleNamespace(review_status="corrected")

    monkeypatch.setattr(review_fact, "record_review_decision", record)
    return state


def test_a_correction_stores_entered_and_normalized_values(review_stubs) -> None:
    fact_id = review_stubs.fact.id
    correction = review_fact.Correction(value="13.9")
    review_fact.review_fact(PATIENT, fact_id, "correct", correction=correction, note=" checked ")
    _, _, kw = review_stubs.recorded
    values = kw["corrected_values"]
    assert values["schema"] == "observation-correction-1"
    assert values["entered"] == {"value": "13.9"}
    normalized = values["normalized"]
    assert (normalized["value_num_norm"], normalized["unit_norm"]) == ("13.9", "g/dL")
    assert (normalized["ref_low"], normalized["ref_high"]) == ("13.0", "17.0")
    assert normalized["ref_range_comparison"] == "within"
    assert normalized["analyte_id"] == 3 and normalized["effective_date"] == "2024-03-25"
    assert kw["decision"] == "correct" and kw["note"] == "checked" and callable(kw["detector"])


def test_a_corrected_analyte_gets_its_catalogue_id(review_stubs) -> None:
    correction = review_fact.Correction(analyte="MCV", unit="fL")
    review_fact.review_fact(PATIENT, review_stubs.fact.id, "correct", correction=correction)
    normalized = review_stubs.recorded[2]["corrected_values"]["normalized"]
    assert (normalized["analyte_name"], normalized["analyte_id"]) == ("MCV", 9)


@pytest.mark.parametrize(
    "correction",
    [
        review_fact.Correction(),
        review_fact.Correction(value="about fourteen"),
        review_fact.Correction(unit="furlongs"),
        review_fact.Correction(analyte="Haemoglobn"),
        review_fact.Correction(ref_range="normal"),
        review_fact.Correction(effective_date="03/04/2024"),  # ambiguous: not accepted
        review_fact.Correction(effective_date="31/12/2026"),  # after the reference date
    ],
)
def test_unusable_corrections_are_refused_before_anything_is_written(
    review_stubs, correction
) -> None:
    with pytest.raises(DocumentProcessingError) as caught:
        review_fact.review_fact(PATIENT, review_stubs.fact.id, "correct", correction=correction)
    assert caught.value.kind == "invalid_correction" and review_stubs.recorded is None


def test_verify_and_reject_carry_no_corrected_values(review_stubs) -> None:
    for decision in ("verify", "reject"):
        review_fact.review_fact(PATIENT, review_stubs.fact.id, decision)
        assert review_stubs.recorded[2]["corrected_values"] is None
    with pytest.raises(ValueError):
        review_fact.review_fact(PATIENT, review_stubs.fact.id, "verify",
                                correction=review_fact.Correction(value="1"))  # fmt: skip
    with pytest.raises(ValueError):
        review_fact.review_fact(PATIENT, review_stubs.fact.id, "approve")
