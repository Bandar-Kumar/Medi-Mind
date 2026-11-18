# Phase 6 clinical evaluation — dev

Dataset `clinical_v1@c3f4db6d64fe`; 11 fictional patients, 91 documents. Synthetic and not blind (the generator, the rules and the held-out-only variants share an author). `validated` means only that automated checks passed. No clinical accuracy is claimed.

## Pre-registered targets

| Metric | Value | Target | Met |
|---|---|---|---|
| classification_accuracy | 1.0 | >= 0.95 | yes |
| native_entity_precision | 1.0 | >= 0.95 | yes |
| native_entity_recall | 1.0 | >= 0.95 | yes |
| native_field_exact_match | 1.0 | >= 0.95 | yes |
| native_date_accuracy | 1.0 | >= 0.95 | yes |
| native_review_status_agreement | 1.0 | >= 0.95 | yes |
| native_provenance_precision | 1.0 | >= 0.95 | yes |
| native_provenance_recall | 1.0 | >= 0.95 | yes |
| conflict_precision | 1.0 | >= 0.9 | yes |
| conflict_recall | 1.0 | >= 0.9 | yes |
| comparison_accuracy | 1.0 | >= 0.9 | yes |
| qa_routing_accuracy | 1.0 | >= 0.95 | yes |
| qa_correctness | 0.986 | >= 0.9 | yes |

## Hard gates (must be 0)

| Gate | Count |
|---|---|
| ungrounded_facts | 0 |
| mismatch_profile_facts | 0 |
| refusal_probes_answered | 0 |
| invalid_citations | 0 |

## Extraction by render mode

| Group | Gold | Predicted | Matched | Entity P | Entity R | Field EM | Date acc. | Review agr. | Prov. P | Prov. R |
|---|---|---|---|---|---|---|---|---|---|---|
| native | 445 | 445 | 445 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| raster | 20 | 20 | 18 | 0.9 | 0.9 | 1.0 | 1.0 | 1.0 | 0.8 | 0.8 |

## Conflicts and comparison

Conflicts: TP 27, FP 0, FN 0 (precision 1.0, recall 1.0).
Comparison accuracy (first vs latest prescription, per medication): 1.0; extra compared items: 0.

## Questions

Routing 1.0, correctness 0.986.

| Kind | Correct / total |
|---|---|
| admissions | 11 / 11 |
| advice | 11 / 11 |
| allergies | 11 / 11 |
| changes | 11 / 11 |
| diagnoses | 11 / 11 |
| diagnosis_probe | 11 / 11 |
| history_summary | 9 / 11 |
| medication_conflicts | 11 / 11 |
| medications | 11 / 11 |
| mentions | 11 / 11 |
| prescriptions_in_year | 11 / 11 |
| prescriptions_recent | 11 / 11 |
| unsupported | 11 / 11 |

## Classification

| Expected → predicted | Count |
|---|---|
| allergy_record->allergy_record | 11 |
| consultation_note->consultation_note | 11 |
| discharge_summary->discharge_summary | 11 |
| history_record->history_record | 11 |
| imaging_report->imaging_report | 11 |
| prescription->prescription | 35 |
| unsupported_type->unsupported_type | 1 |

## Identity and gate

- identity_own:match: 88
- identity_own:unknown: 3
- identity_swapped:mismatch: 88
- identity_swapped:unknown: 3

## Abstentions

- abstention:allergy_status_unknown: 3

## Versions

- phase2_classifier_rules_version: `2`
- identity_rules_version: `2`
- classifier_version: `clinical-doctype-1`
- extractor_version: `clinical-rules-1`
- normalizer_version: `clinical-norm-1`
- compare_version: `clinical-compare-1`
- qa_version: `clinical-qa-1`
- date_policy_version: `dates-1`
- lexicon_version: `lex-e86916b193550e5b`
- clinical_source_sha256: `dbce63f36e7fb51a9a3a7be1867b3419c4cfd32186f835fed870fae5bd1bba21`

Errors listed in the JSON report: 6.
