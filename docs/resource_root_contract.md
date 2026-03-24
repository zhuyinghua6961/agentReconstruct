# Resource Root Contract

## Goal

Define a single-repository resource contract for independent services:

- `gateway`
- `public-service`
- future `highThinkingQA`
- future `fastQA`
- future `patent`

This contract is about filesystem and config roots, not about HTTP routing.

## Root Variables

Recommended shared variables:

- `RESOURCE_ROOT`
- `SERVICE_CONFIG_ROOT`
- `SERVICE_STATE_ROOT`
- `SERVICE_RUNTIME_ROOT`
- `SERVICE_ASSET_ROOT`

Rules:

- `RESOURCE_ROOT`: shared repository resource root
- `SERVICE_CONFIG_ROOT`: service-local config templates and env files
- `SERVICE_STATE_ROOT`: durable mutable state for one service
- `SERVICE_RUNTIME_ROOT`: pid/log/temp/runtime files for one service
- `SERVICE_ASSET_ROOT`: read-only assets used by one service

## Recommended Dev Values

### Gateway

- `RESOURCE_ROOT=/home/cqy/worktrees/highThinking/resource`
- `SERVICE_CONFIG_ROOT=/home/cqy/worktrees/highThinking/resource/config/services/gateway`
- `SERVICE_STATE_ROOT=/home/cqy/worktrees/highThinking/resource/state/dev/gateway`
- `SERVICE_RUNTIME_ROOT=/home/cqy/worktrees/highThinking/resource/runtime/dev/gateway`
- `SERVICE_ASSET_ROOT=/home/cqy/worktrees/highThinking/resource/assets`

Note:

- `gateway` should stay runtime-only in practice
- `SERVICE_STATE_ROOT` exists only for contract completeness

### Public-Service

- `RESOURCE_ROOT=/home/cqy/worktrees/highThinking/resource`
- `SERVICE_CONFIG_ROOT=/home/cqy/worktrees/highThinking/resource/config/services/public-service`
- `SERVICE_STATE_ROOT=/home/cqy/worktrees/highThinking/resource/state/dev/public-service`
- `SERVICE_RUNTIME_ROOT=/home/cqy/worktrees/highThinking/resource/runtime/dev/public-service`
- `SERVICE_ASSET_ROOT=/home/cqy/worktrees/highThinking/resource/assets`

### HighThinkingQA

- `RESOURCE_ROOT=/home/cqy/worktrees/highThinking/resource`
- `SERVICE_CONFIG_ROOT=/home/cqy/worktrees/highThinking/resource/config/services/highThinkingQA`
- `SERVICE_STATE_ROOT=/home/cqy/worktrees/highThinking/resource/state/dev/highThinkingQA`
- `SERVICE_RUNTIME_ROOT=/home/cqy/worktrees/highThinking/resource/runtime/dev/highThinkingQA`
- `SERVICE_ASSET_ROOT=/home/cqy/worktrees/highThinking/resource/assets`

### FastQA

- `RESOURCE_ROOT=/home/cqy/worktrees/highThinking/resource`
- `SERVICE_CONFIG_ROOT=/home/cqy/worktrees/highThinking/resource/config/services/fastQA`
- `SERVICE_STATE_ROOT=/home/cqy/worktrees/highThinking/resource/state/dev/fastQA`
- `SERVICE_RUNTIME_ROOT=/home/cqy/worktrees/highThinking/resource/runtime/dev/fastQA`
- `SERVICE_ASSET_ROOT=/home/cqy/worktrees/highThinking/resource/assets`

### Patent

- `RESOURCE_ROOT=/home/cqy/worktrees/highThinking/resource`
- `SERVICE_CONFIG_ROOT=/home/cqy/worktrees/highThinking/resource/config/services/patent`
- `SERVICE_STATE_ROOT=/home/cqy/worktrees/highThinking/resource/state/dev/patent`
- `SERVICE_RUNTIME_ROOT=/home/cqy/worktrees/highThinking/resource/runtime/dev/patent`
- `SERVICE_ASSET_ROOT=/home/cqy/worktrees/highThinking/resource/assets`

## Legacy Variable Mapping

### Current Root HighThinking

