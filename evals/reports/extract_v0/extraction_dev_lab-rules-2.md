# Extraction evaluation: dev (extract_v0@2f0e7a670576)

> Synthetic, fictional data; rules and data share one author; not a blind test; no clinical accuracy is claimed. 'validated' (auto-checked) means only that automated checks passed.

Extractor `lab-rules-2`, normalizer `norm-1`, reference `ref-535ecf5cbb7dc1ee`, date policy `dates-1` (reference date 2026-11-17).

## Hard gates (must all be 0)

| Gate | Value |
|---|---|
| unsupported_facts | 0 |
| trap_extractions | 0 |
| wrong_subject_facts | 0 |
| mismatch_facts | 0 |

## Pre-registered targets

| Metric | Value | Target | Met |
|---|---|---|---|
| silent_error_rate | 0.0 | <= 0.01 | True |
| entity_precision | 1.0 | >= 0.97 | True |
| native_entity_recall | 1.0 | >= 0.95 | True |
| native_fully_correct_recall | 1.0 | >= 0.9 | True |

## Component mode (gold lab reports)

| Group | Docs | Gold | Predicted | Entity P | Entity R | Fully correct R | Silent error | Needs review | Provenance |
|---|---|---|---|---|---|---|---|---|---|
| all | 55 | 407 | 394 | 1.0 | 0.9681 | 0.9042 | 0.0 | 0.5228 | 0.9949 |
| kind:CL | 11 | 132 | 132 | 1.0 | 1.0 | 1.0 | 0.0 | 0.3939 | 1.0 |
| kind:FM | 11 | 0 | 0 | — | — | — | — | — | — |
| kind:HD | 11 | 99 | 99 | 1.0 | 1.0 | 1.0 | 0.0 | 0.3535 | 1.0 |
| kind:LN | 11 | 44 | 44 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 1.0 |
| kind:RS | 11 | 132 | 119 | 1.0 | 0.9015 | 0.7045 | — | 1.0 | 0.9832 |
| native | 44 | 275 | 275 | 1.0 | 1.0 | 1.0 | 0.0 | 0.3164 | 1.0 |
| raster | 11 | 132 | 119 | 1.0 | 0.9015 | 0.7045 | — | 1.0 | 0.9832 |

## End-to-end field metrics (all gold lab reports; misses count as errors)

| Field | End-to-end recall | Precision | F1 | Matched-pair accuracy (diagnostic) |
|---|---|---|---|---|
| value_num | 0.9631 | 0.9949 | 0.9787 | 0.9949 |
| comparator | 0.9681 | 1.0 | 0.9838 | 1.0 |
| qualitative | 0.9681 | 1.0 | 0.9838 | 1.0 |
| unit_raw | 0.9066 | 0.9365 | 0.9213 | 0.9365 |
| unit_norm | 0.9214 | 0.9518 | 0.9364 | 0.9518 |
| value_num_norm | 0.9189 | 0.9492 | 0.9338 | 0.9492 |
| ref_low | 0.9681 | 1.0 | 0.9838 | 1.0 |
| ref_high | 0.9681 | 1.0 | 0.9838 | 1.0 |
| flag_as_printed | 0.9631 | 0.9949 | 0.9787 | 0.9949 |
| effective_date | 0.9681 | 1.0 | 0.9838 | 1.0 |
| date_precision | 0.9681 | 1.0 | 0.9838 | 1.0 |
| date_source | 0.9681 | 1.0 | 0.9838 | 1.0 |
| effective_time | 0.9681 | 1.0 | 0.9838 | 1.0 |

## System mode (classifier, identity gate, extractor)

| Count | Value |
|---|---|
| classified:lab_report->lab_report | 55 |
| classified:other->lab_report | 11 |
| classified:other->other | 11 |
| identity:own:match | 51 |
| identity:own:unknown | 26 |
| identity:swapped:mismatch | 55 |
| identity:swapped:unknown | 22 |
| identity_limit_extractions | 0 |
| mismatch_blocked | 55 |
| mismatch_documents | 55 |
| mismatch_facts | 0 |
| own_facts_written | 394 |
| own_gold_facts | 407 |
| own_unknown_documents | 15 |
| own_unknown_extracted | 4 |
| raster_documents | 11 |
| raster_not_ocr | 0 |

## Review reasons and abstentions (all gold lab reports)

- Review reasons: {'ambiguous_date': 129, 'flag_range_disagreement': 2, 'missing_unit': 4, 'ocr_unverified_source': 119, 'unknown_unit': 15, 'unmapped_analyte': 11, 'unparsed_value': 2}
- Abstentions: {'historical_column': 11, 'line_analyte_unmapped': 55, 'multiple_subjects': 11, 'placeholder_value': 11, 'table_unrecognized': 1}
- Extractor seconds per page (excluding Docling): 0.00042
