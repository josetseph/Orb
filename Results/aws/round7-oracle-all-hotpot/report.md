# Sweep round7-oracle-all-hotpot

Dataset hotpotqa, dev questions 20 to 70, evaluator oracle-all, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "chat_model": "gemma4-e4b-q4", "LLAMA_N_CTX": 32768}`
Varied: `{}` (one-at-a-time)

## Rung: first 50 dev questions (1 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.667 | 52% | 0.000 | n/a | 8 |  | 5 {'research step': 5} |  |
