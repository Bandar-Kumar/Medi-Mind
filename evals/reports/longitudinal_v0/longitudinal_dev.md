# Longitudinal evaluation: dev (longitudinal_v0)

> Synthetic, fictional data; rules and data by the same author; not a blind test; no clinical accuracy is claimed.

Patients: L01, L02, L04, L05, L06, L08, L09, L11, L12, L14, L15, L99. Versions: {'longitudinal': 'longitudinal-1', 'loader': 'longitudinal-loader-1', 'generator': 'longitudinal-gen-1', 'normalizer': 'norm-1'}.

| Metric | Value | Target | Met |
|---|---|---|---|
| timeline_entry_precision | 1.0 | >= 1.0 | yes |
| timeline_entry_recall | 1.0 | >= 1.0 | yes |
| timeline_order_accuracy | 1.0 | >= 1.0 | yes |
| date_source_accuracy | 1.0 | >= 1.0 | yes |
| trend_point_accuracy | 1.0 | >= 1.0 | yes |
| trend_change_accuracy | 1.0 | >= 1.0 | yes |
| unit_series_accuracy | 1.0 | >= 1.0 | yes |
| duplicate_precision | 1.0 | >= 0.95 | yes |
| duplicate_recall | 1.0 | >= 0.95 | yes |
| duplicate_kind_accuracy | 1.0 |  |  |
| exact_duplicate_precision | 1.0 |  |  |
| possible_duplicate_precision | 1.0 |  |  |
| false_duplicate_rate | 0.0 | <= 0.05 | yes |
| conflict_precision | 1.0 | >= 1.0 | yes |
| conflict_recall | 1.0 | >= 1.0 | yes |
| provenance_completeness | 1.0 | >= 1.0 | yes |
| corrected_value_accuracy | 1.0 | >= 1.0 | yes |
| identity_uncertainty_shown | 1.0 | >= 1.0 | yes |
| undated_lists_correct | 1.0 |  |  |
| mismatch_count_correct | 1.0 |  |  |
| patient_isolation_leaks | 0 | <= 0 | yes |
| rejected_shown | 0 | <= 0 | yes |
| needs_review_shown | 0 | <= 0 | yes |
| mismatch_shown | 0 | <= 0 | yes |
| mismatch_gate_refusals | 11 |  |  |

## Timings (scenario patients)

| Call | n | median ms | p95 ms | max ms |
|---|---|---|---|---|
| timeline_page | 11 | 44.01 | 52.02 | 56.66 |
| analytes | 11 | 42.5 | 45.23 | 49.8 |
| trend | 44 | 61.66 | 71.34 | 74.76 |
| duplicates | 11 | 31.89 | 35.64 | 45.56 |
| conflicts | 11 | 23.86 | 29.81 | 30.37 |
| overview | 11 | 51.93 | 59.21 | 60.78 |

## Performance patient (150 documents, 1050 facts)

| Call | n | median ms | p95 ms | max ms |
|---|---|---|---|---|
| timeline_page | 6 | 291.27 | 303.76 | 372.14 |
| analytes | 1 | 376.6 | 376.6 | 376.6 |
| trend | 7 | 93.46 | 112.61 | 119.29 |
| duplicates | 1 | 287.22 | 287.22 | 287.22 |
| conflicts | 1 | 14.27 | 14.27 | 14.27 |
| overview | 1 | 341.73 | 341.73 | 341.73 |

## Counts

```
{
 "change_correct": 115,
 "change_system": 115,
 "change_truth": 115,
 "conflict_correct": 11,
 "conflict_found": 11,
 "conflict_truth": 11,
 "corrected_correct": 11,
 "corrected_total": 11,
 "date_source_correct": 335,
 "date_source_total": 335,
 "dup_correct": 66,
 "dup_found": 66,
 "dup_kind_correct": 66,
 "dup_truth": 66,
 "exact_correct": 44,
 "exact_found": 44,
 "gate_refusals_mismatch": 11,
 "identity_unknown_entries": 22,
 "identity_unknown_flagged": 22,
 "mismatch_count_correct": 11,
 "order_correct": 4286,
 "order_pairs": 4286,
 "possible_correct": 22,
 "possible_found": 22,
 "provenance_complete": 324,
 "provenance_total": 324,
 "timeline_matched": 335,
 "timeline_system": 335,
 "timeline_truth": 335,
 "trend_correct_points": 269,
 "trend_system_points": 269,
 "trend_truth_points": 269,
 "undated_checked": 11,
 "undated_correct": 11,
 "undated_documents_correct": 11,
 "unit_series_correct": 324,
 "unit_series_total": 324
}
```
