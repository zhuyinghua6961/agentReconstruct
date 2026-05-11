# Resource Config Simplification Spec

**Date:** 2026-05-11

## Summary

This spec defines the target contract for simplifying `resource/config/**` after the current code-to-config mapping work.

The goal is to keep only deployment-sensitive settings in configuration files and move fixed runtime decisions into code defaults. The final configuration surface should preserve connection information, secrets, storage/database/cache endpoints, graph database endpoints, model endpoints, request capacity limits, and business scale parameters. Feature switches that are confirmed mandatory should no longer be configurable; they should be hardcoded enabled. Warmup/preheat behavior should be hardcoded disabled for local deployment.

This document is the spec for the future cleanup. It does not itself change code or config files.

Primary reference:

- [docs/config/2026-05-10-resource-config-code-map.md](/home/cqy/worktrees/highThinking/docs/config/2026-05-10-resource-config-code-map.md)

---

## Scope

This spec covers the target shape of:

1. `resource/config/shared/*.env`
2. `resource/config/shared/*.env.example`
3. `resource/config/services/*/config.shared.env`
4. `resource/config/services/*/config.secret.env.example`
5. `resource/config/services/*/config.env.example`
6. the code behavior required before removing old env keys

This spec applies to these services:

1. `gateway`
2. `public-service`
3. `fastQA`
4. `highThinkingQA`
5. `patent`
6. top-level `scripts`

## Non-Goals

1. Do not expose real secret values in docs or committed config.
2. Do not remove deployment-sensitive paths, ports, URLs, credentials, namespaces, or capacity limits.
3. Do not re-enable OCR.
4. Do not keep warmup/preheat configuration for future local deployments.
5. Do not use this spec as permission to delete old env keys before the corresponding code has been migrated to the new target names.
6. Do not collapse all business scale knobs into code constants. This cleanup is focused on deployment config and confirmed mandatory feature switches.

## Hard Boundaries

1. Secrets stay in `config.secret.env`, local `.env`, or external environment injection. Only examples may be committed.
2. A config key may be removed only after all production code and startup scripts stop reading it or provide a compatible fallback.
3. `*.env.example` files are templates. Template values must not be used as proof that a runtime value is safe to delete.
4. Variable families that control connection, storage, auth, model endpoint, or capacity are retained even if current configured values match code defaults.
5. Boolean switches confirmed as non-optional are removed from config only after the code default becomes the fixed intended value.
6. Warmup/preheat switches and parameters are removed only after code behavior is fixed to "off" without depending on env.
7. `RERANK_*` unification must happen in code before old service-level rerank endpoint keys are deleted.
8. `GATEWAY_ADMISSION_WORKER_ENABLED` is a startup gate, not just an app setting. It must be hardcoded enabled in startup behavior before the env key is removed.

---

## Target Configuration Surface

The final resource config should keep the following categories.

### 1. Service Endpoints And Backend Routing

Keep host, port, and backend routing config because these are deployment topology.

Target keys:

```env
GATEWAY_HOST=
GATEWAY_PORT=
PUBLIC_SERVICE_HOST=
PUBLIC_SERVICE_PORT=
FASTQA_HOST=
FASTQA_PORT=
FASTQA_FASTAPI_PORT=
HIGHTHINKINGQA_HOST=
HIGHTHINKINGQA_PORT=
PATENT_HOST=
PATENT_PORT=

PUBLIC_BACKEND_BASE_URL=
FAST_BACKEND_BASE_URL=
THINKING_BACKEND_BASE_URL=
PATENT_BACKEND_BASE_URL=
```

### 2. Resource Roots And Local Data Paths

Keep resource roots and service-local paths because they vary by deployment and packaging.

Target key families:

```env
RESOURCE_ROOT=
GATEWAY_SERVICE_CONFIG_ROOT=
GATEWAY_SERVICE_STATE_ROOT=
GATEWAY_SERVICE_RUNTIME_ROOT=
GATEWAY_SERVICE_ASSET_ROOT=
PUBLIC_SERVICE_SERVICE_CONFIG_ROOT=
PUBLIC_SERVICE_SERVICE_STATE_ROOT=
PUBLIC_SERVICE_SERVICE_RUNTIME_ROOT=
PUBLIC_SERVICE_SERVICE_ASSET_ROOT=
FASTQA_SERVICE_CONFIG_ROOT=
FASTQA_SERVICE_STATE_ROOT=
FASTQA_SERVICE_RUNTIME_ROOT=
FASTQA_SERVICE_ASSET_ROOT=
HIGHTHINKINGQA_SERVICE_CONFIG_ROOT=
HIGHTHINKINGQA_SERVICE_STATE_ROOT=
HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT=
HIGHTHINKINGQA_SERVICE_ASSET_ROOT=
```

Keep service-local runtime/data paths such as:

