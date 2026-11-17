# Document classification: heldout split

- Dataset: `doctype_v0@8d71ffb5f01f`, split `heldout`, 28 documents
- Classifier rules version: `2`
- Score: deterministic rule score, not a probability (ADR-036 D3)

| Metric | Value |
|---|---|
| accuracy | 0.9643 |
| decided_accuracy | 1.0000 |
| unknown_rate | 0.0357 |
| lab_report_precision | 1.0000 |
| lab_report_recall | 1.0000 |

## Confusion matrix (rows: true, columns: predicted)

| true \ predicted | lab_report | other | unknown |
|---|---|---|---|
| lab_report | 8 | 0 | 0 |
| other | 0 | 19 | 1 |

## By document kind (predicted counts)

| kind | lab_report | other | unknown |
|---|---|---|---|
| ambiguous | 0 | 4 | 0 |
| discharge | 0 | 3 | 1 |
| invoice | 0 | 4 | 0 |
| lab_hard | 4 | 0 | 0 |
| lab_standard | 4 | 0 | 0 |
| letter | 0 | 4 | 0 |
| prescription | 0 | 4 | 0 |

## Misclassified or unknown (1)

| document | kind / variant | true | predicted | rules |
|---|---|---|---|---|
| P06-DS | discharge / with_lab_table | other | unknown | doc.conflict, lab.has_tables, lab.reference_range, lab.results_header, lab.units, other.diagnosis, other.discharge, other.prescription |
