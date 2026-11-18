"""The medication-name lexicon (``reference/medications.csv``; ADR-005, ADR-042).

The lexicon is **fictional**: it names only the synthetic medicines of the evaluation datasets, so
no output can be read as information about a real drug. A real deployment would need a licensed,
reviewed drug database; nothing here is medical knowledge.

Lookup is exact on a case-folded, whitespace-collapsed key:
- a generic name → ``generic_exact``;
- a brand naming one generic → ``brand_alias``;
- a brand naming two or more generics → ambiguous (review; no generic is chosen);
- anything else → unmapped (review). Nothing is fuzzy-matched: a near miss is never "corrected".

The version is a SHA-256 prefix over the file bytes, recorded on every fact as its
``reference_data_version``.
"""

import csv
import hashlib
import io
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

LEXICON_PATH = Path(__file__).resolve().parent / "reference" / "medications.csv"


class NameMatch(StrEnum):
    GENERIC_EXACT = "generic_exact"
    BRAND_ALIAS = "brand_alias"


@dataclass(frozen=True)
class NameLookup:
    generic: str | None  # None: unmapped or ambiguous
    match: NameMatch | None
    ambiguous: bool = False


@dataclass(frozen=True)
class Lexicon:
    version: str
    entries: dict[str, tuple[tuple[str, str], ...]] = field(repr=False)  # key -> (generic, kind)

    def lookup(self, name: str) -> NameLookup:
        found = self.entries.get(name_key(name), ())
        generics = sorted({generic for generic, _ in found})
        if not generics:
            return NameLookup(None, None)
        if len(generics) > 1:
            return NameLookup(None, None, ambiguous=True)
        kinds = {kind for _, kind in found}
        match = NameMatch.GENERIC_EXACT if "generic" in kinds else NameMatch.BRAND_ALIAS
        return NameLookup(generics[0], match)


class LexiconError(ValueError):
    """The lexicon file is malformed (messages name rows, never patient content)."""


def name_key(name: str) -> str:
    return " ".join(name.casefold().split())


def parse_lexicon(data: bytes) -> Lexicon:
    entries: dict[str, list[tuple[str, str]]] = {}
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
    if reader.fieldnames != ["name", "generic", "kind"]:
        raise LexiconError("the lexicon header must be name,generic,kind")
    for number, row in enumerate(reader, start=2):
        name, generic, kind = row["name"], row["generic"], row["kind"]
        if not name or not generic or kind not in ("generic", "brand"):
            raise LexiconError(f"lexicon row {number} is incomplete or has an unknown kind")
        if name != name_key(name) or generic != name_key(generic):
            raise LexiconError(f"lexicon row {number} is not normalized")
        if kind == "generic" and name != generic:
            raise LexiconError(f"lexicon row {number}: a generic row must name itself")
        entries.setdefault(name, []).append((generic, kind))
    generics = {g for rows in entries.values() for g, _ in rows}
    for generic in generics:
        if (generic, "generic") not in entries.get(generic, []):
            raise LexiconError("every generic named by a brand must have its own generic row")
    version = "lex-" + hashlib.sha256(data).hexdigest()[:16]
    return Lexicon(version, {k: tuple(v) for k, v in entries.items()})


@lru_cache(maxsize=1)
def load_lexicon() -> Lexicon:
    return parse_lexicon(LEXICON_PATH.read_bytes())
