# Sweep round8-hotpot100

Dataset hotpotqa, dev questions 0 to 100, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": true, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one.", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "INDEX_RAW_PASSAGES": true, "PASSAGE_KEYWORD_SEARCH": true, "PASSAGE_TITLE_HEADER": true, "FIRST_SEARCH_IS_QUESTION": true, "CONTEXT_ORDER": "edges"}`
Varied: `{}` (one-at-a-time)

## Rung: first 100 dev questions (3 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.716 | 57% | 0.985 | 0.995 | 135 | 10125 (977/990 notes) | 9 {'query analysis': 6, 'research step': 3} |  |
| INDEX_RAW_PASSAGES=False,PASSAGE_KEYWORD_SEARCH=False,PASSAGE_TITLE_HEADER=False,FIRST_SEARCH_IS_QUESTION=False,CONTEXT_ORDER=rank | 0.696 | 56% | 0.965 | 0.995 | 113 | 9424 (978/990 notes) | 16 {'query analysis': 14, 'research step': 2} | +8/-9 EM, answer_f1 CI -0.083 to +0.045, within noise |
| chat_model=qwen35-9b-q4,ENABLE_THINKING=False | 0.748 | 60% | 0.985 | 0.995 | 121 | 10125 (977/990 notes) | 6 {'research step': 6} | +10/-7 EM, answer_f1 CI -0.048 to +0.114, within noise |
