# Phase 6 clinical evaluation — heldout

Dataset `clinical_v1@c3f4db6d64fe`; 5 fictional patients, 45 documents. Synthetic and not blind (the generator, the rules and the held-out-only variants share an author). `validated` means only that automated checks passed. No clinical accuracy is claimed.

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
| qa_correctness | 0.9846 | >= 0.9 | yes |

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
| native | 202 | 202 | 202 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| raster | 10 | 10 | 9 | 0.9 | 0.9 | 1.0 | 1.0 | 1.0 | 0.9 | 0.9 |

## Conflicts and comparison

Conflicts: TP 12, FP 0, FN 0 (precision 1.0, recall 1.0).
Comparison accuracy (first vs latest prescription, per medication): 1.0; extra compared items: 0.

## Questions

Routing 1.0, correctness 0.9846.

| Kind | Correct / total |
|---|---|
| admissions | 5 / 5 |
| advice | 5 / 5 |
| allergies | 5 / 5 |
| changes | 5 / 5 |
| diagnoses | 5 / 5 |
| diagnosis_probe | 5 / 5 |
| history_summary | 5 / 5 |
| medication_conflicts | 5 / 5 |
| medications | 4 / 5 |
| mentions | 5 / 5 |
| prescriptions_in_year | 5 / 5 |
| prescriptions_recent | 5 / 5 |
| unsupported | 5 / 5 |

## Classification

| Expected → predicted | Count |
|---|---|
| allergy_record->allergy_record | 5 |
| consultation_note->consultation_note | 5 |
| discharge_summary->discharge_summary | 5 |
| history_record->history_record | 5 |
| imaging_report->imaging_report | 5 |
| prescription->prescription | 17 |
| unsupported_type->unsupported_type | 3 |

## Identity and gate

- identity_own:match: 44
- identity_own:unknown: 1
- identity_swapped:mismatch: 44
- identity_swapped:unknown: 1

## Abstentions

- abstention:allergy_status_unknown: 1

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

Errors listed in the JSON report: 3.
