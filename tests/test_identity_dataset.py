"""Tests for the ``identity_v0`` dataset and the offline identity evaluation (Task 2.8).

No database and no Docling: evaluation tests use a stand-in parser that returns each PDF's
printed lines. The committed held-out split is hashed here, never parsed or evaluated.
"""

import datetime as dt
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.ingestion.validation import validate_pdf_bytes
from evals import run_identity as ri
from evals.dataset import DatasetError, load_dataset
from evals.synthetic import identity as gen

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED = REPO_ROOT / "data" / "synthetic" / "identity_v0"
FROZEN_HELDOUT_SHA256 = "1b4ec4a51b35108e8cc81429c457b1883c76044958dcef493e05c859f2652b90"


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("identity") / "identity_v0"
    gen.build_dataset(out)
    return out


# --- dataset ----------------------------------------------------------------------------------


def test_the_committed_dataset_is_reproducible_and_frozen(built) -> None:
    committed = load_dataset(COMMITTED)
    assert committed.manifest["heldout_sha256"] == FROZEN_HELDOUT_SHA256
    assert json.loads((built / "manifest.json").read_text(encoding="utf-8")) == committed.manifest
    assert committed.manifest["split"] == gen.SPLIT and committed.manifest["synthetic"] is True


def test_the_split_is_by_patient_with_families_and_namesakes_on_each_side() -> None:
    dev, heldout = set(gen.SPLIT["dev"]), set(gen.SPLIT["heldout"])
    assert dev.isdisjoint(heldout)
    people = {p.patient_key: p for p in gen.profiles()}
    for keys in (dev, heldout):
        names = [(people[k].first, people[k].last) for k in keys]
        surnames = [last for _, last in names]
        assert len(set(names)) < len(names)  # a namesake pair
        assert len(set(surnames)) < len(set(names))  # a family sharing a surname


def test_namesakes_have_different_dates_of_birth() -> None:
    people = {p.patient_key: p for p in gen.profiles()}
    assert people["I01"].display_name == people["I02"].display_name
    assert people["I01"].date_of_birth != people["I02"].date_of_birth


def test_every_pdf_is_valid_and_carries_the_disclaimer(built) -> None:
    import pypdfium2 as pdfium

    from evals.synthetic.render import DISCLAIMER

    for path in sorted((built / "pdfs").glob("I01-*.pdf")):
        data = path.read_bytes()
        validate_pdf_bytes(data, max_bytes=10_000_000, max_pages=5)
        pdf = pdfium.PdfDocument(data)
        try:
            assert DISCLAIMER in pdf[0].get_textpage().get_text_range()
        finally:
            pdf.close()


