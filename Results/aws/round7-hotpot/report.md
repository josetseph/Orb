# Sweep round7-hotpot

Dataset hotpotqa, dev questions 20 to 70, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": true, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one.", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "INDEX_RAW_PASSAGES": true}`
Varied: `{"INDEX_RAW_PASSAGES": [false], "PASSAGE_KEYWORD_SEARCH": [true], "PPR_EXPANSION": [true], "CONTEXT_ORDER": ["edges"], "FIRST_SEARCH_IS_QUESTION": [true]}` (one-at-a-time)

## Rung: first 50 dev questions (6 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | not run | | | | | | | |
| INDEX_RAW_PASSAGES=False | 0.524 | 42% | 0.950 | 1.000 | 274 | 5749 (486/492 notes) | 14 {'query analysis': 9, 'research step': 5} |  |
| PASSAGE_KEYWORD_SEARCH=True | 0.658 | 54% | 0.940 | 1.000 | 381 | 5856 (485/492 notes) | 9 {'query analysis': 7, 'research step': 2} |  |
| PPR_EXPANSION=True | not run | | | | | | | |
| CONTEXT_ORDER=edges | not run | | | | | | | |
| FIRST_SEARCH_IS_QUESTION=True | not run | | | | | | | |
