# Phase 5 copilot: copilot_v1 dev

- Dataset: `copilot_v1`, split `dev`, SHA-256 `9395524848064fb107ecaff409cb541d59d2bf7734aeab66e010f9572f25e0cf` (425 questions)
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
| claims_shown | 678 |
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
| latency_p50_ms | 89.0000 |
| latency_p95_ms | 166.0000 |
| invalid_citation_acceptance | 0.0000 |
| unrecognised_abstention_rate | 1.0000 |

## By question kind

| Kind | Questions | Correct | Routing ok |
|---|---|---|---|
| advice | 16 | 16 | 16 |
| all_in_year | 16 | 16 | 16 |
| all_on_date | 16 | 16 | 16 |
| combined_value | 21 | 21 | 21 |
| compare_previous | 16 | 16 | 16 |
| conflict | 16 | 16 | 16 |
| corrected_value | 16 | 16 | 16 |
| diagnosis | 16 | 16 | 16 |
| document_lab | 21 | 21 | 21 |
| document_leak_probe | 12 | 12 | 12 |
| document_unknown | 11 | 11 | 11 |
| general_knowledge | 16 | 16 | 16 |
| history | 16 | 16 | 16 |
| identity_unknown | 16 | 16 | 16 |
| injection | 16 | 16 | 16 |
| latest | 32 | 32 | 32 |
| leak_probe | 16 | 16 | 16 |
| list_analytes | 16 | 16 | 16 |
| mismatch_probe | 16 | 16 | 16 |
| needs_review_probe | 8 | 8 | 8 |
| partial | 16 | 16 | 16 |
| partial_unknown | 16 | 16 | 16 |
| rejected_probe | 16 | 16 | 16 |
| unknown_test | 32 | 32 | 32 |
| year_range | 16 | 16 | 16 |

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

Tokens: extractive generator: no model, no tokens. Cost: $0.00. Run time: 31.8 s.

## Failures (0)

| Question | Kind | Expected | Got | Failed checks |
|---|---|---|---|---|