def test_a_changed_heldout_file_is_refused(built, tmp_path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(built, copy)
    (copy / "pdfs" / "I09-LH.pdf").write_bytes(b"%PDF-1.4 tampered")
    with pytest.raises(DatasetError):
        load_dataset(copy)


def test_the_text_level_case_holds_a_replacement_character() -> None:
    for patient in gen.ground_truth():
        (text_case,) = [d for d in patient["documents"] if d["source"] == "text"]
        assert "�" in text_case["text"] and text_case["variant"] == "fffd"


# --- expected outcomes by construction ---------------------------------------------------------


def _people():
    return {p.patient_key: p for p in gen.profiles()}


def test_expected_outcomes_follow_what_the_document_prints() -> None:
    people = _people()
    me, sibling, namesake = people["I05"], people["I06"], people["I02"]
    assert gen.expected_outcome("lab_header", me, me) == "match"
    assert gen.expected_outcome("lab_header", me, sibling) == "mismatch"  # same surname
    for variant in ("initials", "first_only", "no_label", "two_names", "unreadable", "fffd"):
        assert gen.expected_outcome(variant, me, me) == "unknown"
        assert gen.expected_outcome(variant, me, sibling) == "unknown"
    first = people["I01"]
    assert gen.expected_outcome("lab_header", first, namesake) == "match"  # indistinguishable
    assert gen.expected_outcome("dob_named", first, namesake) == "mismatch"  # DOB tells them apart


def test_numeric_dob_ambiguity_follows_adr_025() -> None:
    assert gen.dob_numeric_is_ambiguous(dt.date(1985, 4, 3))
    assert not gen.dob_numeric_is_ambiguous(dt.date(1985, 5, 5))
    assert not gen.dob_numeric_is_ambiguous(dt.date(1985, 3, 25))


# --- evaluation --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Page:
    page_number: int
    text: str


def _printed_parser():
    """Returns the printed lines of each PDF (Docling stand-in), keyed by the PDF bytes."""
    lines_by_bytes = {}
    for _, document_id, _, pages in gen.documents():
        text = "\n".join(block.text for block in pages[0] if hasattr(block, "text"))
        lines_by_bytes[gen.render(document_id, pages)] = text
    seen: list[bytes] = []

    def parse(data: bytes):
        seen.append(data)
        return [_Page(1, lines_by_bytes[data])]

    return parse, seen


def test_dev_evaluation_checks_every_pair_and_reads_dev_only(built) -> None:
    dataset = load_dataset(built)
    parse, seen = _printed_parser()
    pairs = ri.evaluate(dataset, "dev", parse)
    dev_docs = [d for p in dataset.patients("dev") for d in p["documents"]]
    assert len(pairs) == len(dev_docs) * len(gen.SPLIT["dev"])
    assert len(seen) == sum(1 for d in dev_docs if d["source"] == "pdf")
    heldout_bytes = {(built / "pdfs" / f"{k}-LH.pdf").read_bytes() for k in gen.SPLIT["heldout"]}
    assert not heldout_bytes & set(seen)


def test_scores_count_false_matches_and_namesakes_apart(built) -> None:
    dataset = load_dataset(built)
    parse, _ = _printed_parser()
    scores = ri.score(ri.evaluate(dataset, "dev", parse))
    assert scores["false_matches_on_swapped_profiles"] == 0
    assert scores["own_profile_false_mismatches"] == 0
    assert scores["namesake_pairs_without_dob"]["pairs"] == 4  # I01/I02 x lab_header, reordered
    assert sum(scores["own_profile"].values()) == 80


def test_score_by_hand() -> None:
    def pair(own, expected, actual, namesake=False):
        return ri.Pair("I01-LH", "lab_header", "pdf", "I01", "I01" if own else "I03", own,
                       namesake, expected, actual, ())  # fmt: skip

    pairs = [
        pair(True, "match", "match"),
        pair(True, "match", "mismatch"),  # a false mismatch
        pair(False, "mismatch", "match"),  # a false match
        pair(False, "match", "match", namesake=True),  # a namesake: reported apart
    ]
    scores = ri.score(pairs)
    assert scores["accuracy"] == 0.5
    assert scores["own_profile_false_mismatches"] == 1
    assert scores["false_matches_on_swapped_profiles"] == 1
    assert scores["namesake_pairs_without_dob"] == {
        "pairs": 1,
        "match": 1,
        "unknown": 0,
        "mismatch": 0,
    }
    with pytest.raises(ValueError):
        ri.score([])


def test_reports_hold_no_names_or_dates_and_are_never_overwritten(built, tmp_path) -> None:
    dataset = load_dataset(built)
    parse, _ = _printed_parser()
    pairs = ri.evaluate(dataset, "dev", parse)
    json_path, md_path = ri.write_report(dataset, "dev", pairs, tmp_path)
    people = gen.profiles()
    for path in (json_path, md_path):
        text = path.read_text(encoding="utf-8")
        for person in people:
            assert person.first not in text and person.date_of_birth.isoformat() not in text
    with pytest.raises(FileExistsError):
        ri.write_report(dataset, "dev", pairs, tmp_path)


# --- freeze and the one-time held-out run (temporary report folders, stand-in parser) -----------


@pytest.fixture
def frozen(built, tmp_path):
    """A dev report and a frozen selection in a temporary report folder."""
    dataset = load_dataset(built)
    parse, seen = _printed_parser()
    ri.write_report(dataset, "dev", ri.evaluate(dataset, "dev", parse), tmp_path)
    ri.freeze(dataset, tmp_path, ri.evaluate(dataset, "dev", parse))
    seen.clear()
    return dataset, tmp_path, parse, seen


def test_freeze_needs_a_dev_report_that_the_current_rules_reproduce(built, tmp_path) -> None:
    dataset = load_dataset(built)
    parse, _ = _printed_parser()
    dev = ri.evaluate(dataset, "dev", parse)
    with pytest.raises(ri.EvaluationError, match="no dev report"):
        ri.freeze(dataset, tmp_path, dev)
    json_path, _ = ri.write_report(dataset, "dev", dev, tmp_path)
    stored = json.loads(json_path.read_text(encoding="utf-8"))
    stored["pairs"][0]["actual"] = (
        "mismatch" if stored["pairs"][0]["actual"] != "mismatch" else "match"
    )
    json_path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ri.EvaluationError, match="do not reproduce"):
        ri.freeze(dataset, tmp_path, dev)
    assert not (tmp_path / ri.SELECTION).exists()


