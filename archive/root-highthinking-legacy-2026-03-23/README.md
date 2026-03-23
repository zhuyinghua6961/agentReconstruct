# Root Legacy highThinking Backend Archive

Archived on 2026-03-23 after confirming the active runtime stack uses:
- `frontend-vue/`
- `gateway/`
- `public-service/`
- `fastQA/`
- `highThinkingQA/`

This archive preserves the former root-level highThinking backend baseline:
- `agent_core/`
- `ingest/`
- `retriever/`
- `server/`
- `server_fastapi/`
- root `config.py` / `env_loader.py`
- legacy root tests
- legacy root gunicorn helper scripts

Reason for archival:
- current `scripts/start_all.sh` no longer launches the root backend
- current `scripts/_service_common.sh` starts `highThinkingQA/` instead
- root backend remained only as rollback/reference baseline and legacy tests

Resource migration on 2026-03-23:
- active `papers/` moved to `resource/highThinkingQA/papers/`
- active `vectordb/` moved to `resource/highThinkingQA/vectordb/`
- unused root `prompts/`, `uploads/`, `data/`, `cache/` moved into this archive
