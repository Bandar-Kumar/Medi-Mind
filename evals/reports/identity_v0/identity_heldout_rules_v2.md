# Identity verification: heldout split

> **NOT a blind or independent validation. The identity rules, this dataset's generator, the held-out documents and their expected outcomes were written by the same developer, with the same templates and categories as dev. Read these numbers as a regression check of the frozen rules on unseen synthetic patients, not as evidence of real-world accuracy.**

- Dataset: `identity_v0@1b4ec4a51b35`, split `heldout`: 40 documents x profiles = 160 pairs
- Identity rules version: `2`

| Measure | Value |
|---|---|
| accuracy vs expected (by construction) | 1.0000 |
| own profile: match / unknown / mismatch | 15 / 25 / 0 |
| own profile: false mismatches | 0 |
| swapped profiles: match / unknown / mismatch | 4 / 73 / 43 |
| **false matches on swapped profiles** (distinguishable; target 0) | **0** |
| namesake pairs without a printed DOB: pairs (match / unknown / mismatch) | 4 (4 / 0 / 0) |

Namesake pairs without a printed date of birth carry the same full name and nothing else:
they cannot be told apart from the document, so `match` is expected (a documented limit).

## Expected x actual

| expected \ actual | match | unknown | mismatch |
|---|---|---|---|
| match | 19 | 0 | 0 |
| unknown | 0 | 98 | 0 |
| mismatch | 0 | 0 | 43 |

## By variant (own profile | swapped profiles)

| variant | own: m / u / x | swapped: m / u / x |
|---|---|---|
| dob_named | 4 / 0 / 0 | 0 / 0 / 12 |
| dob_numeric | 3 / 1 / 0 | 0 / 1 / 11 |
| fffd (text-level) | 0 / 4 / 0 | 0 / 12 / 0 |
| first_only | 0 / 4 / 0 | 0 / 12 / 0 |
| initials | 0 / 4 / 0 | 0 / 12 / 0 |
| lab_header | 4 / 0 / 0 | 2 / 0 / 10 |
| no_label | 0 / 4 / 0 | 0 / 12 / 0 |
| reordered | 4 / 0 / 0 | 2 / 0 / 10 |
| two_names | 0 / 4 / 0 | 0 / 12 / 0 |
| unreadable | 0 / 4 / 0 | 0 / 12 / 0 |

## Differences from the expected outcome (0)

| document | profile | variant | expected | actual | reasons |
|---|---|---|---|---|---|
