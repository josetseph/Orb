# Sweep round4-musique

Dataset musique, dev questions 0 to 20, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "MAX_LOOP_ITERATIONS": 5}`
Varied: `{"EXTRACTION_ATTEMPTS": [2], "EXTRACTION_PROMPT_SUFFIX": ["Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one."], "EXTRACTION_JSON_CONSTRAINED": [true], "EXTRACTION_MODE": ["task_split"], "EXTRACTION_CHUNK_TOKENS": [1000], "MAX_LOOP_ITERATIONS": [8], "chat_model": ["gemma4-12b-q4"], "CHAT_MAX_CONTEXT_DOCS": [12]}` (one-at-a-time)

## Rung: first 20 dev questions (10 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.096 | 5% | 0.396 | 0.446 | 142 | 3172 (161/239 notes) | 5 {'query analysis': 5} |  |
| EXTRACTION_ATTEMPTS=2 | 0.198 | 15% | 0.396 | 0.463 | 193 | 16896 (160/239 notes) | 6 {'query analysis': 6} | +2/-0 EM, answer_f1 CI +0.000 to +0.252, within noise |
| EXTRACTION_PROMPT_SUFFIX=Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one. | 0.150 | 10% | 0.500 | 0.596 | 208 | 56906 (173/239 notes) | 5 {'query analysis': 5} | +1/-0 EM, answer_f1 CI -0.005 to +0.159, within noise |
| EXTRACTION_JSON_CONSTRAINED=True | 0.251 | 20% | 0.442 | 0.496 | 167 | 97893 (142/239 notes) | 7 {'query analysis': 7} | +3/-0 EM, answer_f1 CI -0.027 to +0.352, within noise |
| EXTRACTION_MODE=task_split | 0.097 | 5% | 0.171 | 0.254 | 106 | 64252 (82/239 notes) | 8 {'query analysis': 7, 'research step': 1} | +1/-1 EM, answer_f1 CI -0.149 to +0.151, within noise |
| EXTRACTION_CHUNK_TOKENS=1000 | 0.148 | 10% | 0.287 | 0.371 | 146 | 16164 (135/239 notes) | 2 {'query analysis': 2} | +1/-0 EM, answer_f1 CI -0.001 to +0.154, within noise |
| MAX_LOOP_ITERATIONS=8 | 0.154 | 10% | 0.421 | 0.446 | 156 | 3172 (161/239 notes) | 9 {'query analysis': 8, 'research step': 1} | +1/-0 EM, answer_f1 CI -0.000 to +0.163, within noise |
| chat_model=gemma4-12b-q4 | 0.171 | 15% | 0.379 | 0.446 | 183 | 3172 (161/239 notes) | 0  | +2/-0 EM, answer_f1 CI +0.011 to +0.185, within noise |
| CHAT_MAX_CONTEXT_DOCS=12 | 0.096 | 5% | 0.396 | 0.446 | 19 | 3172 (161/239 notes) | 5 {'query analysis': 5} | +0/-0 EM, answer_f1 CI -0.002 to +0.000, within noise |
| chat_model=qwen35-9b-q4,ENABLE_THINKING=False | 0.237 | 20% | 0.354 | 0.446 | 195 | 3172 (161/239 notes) | 1 {'research step': 1} | +3/-0 EM, answer_f1 CI +0.009 to +0.298, within noise |