```env
UPLOAD_DIR=
PAPERS_DIR=
CHAT_JSON_BASE_DIR=
CHAT_JSON_STORAGE_PREFIX=
VECTOR_DB_PATH=
VECTOR_DB_SUMMARY_PATH=
VECTOR_DB_PDF_PATH=
VECTOR_DB_COMMUNITY_PATH=
VECTOR_DB_MD_PATH=
VECTOR_DB_MD_COLLECTION=
TOPIC_INDEX_PATH=
JSON_DIR=
JSON_NORMALIZED_DIR=
PDF_CHUNKS_DIR=
JSON_SUMMARY_DIR=
TRANSLATION_CACHE_DIR=
TRANSLATION_CACHE_OBJECT_NAME=
MATERIAL_AGENT_PROMPTS_DIR=
PROMPTS_DIR=
CHROMA_PERSIST_DIR=
CHROMA_COLLECTION_NAME=
LOCAL_STORAGE_ROOT=
PUBLIC_SERVICE_LOGS_DIR=
```

### 3. Auth And Internal Service Tokens

Keep auth and internal token settings. These are security policy or secrets.

Target keys:

```env
JWT_SECRET=
JWT_EXPIRE_SECONDS=
PASSWORD_EXPIRE_DAYS=
LOGIN_FAILURE_LOCK_THRESHOLD=
LOGIN_FAILURE_LOCK_MINUTES=
PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN=
GATEWAY_ADMISSION_CONTROL_TOKEN=
```

### 4. MySQL

Keep all MySQL connection, credential, pool, timeout, and retry settings.

Target keys:

```env
MYSQL_HOST=
MYSQL_PORT=
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_DATABASE=
MYSQL_CONNECT_TIMEOUT_SECONDS=
MYSQL_READ_TIMEOUT_SECONDS=
MYSQL_WRITE_TIMEOUT_SECONDS=
MYSQL_CONNECT_RETRIES=
MYSQL_CONNECT_RETRY_DELAY_SECONDS=
MYSQL_QUERY_RETRIES=
MYSQL_QUERY_RETRY_DELAY_SECONDS=
```

### 5. MinIO

Keep MinIO endpoint, credentials, bucket, region, and download URL policy. Remove only the mandatory proxy switch.

Target keys:

```env
MINIO_ENDPOINT=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_BUCKET=
MINIO_SECURE=
MINIO_REGION=
MINIO_DOWNLOAD_EXPIRES=
```

Retire from config:

```env
MINIO_USE_PROXY=
```

Target behavior:

- MinIO proxy downloads are always enabled.

### 6. Redis

Keep Redis connection and namespace config. Remove only Redis enable switches that are confirmed mandatory.

Target keys:

```env
REDIS_URL=
REDIS_HOST=
REDIS_PORT=
REDIS_USERNAME=
REDIS_PASSWORD=
REDIS_DB=
REDIS_KEY_PREFIX=
REDIS_SOCKET_CONNECT_TIMEOUT_SEC=
REDIS_SOCKET_TIMEOUT_SEC=
PATENT_REDIS_KEY_PREFIX=
```

Retire from config:

```env
REDIS_ENABLED=
PATENT_REDIS_ENABLED=
```

Target behavior:

- Redis is always enabled where the service depends on it.
- Per-service Redis key prefixes remain configurable.

### 7. Neo4j

Keep graph database connection and credential settings, including service-prefixed variants.

Target keys:

```env
NEO4J_URL=
NEO4J_USERNAME=
NEO4J_PASSWORD=
NEO4J_DATABASE=
FASTQA_NEO4J_URL=
FASTQA_NEO4J_USERNAME=
FASTQA_NEO4J_PASSWORD=
FASTQA_NEO4J_DATABASE=
PUBLIC_SERVICE_NEO4J_URL=
PUBLIC_SERVICE_NEO4J_USERNAME=
PUBLIC_SERVICE_NEO4J_PASSWORD=
PUBLIC_SERVICE_NEO4J_DATABASE=
PATENT_NEO4J_URL=
PATENT_NEO4J_USERNAME=
PATENT_NEO4J_PASSWORD=
PATENT_NEO4J_DATABASE=
```

### 8. Request Admission And Runtime Capacity

Keep all request-capacity values. Remove only mandatory enable switches.

Target keys:

```env
GATEWAY_ADMISSION_POLL_INTERVAL_SECONDS=
INTERACTIVE_EXECUTION_MAX_CONCURRENT=
INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE=
INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT=
INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT=
INTERACTIVE_EXECUTION_THINKING_MIN_SLOTS=
INTERACTIVE_QUEUE_MAX_SIZE=
INTERACTIVE_QUEUED_TTL_SECONDS=
INTERACTIVE_POST_ADMIT_ATTACH_TTL_SECONDS=
ASK_STREAM_MAX_CONCURRENT=
PATENT_ASK_STREAM_MAX_CONCURRENT=
ASK_EXECUTOR_MAX_WORKERS=
PATENT_ASK_EXECUTOR_MAX_WORKERS=
```

Keep related runtime sizing and worker values unless a later cleanup separately proves they are not deployment-sensitive:

```env
GATEWAY_GUNICORN_WORKERS=
PUBLIC_SERVICE_GUNICORN_WORKERS=
FASTQA_GUNICORN_WORKERS=
GUNICORN_WORKERS=
GUNICORN_THREADS=
GUNICORN_TIMEOUT=
GUNICORN_KEEPALIVE=
GUNICORN_MAX_REQUESTS=
GUNICORN_MAX_REQUESTS_JITTER=
PATENT_GUNICORN_WORKERS=
PATENT_GUNICORN_THREADS=
PATENT_GUNICORN_TIMEOUT=
```

