"""Synthetic identity edge-case dataset ``identity_v1`` (Task 2.8, identity rules v2, ADR-036).

    uv run python -m evals.synthetic.identity_v1 --out data/synthetic/identity_v1

A dataset version, not a rules version: ``identity_v0`` stays frozen and unchanged. This one adds
the cases the review of identity rules v1 found missing: newborn and mother, initials in a
profile, single-token (mononym) profiles, near-miss spellings, token-order swaps, other people's
dates of birth, ``Age/DOB`` fields, words starting with "dob", generic ``Name:`` fields (doctor,
guardian), curly apostrophes, and Unicode format characters (soft hyphen, ZWJ, ZWNJ, ZWSP, BOM).
Every patient, name and value is fictional, and every PDF carries the synthetic disclaimer.

**Expected outcomes are written by hand, from what the document says about who it belongs to**,
never from the checker's output:
- ``subject``: the patient the document is really about (the truth).
- ``own_expected``: the outcome for the subject's own profile under the approved policy (ADR-036
  D5: strict, no fuzzy matching). ``match`` only when the document prints a patient-labelled full
  name equal to the profile's; ``unknown`` when the evidence is incomplete; ``mismatch`` only for
  a documented limit (``own_limit``: the printed name is a different spelling of the same person).
- ``indistinguishable_from``: other profiles of the split that the printed evidence cannot tell
  apart from the subject (a namesake without a printed DOB, the same tokens in another order, or
  a misspelling that equals another patient's name). ``match`` is the documented limit there.
- Every other swapped profile must **not** be ``match``; ``unknown`` and ``mismatch`` are both
  safe. No exact outcome or reason code is prescribed for swapped pairs.

Documents combine several features on purpose (a discharge summary with a doctor's ``Name:``
line, the patient's DOB and the baby's DOB), so a document does not map to one reason code.

Split by patient: ``E01``-``E14`` dev, ``H01``-``H14`` held-out. Both splits cover every category,
with different surface forms (labels, date formats, letter case) on the held-out side. The
held-out digest is frozen when the dataset is built. Format-character cases are stored as page
text (``source: text``): how a PDF parser treats those characters is not fixed by construction.
"""

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from evals.synthetic.build import _json_bytes, _sha256, heldout_digest
from evals.synthetic.doctype import Line
from evals.synthetic.identity import render

DATASET_NAME = "identity_v1"
GENERATOR_VERSION = "0.1"
MANIFEST_VERSION = 1
MATCH, MISMATCH, UNKNOWN = "match", "mismatch", "unknown"
ROLES = ("M", "B", "F", "I", "R", "S", "N1", "N2", "D", "O1", "O2", "K1", "K2", "J")
# role: M mother, B her newborn, F father, I a profile with an initial, R a full name sharing a
# token with I, S a single-token name, N1/N2 near-miss spellings, D a surname starting "Dob",
# O1/O2 the same tokens in another order, K1/K2 namesakes, J a joined name (no DOB in profile)
_PEOPLE = {
    "dev": {
        "M": ("E01", "Priya Fakeworth", "F", dt.date(1990, 8, 21)),
        "B": ("E02", "Baby Priya Fakeworth", "F", dt.date(2024, 6, 15)),
        "F": ("E03", "Rakesh Fakeworth", "M", dt.date(1986, 2, 14)),
        "I": ("E04", "R. Lakshmi", "F", dt.date(1972, 9, 30)),
        "R": ("E05", "Ramesh Lakshmi", "M", dt.date(1969, 11, 3)),
        "S": ("E06", "Selvi", "F", dt.date(1980, 7, 19)),
        "N1": ("E07", "Aarthi Demoraj", "F", dt.date(1995, 3, 27)),
        "N2": ("E08", "Aarthy Demoraj", "F", dt.date(2001, 10, 17)),
        "D": ("E09", "Neha Dobriyal", "F", dt.date(1983, 12, 29)),
        "O1": ("E10", "Mohan Arjun", "M", dt.date(1975, 5, 23)),
        "O2": ("E11", "Arjun Mohan", "M", dt.date(1978, 4, 16)),
        "K1": ("E12", "Kavin Samplenath", "M", dt.date(1964, 1, 28)),
        "K2": ("E13", "Kavin Samplenath", "M", dt.date(1991, 6, 19)),
        "J": ("E14", "Saikrishna Mockdev", "M", None),
    },
    "heldout": {
        "M": ("H01", "Divya Noname", "F", dt.date(1993, 3, 14)),
        "B": ("H02", "Baby Divya Noname", "F", dt.date(2025, 1, 20)),
        "F": ("H03", "Suresh Noname", "M", dt.date(1989, 9, 25)),
        "I": ("H04", "K. Meenakshi", "F", dt.date(1968, 5, 17)),
        "R": ("H05", "Karthik Meenakshi", "M", dt.date(1966, 10, 22)),
        "S": ("H06", "Murugan", "M", dt.date(1977, 8, 29)),
        "N1": ("H07", "Shreya Fabriq", "F", dt.date(1998, 2, 24)),
        "N2": ("H08", "Shriya Fabriq", "F", dt.date(2003, 11, 15)),
        "D": ("H09", "Anil Dobhal", "M", dt.date(1971, 7, 26)),
        "O1": ("H10", "Gopal Krishnan", "M", dt.date(1960, 12, 18)),
        "O2": ("H11", "Krishnan Gopal", "M", dt.date(1962, 3, 21)),
        "K1": ("H12", "Lalit Synthwal", "M", dt.date(1985, 10, 13)),
        "K2": ("H13", "Lalit Synthwal", "M", dt.date(1957, 4, 27)),
        "J": ("H14", "Jayaprakash Fauxnair", "M", None),
    },
}
SPLIT = {name: [key for key, *_ in people.values()] for name, people in _PEOPLE.items()}


