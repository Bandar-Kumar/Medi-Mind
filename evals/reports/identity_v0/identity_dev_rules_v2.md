# Identity verification: dev split

- Dataset: `identity_v0@1b4ec4a51b35`, split `dev`: 80 documents x profiles = 640 pairs
- Identity rules version: `2`

| Measure | Value |
|---|---|
| accuracy vs expected (by construction) | 1.0000 |
| own profile: match / unknown / mismatch | 29 / 51 / 0 |
| own profile: false mismatches | 0 |
| swapped profiles: match / unknown / mismatch | 4 / 337 / 219 |
| **false matches on swapped profiles** (distinguishable; target 0) | **0** |
| namesake pairs without a printed DOB: pairs (match / unknown / mismatch) | 4 (4 / 0 / 0) |

Namesake pairs without a printed date of birth carry the same full name and nothing else:
they cannot be told apart from the document, so `match` is expected (a documented limit).

## Expected x actual

| expected \ actual | match | unknown | mismatch |
|---|---|---|---|
| match | 33 | 0 | 0 |
| unknown | 0 | 388 | 0 |
| mismatch | 0 | 0 | 219 |

## By variant (own profile | swapped profiles)

| variant | own: m / u / x | swapped: m / u / x |
|---|---|---|
| dob_named | 8 / 0 / 0 | 0 / 0 / 56 |
| dob_numeric | 5 / 3 / 0 | 0 / 1 / 55 |
| fffd (text-level) | 0 / 8 / 0 | 0 / 56 / 0 |
| first_only | 0 / 8 / 0 | 0 / 56 / 0 |
| initials | 0 / 8 / 0 | 0 / 56 / 0 |
| lab_header | 8 / 0 / 0 | 2 / 0 / 54 |
| no_label | 0 / 8 / 0 | 0 / 56 / 0 |
| reordered | 8 / 0 / 0 | 2 / 0 / 54 |
| two_names | 0 / 8 / 0 | 0 / 56 / 0 |
| unreadable | 0 / 8 / 0 | 0 / 56 / 0 |

## Differences from the expected outcome (0)

| document | profile | variant | expected | actual | reasons |
|---|---|---|---|---|---|