Retire from config:

```env
GATEWAY_ADMISSION_ENABLED=
GATEWAY_ADMISSION_DISPATCHER_ENABLED=
GATEWAY_ADMISSION_WORKER_ENABLED=
```

Target behavior:

- Gateway admission is always enabled.
- Admission dispatcher is always enabled.
- Admission worker startup is enabled by default in scripts without relying on `GATEWAY_ADMISSION_WORKER_ENABLED`.

### 9. Unified LLM

Keep one global LLM config for the three document QA backends.

Target keys:

```env
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
LLM_CONNECT_TIMEOUT_SECONDS=
LLM_READ_TIMEOUT_SECONDS=
LLM_STREAM_READ_TIMEOUT_SECONDS=
LLM_WRITE_TIMEOUT_SECONDS=
LLM_POOL_TIMEOUT_SECONDS=
LLM_KEEPALIVE_EXPIRY_SECONDS=
LLM_MAX_CONNECTIONS=
LLM_MAX_KEEPALIVE_CONNECTIONS=
```

Retire from config:

```env
LLM_PROVIDER=
LLM_ENABLE_THINKING=
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=
DASHSCOPE_MODEL=
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
OPENAI_CONNECT_TIMEOUT_SECONDS=
OPENAI_READ_TIMEOUT_SECONDS=
OPENAI_STREAM_READ_TIMEOUT_SECONDS=
OPENAI_WRITE_TIMEOUT_SECONDS=
OPENAI_POOL_TIMEOUT_SECONDS=
PATENT_OPENAI_API_KEY=
PATENT_OPENAI_BASE_URL=
PATENT_OPENAI_MODEL=
PATENT_OPENAI_TIMEOUT_SECONDS=
DECOMPOSE_MODEL=
DIRECT_ANSWER_MODEL=
SUB_ANSWER_MODEL=
CHECKER_MODEL=
QUERY_EXPANSION_MODEL=
PDF_QA_USE_DEDICATED_LLM=
PDF_QA_MODEL=
DIRECT_ANSWER_ENABLE_THINKING=
DECOMPOSE_ENABLE_THINKING=
```

Target behavior:

- fastQA, highThinkingQA, and patent document QA read `LLM_*`.
- OpenAI-compatible calling behavior is fixed in code; `LLM_PROVIDER` is not a runtime setting.
- Thinking behavior is fixed per target flow, not configurable.
- Stage-specific model names collapse into `LLM_MODEL`.

### 10. Unified Rerank

Keep one rerank endpoint/model config shared by fastQA and patent.

Target keys:

```env
RERANK_API_KEY=
RERANK_PROVIDER=
RERANK_BASE_URL=
RERANK_MODEL=
RERANK_TIMEOUT_SECONDS=
```

Keep retrieval scale values:

```env
QA_RETRIEVAL_RERANK_CANDIDATES=
PATENT_STAGE2_RERANK_CANDIDATES=
PATENT_STAGE2_RERANK_TOP_PATENTS=
```

Retire from config after code migration:

```env
QA_RETRIEVAL_RERANK_API_KEY=
QA_RETRIEVAL_RERANK_PROVIDER=
QA_RETRIEVAL_RERANK_BASE_URL=
QA_RETRIEVAL_RERANK_MODEL=
QA_RETRIEVAL_RERANK_TIMEOUT=
PATENT_STAGE2_RERANK_API_KEY=
PATENT_STAGE2_RERANK_PROVIDER=
PATENT_STAGE2_RERANK_BASE_URL=
PATENT_STAGE2_RERANK_MODEL=
PATENT_STAGE2_RERANK_TIMEOUT_SECONDS=
PATENT_STAGE2_RERANK_ENDPOINT_FAMILY=
QA_RETRIEVAL_RERANK_ENABLED=
PATENT_STAGE2_RERANK_ENABLED=
```

Target behavior:

- Code must first migrate fastQA and patent to read `RERANK_*`.
- Only after that migration can old `QA_RETRIEVAL_RERANK_*` and `PATENT_STAGE2_RERANK_*` endpoint keys be deleted.
- fastQA rerank and patent stage2 rerank are always enabled.

### 11. fastQA + patent Embedding

Keep one embedding config shared by fastQA and patent.

Target keys:

```env
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=
EMBEDDING_MODEL=
EMBEDDING_MODEL_TYPE=
EMBEDDING_API_URL=
EMBEDDING_API_MODEL=
EMBEDDING_API_TIMEOUT_SECONDS=
EMBEDDING_MODEL_PATH=
```

Retire from config:

```env
EMBEDDING_TIMEOUT_SECONDS=
PATENT_EMBEDDING_BASE_URL=
PATENT_EMBEDDING_MODEL=
PATENT_EMBEDDING_MODEL_TYPE=
PATENT_EMBEDDING_API_URL=
PATENT_EMBEDDING_API_MODEL=
PATENT_EMBEDDING_API_TIMEOUT_SECONDS=
```

Target behavior:

- fastQA and patent use the same embedding model and endpoint config.
- Timeout uses `EMBEDDING_API_TIMEOUT_SECONDS`.

### 12. highThinkingQA Embedding

highThinkingQA keeps its own embedding model and ingestion throughput limits. These must not reuse the fastQA/patent `EMBEDDING_*` namespace.