@dataclass(frozen=True)
class Profile:
    patient_key: str
    display_name: str
    sex: str
    date_of_birth: dt.date | None


@dataclass(frozen=True)
class Case:
    """One document: its truth (``subject``) and hand-written expectations."""

    suffix: str
    subject: str  # role
    category: str
    lines: tuple[str, ...] = ()  # PDF lines (source "pdf")
    text: str | None = None  # page text (source "text")
    own_expected: str = MATCH
    own_limit: str | None = None  # why a strict policy cannot get the own profile right
    indistinguishable_from: tuple[str, ...] = field(default=())  # roles


def _d(date: dt.date | None, fmt: str) -> str:
    assert date is not None
    return date.strftime(fmt)


def _cases(split: str) -> tuple[Case, ...]:
    """The documents of one split. Dev and held-out share categories, not surface forms."""
    p = {role: Profile(*values) for role, values in _PEOPLE[split].items()}
    name = {role: profile.display_name for role, profile in p.items()}
    dob = {role: profile.date_of_birth for role, profile in p.items()}
    dev = split == "dev"
    return (
        # a mother's discharge: a doctor's generic Name: line and the baby's DOB are not hers
        Case("DIS", "M", "other_person_dob", (
            "DISCHARGE SUMMARY",
            f"Patient Name: {name['M']}    Age/Sex: 33 Y / F",
            f"DOB: {_d(dob['M'], '%d-%b-%Y')}",
            f"Baby DOB: {_d(dob['B'], '%d/%m/%Y')}",
            "Name: Dr. Kiran Imaginary (Consultant)",
        ) if dev else (
            "DISCHARGE SUMMARY",
            f"Name of Patient: {name['M']}",
            f"D.O.B.: {_d(dob['M'], '%d/%m/%Y')}",
            f"Date of Birth of Baby: {_d(dob['B'], '%d-%b-%Y')}",
            "Name : Dr. Asha Notreal",
        )),
        # the newborn's record names the mother and prints the father's DOB
        Case("NEO", "B", "newborn_mother", (
            "NEONATAL RECORD",
            f"Patient Name: {name['B']}",
            f"DOB: {_d(dob['B'], '%d-%b-%Y')}",
            f"Mother's Name: {name['M']}",
            f"Father's DOB: {_d(dob['F'], '%d/%m/%Y')}",
        ) if dev else (
            "NEONATAL RECORD",
            f"Pt. Name: {name['B']}",
            f"Date of Birth: {_d(dob['B'], '%d.%m.%Y')}",
            f"Mother’s D.O.B: {_d(dob['M'], '%d-%b-%Y')}",
            f"Father’s Name: {name['F']}",
        )),
        # the newborn's card prints only the guardian (the mother) in a generic Name: field
        Case("GRD", "B", "generic_name", (
            "IMMUNISATION CARD",
            "Guardian Details",
            f"Name: {name['M']}",
            "Relation: Mother",
            f"Baby DOB: {_d(dob['B'], '%d/%m/%Y')}",
        ) if dev else (
            "VACCINATION RECORD",
            "Parent / Guardian",
            f"Name : {name['M']}",
            f"DOB (Guardian): {_d(dob['M'], '%d/%m/%Y')}",
        ), own_expected=UNKNOWN),
        Case("LAB", "M", "newborn_mother", (
            f"Patient Name: Mrs. {name['M']}    Age/Sex: 33 Y / F",
            "Haemoglobin 11.2 gm/dL",
        ) if dev else (
            f"PATIENT NAME : MS. {name['M'].upper()}  Ref. By: Dr. Notreal",
            "Haemoglobin 12.0 gm/dL",
        )),
        Case("FAD", "F", "combined_age_dob", (
            f"Patient Name: Mr. {name['F']}  Age/DOB: 38 Y",
        ) if dev else (
            f"Name of Patient: Mr. {name['F']}  DOB/Age: 35 Yrs",
        )),
        Case("INI", "I", "initials_profile", (
            f"Patient Name: {name['I']}  Age/Sex: 52 Y / F",
        ) if dev else (
            "Patient Name: Meenakshi K",
        ), own_expected=UNKNOWN),
        # the profile holds an initial; the document spells the name out: cannot be confirmed
        Case("INX", "I", "initials_profile", (
            "Patient Name: Raman Lakshmi",
            f"DOB: {_d(dob['I'], '%d-%b-%Y')}",
        ) if dev else (
            "Pt. Name: Kannan Meenakshi",
            f"Date of Birth: {_d(dob['I'], '%d/%m/%Y')}",
        ), own_expected=UNKNOWN),
        Case("RAM", "R", "near_miss", (
            f"Patient Name: {name['R']}",
            f"DOB: {_d(dob['R'], '%d-%b-%Y')}",
        ) if dev else (
            f"Patient's Name: {name['R']}    Age/Sex: 58 Y / M",
        )),
        Case("MON", "S", "single_token", (
            f"Patient Name: {name['S']}    Age/Sex: 44 Y / F",
        ) if dev else (
            f"Patient: {name['S']}",
            f"DOB: {_d(dob['S'], '%d/%m/%Y')}",
        ), own_expected=UNKNOWN),
        Case("MOX", "S", "single_token", (
            f"Patient Name: {name['S']} Kumaravel",
        ) if dev else (
            f"Name of Patient: {name['S']} Velappan",
        ), own_expected=UNKNOWN),
        Case("NEA", "N1", "near_miss", (
            f"Patient Name: {name['N1']}",
            f"DOB: {_d(dob['N1'], '%d/%m/%Y')}",
        ) if dev else (
            f"Name of Patient - {name['N1']}  Age/Sex: 26 Y / F",
        )),
        # N1's document misspelt as N2's name: strict matching cannot know (documented limit)
        Case("TYP", "N1", "near_miss", (
            f"Patient Name: {name['N2']}    Age/Sex: 29 Y / F",
        ) if dev else (
            f"Pt. Name: {name['N2']}",
        ), own_expected=MISMATCH, own_limit="spelling_variant", indistinguishable_from=("N2",)),
        Case("CUR", "N2", "curly_apostrophe", (
            f"Patient’s Name: {name['N2']}",
            f"D.O.B: {_d(dob['N2'], '%d-%b-%Y')}",
        ) if dev else (
            f"PATIENT’S NAME : {name['N2'].upper()}    Age/Sex: 21 Y / F",
        )),
        Case("DOB", "D", "dob_word", (
            "DISCHARGE SUMMARY",
            f"Patient Name: {name['D']}  Age/DOB: 41 Y",
            "Dobutamine infusion was tapered and stopped.",
        ) if dev else (
            "DISCHARGE SUMMARY",
            f"Patient Name: {name['D']}",
            f"DOB: {_d(dob['D'], '%d-%b-%Y')}",
            "Calcium dobesilate continued.",
        )),
        Case("ORD", "O1", "token_order", (
            f"Patient Name: {name['O1']}    Age/Sex: 49 Y / M",
        ) if dev else (
            f"Pt. Name: {name['O1']}  Ref. By: Dr. Notreal",
        ), indistinguishable_from=("O2",)),
        Case("ORC", "O1", "token_order", (
            "PATIENT NAME : MR. ARJUN, MOHAN",
            f"DOB: {_d(dob['O1'], '%d-%b-%Y')}",
        ) if dev else (
            "Patient Name: Krishnan, Gopal",
            f"Date of Birth: {_d(dob['O1'], '%Y-%m-%d')}",
        )),
        Case("NSK", "K1", "namesake", (
            f"Patient Name: {name['K1']}    Age/Sex: 60 Y / M",
        ) if dev else (
            f"Name of Patient: {name['K1']}",
        ), indistinguishable_from=("K2",)),
        Case("NSD", "K2", "namesake", (
            f"Patient Name: {name['K2']}",
            f"DOB: {_d(dob['K2'], '%d/%m/%Y')}",
        ) if dev else (
            f"Patient's Name: {name['K2']}  D.O.B: {_d(dob['K2'], '%d/%m/%Y')}",
        )),
        Case("SPC", "J", "spacing_variant", (
            "Patient Name: Sai Krishna Mockdev",
        ) if dev else (
            "Patient Name: Jaya Prakash Fauxnair",
        ), own_expected=MISMATCH, own_limit="spacing_variant"),
        Case("JOK", "J", "spacing_variant", (
            f"Patient Name: {name['J']}",
            "DOB: 08-Aug-1988",
        ) if dev else (
            f"Patient Name: {name['J'].upper()}  DOB: 11-Nov-1979",
        )),
        Case("GEN", "F" if dev else "R", "generic_name", (
            f"Name: {name['F']}  Age: 38",
        ) if dev else (
            f"Name : Mr. {name['R']}  Sex: M",
        ), own_expected=UNKNOWN),
        # format characters inside the name: unreadable evidence (stored as page text)
        Case("ZW1", "M", "format_char", text=(
            "Patient Name: Pri‌ya Fakeworth\nReport Date: 15/06/2024"
            if dev else "Patient Name: Div​ya Noname\nReport Date: 20/01/2025"
        ), own_expected=UNKNOWN),
        Case("ZW2", "D", "format_char", text=(
            "Patient Name: Neha Dob­riyal\nReport Date: 15/06/2024"
            if dev else "Patient Name: ﻿Anil Dobhal\nReport Date: 20/01/2025"
        ), own_expected=UNKNOWN),
        Case("ZW3", "F", "format_char", text=(
            "Patient Name: Rakesh‍ Fakeworth\nReport Date: 15/06/2024"
            if dev else "Patient Name: Suresh No­name\nReport Date: 20/01/2025"
        ), own_expected=UNKNOWN),
    )  # fmt: skip


