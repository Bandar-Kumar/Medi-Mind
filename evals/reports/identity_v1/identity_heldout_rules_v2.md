# Identity verification (edge cases): heldout split

> **NOT a blind or independent validation. The identity rules, this dataset's generator, the held-out documents and their expected outcomes were written by the same developer, with the same templates and categories as dev. Read these numbers as a regression check of the frozen rules on unseen synthetic patients, not as evidence of real-world accuracy.**

- Dataset: `identity_v1@12ea2976caac`, split `heldout`: 24 documents x profiles = 336 pairs
- Identity rules version: `2`
- Scored against the truth (whose document it is) and hand-written own-profile expectations, not against reason codes.

| Measure | Value |
|---|---|
| **false matches on swapped profiles** (excluding indistinguishable; target 0) | **0** |
| **own profile: false mismatches** (excluding documented limits; target 0) | **0** |
| own profile: documented-limit mismatches | 2 |
| indistinguishable swapped pairs: pairs (match / unknown / mismatch) | 3 (3 / 0 / 0) |
| own profile: agreement with the hand-written expectation | 1.0000 |
| own profile: match / unknown / mismatch | 13 / 9 / 2 |
| swapped profiles: match / unknown / mismatch | 3 / 99 / 210 |
| unknown rate: all / own / swapped | 0.3214 / 0.3750 / 0.3173 |

## By category (own profile | swapped profiles; match / unknown / mismatch)

| category | own | swapped |
|---|---|---|
| combined_age_dob | 1 / 0 / 0 | 0 / 2 / 11 |
| curly_apostrophe | 1 / 0 / 0 | 0 / 2 / 11 |
| dob_word | 1 / 0 / 0 | 0 / 0 / 13 |
| format_char | 0 / 3 / 0 | 0 / 39 / 0 |
| generic_name | 0 / 2 / 0 | 0 / 26 / 0 |
| initials_profile | 0 / 2 / 0 | 0 / 13 / 13 |
| namesake | 2 / 0 / 0 | 1 / 2 / 23 |
| near_miss | 2 / 0 / 1 | 1 / 6 / 32 |
| newborn_mother | 2 / 0 / 0 | 0 / 3 / 23 |
| other_person_dob | 1 / 0 / 0 | 0 / 0 / 13 |
| single_token | 0 / 2 / 0 | 0 / 2 / 24 |
| spacing_variant | 1 / 0 / 1 | 0 / 2 / 24 |
| token_order | 2 / 0 / 0 | 1 / 2 / 23 |

## Reason codes (count of pairs carrying each code)

| code | own | swapped |
|---|---|---|
| dob_match | 7 | 0 |
| dob_mismatch | 0 | 97 |
| dob_not_in_profile | 1 | 7 |
| dob_not_printed | 16 | 208 |
| name_generic_label | 1 | 13 |
| name_initials | 1 | 13 |
| name_match | 13 | 5 |
| name_mismatch | 2 | 181 |
| name_not_found | 1 | 13 |
| name_partial | 0 | 15 |
| name_unreadable | 3 | 39 |
| profile_name_incomplete | 3 | 33 |

## Own-profile differences, limits and every swapped match (5)

| document | profile | category | expected | actual | limit | reasons |
|---|---|---|---|---|---|---|
| H07-TYP | H07 | near_miss | mismatch | mismatch | spelling_variant | dob_not_printed, name_mismatch |
| H07-TYP | H08 | near_miss | not match | match | indistinguishable | dob_not_printed, name_match |
| H10-ORD | H11 | token_order | not match | match | indistinguishable | dob_not_printed, name_match |
| H12-NSK | H13 | namesake | not match | match | indistinguishable | dob_not_printed, name_match |
| H14-SPC | H14 | spacing_variant | mismatch | mismatch | spacing_variant | dob_not_printed, name_mismatch |
