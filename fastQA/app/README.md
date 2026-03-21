# FastQA App Skeleton

Phase 1 target layout for the extracted `fastQA` service.

Planned minimal ownership:
- `core/`: env, config, logging, prompts, SSE
- `integrations/`: llm, embedding, vector DB, redis, neo4j
- `modules/qa_kb/`
- `modules/retrieval/`
- `modules/generation_pipeline/`
- `modules/qa_cache/`
- `modules/storage/`

Do not add public-service modules here.