def profiles() -> tuple[Profile, ...]:
    return tuple(Profile(*values) for people in _PEOPLE.values() for values in people.values())


def _key(split: str, role: str) -> str:
    return _PEOPLE[split][role][0]


def documents() -> list[tuple[str, str, Case]]:
    """``(split, document id, case)`` for every document."""
    return [
        (split, f"{_key(split, case.subject)}-{case.suffix}", case)
        for split in _PEOPLE
        for case in _cases(split)
    ]


def _pages(case: Case) -> tuple[tuple[Line, ...], ...]:
    head = (Line("Sunrise Diagnostics (Fictional)", 14, True),)
    tail = (Line("Report Date: 15/06/2024"),)
    return (head + tuple(Line(text) for text in case.lines) + tail,)


def ground_truth() -> list[dict]:
    """Profiles and their documents, with the hand-written expectations."""
    by_patient: dict[str, list[dict]] = {}
    for split, document_id, case in documents():
        entry: dict = {
            "document_id": document_id,
            "category": case.category,
            "source": "text" if case.text is not None else "pdf",
            "own_expected": case.own_expected,
            "own_limit": case.own_limit,
            "indistinguishable_from": [_key(split, r) for r in case.indistinguishable_from],
        }
        if case.text is not None:
            entry["text"] = case.text
        by_patient.setdefault(_key(split, case.subject), []).append(entry)
    return [
        {
            "patient_key": person.patient_key,
            "display_name": person.display_name,
            "sex": person.sex,
            "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None,
            "documents": by_patient.get(person.patient_key, []),
        }
        for person in profiles()
    ]


