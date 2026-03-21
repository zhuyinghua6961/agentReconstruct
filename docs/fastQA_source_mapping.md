# FastQA Source Mapping

## Source Baseline

- source root: `/home/cqy/worktrees/fastapi-version/backend/app`
- target root: `/home/cqy/worktrees/highThinking/fastQA`

## Phase 1 Scope

Phase 1 extracts only the `kb_qa` execution closure.

## Keep In Scope

### Core
- `core/env_loader.py`
- `core/config.py`
- `core/logging.py`
- `core/prompts.py`
- `core/sse.py`

### Integrations
- `integrations/llm/`
- `integrations/embedding/`
- `integrations/vector_db/`
- `integrations/redis/`
- `integrations/neo4j/`

### Modules
- `modules/retrieval/`
- `modules/storage/paper_storage.py`
- `modules/qa_cache/`
- `modules/generation_pipeline/`
- `modules/qa_kb/`

### Rebuild Instead Of Copying
- thin FastAPI app exposing only `health`, `ask`, `ask_stream`
- thin request adapter from gateway payload into `QaKbRequest`
- thin runtime/bootstrap for logging and service wiring

## Exclude From Phase 1

Do not extract into `fastQA` Phase 1:
- `modules/ask_gateway/`
- `modules/ask_dispatch/`
- `modules/auth/`
- `modules/conversation/`
- `modules/uploads/`
- `modules/documents/`
- `modules/admin_users/`
- `modules/quota/`
- `modules/system/`
- `modules/file_context/`
- `modules/qa_pdf/`
- `modules/qa_tabular/`
- backend-served frontend

## Copy Order

1. `core`: env/config/logging/prompts/sse
2. `integrations`: llm / embedding / vector_db / redis / neo4j
3. `modules/retrieval`
4. `modules/storage/paper_storage.py`
5. `modules/qa_cache`
6. `modules/generation_pipeline`
7. `modules/qa_kb`
8. rebuild thin API and runtime layer
9. add focused tests

## Known Hard Dependencies

Check explicitly during extraction:
- `QA_QUERY_PIPELINE_MODE=legacy` path expansion
- vector DB path and local model path assumptions
- prompt loading root assumptions
- redis availability in cache / runtime helpers
- SSE normalization expected by gateway
