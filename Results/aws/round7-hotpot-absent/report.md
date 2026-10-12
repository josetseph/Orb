# Sweep round7-hotpot-absent

Dataset hotpotqa, dev questions 20 to 70, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": true, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one.", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "INDEX_RAW_PASSAGES": true}`
Varied: `{"INDEX_RAW_PASSAGES": [false], "FIRST_SEARCH_IS_QUESTION": [true]}` (one-at-a-time)

## Rung: first 50 dev questions (3 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.570 | 40% | 0.980 | 1.000 | 305 | 5774 (484/492 notes) | 7 {'query analysis': 6, 'research step': 1} |  |
| INDEX_RAW_PASSAGES=False | 0.591 | 46% | 0.930 | 1.000 | 242 | 5749 (486/492 notes) | 13 {'query analysis': 9, 'research step': 4} | +7/-4 EM, answer_f1 CI -0.104 to +0.142, within noise |
| FIRST_SEARCH_IS_QUESTION=True | 0.678 | 56% | 0.990 | 1.000 | 302 | 5774 (484/492 notes) | 10 {'query analysis': 4, 'research step': 6} | +12/-4 EM, answer_f1 CI -0.009 to +0.225, within noise |

## Absent: questions [[0, 20], [70, 100]], whose notes are not in the index (lower answered is better)

| variant | answered | s / question |
|---|---|---|
| base | 0% | 61 |
| INDEX_RAW_PASSAGES=False | 0% | 58 |
| FIRST_SEARCH_IS_QUESTION=True | 0% | 80 |