- `PAPERS_DIR` -> `${SERVICE_STATE_ROOT}/papers`
- `UPLOAD_DIR` -> `${SERVICE_STATE_ROOT}/uploads`
- `CHAT_JSON_BASE_DIR` -> `${SERVICE_STATE_ROOT}/data/conversations`
- `CHROMA_PERSIST_DIR` -> `${SERVICE_STATE_ROOT}/vectordb`
- `PROMPTS_DIR` -> `${SERVICE_ASSET_ROOT}/prompts`
- implicit `cache/parsed_markdown` -> `${SERVICE_STATE_ROOT}/cache/parsed_markdown`

### Public-Service

- `PUBLIC_SERVICE_DATA_ROOT` -> compatibility alias for `SERVICE_STATE_ROOT`
- `UPLOAD_DIR` -> `${SERVICE_STATE_ROOT}/uploads`
- `PAPERS_DIR` -> `${SERVICE_STATE_ROOT}/papers`
- `CHAT_JSON_BASE_DIR` -> `${SERVICE_STATE_ROOT}/data/conversations`
- `VECTOR_DB_PATH` -> `${SERVICE_STATE_ROOT}/vector_database`
- `TRANSLATION_CACHE_DIR` -> `${SERVICE_STATE_ROOT}/translation_cache`
- `LOCAL_STORAGE_ROOT` -> `${SERVICE_STATE_ROOT}/storage`
- `PUBLIC_SERVICE_LOGS_DIR` -> `${SERVICE_RUNTIME_ROOT}/logs`
- `PUBLIC_SERVICE_ENV_FILE` -> file under `SERVICE_CONFIG_ROOT`
- `PUBLIC_SERVICE_ENV_FILES` -> files under `SERVICE_CONFIG_ROOT`

### Gateway

- local `.runtime` -> `${SERVICE_RUNTIME_ROOT}`
- backend endpoint env stays endpoint-based, not filesystem-root based

### FastQA Baseline

- `PAPERS_DIR` -> `${SERVICE_STATE_ROOT}/papers`
- `CHAT_JSON_BASE_DIR` -> `${SERVICE_STATE_ROOT}/data/conversations`
- `TRANSLATION_CACHE_DIR` -> `${SERVICE_STATE_ROOT}/translation_cache`
- `VECTOR_DB_PATH` -> `${SERVICE_STATE_ROOT}/vector_database`
- `VECTOR_DB_SUMMARY_PATH` -> `${SERVICE_STATE_ROOT}/vector_database_normalized`
- `VECTOR_DB_PDF_PATH` -> `${SERVICE_STATE_ROOT}/vector_database_pdf`
- `VECTOR_DB_COMMUNITY_PATH` -> `${SERVICE_STATE_ROOT}/community_vector_database`
- `VECTOR_DB_MD_PATH` -> `${SERVICE_STATE_ROOT}/vector_database_md`
- `TOPIC_INDEX_PATH` -> `${SERVICE_STATE_ROOT}/vector_db_topic_index.json`
- `JSON_DIR` -> `${SERVICE_STATE_ROOT}/json`
- `JSON_NORMALIZED_DIR` -> `${SERVICE_STATE_ROOT}/json_normalized`
- `JSON_SUMMARY_DIR` -> `${SERVICE_STATE_ROOT}/json_summary`
- `PDF_CHUNKS_DIR` -> `${SERVICE_STATE_ROOT}/pdf_chunks`
- `MATERIAL_AGENT_PROMPTS_DIR` -> `${SERVICE_ASSET_ROOT}/prompts`
- `EMBEDDING_MODEL_PATH` -> `${SERVICE_ASSET_ROOT}/models/...` or a shared read-only model path

## Compatibility Policy

Keep legacy variables during migration until code stops reading them directly.

Keep as compatibility aliases:

- root `highThinking`: `PAPERS_DIR`, `UPLOAD_DIR`, `CHAT_JSON_BASE_DIR`, `CHROMA_PERSIST_DIR`, `PROMPTS_DIR`
- `public-service`: `PUBLIC_SERVICE_DATA_ROOT`, `UPLOAD_DIR`, `PAPERS_DIR`, `CHAT_JSON_BASE_DIR`, `VECTOR_DB_PATH`, `TRANSLATION_CACHE_DIR`, `LOCAL_STORAGE_ROOT`, `PUBLIC_SERVICE_LOGS_DIR`, `PUBLIC_SERVICE_ENV_FILE`, `PUBLIC_SERVICE_ENV_FILES`
- `fastQA` baseline: existing vector/path/prompt variables listed above

Do not force non-root semantic variables into this contract:

- object-storage prefix variables
- translation object names
- vector collection names
- Redis key prefixes

Those are naming and namespace settings, not filesystem roots.
