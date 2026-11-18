"""Versioned reference data for normalization (docs/PHASE_3_PLAN.md §5.7, §7.3, §7.5).

Version-controlled CSV files in ``app/extraction/reference/``:
- ``analytes_v1.csv``            canonical analytes, default unit, unitless flag, sanity bounds;
- ``aliases_v1.csv``             printed names (lowercase) -> canonical analyte;
- ``unit_synonyms_v1.csv``       printed unit spellings -> canonical unit;
- ``unit_scales_v1.csv``         generic power-of-ten unit scales (lakh, million, thousand);
- ``unit_conversions_v1.csv``    analyte-specific conversions toward the default unit, with sources;
- ``qualitative_scales_v1.csv``  analyte-specific classes of qualitative tokens (§6.6).

``reference_data_version`` is a SHA-256 prefix over every file's name and bytes, so any change to
the data changes the version recorded on each fact. The pure normalizers read these files; the
``seed-reference`` command copies the analyte tables into the database.
"""

import csv
import hashlib
import io
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from pathlib import Path

from app.extraction.normalize.numbers import text_key

REFERENCE_DIR = Path(__file__).parent / "reference"
FILES = (
    "aliases_v1.csv",
    "analytes_v1.csv",
    "qualitative_scales_v1.csv",
    "unit_conversions_v1.csv",
    "unit_scales_v1.csv",
    "unit_synonyms_v1.csv",
)


class ReferenceDataError(ValueError):
    """A reference file is missing or inconsistent (a programming error, never user data)."""


@dataclass(frozen=True)
class Analyte:
    canonical_name: str
    default_unit: str  # canonical spelling; "" for a qualitative (unitless) analyte
    unitless: bool
    plausible_min: Decimal | None
    plausible_max: Decimal | None


@dataclass(frozen=True)
class Conversion:
    factor: Decimal
    addend: Decimal


@dataclass(frozen=True)
class QualitativeScale:
    classes: dict[str, str]  # canonical token -> class
    opposite: frozenset[str]  # the one declared pair of opposite classes


@dataclass(frozen=True)
class Reference:
    version: str
    analytes: dict[str, Analyte] = field(repr=False)
    aliases: dict[str, str] = field(repr=False)  # alias key -> canonical name
    unit_synonyms: dict[str, str] = field(repr=False)  # spelling key -> canonical unit
    unit_scales: dict[str, tuple[str, int]] = field(repr=False)  # unit -> (base, power)
    conversions: dict[tuple[str, str, str], Conversion] = field(repr=False)
    qualitative: dict[str, QualitativeScale] = field(repr=False)
    alias_rows: tuple[tuple[str, str], ...] = field(repr=False)  # for database seeding


def _rows(directory: Path, name: str) -> list[dict[str, str]]:
    data = (directory / name).read_bytes().decode("utf-8")
    return list(csv.DictReader(io.StringIO(data)))


def _decimal(text: str) -> Decimal | None:
    return Decimal(text) if text.strip() else None


def compute_version(directory: Path = REFERENCE_DIR) -> str:
    digest = hashlib.sha256()
    for name in FILES:
        digest.update(name.encode("utf-8") + b"\0" + (directory / name).read_bytes() + b"\0")
    return "ref-" + digest.hexdigest()[:16]


def load_reference_from(directory: Path) -> Reference:
    analytes: dict[str, Analyte] = {}
    for row in _rows(directory, "analytes_v1.csv"):
        name = row["canonical_name"]
        if name in analytes:
            raise ReferenceDataError(f"duplicate analyte {name!r}")
        low, high = _decimal(row["plausible_min"]), _decimal(row["plausible_max"])
        if low is not None and high is not None and low >= high:
            raise ReferenceDataError(f"invalid bounds for {name!r}")
        analytes[name] = Analyte(name, row["default_unit"], row["unitless"] == "true", low, high)
    alias_rows: list[tuple[str, str]] = []
    aliases: dict[str, str] = {}
    for row in _rows(directory, "aliases_v1.csv"):
        alias, name = row["alias"], row["canonical_name"]
        if name not in analytes or alias != alias.strip().lower():
            raise ReferenceDataError(f"invalid alias row for {name!r}")
        key = analyte_key(alias)
        if aliases.get(key, name) != name:
            raise ReferenceDataError(f"alias {alias!r} maps to two analytes")
        aliases[key] = name
        alias_rows.append((alias, name))
    synonyms: dict[str, str] = {}
    for row in _rows(directory, "unit_synonyms_v1.csv"):
        key = text_key(row["spelling"])
        if synonyms.get(key, row["canonical_unit"]) != row["canonical_unit"]:
            raise ReferenceDataError(f"unit spelling {row['spelling']!r} is ambiguous")
        synonyms[key] = row["canonical_unit"]
    for canonical in set(synonyms.values()):
        synonyms.setdefault(text_key(canonical), canonical)
    scales = {
        row["canonical_unit"]: (row["base_unit"], int(row["power_of_ten"]))
        for row in _rows(directory, "unit_scales_v1.csv")
    }
    conversions: dict[tuple[str, str, str], Conversion] = {}
    for row in _rows(directory, "unit_conversions_v1.csv"):
        name = row["canonical_name"]
        if name not in analytes or row["to_unit"] != analytes[name].default_unit:
            raise ReferenceDataError(f"conversion for {name!r} must target the default unit")
        if not row["source_note"].strip():
            raise ReferenceDataError(f"conversion for {name!r} has no source")
        factor = Decimal(row["factor"])
        if factor == 0:
            raise ReferenceDataError(f"conversion for {name!r} has a zero factor")
        conversions[(name, row["from_unit"], row["to_unit"])] = Conversion(
            factor, Decimal(row["addend"] or "0")
        )
    qualitative: dict[str, QualitativeScale] = {}
    for row in _rows(directory, "qualitative_scales_v1.csv"):
        name = row["canonical_name"]
        if name not in analytes or not analytes[name].unitless:
            raise ReferenceDataError(f"qualitative scale for non-qualitative analyte {name!r}")
        opposite = frozenset(row["opposite_classes"].split("|"))
        scale = qualitative.setdefault(name, QualitativeScale({}, opposite))
        if scale.opposite != opposite or len(opposite) != 2:
            raise ReferenceDataError(f"inconsistent opposite classes for {name!r}")
        scale.classes[row["token"]] = row["class"]
    return Reference(
        version=compute_version(directory),
        analytes=analytes,
        aliases=aliases,
        unit_synonyms=synonyms,
        unit_scales=scales,
        conversions=conversions,
        qualitative=qualitative,
        alias_rows=tuple(alias_rows),
    )


@cache
def load_reference() -> Reference:
    """The packaged reference data (loaded once per process)."""
    return load_reference_from(REFERENCE_DIR)


def analyte_key(text: str) -> str:
    """Lookup key for a printed analyte name: NFKC, lower case, collapsed whitespace, trailing
    ``:`` or ``.`` removed. Parentheses are kept, so ``Vitamin D (25-OH)`` stays distinct."""
    key = text_key(text).rstrip(":. ")
    return key
