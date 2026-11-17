"""Tests for the ``identity_v1`` edge-case dataset and its truth-based evaluation (rules v2).

No database and no Docling: evaluation tests use a stand-in parser that returns each PDF's
printed lines. The committed held-out split is hashed here, never parsed or evaluated.
"""

import json
from pathlib import Path

import pytest

from app.ingestion.validation import validate_pdf_bytes
from evals import run_identity_v1 as rv
from evals.dataset import load_dataset
from evals.synthetic import identity_v1 as gen

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED = REPO_ROOT / "data" / "synthetic" / "identity_v1"
FROZEN_HELDOUT_SHA256 = "12ea2976caac91531fd88787b6a8d3041802b69e2c06026ee8b8a15c8e72415c"
CATEGORIES = {
    "newborn_mother", "other_person_dob", "generic_name", "combined_age_dob", "initials_profile",
    "near_miss", "single_token", "curly_apostrophe", "dob_word", "token_order", "namesake",
    "spacing_variant", "format_char",
}  # fmt: skip


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("identity_v1") / "identity_v1"
    gen.build_dataset(out)
    return out


def test_the_committed_dataset_is_reproducible_and_frozen(built) -> None:
    committed = load_dataset(COMMITTED)
    assert committed.manifest["heldout_sha256"] == FROZEN_HELDOUT_SHA256
    assert json.loads((built / "manifest.json").read_text(encoding="utf-8")) == committed.manifest
    assert committed.manifest["synthetic"] is True


def test_splits_are_patient_disjoint_and_cover_every_category() -> None:
    assert set(gen.SPLIT["dev"]).isdisjoint(gen.SPLIT["heldout"])
    for split in ("dev", "heldout"):
        keys = set(gen.SPLIT[split])
        docs = [(s, d, c) for s, d, c in gen.documents() if s == split]
        assert {case.category for _, _, case in docs} == CATEGORIES
        assert {document_id.split("-")[0] for _, document_id, _ in docs} <= keys


def test_heldout_uses_other_surface_forms_than_dev() -> None:
    printed = {split: set() for split in gen.SPLIT}
    for split, _, case in gen.documents():
        printed[split].update(line for line in case.lines if ":" in line)  # labelled fields
    assert printed["dev"] and not printed["dev"] & printed["heldout"]


def test_expectations_are_hand_written_and_consistent() -> None:
    truth = gen.ground_truth()
    split_of = {key: split for split, keys in gen.SPLIT.items() for key in keys}
    for patient in truth:
        for doc in patient["documents"]:
            assert doc["own_expected"] in {"match", "unknown", "mismatch"}
            # a mismatch on the own profile is only ever a documented limit
            assert (doc["own_expected"] == "mismatch") == (doc["own_limit"] is not None)
            for other in doc["indistinguishable_from"]:
                assert other != patient["patient_key"]
                assert split_of[other] == split_of[patient["patient_key"]]


def test_format_character_cases_are_text_level() -> None:
    for patient in gen.ground_truth():
        for doc in patient["documents"]:
            if doc["category"] == "format_char":
                assert doc["source"] == "text"
                assert any(ord(ch) in (0xAD, 0x200B, 0x200C, 0x200D, 0xFEFF) for ch in doc["text"])


def test_pdfs_are_valid_and_carry_the_disclaimer(built) -> None:
    import pypdfium2 as pdfium

    from evals.synthetic.render import DISCLAIMER

    for path in sorted((built / "pdfs").glob("E0*.pdf")):
        data = path.read_bytes()
        validate_pdf_bytes(data, max_bytes=10_000_000, max_pages=5)
        pdf = pdfium.PdfDocument(data)
        try:
            assert DISCLAIMER in pdf[0].get_textpage().get_text_range()
        finally:
            pdf.close()


# --- evaluation ----------------------------------------------------------------------------------