`HIGHTHINKINGQA_EMBEDDING_API_KEY` is a highThinkingQA service secret, not the shared fastQA/patent embedding secret. It belongs only in `resource/config/services/highThinkingQA/config.secret.env.example` and the corresponding local secret file or external secret injection. Non-secret highThinkingQA embedding endpoint, model, dimensions, throughput, concurrency, retry, and queue settings belong in `resource/config/services/highThinkingQA/config.shared.env`.

Target keys:

```env
HIGHTHINKINGQA_EMBEDDING_API_KEY=
HIGHTHINKINGQA_EMBEDDING_BASE_URL=
HIGHTHINKINGQA_EMBEDDING_MODEL=
HIGHTHINKINGQA_EMBEDDING_DIMENSIONS=
HIGHTHINKINGQA_EMBEDDING_BATCH_SIZE=
HIGHTHINKINGQA_EMBEDDING_API_RPM=
HIGHTHINKINGQA_EMBEDDING_API_TPM=
HIGHTHINKINGQA_EMBEDDING_CONCURRENCY=
HIGHTHINKINGQA_EMBEDDING_MAX_CONCURRENT_REQUESTS=
HIGHTHINKINGQA_EMBEDDING_MAX_INPUT_TOKENS=
HIGHTHINKINGQA_EMBEDDING_MAX_RETRIES=
HIGHTHINKINGQA_EMBEDDING_QUEUE_SIZE=
```

Retire from highThinkingQA service config after code migration:

```env
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=
EMBEDDING_MODEL=
EMBEDDING_DIMENSIONS=
EMBED_BATCH_SIZE=
EMBED_API_RPM=
EMBED_API_TPM=
EMBED_CONCURRENCY=
EMBED_MAX_CONCURRENT_REQUESTS=
EMBED_MAX_INPUT_TOKENS=
EMBED_MAX_RETRIES=
EMBED_QUEUE_SIZE=
```

Target behavior:

- highThinkingQA reads only the `HIGHTHINKINGQA_EMBEDDING_*` namespace for its own embedding path.
- fastQA/patent global embedding config and highThinkingQA embedding config can point to different providers and models.

### 13. OCR

OCR is not part of the final local deployment config.

Retire from config:

```env
OCR_API_KEY=
OCR_BASE_URL=
OCR_MODEL=
OCR_TIMEOUT_SECONDS=
OCR_CONCURRENCY=
OCR_MAX_CONCURRENT_REQUESTS=
OCR_PAGES_PER_BATCH=
OCR_MAX_RETRIES=
OCR_RETRY_BASE=
```

Target behavior:

- OCR behavior is absent or disabled for this local deployment target.
- Reintroducing OCR later requires a separate spec.

### 14. Mandatory Feature Switches

The following switches should not remain in config. They are fixed behavior.

Hardcode enabled:

```env
REDIS_ENABLED=
PATENT_REDIS_ENABLED=
MINIO_USE_PROXY=
CHAT_PERSIST_ENABLED=
CHAT_PERSIST_ASYNC=
UPLOAD_FILE_PROCESSING_ENABLED=
UPLOAD_QA_USE_SIDECAR=
GATEWAY_ADMISSION_ENABLED=
GATEWAY_ADMISSION_DISPATCHER_ENABLED=
GATEWAY_ADMISSION_WORKER_ENABLED=
PATENT_LLM_HTTP_SHARED_POOL_ENABLED=
PATENT_PLANNING_HOT_POOL_ENABLED=
PATENT_PLANNING_UPSTREAM_GATE_ENABLED=
FASTQA_GRAPH_KB_ENABLED=
FASTQA_GRAPH_KB_V2_ENABLED=
FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED=
QA_RETRIEVAL_RERANK_ENABLED=
PATENT_STAGE2_RERANK_ENABLED=
```

Hardcode disabled:

```env
FASTQA_STAGE2_CHAT_WARMUP_ENABLED=
FASTQA_STAGE2_RERANK_WARMUP_ENABLED=
PDF_QA_WARMUP_ENABLED=
PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED=
```

Remove warmup parameters:

```env
FASTQA_STAGE2_CHAT_WARM_INTERVAL_SECONDS=
FASTQA_STAGE2_RERANK_WARM_INTERVAL_SECONDS=
FASTQA_STAGE2_WARM_ACTIVE_START_HOUR=
FASTQA_STAGE2_WARM_ACTIVE_END_HOUR=
FASTQA_STAGE2_*_WARM_TIMEOUT_SECONDS=
FASTQA_STAGE2_*_WARM_JITTER_SECONDS=
FASTQA_STAGE2_BOOTSTRAP_WARM_*=
FASTQA_STAGE2_WARM_ACTIVE_*=
PATENT_PLANNING_HOT_POOL_WARM_INTERVAL_SECONDS=
PATENT_PLANNING_HOT_POOL_WARM_TIMEOUT_SECONDS=
PATENT_PLANNING_HOT_POOL_WARM_JITTER_SECONDS=
PATENT_PLANNING_HOT_POOL_WARM_ACTIVE_START_HOUR=
PATENT_PLANNING_HOT_POOL_WARM_ACTIVE_END_HOUR=
```

Do not remove these related parameters:

```env
CHAT_PERSIST_ASYNC_WORKERS=
UPLOAD_PROCESSING_WORKER_MAX_WORKERS=
UPLOAD_PROCESSING_MAX_PDF_PAGES=
UPLOAD_PROCESSING_POLL_INTERVAL_MS=
UPLOAD_PROCESSING_RECOVERY_SCAN_LIMIT=
UPLOAD_QA_SIDECAR_MODE=
PDFQA_SIDECAR_BASE_URL_INTERNAL=
PDFQA_SIDECAR_SELF_PORT=
UPLOAD_QA_FIRST_TOKEN_TIMEOUT_SEC=
PATENT_LLM_HTTP_*_TIMEOUT_SECONDS=
PATENT_LLM_HTTP_MAX_CONNECTIONS=
PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS=
PATENT_PLANNING_HOT_POOL_LANE_COUNT=
PATENT_PLANNING_HOT_POOL_LANE_DEGRADED_AFTER_SECONDS=
PATENT_PLANNING_UPSTREAM_GATE_LIMIT=
FASTQA_GRAPH_KB_TIMEOUT_MS=
FASTQA_GRAPH_KB_MAX_ROWS=
FASTQA_GRAPH_MAX_DOI_CANDIDATES=
QA_RETRIEVAL_RERANK_CANDIDATES=
PATENT_STAGE2_RERANK_CANDIDATES=
PATENT_STAGE2_RERANK_TOP_PATENTS=
```

---

## Target File-Level Layout

This section describes the intended final ownership, not the exact formatting.

### `resource/config/shared/infrastructure.shared.env`

Keep:

1. service host/port
2. gateway backend target URLs
3. Redis connection settings except `REDIS_ENABLED`
4. MySQL connection/pool/retry settings
5. MinIO bucket/secure/download policy except `MINIO_USE_PROXY`

Remove:

1. `REDIS_ENABLED`
2. `MINIO_USE_PROXY`

### `resource/config/shared/infrastructure.secret.env.example`

Keep secret placeholders:

```env
PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN=
REDIS_USERNAME=
REDIS_PASSWORD=
REDIS_URL=
MYSQL_USER=
MYSQL_PASSWORD=
MINIO_ENDPOINT=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_REGION=
```

Remove model aliases from this file after model endpoint secrets are centralized:

```env
DASHSCOPE_API_KEY=
OPENAI_API_KEY=
```

Keep:

```env
LLM_API_KEY=
EMBEDDING_API_KEY=
RERANK_API_KEY=
```

### `resource/config/shared/model-endpoints.shared.env`

Keep only:

```env
LLM_BASE_URL=
LLM_MODEL=
LLM_CONNECT_TIMEOUT_SECONDS=
LLM_READ_TIMEOUT_SECONDS=
LLM_STREAM_READ_TIMEOUT_SECONDS=
LLM_WRITE_TIMEOUT_SECONDS=
LLM_POOL_TIMEOUT_SECONDS=
LLM_KEEPALIVE_EXPIRY_SECONDS=
LLM_MAX_CONNECTIONS=
LLM_MAX_KEEPALIVE_CONNECTIONS=
EMBEDDING_BASE_URL=
EMBEDDING_MODEL=
EMBEDDING_MODEL_TYPE=
EMBEDDING_API_URL=
EMBEDDING_API_MODEL=
EMBEDDING_API_TIMEOUT_SECONDS=
RERANK_PROVIDER=
RERANK_BASE_URL=
RERANK_MODEL=
RERANK_TIMEOUT_SECONDS=
```

Remove:

```env
LLM_PROVIDER=
LLM_ENABLE_THINKING=
DASHSCOPE_BASE_URL=
DASHSCOPE_MODEL=
OPENAI_BASE_URL=
OPENAI_MODEL=
PATENT_OPENAI_BASE_URL=
PATENT_OPENAI_MODEL=
OPENAI_*_TIMEOUT_SECONDS=
EMBEDDING_TIMEOUT_SECONDS=
PATENT_EMBEDDING_*=
QA_RETRIEVAL_RERANK_* endpoint/model/provider/timeout aliases
PATENT_STAGE2_RERANK_* endpoint/model/provider/timeout aliases
OCR_*=
```

### `resource/config/shared/model-endpoints.secret.env.example`

Keep:

```env
LLM_API_KEY=
EMBEDDING_API_KEY=
RERANK_API_KEY=
```

Remove:

```env
OPENAI_API_KEY=
DASHSCOPE_API_KEY=
QA_RETRIEVAL_RERANK_API_KEY=
PATENT_STAGE2_RERANK_API_KEY=
OCR_API_KEY=
```

### `resource/config/shared/graph.shared.env`

Keep all graph connection identity settings:

```env
FASTQA_NEO4J_URL=
FASTQA_NEO4J_USERNAME=
FASTQA_NEO4J_DATABASE=
PUBLIC_SERVICE_NEO4J_URL=
PUBLIC_SERVICE_NEO4J_USERNAME=
PUBLIC_SERVICE_NEO4J_DATABASE=
PATENT_NEO4J_URL=
PATENT_NEO4J_USERNAME=
PATENT_NEO4J_DATABASE=
NEO4J_URL=
NEO4J_USERNAME=
NEO4J_DATABASE=
```