def build_dataset(out: Path) -> dict:
    """Write ``identity_v1``; returns the manifest. Never overwrites a dataset."""
    if out.exists():
        raise FileExistsError(f"{out} already exists; a dataset is never overwritten")
    truth = ground_truth()
    (out / "pdfs").mkdir(parents=True)
    files: dict[str, dict] = {}
    data = _json_bytes(truth)
    (out / "ground_truth.json").write_bytes(data)
    files["ground_truth.json"] = {"sha256": _sha256(data), "size": len(data)}
    for _, document_id, case in documents():
        if case.text is not None:
            continue
        pdf = render(document_id, _pages(case))
        path = f"pdfs/{document_id}.pdf"
        (out / path).write_bytes(pdf)
        files[path] = {"sha256": _sha256(pdf), "size": len(pdf)}
    manifest = {
        "dataset": DATASET_NAME,
        "manifest_version": MANIFEST_VERSION,
        "generator_version": GENERATOR_VERSION,
        "patients": len(truth),
        "documents": sum(len(p["documents"]) for p in truth),
        "synthetic": True,
        "split": SPLIT,
        "heldout_sha256": heldout_digest(truth, files, SPLIT["heldout"]),
        "files": files,
    }
    (out / "manifest.json").write_bytes(_json_bytes(manifest))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic identity edge-case dataset.")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = build_dataset(args.out)
    print(
        f"Dataset {manifest['dataset']}: {manifest['patients']} patients, "
        f"{manifest['documents']} documents; dev {len(manifest['split']['dev'])} / held-out "
        f"{len(manifest['split']['heldout'])} patients; heldout_sha256 {manifest['heldout_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
