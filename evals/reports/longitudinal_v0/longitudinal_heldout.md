# Longitudinal evaluation: heldout (longitudinal_v0)

> Synthetic, fictional data; rules and data by the same author; not a blind test; no clinical accuracy is claimed.

Patients: L03, L07, L10, L13, L16. Versions: {'longitudinal': 'longitudinal-1', 'loader': 'longitudinal-loader-1', 'generator': 'longitudinal-gen-1', 'normalizer': 'norm-1'}.

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
| mismatch_gate_refusals | 5 |  |  |

## Timings (scenario patients)

| Call | n | median ms | p95 ms | max ms |
|---|---|---|---|---|
| timeline_page | 5 | 25.2 | 26.28 | 31.73 |
| analytes | 5 | 21.92 | 22.27 | 40.27 |
| trend | 20 | 35.31 | 44.88 | 48.01 |
| duplicates | 5 | 15.99 | 16.06 | 19.07 |
| conflicts | 5 | 12.68 | 13.75 | 14.66 |
| overview | 5 | 28.01 | 28.18 | 33.71 |

## Counts

```
{
 "change_correct": 53,
 "change_system": 53,
 "change_truth": 53,
 "conflict_correct": 5,
 "conflict_found": 5,
 "conflict_truth": 5,
 "corrected_correct": 5,
 "corrected_total": 5,
 "date_source_correct": 153,
 "date_source_total": 153,
 "dup_correct": 30,
 "dup_found": 30,
 "dup_kind_correct": 30,
 "dup_truth": 30,
 "exact_correct": 20,
 "exact_found": 20,
 "gate_refusals_mismatch": 5,
 "identity_unknown_entries": 10,
 "identity_unknown_flagged": 10,
 "mismatch_count_correct": 5,
 "order_correct": 1970,
 "order_pairs": 1970,
 "possible_correct": 10,
 "possible_found": 10,
 "provenance_complete": 148,
 "provenance_total": 148,
 "timeline_matched": 153,
 "timeline_system": 153,
 "timeline_truth": 153,
 "trend_correct_points": 123,
 "trend_system_points": 123,
 "trend_truth_points": 123,
 "undated_checked": 5,
 "undated_correct": 5,
 "undated_documents_correct": 5,
 "unit_series_correct": 148,
 "unit_series_total": 148
}
```
