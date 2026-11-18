# R1 evidence retrieval: dev ablation

- Dataset: `lab_v0@5dcdc7dfa3ff`, split `dev`
- Chunker version: `1`
- Embedding model: `BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`
- Reranker: `none`
- Classifier rules: `2`; identity rules: `2`

| Mode | abstain_f1 | abstain_precision | abstain_recall | advice_refusal_rate | hit@1 | hit@10 | hit@3 | hit@5 | injection_refusal_rate | latency_p50_ms | latency_p95_ms | leak_probe_abstain_rate | leak_rate | mrr | ndcg@10 | questions_with_foreign_pages | recall@1 | recall@10 | recall@3 | recall@5 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dense | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.3205 | 1.0000 | 0.6731 | 0.8910 | 1.0000 | 59.0000 | 70.0000 | 0.0000 | 0.0000 | 0.5332 | 0.6301 | 0 | 0.2575 | 1.0000 | 0.6517 | 0.8843 |
| lexical | 0.7200 | 1.0000 | 0.5625 | 1.0000 | 0.8077 | 1.0000 | 0.9679 | 1.0000 | 1.0000 | 17.0000 | 26.0000 | 0.8125 | 0.0000 | 0.8884 | 0.8903 | 0 | 0.7447 | 0.9895 | 0.9450 | 0.9895 |
| hybrid | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.5449 | 1.0000 | 0.8718 | 1.0000 | 1.0000 | 65.0000 | 82.0000 | 0.0000 | 0.0000 | 0.7241 | 0.7747 | 0 | 0.4818 | 1.0000 | 0.8504 | 0.9933 |
| hybrid_rerank | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.7692 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 970.0000 | 1090.0000 | 0.0000 | 0.0000 | 0.8771 | 0.8939 | 0 | 0.7062 | 0.9971 | 0.9786 | 0.9933 |
| hybrid_rerank | 0.9143 | 0.8421 | 1.0000 | 1.0000 | 0.7500 | 0.9808 | 0.9808 | 0.9808 | 1.0000 | 975.0000 | 1133.0000 | 1.0000 | 0.0000 | 0.8579 | 0.8763 | 0 | 0.6918 | 0.9779 | 0.9610 | 0.9740 |

Notes (dense):
- no abstention threshold

Notes (lexical):
- no abstention threshold

Notes (hybrid):
- no abstention threshold

Notes (hybrid_rerank):
- no abstention threshold

Notes (hybrid_rerank):
- threshold -3.082892417907715 calibrated on dev (in-sample); exact value in config.abstain_threshold
- calibration questions: answerable kinds ('lab', 'latest', 'value') vs ('unanswerable',); excluded kinds ('advice', 'injection', 'leak')

## Ingestion timing (recorded by `ingest`)

| Measure | Value |
|---|---|
| reports_timed | 35 |
| reports_not_timed | 0 |
| pages_timed | 57 |
| index_ms_per_page | 2015.0877 |
| index_ms_per_page_p50 | 2132.0000 |
| index_ms_per_page_p95 | 2631.0000 |