def test_freeze_is_written_once_and_records_the_rules_source(frozen) -> None:
    dataset, directory, parse, _ = frozen
    selection = json.loads((directory / ri.SELECTION).read_text(encoding="utf-8"))
    assert selection["rules_version"] == ri.IDENTITY_RULES_VERSION
    assert selection["rules_source_sha256"] == ri.rules_source_sha256()
    assert selection["heldout_sha256"] == dataset.manifest["heldout_sha256"]
    with pytest.raises(ri.EvaluationError, match="already frozen"):
        ri.freeze(dataset, directory, ri.evaluate(dataset, "dev", parse))


def test_heldout_needs_a_frozen_selection(built, tmp_path) -> None:
    dataset = load_dataset(built)
    parse, seen = _printed_parser()
    with pytest.raises(ri.EvaluationError, match="no frozen configuration"):
        ri.run_heldout(dataset, tmp_path, parse, ri.evaluate, ri.write_report)
    assert not seen and not (tmp_path / ri.HELDOUT_DONE).exists()


@pytest.mark.parametrize(
    ("key", "problem"),
    [
        ("rules_version", "rules version changed"),
        ("rules_source_sha256", "rules source changed"),
        ("dataset_version", "dataset version changed"),
        ("heldout_sha256", "held-out digest changed"),
        ("dev_report_sha256", "dev report is missing or changed"),
    ],
)
def test_heldout_refuses_any_change_since_freeze(frozen, key, problem) -> None:
    dataset, directory, parse, seen = frozen
    path = directory / ri.SELECTION
    selection = json.loads(path.read_text(encoding="utf-8"))
    selection[key] = "changed"
    path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(ri.EvaluationError, match=problem):
        ri.run_heldout(dataset, directory, parse, ri.evaluate, ri.write_report)
    assert not seen and not (directory / ri.HELDOUT_DONE).exists()


def test_heldout_runs_once_reads_heldout_only_and_says_it_is_not_blind(frozen) -> None:
    dataset, directory, parse, seen = frozen
    json_path, md_path = ri.run_heldout(dataset, directory, parse, ri.evaluate, ri.write_report)
    assert (directory / ri.HELDOUT_DONE).is_file()
    dev_bytes = {(dataset.root / "pdfs" / f"{k}-LH.pdf").read_bytes() for k in gen.SPLIT["dev"]}
    assert seen and not dev_bytes & set(seen)
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["split"] == "heldout" and report["notice"] == ri.NOT_BLIND
    assert {p["document_patient"] for p in report["pairs"]} == set(gen.SPLIT["heldout"])
    assert "NOT a blind or independent validation" in md_path.read_text(encoding="utf-8")
    with pytest.raises(ri.EvaluationError, match="already evaluated"):
        ri.run_heldout(dataset, directory, parse, ri.evaluate, ri.write_report)


def test_a_failed_heldout_run_still_uses_up_the_one_run(frozen) -> None:
    dataset, directory, parse, _ = frozen

    def broken(*_args):
        raise RuntimeError("parser crashed")

    with pytest.raises(RuntimeError):
        ri.run_heldout(dataset, directory, parse, broken, ri.write_report)
    assert (directory / ri.HELDOUT_DONE).is_file()  # never silently retried
    with pytest.raises(ri.EvaluationError, match="already evaluated"):
        ri.run_heldout(dataset, directory, parse, ri.evaluate, ri.write_report)


def test_the_committed_dev_report_matches_the_frozen_dataset() -> None:
    report = json.loads(
        (REPO_ROOT / "evals/reports/identity_v0/identity_dev_rules_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["heldout_sha256"] == FROZEN_HELDOUT_SHA256 and report["split"] == "dev"
    assert report["metrics"]["false_matches_on_swapped_profiles"] == 0
    assert {p["document_patient"] for p in report["pairs"]} == set(gen.SPLIT["dev"])
