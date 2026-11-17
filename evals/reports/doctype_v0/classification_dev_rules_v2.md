# Document classification: dev split

- Dataset: `doctype_v0@8d71ffb5f01f`, split `dev`, 56 documents
- Classifier rules version: `2`
- Score: deterministic rule score, not a probability (ADR-036 D3)

| Metric | Value |
|---|---|
| accuracy | 0.9464 |
| decided_accuracy | 1.0000 |
| unknown_rate | 0.0536 |
| lab_report_precision | 1.0000 |
| lab_report_recall | 0.8750 |

## Confusion matrix (rows: true, columns: predicted)

| true \ predicted | lab_report | other | unknown |
|---|---|---|---|
| lab_report | 14 | 0 | 2 |
| other | 0 | 39 | 1 |

## By document kind (predicted counts)

| kind | lab_report | other | unknown |
|---|---|---|---|
| ambiguous | 0 | 8 | 0 |
| discharge | 0 | 7 | 1 |
| invoice | 0 | 8 | 0 |
| lab_hard | 6 | 0 | 2 |
| lab_standard | 8 | 0 | 0 |
| letter | 0 | 8 | 0 |
| prescription | 0 | 8 | 0 |

## Misclassified or unknown (3)

| document | kind / variant | true | predicted | rules |
|---|---|---|---|---|
| P01-DS | discharge / with_lab_table | other | unknown | doc.conflict, lab.has_tables, lab.reference_range, lab.results_header, lab.units, other.diagnosis, other.discharge, other.prescription |
| P02-LH | lab_hard / culture | lab_report | unknown | doc.weak, lab.letterhead, lab.sample, lab.section_heading, lab.verified_by |
| P09-LH | lab_hard / culture | lab_report | unknown | doc.weak, lab.letterhead, lab.sample, lab.section_heading, lab.verified_by |