### `resource/config/shared/graph.secret.env.example`

Keep:

```env
FASTQA_NEO4J_PASSWORD=
PUBLIC_SERVICE_NEO4J_PASSWORD=
PATENT_NEO4J_PASSWORD=
NEO4J_PASSWORD=
```

### `resource/config/services/gateway/config.shared.env`

Keep:

```env
GATEWAY_GUNICORN_WORKERS=
GATEWAY_REFRESH_SURVIVABLE_QA_TASKS_ENABLED=
GATEWAY_ADMISSION_POLL_INTERVAL_SECONDS=
INTERACTIVE_EXECUTION_MAX_CONCURRENT=
INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE=
INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT=
INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT=
INTERACTIVE_EXECUTION_THINKING_MIN_SLOTS=
INTERACTIVE_QUEUE_MAX_SIZE=
INTERACTIVE_QUEUED_TTL_SECONDS=
INTERACTIVE_POST_ADMIT_ATTACH_TTL_SECONDS=
REDIS_KEY_PREFIX=
```

Remove after code/script defaults are fixed:

```env
GATEWAY_ADMISSION_ENABLED=
GATEWAY_ADMISSION_DISPATCHER_ENABLED=
GATEWAY_ADMISSION_WORKER_ENABLED=
```

### `resource/config/services/public-service/config.shared.env`

Keep app, path, auth policy, Redis namespace, cache/quota, lock, outbox, upload sizing, and document limits.

Remove:

```env
UPLOAD_FILE_PROCESSING_ENABLED=
```

Keep related upload settings:

```env
UPLOAD_PROCESSING_WORKER_MAX_WORKERS=
UPLOAD_PROCESSING_MAX_PDF_PAGES=
UPLOAD_PROCESSING_POLL_INTERVAL_MS=
UPLOAD_PROCESSING_RECOVERY_SCAN_LIMIT=
```

### `resource/config/services/fastQA/config.shared.env`

Keep:

1. app/log/CORS/runtime worker values
2. `ASK_STREAM_MAX_CONCURRENT`
3. SSE heartbeat
4. graph timeout, row count, DOI candidate count, logging/suspicious DOI policy
5. embedding model path and vector/data paths
6. retrieval/cache/source/citation scale parameters
7. PDF/file QA limits and sidecar endpoint/timeout settings
8. `REDIS_KEY_PREFIX`
9. `QA_RETRIEVAL_RERANK_CANDIDATES`

Remove or replace:

```env
QUERY_EXPANSION_MODEL=
QA_RETRIEVAL_RERANK_API_KEY=
FASTQA_STAGE2_CHAT_WARM_INTERVAL_SECONDS=
FASTQA_STAGE2_RERANK_WARMUP_ENABLED=
FASTQA_STAGE2_RERANK_WARM_INTERVAL_SECONDS=
FASTQA_STAGE2_WARM_ACTIVE_START_HOUR=
FASTQA_STAGE2_WARM_ACTIVE_END_HOUR=
PDF_QA_USE_DEDICATED_LLM=
PDF_QA_MODEL=
PDF_QA_WARMUP_ENABLED=
UPLOAD_QA_USE_SIDECAR=
```

Retain until explicitly decided:

```env
FASTQA_LLM_HTTP_SHARED_POOL_ENABLED=
```

Notes:

- `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED` is not in the confirmed mandatory switch list. Do not delete it in this cleanup unless product/architecture explicitly confirms it is required mainline behavior.
- `QA_RETRIEVAL_RERANK_API_KEY` moves to shared `RERANK_API_KEY`.
- `UPLOAD_QA_USE_SIDECAR` is hardcoded enabled, but `UPLOAD_QA_SIDECAR_MODE`, `PDFQA_SIDECAR_BASE_URL_INTERNAL`, `PDFQA_SIDECAR_SELF_PORT`, and `UPLOAD_QA_FIRST_TOKEN_TIMEOUT_SEC` remain configurable.

### `resource/config/services/highThinkingQA/config.shared.env`

Keep:

1. chunk/retrieval values
2. highThinkingQA DOI diagnostics
3. local paths
4. Gunicorn/runtime/SSE values
5. request capacity values
6. chat persistence worker and authority targets
7. auth policy
8. PDF limits
9. Redis namespace and cache TTL/lock values

Replace:

```env
EMBEDDING_BASE_URL=...       -> HIGHTHINKINGQA_EMBEDDING_BASE_URL=...
EMBEDDING_MODEL=...          -> HIGHTHINKINGQA_EMBEDDING_MODEL=...
EMBEDDING_DIMENSIONS=...     -> HIGHTHINKINGQA_EMBEDDING_DIMENSIONS=...
EMBED_BATCH_SIZE=...         -> HIGHTHINKINGQA_EMBEDDING_BATCH_SIZE=...
EMBED_API_RPM=...            -> HIGHTHINKINGQA_EMBEDDING_API_RPM=...
EMBED_API_TPM=...            -> HIGHTHINKINGQA_EMBEDDING_API_TPM=...
EMBED_CONCURRENCY=...        -> HIGHTHINKINGQA_EMBEDDING_CONCURRENCY=...
EMBED_MAX_CONCURRENT_REQUESTS=... -> HIGHTHINKINGQA_EMBEDDING_MAX_CONCURRENT_REQUESTS=...
EMBED_MAX_INPUT_TOKENS=...   -> HIGHTHINKINGQA_EMBEDDING_MAX_INPUT_TOKENS=...
EMBED_MAX_RETRIES=...        -> HIGHTHINKINGQA_EMBEDDING_MAX_RETRIES=...
EMBED_QUEUE_SIZE=...         -> HIGHTHINKINGQA_EMBEDDING_QUEUE_SIZE=...
```