def _printed_parser():
    text_by_bytes = {}
    for _, document_id, case in gen.documents():
        if case.text is None:
            pages = gen._pages(case)
            text_by_bytes[gen.render(document_id, pages)] = "\n".join(
                line.text for line in pages[0]
            )
    seen: list[bytes] = []

    def parse(data: bytes):
        seen.append(data)
        return [rv._TextPage(1, text_by_bytes[data])]

    return parse, seen


def test_dev_evaluation_reads_dev_only_and_is_safe(built) -> None:
    dataset = load_dataset(built)
    parse, seen = _printed_parser()
    pairs = rv.evaluate(dataset, "dev", parse)
    assert len(pairs) == 24 * len(gen.SPLIT["dev"])
    heldout = {p.read_bytes() for p in (built / "pdfs").glob("H*.pdf")}
    assert heldout and not heldout & set(seen)
    scores = rv.score(pairs)
    assert scores["false_matches_on_swapped_profiles"] == 0
    assert scores["own_profile_false_mismatches"] == 0
    assert scores["indistinguishable_swapped_pairs"]["pairs"] == 3


def test_score_by_hand() -> None:
    def pair(own, actual, expected=None, limit=None):
        return rv.Pair("E01-LAB", "near_miss", "pdf", "E01", "E01" if own else "E02", own,
                       expected, limit, actual, ())  # fmt: skip

    scores = rv.score(
        [
            pair(True, "match", "match"),
            pair(True, "mismatch", "match"),  # a false mismatch
            pair(True, "mismatch", "mismatch", limit="spelling_variant"),  # a documented limit
            pair(False, "match"),  # a false match
            pair(False, "match", limit="indistinguishable"),  # reported apart
            pair(False, "unknown"),
        ]
    )
    assert scores["false_matches_on_swapped_profiles"] == 1
    assert scores["own_profile_false_mismatches"] == 1
    assert scores["own_profile_documented_limit_mismatches"] == 1
    assert scores["indistinguishable_swapped_pairs"]["match"] == 1
    assert scores["own_profile_agreement"] == pytest.approx(2 / 3)
    assert scores["unknown_rate"]["swapped"] == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        rv.score([])


def test_freeze_and_one_heldout_run_with_the_not_blind_notice(built, tmp_path) -> None:
    from evals import run_identity as protocol

    dataset = load_dataset(built)
    parse, seen = _printed_parser()
    rv.write_report(dataset, "dev", rv.evaluate(dataset, "dev", parse), tmp_path)
    protocol.freeze(dataset, tmp_path, rv.evaluate(dataset, "dev", parse))
    seen.clear()
    json_path, md_path = protocol.run_heldout(
        dataset, tmp_path, parse, rv.evaluate, rv.write_report
    )
    dev = {p.read_bytes() for p in (built / "pdfs").glob("E*.pdf")}
    assert seen and not dev & set(seen)
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["split"] == "heldout" and report["notice"] == protocol.NOT_BLIND
    assert {p["subject"] for p in report["pairs"]} <= set(gen.SPLIT["heldout"])
    assert "NOT a blind or independent validation" in md_path.read_text(encoding="utf-8")
    with pytest.raises(protocol.EvaluationError, match="already evaluated"):
        protocol.run_heldout(dataset, tmp_path, parse, rv.evaluate, rv.write_report)


def test_reports_hold_no_names_dates_or_text_and_are_never_overwritten(built, tmp_path) -> None:
    dataset = load_dataset(built)
    parse, _ = _printed_parser()
    pairs = rv.evaluate(dataset, "dev", parse)
    json_path, md_path = rv.write_report(dataset, "dev", pairs, tmp_path)
    for path in (json_path, md_path):
        text = path.read_text(encoding="utf-8")
        for person in gen.profiles():
            for token in person.display_name.replace(".", " ").split():
                if len(token) > 2:
                    assert token not in text
            if person.date_of_birth:
                assert person.date_of_birth.isoformat() not in text
    with pytest.raises(FileExistsError):
        rv.write_report(dataset, "dev", pairs, tmp_path)
