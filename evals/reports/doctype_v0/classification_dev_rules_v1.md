# Document classification: dev split

- Dataset: `doctype_v0@8d71ffb5f01f`, split `dev`, 56 documents
- Classifier rules version: `1`
- Score: deterministic rule score, not a probability (ADR-036 D3)

| Metric | Value |
|---|---|
| accuracy | 0.9107 |
| decided_accuracy | 1.0000 |
| unknown_rate | 0.0893 |
| lab_report_precision | 1.0000 |
| lab_report_recall | 0.7500 |

## Confusion matrix (rows: true, columns: predicted)

| true \ predicted | lab_report | other | unknown |
|---|---|---|---|
| lab_report | 12 | 0 | 4 |
| other | 0 | 39 | 1 |

## By document kind (predicted counts)

| kind | lab_report | other | unknown |
|---|---|---|---|
| ambiguous | 0 | 8 | 0 |
| discharge | 0 | 7 | 1 |
| invoice | 0 | 8 | 0 |
| lab_hard | 4 | 0 | 4 |
| lab_standard | 8 | 0 | 0 |
| letter | 0 | 8 | 0 |
| prescription | 0 | 8 | 0 |

## Misclassified or unknown (5)

| document | kind / variant | true | predicted | rules |
|---|---|---|---|---|
| P01-DS | discharge / with_lab_table | other | unknown | doc.conflict, lab.has_tables, lab.reference_range, lab.results_header, lab.units, other.diagnosis, other.discharge, other.prescription |
| P02-LH | lab_hard / culture | lab_report | unknown | doc.weak, lab.letterhead, lab.sample, lab.section_heading, lab.verified_by |
| P03-LH | lab_hard / key_value | lab_report | unknown | doc.weak, lab.letterhead, lab.section_heading |
| P04-LH | lab_hard / key_value | lab_report | unknown | doc.weak, lab.letterhead, lab.section_heading |
| P09-LH | lab_hard / culture | lab_report | unknown | doc.weak, lab.letterhead, lab.sample, lab.section_heading, lab.verified_by |