Remove:

```env
LLM_MODEL=
LLM_ENABLE_THINKING=
DECOMPOSE_MODEL=
DIRECT_ANSWER_MODEL=
SUB_ANSWER_MODEL=
DIRECT_ANSWER_ENABLE_THINKING=
DECOMPOSE_ENABLE_THINKING=
CHECKER_MODEL=
OCR_BASE_URL=
OCR_MODEL=
OCR_CONCURRENCY=
OCR_MAX_CONCURRENT_REQUESTS=
OCR_PAGES_PER_BATCH=
OCR_MAX_RETRIES=
OCR_RETRY_BASE=
CHAT_PERSIST_ENABLED=
CHAT_PERSIST_ASYNC=
```

Keep:

```env
CHAT_PERSIST_ASYNC_WORKERS=
CONVERSATION_EXECUTION_AUTHORITY_TARGET=
CONVERSATION_ASSISTANT_WRITE_TARGET=
```

Keep highThinkingQA embedding secret placeholder in `resource/config/services/highThinkingQA/config.secret.env.example`:

```env
EMBEDDING_API_KEY=...        -> HIGHTHINKINGQA_EMBEDDING_API_KEY=...
HIGHTHINKINGQA_EMBEDDING_API_KEY=
```

### `resource/config/services/patent/config.shared.env`

Keep:

1. `PATENT_ENV`
2. Gunicorn sizing
3. `PATENT_ASK_STREAM_MAX_CONCURRENT`
4. `PATENT_ASK_EXECUTOR_MAX_WORKERS`
5. stage2 retrieval scale and validation controls not explicitly confirmed as mandatory feature switches
6. `PATENT_STAGE2_RERANK_CANDIDATES`
7. `PATENT_STAGE2_RERANK_TOP_PATENTS`
8. stage4 citation scale
9. `PATENT_REDIS_KEY_PREFIX`
10. durable authority settings if still business-configurable
11. LLM HTTP pool sizing/timeouts
12. `PATENT_PLANNING_HOT_POOL_LANE_COUNT`
13. `PATENT_PLANNING_HOT_POOL_LANE_DEGRADED_AFTER_SECONDS`
14. `PATENT_PLANNING_UPSTREAM_GATE_LIMIT` and related poll/capacity parameters

Remove:

```env
PATENT_REDIS_ENABLED=
PATENT_STAGE2_RERANK_ENABLED=
PATENT_STAGE2_RERANK_PROVIDER=
PATENT_STAGE2_RERANK_BASE_URL=
PATENT_STAGE2_RERANK_MODEL=
PATENT_STAGE2_RERANK_TIMEOUT_SECONDS=
PATENT_STAGE2_RERANK_ENDPOINT_FAMILY=
PATENT_EMBEDDING_API_TIMEOUT_SECONDS=
PATENT_OPENAI_TIMEOUT_SECONDS=
PATENT_LLM_HTTP_SHARED_POOL_ENABLED=
PATENT_PLANNING_HOT_POOL_ENABLED=
PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED=
PATENT_PLANNING_UPSTREAM_GATE_ENABLED=
```

Notes:

- The current file does not include every pool/gate parameter that code may read. If code reads additional `PATENT_LLM_HTTP_*`, `PATENT_PLANNING_HOT_POOL_*`, or `PATENT_PLANNING_UPSTREAM_GATE_*` parameters, those should remain configurable unless they are warmup-only.
- `PATENT_PLANNING_HOT_POOL_WARM_*` parameters are warmup-only and should not be retained.

---

## Implementation Order Requirements

### Phase 1: Add hardcoded defaults without deleting env keys

1. Update code and scripts so mandatory switches ignore env overrides and use the target fixed value.
2. Update warmup/preheat code so it ignores env overrides and stays disabled.
3. Preserve backward-compatible fallback only for renamed value-bearing aliases such as model endpoint keys, embedding keys, and rerank keys. Do not preserve fallback behavior for retired fixed switches.
4. Add or update tests for the fixed behavior of mandatory switches and warmup/preheat disablement.

### Phase 2: Migrate model and embedding readers

1. Make fastQA, highThinkingQA, and patent document LLM paths read `LLM_*`.
2. Make fastQA and patent rerank paths read `RERANK_*`.
3. Make fastQA and patent embedding paths read shared `EMBEDDING_*` / `EMBEDDING_API_*`.
4. Make highThinkingQA embedding paths read `HIGHTHINKINGQA_EMBEDDING_*`.
5. Keep old value-bearing keys as temporary fallback only during this phase.

### Phase 3: Remove retired config keys

1. Remove old aliases and fixed switches from `resource/config/**`.
2. Remove matching placeholders from `*.env.example`.
3. Keep secrets examples only for target keys.
4. Update docs that mention retired variables.

