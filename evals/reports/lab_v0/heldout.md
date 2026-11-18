# R1 evidence retrieval: held-out

- Dataset: `lab_v0@5dcdc7dfa3ff`, split `heldout`
- Chunker version: `1`
- Embedding model: `none`
- Reranker: `none`
- Classifier rules: `2`; identity rules: `2`

| Mode | abstain_f1 | abstain_precision | abstain_recall | advice_refusal_rate | hit@1 | hit@10 | hit@3 | hit@5 | injection_refusal_rate | latency_p50_ms | latency_p95_ms | leak_probe_abstain_rate | leak_rate | mrr | ndcg@10 | questions_with_foreign_pages | recall@1 | recall@10 | recall@3 | recall@5 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| lexical | 0.5455 | 1.0000 | 0.3750 | 1.0000 | 0.8088 | 1.0000 | 0.9853 | 1.0000 | 1.0000 | 13.0000 | 21.0000 | 0.7500 | 0.0000 | 0.8909 | 0.8756 | 0 | 0.7328 | 0.9706 | 0.9485 | 0.9706 |

Notes (lexical):
- single held-out run of the frozen dev selection
