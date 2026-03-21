# HighThinkingQA Migration Checklist

## Goal

Move the current root `highThinking` service into a future `highThinkingQA/` directory while keeping it as an independent backend service.

This is a move-and-isolate task, not a process merge.

## Minimum Move Unit

Must move together:

- `server_fastapi/`
- `server/`
- `agent_core/`
- `ingest/`
- `retriever/`
- `prompts/`
- `tests/`
- `scripts/`
- `config.py`
- `env_loader.py`
- `requirements.txt`
- config env templates

Also move with persistence features if current behavior must be preserved:

- `server/database/`
- `server/repositories/`
- `server/storage/`
- `server/services/conversation/`
- `server/tools/run_chat_json_outbox_worker.py`
- `server/database/migrations/`

## Do Not Move First

- do not move only `server_fastapi/`
- do not move only `agent_core/`, `ingest/`, or `retriever/`
- do not treat root `frontend-vue/` as the long-term frontend for `highThinkingQA`
- do not merge `gateway/` or `public-service/` into `highThinkingQA`
- do not blindly carry root runtime directories:
  - `uploads/`
  - `papers/`
  - `vectordb/`
  - `cache/`
  - `data/`
  - `.runtime/`

## Required Follow-Up When Moving

### Import and Path Fixes

Rework root-bound assumptions in:

- `config.py`
- `env_loader.py`
- `server_fastapi/routers/upload.py`
- `server/services/conversation/chat_json_store.py`
- `server/storage/storage_factory.py`
- `server/storage/file_delivery_service.py`
- `server/tools/run_chat_json_outbox_worker.py`

### Script Fixes

Rework service-root assumptions in:

- `scripts/start_fastapi_gunicorn.sh`
- `scripts/status_fastapi_gunicorn.sh`
- `scripts/stop_fastapi_gunicorn.sh`

### Env Fixes

At minimum, split and explicitly rebind:

- `APP_PORT`
- `CORS_ORIGINS`
- `UPLOAD_DIR`
- `PAPERS_DIR`
- `CHROMA_PERSIST_DIR`
- `CHAT_JSON_BASE_DIR`
- MySQL settings
- MinIO settings
- `JWT_*`

### Test Fixes

Split tests by ownership:

- keep in `highThinkingQA`:
  - ask/ask_stream
  - agent/retrieval/prompt
  - mode profiles
- migrate to `public-service` or gateway-side validation:
  - auth
  - conversation
  - upload
  - documents
  - quota
  - admin
  - file delivery

## Recommended Order

1. Keep `gateway` and `public-service` independent.
2. Freeze env boundaries and runtime roots.
3. Create `resource/` contract and target state/runtime roots.
4. Move the whole `highThinking` execution closure into `highThinkingQA/`.
5. Only after the move, start reducing public capability ownership from `highThinkingQA`.

## Main Risks

- critical: current code assumes repository root as service root
- high: current app still mixes public APIs and QA APIs
- high: runtime directories are still root-shared
- high: ask execution closure is larger than it looks
- medium: outbox worker is a separate deployment concern and can be forgotten