### Phase 4: Remove fallback reads

1. After config files no longer use old keys, remove legacy fallback reads from production code.
2. Keep only target variable names.
3. Run targeted tests and service startup checks.

---

## Verification Requirements

Minimum checks for the future implementation:

1. Grep retired keys in production code and config:

```bash
rg -n "OPENAI_|DASHSCOPE_|PATENT_OPENAI_|PATENT_EMBEDDING_|QA_RETRIEVAL_RERANK_(API_KEY|PROVIDER|BASE_URL|MODEL|TIMEOUT)|PATENT_STAGE2_RERANK_(API_KEY|PROVIDER|BASE_URL|MODEL|TIMEOUT_SECONDS|ENDPOINT_FAMILY)|OCR_|REDIS_ENABLED|PATENT_REDIS_ENABLED|MINIO_USE_PROXY|CHAT_PERSIST_ENABLED|CHAT_PERSIST_ASYNC=|UPLOAD_FILE_PROCESSING_ENABLED|UPLOAD_QA_USE_SIDECAR|GATEWAY_ADMISSION_(ENABLED|DISPATCHER_ENABLED|WORKER_ENABLED)|PATENT_LLM_HTTP_SHARED_POOL_ENABLED|PATENT_PLANNING_(HOT_POOL_ENABLED|UPSTREAM_GATE_ENABLED)|FASTQA_GRAPH_KB(_V2|_RAG_INJECTION)?_ENABLED|QA_RETRIEVAL_RERANK_ENABLED|PATENT_STAGE2_RERANK_ENABLED|WARMUP_ENABLED|WARM_INTERVAL|WARM_TIMEOUT|WARM_JITTER|WARM_ACTIVE|BOOTSTRAP_WARM" resource gateway public-service fastQA highThinkingQA patent scripts
```

Expected:

- No config entries for retired keys.
- No production runtime dependency on retired keys after Phase 4.
- Tests may still mention retired keys only if they intentionally verify compatibility before fallback removal.

2. Grep required retained key families:

```bash
rg -n "LLM_BASE_URL|RERANK_BASE_URL|EMBEDDING_API_URL|HIGHTHINKINGQA_EMBEDDING_BASE_URL|MYSQL_HOST|MINIO_ENDPOINT|REDIS_HOST|NEO4J_URL|INTERACTIVE_EXECUTION_MAX_CONCURRENT|ASK_STREAM_MAX_CONCURRENT" resource/config
```

Expected:

- Target keys exist in the appropriate final config files or examples.

3. Run backend config/unit tests for touched services:

```bash
pytest gateway/tests public-service/backend/tests fastQA/tests highThinkingQA/tests patent/tests -q
```

Expected:

- Existing tests pass, or any unrelated pre-existing failures are documented with exact failure output.

4. Validate startup scripts do not depend on removed switches:

```bash
bash scripts/status_all.sh
```

Expected:

- The script can evaluate service state without requiring removed env keys.

5. Validate targeted model config resolution in each document backend:

```bash
rg -n "LLM_BASE_URL|RERANK_BASE_URL|EMBEDDING_API_URL|HIGHTHINKINGQA_EMBEDDING" fastQA highThinkingQA patent
```

Expected:

- fastQA, highThinkingQA, and patent LLM paths resolve through `LLM_*`.
- fastQA and patent rerank paths resolve through `RERANK_*`.
- fastQA and patent embedding paths resolve through shared `EMBEDDING_*`.
- highThinkingQA embedding paths resolve through `HIGHTHINKINGQA_EMBEDDING_*`.

---

## Acceptance Criteria

The cleanup is complete only when:

1. `resource/config/**` no longer contains fixed mandatory feature switches listed in this spec.
2. `resource/config/**` no longer contains warmup/preheat settings listed for removal.
3. `resource/config/**` no longer contains OCR settings for the local deployment target.
4. `resource/config/**` contains only the target model namespaces:
   - `LLM_*`
   - `RERANK_*`
   - shared fastQA/patent `EMBEDDING_*` / `EMBEDDING_API_*`
   - highThinkingQA `HIGHTHINKINGQA_EMBEDDING_*`
5. Connection, credential, storage, graph, path, and capacity settings remain configurable.
6. Startup scripts do not require removed switches to start required workers.
7. Tests and startup/status checks have been run and their results are recorded.

## Open Decisions

The following are intentionally not decided by this spec and should not be silently removed:

1. Whether `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED` is mandatory like the patent shared pool. Current docs do not list it in the confirmed mandatory set.
2. Whether `PATENT_STAGE2_CONVERGENCE_ENABLED`, `PATENT_STAGE2_VALIDATION_ENABLED`, `PATENT_STAGE2_C_*`, `PATENT_DURABLE_MODE_ENABLED`, and `PATENT_DURABLE_AUTHORITY_ENABLED` are business switches to retain or fixed mainline behavior to hardcode.
3. Whether fastQA generation pipeline switches such as `QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED`, `QA_STAGE35_EVIDENCE_RERANK_ENABLED`, `QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED`, and citation/structure toggles are business-tunable or fixed flow behavior.
4. Whether app/debug/CORS/logging/gunicorn values should remain in `resource/config` or move to deployment-specific files in a later pass.

Until these are explicitly decided, retain them.
