# Phase 5 copilot: copilot_v1 held-out (single run)

- Dataset: `copilot_v1`, split `heldout`, SHA-256 `d7bb8b46903504b9a553a07a6d2af155950cadee7dc1ea9208ea53f39da69364` (167 questions)
- Held-out digest (frozen at build): `d7bb8b46903504b9a553a07a6d2af155950cadee7dc1ea9208ea53f39da69364`
- Synthetic, fictional data; labels from the source datasets' truth. Not a clinical accuracy claim.

## Configuration

- `generator`: `extractive`
- `composer_version`: `extractive-1`
- `prompt_version`: `copilot-prompt-1`
- `vocabulary_version`: `vocab-1`
- `reference_data_version`: `ref-535ecf5cbb7dc1ee`
- `code_sha256`: `3ce4fa5beb2ebad67067487e9f7dc874ce5101b2044ae872bd3f4b8a9366b08a`
- `today`: `2026-11-17`
- `retrieval_mode`: `lexical`
- `retrieval_abstain_threshold`: `None`
- `retrieval_abstain_threshold_hex`: `None`
- `retrieval_top_k`: `5`
- `retrieval_candidates`: `50`
- `copilot_v1_selection_sha256`: `85d0981069927cb63d72cd766b1d1fc7a177d088c71500c804f8d3d812652df7`

## Targets (pre-registered)

| Metric | Value | Target | Met |
|---|---|---|---|
| routing_accuracy | 1.0000 | >= 0.9 | True |
| answer_correctness | 1.0000 | >= 0.8 | True |
| citation_precision | 1.0000 | >= 0.95 | True |
| citation_recall | 1.0000 | >= 0.9 | True |
| unsupported_claim_rate | 0.0000 | <= 0.0 | True |
| abstention_f1 | 1.0000 | >= 0.9 | True |
| refusal_rate | 1.0000 | >= 1.0 | True |
| unsupported_route_rate | 1.0000 | >= 1.0 | True |
| leak_questions | 0 | <= 0 | True |
| rejected_exposed | 0 | <= 0 | True |
| needs_review_exposed | 0 | <= 0 | True |
| mismatch_exposed | 0 | <= 0 | True |
| invalid_citation_acceptance | 0.0000 | <= 0.0 | True |

## All metrics

| Metric | Value |
|---|---|
| routing_accuracy | 1.0000 |
| route_accuracy | 1.0000 |
| status_accuracy | 1.0000 |
| answer_correctness | 1.0000 |
| citation_recall | 1.0000 |
| citation_precision | 1.0000 |
| claims_shown | 263 |
| unsupported_claim_rate | 0.0000 |
| abstention_precision | 1.0000 |
| abstention_recall | 1.0000 |
| abstention_f1 | 1.0000 |
| partial_answer_correctness | 1.0000 |
| refusal_rate | 1.0000 |
| advice_refusal_rate | 1.0000 |
| diagnosis_refusal_rate | 1.0000 |
| injection_refusal_rate | 1.0000 |
| unsupported_route_rate | 1.0000 |
| over_refusal_rate | 0.0000 |
| leak_questions | 0 |
| rejected_exposed | 0 |
| needs_review_exposed | 0 |
| mismatch_exposed | 0 |
| identity_label_accuracy | 1.0000 |
| corrected_value_accuracy | 1.0000 |
| comparison_delta_accuracy | 1.0000 |
| latency_p50_ms | 81.0000 |
| latency_p95_ms | 139.0000 |
| invalid_citation_acceptance | 0.0000 |
| unrecognised_abstention_rate | 1.0000 |

## By question kind

| Kind | Questions | Correct | Routing ok |
|---|---|---|---|
| advice | 6 | 6 | 6 |
| all_in_year | 6 | 6 | 6 |
| all_on_date | 6 | 6 | 6 |
| combined_value | 10 | 10 | 10 |
| compare_previous | 6 | 6 | 6 |
| conflict | 6 | 6 | 6 |
| corrected_value | 6 | 6 | 6 |
| diagnosis | 6 | 6 | 6 |
| document_lab | 10 | 10 | 10 |
| document_leak_probe | 6 | 6 | 6 |
| document_unknown | 6 | 6 | 6 |
| general_knowledge | 6 | 6 | 6 |
| history | 6 | 6 | 6 |
| identity_unknown | 6 | 6 | 6 |
| injection | 6 | 6 | 6 |
| latest | 12 | 12 | 12 |
| leak_probe | 6 | 6 | 6 |
| list_analytes | 6 | 6 | 6 |
| mismatch_probe | 6 | 6 | 6 |
| needs_review_probe | 3 | 3 | 3 |
| partial | 6 | 6 | 6 |
| partial_unknown | 6 | 6 | 6 |
| rejected_probe | 6 | 6 | 6 |
| unknown_test | 12 | 12 | 12 |
| year_range | 6 | 6 | 6 |

## Attack suite (every attacked answer must be withheld)

| Attack | Attempted | Accepted | Issue codes |
|---|---|---|---|
| fabricated_id | 40 | 0 | unknown_source=40 |
| invented_value | 40 | 0 | unsupported_number=40 |
| invented_date | 40 | 0 | unsupported_date=40 |
| uncited_claim | 40 | 0 | uncited_claim=40 |
| advice | 40 | 0 | prohibited_content=40 |
| misquote | 40 | 0 | misquoted_text=40 |
| wrong_page | 40 | 0 | page_mismatch=40, unsupported_number=40 |
| foreign_evidence | 40 | 0 | source_not_found=40 |

Tokens: extractive generator: no model, no tokens. Cost: $0.00. Run time: 11.3 s.

## Failures (0)

| Question | Kind | Expected | Got | Failed checks |
|---|---|---|---|---|
