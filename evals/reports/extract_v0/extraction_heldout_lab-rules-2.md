# Extraction evaluation: heldout (extract_v0@2f0e7a670576)

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
| all | 25 | 191 | 188 | 1.0 | 0.9843 | 0.9058 | 0.0 | 0.5479 | 0.9894 |
| kind:CL | 5 | 63 | 63 | 1.0 | 1.0 | 1.0 | 0.0 | 0.3492 | 1.0 |
| kind:FM | 5 | 0 | 0 | — | — | — | — | — | — |
| kind:HD | 5 | 45 | 45 | 1.0 | 1.0 | 1.0 | 0.0 | 0.4667 | 1.0 |
| kind:LN | 5 | 20 | 20 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 1.0 |
| kind:RS | 5 | 63 | 60 | 1.0 | 0.9524 | 0.7143 | — | 1.0 | 0.9667 |
| native | 20 | 128 | 128 | 1.0 | 1.0 | 1.0 | 0.0 | 0.3359 | 1.0 |
| raster | 5 | 63 | 60 | 1.0 | 0.9524 | 0.7143 | — | 1.0 | 0.9667 |

## End-to-end field metrics (all gold lab reports; misses count as errors)

| Field | End-to-end recall | Precision | F1 | Matched-pair accuracy (diagnostic) |
|---|---|---|---|---|
| value_num | 0.9738 | 0.9894 | 0.9815 | 0.9894 |
| comparator | 0.9843 | 1.0 | 0.9921 | 1.0 |
| qualitative | 0.9843 | 1.0 | 0.9921 | 1.0 |
| unit_raw | 0.9476 | 0.9628 | 0.9551 | 0.9628 |
| unit_norm | 0.9529 | 0.9681 | 0.9604 | 0.9681 |
| value_num_norm | 0.9424 | 0.9574 | 0.9498 | 0.9574 |
| ref_low | 0.9843 | 1.0 | 0.9921 | 1.0 |
| ref_high | 0.9843 | 1.0 | 0.9921 | 1.0 |
| flag_as_printed | 0.9738 | 0.9894 | 0.9815 | 0.9894 |
| effective_date | 0.9372 | 0.9521 | 0.9446 | 0.9521 |
| date_precision | 0.9372 | 0.9521 | 0.9446 | 0.9521 |
| date_source | 0.9372 | 0.9521 | 0.9446 | 0.9521 |
| effective_time | 0.9843 | 1.0 | 0.9921 | 1.0 |

## System mode (classifier, identity gate, extractor)

| Count | Value |
|---|---|
| classified:lab_report->lab_report | 25 |
| classified:other->lab_report | 5 |
| classified:other->other | 5 |
| identity:own:match | 24 |
| identity:own:unknown | 11 |
| identity:swapped:mismatch | 25 |
| identity:swapped:unknown | 10 |
| identity_limit_extractions | 0 |
| mismatch_blocked | 25 |
| mismatch_documents | 25 |
| mismatch_facts | 0 |
| own_facts_written | 188 |
| own_gold_facts | 191 |
| own_unknown_documents | 6 |
| own_unknown_extracted | 1 |
| raster_documents | 5 |
| raster_not_ocr | 0 |

## Review reasons and abstentions (all gold lab reports)

- Review reasons: {'ambiguous_date': 62, 'ocr_unverified_source': 60, 'unknown_unit': 6, 'unmapped_analyte': 5, 'unparsed_value': 2}
- Abstentions: {'historical_column': 5, 'line_analyte_unmapped': 26, 'multiple_subjects': 5, 'placeholder_value': 5}
- Extractor seconds per page (excluding Docling): 0.00063
