# Resource Config Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify `resource/config/**` so only deployment-sensitive settings remain configurable, while confirmed mandatory runtime behavior is hardcoded and model/embedding/rerank namespaces are unified.

**Architecture:** Migrate code first, then config files, then remove fallback reads. Fixed switches and warmup/preheat flags must stop honoring env overrides before their config keys are deleted. Value-bearing model aliases may keep temporary fallback only until final config files no longer contain old keys.

**Tech Stack:** Python/FastAPI services, shell startup scripts, env-file based configuration, pytest, `rg`.

---

## Source Spec

Implement from:

- [Resource Config Simplification Spec](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-05-11-resource-config-simplification-spec.md)
- [Resource Config Code Map](/home/cqy/worktrees/highThinking/docs/config/2026-05-10-resource-config-code-map.md)

## Scope Boundaries

This plan changes production code, tests, and `resource/config/**`. It does not change real secret values except renaming example placeholders or moving existing local secret keys when required by the target contract.

Hard rules:

1. Do not delete old env keys before production code no longer depends on them.
2. Do not keep env override behavior for fixed mandatory switches or warmup/preheat flags.
3. Keep connection, credential, path, graph, auth, and capacity settings configurable.
4. Keep undecided business switches configurable.
5. Do not implement OCR.
6. Do not silently delete `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED`; it remains configurable until explicitly decided.

## File Structure

Primary code files expected to change:

- `gateway/app/core/config.py` - Redis and admission fixed behavior, retained admission capacity settings.
- `scripts/_service_common.sh` - admission worker startup gate fixed enabled.
- `scripts/status_all.sh` - status text no longer refers to `GATEWAY_ADMISSION_WORKER_ENABLED`.
- `gateway/scripts/start_admission_worker.sh` - admission/dispatcher fixed enabled for worker process.
- `gateway/scripts/run_admission_worker_foreground.sh` - admission/dispatcher fixed enabled for foreground worker.
- `fastQA/app/core/config.py` - Redis, graph KB, warmup, chat persistence fixed behavior.
- `fastQA/app/modules/generation_pipeline/stage2_retrieval.py` - rerank fixed enabled and candidate count retained.
- `fastQA/app/modules/qa_cache/stage2_cache.py` - cache fingerprint stops using retired rerank enable/provider/model aliases.
- `fastQA/app/core/runtime.py` - unified LLM/rerank/embedding key reads.
- `fastQA/app/modules/microscopic_expert.py` - unified rerank and embedding reads.
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py` - unified LLM/embedding reads.
- `fastQA/app/modules/generation_pipeline/query_expander.py` - no stage-specific model alias.
- `fastQA/app/modules/qa_pdf/llm_factory.py` - unified LLM and no PDF dedicated LLM env.
- `fastQA/app/modules/qa_pdf/service.py` - sidecar fixed enabled.
- `public-service/backend/app/core/config.py` - Redis fixed enabled if still read there.
- `public-service/backend/app/modules/documents/service.py` - document LLM reads migrate from OpenAI/DashScope aliases to `LLM_*`.
- `public-service/backend/app/modules/documents/translator.py` - translation LLM reads migrate from OpenAI/DashScope aliases to `LLM_*`.
- `public-service/backend/app/modules/conversation/upload_processing_worker.py` - upload processing fixed enabled.
- `public-service/backend/app/modules/conversation/service.py` - MinIO proxy fixed enabled.
- `highThinkingQA/config.py` - unified LLM, highThinkingQA embedding namespace, OCR removal/disablement, chat persistence fixed behavior.
- `highThinkingQA/agent_core/llm_client.py` - error text and API key resolution target `LLM_API_KEY`.
- `highThinkingQA/ingest/embedder.py` - highThinkingQA embedding API key namespace.
- `highThinkingQA/server/services/documents_service.py` - unified LLM namespace.
- `highThinkingQA/server/storage/file_delivery_service.py` - MinIO proxy fixed enabled.
- `patent/config.py` - patent Redis/shared pool/hot pool/upstream gate fixed behavior; warmup disabled; LLM/rerank/embedding target config.
- `patent/server/patent/upstream_http.py` - shared pool fixed enabled.
- `patent/server/patent/planning_hot_pool.py` - hot pool fixed enabled, warmup disabled, retained lane parameters.
- `patent/server/patent/upstream_gate.py` - upstream gate fixed enabled, retained limit.
- `patent/server/patent/stage2_controls.py` - rerank fixed enabled, endpoint config from `RERANK_*`, scale values retained.
- `patent/server/patent/rerank_service.py` - rerank endpoint config from `RERANK_*`.
- `patent/server/patent/runtime.py` - unified LLM and embedding config.
- `patent/server/patent/answering.py` - unified LLM config.
- `patent/server/patent/pdf_service.py` - unified LLM config.
- `patent/server/patent/hybrid_synthesis.py` - unified LLM config.
- `patent/server/patent/tabular_service.py` - unified LLM config.
- `patent/scripts/start.sh` - patent Redis fixed enabled behavior.
- `patent/scripts/start_gunicorn.sh` - patent Redis fixed enabled behavior.

Primary config files expected to change:

- `resource/config/shared/infrastructure.shared.env`
- `resource/config/shared/infrastructure.secret.env.example`
- `resource/config/shared/model-endpoints.shared.env`
- `resource/config/shared/model-endpoints.secret.env.example`
- `resource/config/shared/graph.shared.env`
- `resource/config/shared/graph.secret.env.example`
- `resource/config/services/gateway/config.shared.env`
- `resource/config/services/gateway/config.secret.env.example`
- `resource/config/services/public-service/config.shared.env`
- `resource/config/services/public-service/config.secret.env.example`
- `resource/config/services/fastQA/config.shared.env`
- `resource/config/services/fastQA/config.env.example`
- `resource/config/services/fastQA/config.secret.env.example`
- `resource/config/services/highThinkingQA/config.shared.env`
- `resource/config/services/highThinkingQA/config.env.example`
- `resource/config/services/highThinkingQA/config.secret.env.example`
- `resource/config/services/patent/config.shared.env`
- `patent/config.shared.env.example`

Primary tests expected to change or add:

- `gateway/tests/test_config.py`
- `gateway/tests/test_execution_admission.py`
- `gateway/tests/test_admission_worker_scripts.py`
- `fastQA/tests/test_redis_runtime.py`
- `fastQA/tests/test_graph_kb_runtime.py`
- `fastQA/tests/test_generation_stage2_retrieval.py`
- `fastQA/tests/test_generation_runtime_bootstrap.py`
- `fastQA/tests/test_microscopic_expert.py`
- `fastQA/tests/test_qa_pdf_llm_factory.py`
- `fastQA/tests/test_qa_pool_timeout_contract.py`
- `public-service/backend/tests/test_config_independence.py`
- `public-service/backend/tests/test_uploads_module.py`
- `highThinkingQA/tests/test_config_runtime_defaults.py`
- `highThinkingQA/tests/test_api_key_validation.py`
- `highThinkingQA/tests/test_env_loader.py`
- `highThinkingQA/tests/fastapi_migration/test_file_delivery_baseline.py`
- `patent/tests/test_runtime_controls.py`
- `patent/tests/test_redis_runtime.py`
- `patent/tests/test_patent_upstream_config.py`
- `patent/tests/test_patent_upstream_http.py`
- `patent/tests/test_patent_planning_hot_pool.py`
- `patent/tests/test_patent_upstream_gate.py`
- `patent/tests/test_patent_stage2_controls.py`
- `patent/tests/test_patent_rerank_service.py`
- `patent/tests/test_env_loader.py`

---

## Task 1: Fixed Infrastructure Switches And Gateway Admission

**Files:**
- Modify: `gateway/app/core/config.py`
- Modify: `scripts/_service_common.sh`
- Modify: `scripts/status_all.sh`
- Modify: `gateway/scripts/start_admission_worker.sh`
- Modify: `gateway/scripts/run_admission_worker_foreground.sh`
- Modify: `public-service/backend/app/core/config.py`
- Modify: `fastQA/app/core/config.py`
- Modify: `highThinkingQA/server/services/redis_client.py`
- Test: `gateway/tests/test_config.py`
- Test: `gateway/tests/test_execution_admission.py`
- Test: `gateway/tests/test_admission_worker_scripts.py`
- Test: `public-service/backend/tests/test_config_independence.py`
- Test: `fastQA/tests/test_redis_runtime.py`
- Test: `highThinkingQA/tests/test_stage_cache_runtime.py`

- [ ] **Step 1: Update tests for fixed Redis and admission behavior**

Add/adjust tests so setting these env vars to disabled values no longer disables the feature:

```python
monkeypatch.setenv("REDIS_ENABLED", "0")
monkeypatch.setenv("GATEWAY_ADMISSION_ENABLED", "0")
monkeypatch.setenv("GATEWAY_ADMISSION_DISPATCHER_ENABLED", "0")
```

Expected assertions:

```python
assert settings.redis.enabled is True
assert settings.admission.enabled is True
assert settings.admission.dispatcher_enabled is True
```

For scripts, update tests so `GATEWAY_ADMISSION_WORKER_ENABLED=0` no longer disables the worker-start decision.

- [ ] **Step 2: Run the targeted tests and confirm they fail before implementation**

Run:

```bash
pytest gateway/tests/test_config.py gateway/tests/test_execution_admission.py gateway/tests/test_admission_worker_scripts.py public-service/backend/tests/test_config_independence.py fastQA/tests/test_redis_runtime.py highThinkingQA/tests/test_stage_cache_runtime.py -q
```

Expected: at least the tests changed in Step 1 fail because code still honors disabled env values.

- [ ] **Step 3: Hardcode Redis enabled where currently confirmed mandatory**

In code that currently reads `REDIS_ENABLED`, change runtime behavior to enabled:

```python
redis_enabled = True
```

Keep all Redis connection and namespace settings as env-driven:

```python
REDIS_URL
REDIS_HOST
REDIS_PORT
REDIS_USERNAME
REDIS_PASSWORD
REDIS_DB
REDIS_KEY_PREFIX
REDIS_SOCKET_CONNECT_TIMEOUT_SEC
REDIS_SOCKET_TIMEOUT_SEC
```

Do not remove Redis connection reads.

- [ ] **Step 4: Hardcode gateway admission enabled**

In `gateway/app/core/config.py`, stop reading `GATEWAY_ADMISSION_ENABLED` and `GATEWAY_ADMISSION_DISPATCHER_ENABLED` as switches:

```python
admission_enabled = True
dispatcher_enabled = True
```

Keep these env-driven:

```python
GATEWAY_ADMISSION_CONTROL_TOKEN
GATEWAY_ADMISSION_POLL_INTERVAL_SECONDS
INTERACTIVE_EXECUTION_MAX_CONCURRENT
INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT
INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT
INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE
INTERACTIVE_EXECUTION_THINKING_MIN_SLOTS
INTERACTIVE_QUEUE_MAX_SIZE
INTERACTIVE_QUEUED_TTL_SECONDS
INTERACTIVE_POST_ADMIT_ATTACH_TTL_SECONDS
```

- [ ] **Step 5: Hardcode admission worker startup enabled**

In `scripts/_service_common.sh`, make `gateway_admission_worker_enabled` return success without reading `GATEWAY_ADMISSION_WORKER_ENABLED`.

In `scripts/status_all.sh`, remove text that says the worker is disabled by `GATEWAY_ADMISSION_WORKER_ENABLED`.

In `gateway/scripts/start_admission_worker.sh` and `gateway/scripts/run_admission_worker_foreground.sh`, set admission/dispatcher behavior without preserving env override:

```bash
export GATEWAY_ADMISSION_ENABLED="1"
export GATEWAY_ADMISSION_DISPATCHER_ENABLED="1"
```

Keep `GATEWAY_ADMISSION_STARTUP_STABLE_CHECKS` configurable.

- [ ] **Step 6: Run targeted tests**

Run:

```bash
pytest gateway/tests/test_config.py gateway/tests/test_execution_admission.py gateway/tests/test_admission_worker_scripts.py public-service/backend/tests/test_config_independence.py fastQA/tests/test_redis_runtime.py highThinkingQA/tests/test_stage_cache_runtime.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add gateway/app/core/config.py scripts/_service_common.sh scripts/status_all.sh gateway/scripts/start_admission_worker.sh gateway/scripts/run_admission_worker_foreground.sh public-service/backend/app/core/config.py fastQA/app/core/config.py highThinkingQA/server/services/redis_client.py gateway/tests/test_config.py gateway/tests/test_execution_admission.py gateway/tests/test_admission_worker_scripts.py public-service/backend/tests/test_config_independence.py fastQA/tests/test_redis_runtime.py highThinkingQA/tests/test_stage_cache_runtime.py
git commit -m "refactor: hardcode mandatory redis and admission switches"
```

---

## Task 2: Fixed Persistence, Upload, MinIO Proxy, Graph, Sidecar, And Warmup Behavior

**Files:**
- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/modules/qa_pdf/service.py`
- Modify: `public-service/backend/app/modules/conversation/upload_processing_worker.py`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `highThinkingQA/config.py`
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Modify: `highThinkingQA/server_fastapi/routers/upload.py`
- Modify: `highThinkingQA/server/storage/file_delivery_service.py`
- Test: `fastQA/tests/test_graph_kb_runtime.py`
- Test: `fastQA/tests/test_qa_pool_timeout_contract.py`
- Test: `public-service/backend/tests/test_uploads_module.py`
- Test: `highThinkingQA/tests/test_config_runtime_defaults.py`
- Test: `highThinkingQA/tests/fastapi_migration/test_file_delivery_baseline.py`

- [ ] **Step 1: Write or update tests for fixed behavior**

Update tests so disabled env values no longer disable:

```python
FASTQA_GRAPH_KB_ENABLED=0
FASTQA_GRAPH_KB_V2_ENABLED=0
FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED=0
CHAT_PERSIST_ENABLED=0
CHAT_PERSIST_ASYNC=0
UPLOAD_FILE_PROCESSING_ENABLED=0
UPLOAD_QA_USE_SIDECAR=0
MINIO_USE_PROXY=0
FASTQA_STAGE2_CHAT_WARMUP_ENABLED=1
FASTQA_STAGE2_RERANK_WARMUP_ENABLED=1
PDF_QA_WARMUP_ENABLED=1
```

Expected behavior:

```python
assert graph_kb_enabled is True
assert graph_kb_v2_enabled is True
assert graph_kb_rag_injection_enabled is True
assert chat_persist_enabled is True
assert chat_persist_async is True
assert upload_processing_enabled is True
assert upload_qa_sidecar_enabled is True
assert minio_proxy_enabled is True
assert warmup_enabled is False
```

- [ ] **Step 2: Run targeted tests and confirm failures**

Run:

```bash
pytest fastQA/tests/test_graph_kb_runtime.py fastQA/tests/test_qa_pool_timeout_contract.py public-service/backend/tests/test_uploads_module.py highThinkingQA/tests/test_config_runtime_defaults.py highThinkingQA/tests/fastapi_migration/test_file_delivery_baseline.py -q
```

Expected: tests added in Step 1 fail until code is updated.

- [ ] **Step 3: Hardcode fastQA graph and warmup behavior**

In `fastQA/app/core/config.py`:

```python
graph_kb_enabled=True
graph_kb_v2_enabled=True
graph_kb_rag_injection_enabled=True
stage2_chat_warmup_enabled=False
stage2_rerank_warmup_enabled=False
chat_persist_enabled=True
chat_persist_async=True
```

Remove or stop reading fastQA warmup-only parameter env keys once warmup is fixed off:

```env
FASTQA_STAGE2_CHAT_WARM_INTERVAL_SECONDS
FASTQA_STAGE2_RERANK_WARM_INTERVAL_SECONDS
FASTQA_STAGE2_CHAT_WARM_TIMEOUT_SECONDS
FASTQA_STAGE2_RERANK_WARM_TIMEOUT_SECONDS
FASTQA_STAGE2_WARM_JITTER_SECONDS
FASTQA_STAGE2_BOOTSTRAP_WARM_MAX_PARALLEL
FASTQA_STAGE2_BOOTSTRAP_WARM_JITTER_SECONDS
FASTQA_STAGE2_WARM_ACTIVE_START_HOUR
FASTQA_STAGE2_WARM_ACTIVE_END_HOUR
```

Do not remove unrelated hot-pool/gate sizing such as `FASTQA_STAGE2_CHAT_HOT_LANE_COUNT`, `FASTQA_STAGE2_RERANK_HOT_LANE_COUNT`, `FASTQA_STAGE2_CHAT_GATE_MAX_IN_FLIGHT`, or `FASTQA_STAGE2_RERANK_GATE_MAX_IN_FLIGHT`; those are not warmup-only.

Keep graph sizing and chat worker settings configurable:

```python
FASTQA_GRAPH_KB_TIMEOUT_MS
FASTQA_GRAPH_KB_MAX_ROWS
FASTQA_GRAPH_DIRECT_ANSWER_MIN_CONFIDENCE
FASTQA_GRAPH_MAX_DOI_CANDIDATES
FASTQA_GRAPH_ALLOW_SUSPICIOUS_DOI_FOR_RAG
CHAT_PERSIST_ASYNC_WORKERS
```

- [ ] **Step 4: Hardcode upload sidecar and public-service upload processing**

In `fastQA/app/modules/qa_pdf/service.py`, make sidecar behavior enabled independent of `UPLOAD_QA_USE_SIDECAR`.

In `public-service/backend/app/modules/conversation/upload_processing_worker.py`, make processing enabled independent of `UPLOAD_FILE_PROCESSING_ENABLED`.

Keep related sizing settings configurable.

- [ ] **Step 5: Hardcode MinIO proxy**

In `public-service/backend/app/modules/conversation/service.py` and `highThinkingQA/server/storage/file_delivery_service.py`, make MinIO proxy behavior enabled independent of `MINIO_USE_PROXY`.

Keep MinIO endpoint, bucket, credentials, secure, region, and download expiry configurable.

- [ ] **Step 6: Hardcode highThinkingQA chat persistence**

In `highThinkingQA/config.py`, make:

```python
chat_persist_enabled=True
chat_persist_async=True
```

Keep:

```python
CHAT_PERSIST_ASYNC_WORKERS
CONVERSATION_EXECUTION_AUTHORITY_TARGET
CONVERSATION_ASSISTANT_WRITE_TARGET
CHAT_JSON_BASE_DIR
CHAT_JSON_STORAGE_PREFIX
```

Update routers only if they rely on env-derived app state in a way that can still disable persistence.

- [ ] **Step 7: Run targeted tests**

Run:

```bash
pytest fastQA/tests/test_graph_kb_runtime.py fastQA/tests/test_qa_pool_timeout_contract.py public-service/backend/tests/test_uploads_module.py highThinkingQA/tests/test_config_runtime_defaults.py highThinkingQA/tests/fastapi_migration/test_file_delivery_baseline.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add fastQA/app/core/config.py fastQA/app/modules/qa_pdf/service.py public-service/backend/app/modules/conversation/upload_processing_worker.py public-service/backend/app/modules/conversation/service.py highThinkingQA/config.py highThinkingQA/server_fastapi/routers/ask.py highThinkingQA/server_fastapi/routers/upload.py highThinkingQA/server/storage/file_delivery_service.py fastQA/tests/test_graph_kb_runtime.py fastQA/tests/test_qa_pool_timeout_contract.py public-service/backend/tests/test_uploads_module.py highThinkingQA/tests/test_config_runtime_defaults.py highThinkingQA/tests/fastapi_migration/test_file_delivery_baseline.py
git commit -m "refactor: hardcode mandatory persistence upload and graph switches"
```

---

## Task 3: Unified LLM Config Across Document Backends

**Files:**
- Modify: `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
- Modify: `fastQA/app/modules/generation_pipeline/query_expander.py`
- Modify: `fastQA/app/modules/qa_pdf/llm_factory.py`
- Modify: `fastQA/app/services/file_route_service.py`
- Modify: `fastQA/app/integrations/llm/shared_http_pool.py`
- Modify: `highThinkingQA/config.py`
- Modify: `highThinkingQA/agent_core/llm_client.py`
- Modify: `highThinkingQA/server/services/documents_service.py`
- Modify: `public-service/backend/app/modules/documents/service.py`
- Modify: `public-service/backend/app/modules/documents/translator.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/answering.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/hybrid_synthesis.py`
- Modify: `patent/server/patent/tabular_service.py`
- Test: `fastQA/tests/test_generation_runtime_bootstrap.py`
- Test: `fastQA/tests/test_qa_pdf_llm_factory.py`
- Test: `fastQA/tests/test_llm_shared_http_pool.py`
- Test: `highThinkingQA/tests/test_api_key_validation.py`
- Test: `highThinkingQA/tests/test_env_loader.py`
- Test: `highThinkingQA/tests/test_llm_client.py`
- Test: `public-service/backend/tests/test_documents_module.py`
- Test: `patent/tests/test_env_loader.py`
- Test: `patent/tests/test_patent_pdf_contract.py`
- Test: `patent/tests/test_patent_tabular_service.py`
- Test: `patent/tests/test_patent_hybrid_synthesis.py`

- [ ] **Step 1: Update tests for target LLM namespace**

Tests should assert that LLM config comes from:

```env
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
LLM_CONNECT_TIMEOUT_SECONDS
LLM_READ_TIMEOUT_SECONDS
LLM_STREAM_READ_TIMEOUT_SECONDS
LLM_WRITE_TIMEOUT_SECONDS
LLM_POOL_TIMEOUT_SECONDS
LLM_KEEPALIVE_EXPIRY_SECONDS
LLM_MAX_CONNECTIONS
LLM_MAX_KEEPALIVE_CONNECTIONS
```

The document service and translation service in `public-service` are part of this cleanup because they still call OpenAI-compatible LLM endpoints for document utilities. They should read `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` target-first during migration and must not keep OpenAI/DashScope aliases after Task 8.

Tests should assert old aliases are ignored after final fallback removal:

```env
OPENAI_API_KEY
DASHSCOPE_API_KEY
PATENT_OPENAI_API_KEY
OPENAI_BASE_URL
DASHSCOPE_BASE_URL
PATENT_OPENAI_BASE_URL
OPENAI_MODEL
DASHSCOPE_MODEL
PATENT_OPENAI_MODEL
DECOMPOSE_MODEL
DIRECT_ANSWER_MODEL
SUB_ANSWER_MODEL
CHECKER_MODEL
QUERY_EXPANSION_MODEL
PDF_QA_MODEL
```

- [ ] **Step 2: Run target tests and confirm failures**

Run:

```bash
pytest fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_qa_pdf_llm_factory.py fastQA/tests/test_llm_shared_http_pool.py highThinkingQA/tests/test_api_key_validation.py highThinkingQA/tests/test_env_loader.py highThinkingQA/tests/test_llm_client.py public-service/backend/tests/test_documents_module.py patent/tests/test_env_loader.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_hybrid_synthesis.py -q
```

Expected: tests added in Step 1 fail until readers are migrated.

- [ ] **Step 3: Add temporary `LLM_*` first fallback readers**

In each LLM reader, resolve in this order during migration:

```python
api_key = os.getenv("LLM_API_KEY") or legacy_api_key
base_url = os.getenv("LLM_BASE_URL") or legacy_base_url
model = os.getenv("LLM_MODEL") or legacy_model
```

For patent LLM callers that currently use one old `PATENT_OPENAI_TIMEOUT_SECONDS` value, map it to the unified read timeout during migration:

```python
read_timeout = os.getenv("LLM_READ_TIMEOUT_SECONDS") or os.getenv("PATENT_OPENAI_TIMEOUT_SECONDS") or "30"
```

If a caller constructs a richer `httpx.Timeout`, use the unified timeout keys by role:

```python
connect_timeout = os.getenv("LLM_CONNECT_TIMEOUT_SECONDS") or read_timeout
stream_read_timeout = os.getenv("LLM_STREAM_READ_TIMEOUT_SECONDS") or read_timeout
write_timeout = os.getenv("LLM_WRITE_TIMEOUT_SECONDS") or read_timeout
pool_timeout = os.getenv("LLM_POOL_TIMEOUT_SECONDS") or read_timeout
```

Keep this fallback only until Task 8.

In `public-service/backend/app/modules/documents/service.py` and `public-service/backend/app/modules/documents/translator.py`, apply the same target-first fallback for now:

```python
api_key = _first_env("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")
base_url = _first_env("LLM_BASE_URL", "OPENAI_BASE_URL", "DASHSCOPE_BASE_URL", default=DEFAULT_LLM_BASE_URL)
model = _first_env("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="deepseek-v3.1")
```

Remove service-local model alias use in highThinkingQA:

```python
decompose_model = llm_model
direct_answer_model = llm_model
sub_answer_model = llm_model
checker_model = llm_model
```

Do not keep env overrides for thinking behavior:

```python
llm_enable_thinking = True
direct_answer_enable_thinking = False
decompose_enable_thinking = False
```

This preserves the current target flow from `resource/config/services/highThinkingQA/config.shared.env`: main thinking synthesis defaults to thinking on, direct answer and decomposition stay non-thinking unless a caller explicitly passes an in-process `enable_thinking` override. Remove config/env reads for `LLM_ENABLE_THINKING`, `DIRECT_ANSWER_ENABLE_THINKING`, and `DECOMPOSE_ENABLE_THINKING`.

- [ ] **Step 4: Update PDF QA dedicated LLM behavior**

In fastQA PDF QA, stop honoring:

```env
PDF_QA_USE_DEDICATED_LLM
PDF_QA_MODEL
```

Use `LLM_MODEL`. Keep non-model PDF behavior if still needed:

```env
PDF_QA_TIMEOUT_SECONDS
PDF_QA_MAX_RETRIES
PDF_QA_MAX_TOKENS
PDF_QA_TEMPERATURE
PDF_QA_TOP_P
PDF_QA_MAX_PDF_CHARS
```

- [ ] **Step 5: Run target tests**

Run:

```bash
pytest fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_qa_pdf_llm_factory.py fastQA/tests/test_llm_shared_http_pool.py highThinkingQA/tests/test_api_key_validation.py highThinkingQA/tests/test_env_loader.py highThinkingQA/tests/test_llm_client.py public-service/backend/tests/test_documents_module.py patent/tests/test_env_loader.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_hybrid_synthesis.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/modules/generation_pipeline/runtime_bootstrap.py fastQA/app/modules/generation_pipeline/query_expander.py fastQA/app/modules/qa_pdf/llm_factory.py fastQA/app/services/file_route_service.py fastQA/app/integrations/llm/shared_http_pool.py highThinkingQA/config.py highThinkingQA/agent_core/llm_client.py highThinkingQA/server/services/documents_service.py public-service/backend/app/modules/documents/service.py public-service/backend/app/modules/documents/translator.py patent/server/patent/runtime.py patent/server/patent/answering.py patent/server/patent/pdf_service.py patent/server/patent/hybrid_synthesis.py patent/server/patent/tabular_service.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_qa_pdf_llm_factory.py fastQA/tests/test_llm_shared_http_pool.py highThinkingQA/tests/test_api_key_validation.py highThinkingQA/tests/test_env_loader.py highThinkingQA/tests/test_llm_client.py public-service/backend/tests/test_documents_module.py patent/tests/test_env_loader.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_hybrid_synthesis.py
git commit -m "refactor: route document llm config through shared llm env"
```

---

## Task 4: Unified Rerank Config And Fixed Rerank Flow

**Files:**
- Modify: `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`
- Modify: `fastQA/app/modules/qa_cache/stage2_cache.py`
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/modules/microscopic_expert.py`
- Modify: `patent/server/patent/stage2_controls.py`
- Modify: `patent/server/patent/rerank_service.py`
- Test: `fastQA/tests/test_generation_stage2_retrieval.py`
- Test: `fastQA/tests/test_microscopic_expert.py`
- Test: `fastQA/tests/test_stage2_hot_connection_runtime.py`
- Test: `patent/tests/test_patent_stage2_controls.py`
- Test: `patent/tests/test_patent_rerank_service.py`
- Test: `patent/tests/test_patent_retrieval_service.py`

- [ ] **Step 1: Update tests for unified rerank**

Tests should set:

```env
RERANK_API_KEY=
RERANK_PROVIDER=
RERANK_BASE_URL=
RERANK_MODEL=
RERANK_TIMEOUT_SECONDS=
```

And assert old endpoint aliases are no longer needed:

```env
QA_RETRIEVAL_RERANK_API_KEY
QA_RETRIEVAL_RERANK_PROVIDER
QA_RETRIEVAL_RERANK_BASE_URL
QA_RETRIEVAL_RERANK_MODEL
QA_RETRIEVAL_RERANK_TIMEOUT
PATENT_STAGE2_RERANK_API_KEY
PATENT_STAGE2_RERANK_PROVIDER
PATENT_STAGE2_RERANK_BASE_URL
PATENT_STAGE2_RERANK_MODEL
PATENT_STAGE2_RERANK_TIMEOUT_SECONDS
PATENT_STAGE2_RERANK_ENDPOINT_FAMILY
```

Tests should assert these env vars can no longer disable rerank:

```env
QA_RETRIEVAL_RERANK_ENABLED=0
PATENT_STAGE2_RERANK_ENABLED=false
```

Keep scale settings:

```env
QA_RETRIEVAL_RERANK_CANDIDATES
PATENT_STAGE2_RERANK_CANDIDATES
PATENT_STAGE2_RERANK_TOP_PATENTS
```

- [ ] **Step 2: Run target tests and confirm failures**

Run:

```bash
pytest fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_microscopic_expert.py fastQA/tests/test_stage2_hot_connection_runtime.py patent/tests/test_patent_stage2_controls.py patent/tests/test_patent_rerank_service.py patent/tests/test_patent_retrieval_service.py -q
```

Expected: tests added in Step 1 fail until readers are migrated.

- [ ] **Step 3: Migrate rerank readers to `RERANK_*`**

During migration, use target-first fallback:

```python
provider = os.getenv("RERANK_PROVIDER") or legacy_provider
base_url = os.getenv("RERANK_BASE_URL") or legacy_base_url
model = os.getenv("RERANK_MODEL") or legacy_model
timeout = os.getenv("RERANK_TIMEOUT_SECONDS") or legacy_timeout
api_key = os.getenv("RERANK_API_KEY") or legacy_api_key
```

Remove fallback in Task 8.

- [ ] **Step 4: Hardcode rerank enabled**

In fastQA stage2 runtime toggles and patent stage2 controls:

```python
use_rerank = True
rerank_enabled = True
```

Do not read `QA_RETRIEVAL_RERANK_ENABLED` or `PATENT_STAGE2_RERANK_ENABLED`.

- [ ] **Step 5: Update cache fingerprints**

In `fastQA/app/modules/qa_cache/stage2_cache.py`, stop fingerprinting retired provider/model/enabled aliases. Include:

```python
"rerank": "enabled"
"rerank_candidates": os.getenv("QA_RETRIEVAL_RERANK_CANDIDATES", "50")
"rerank_provider": os.getenv("RERANK_PROVIDER", "local")
"rerank_model": os.getenv("RERANK_MODEL", "qwen3-vl-rerank")
```

- [ ] **Step 6: Run target tests**

Run:

```bash
pytest fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_microscopic_expert.py fastQA/tests/test_stage2_hot_connection_runtime.py patent/tests/test_patent_stage2_controls.py patent/tests/test_patent_rerank_service.py patent/tests/test_patent_retrieval_service.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add fastQA/app/modules/generation_pipeline/stage2_retrieval.py fastQA/app/modules/qa_cache/stage2_cache.py fastQA/app/core/runtime.py fastQA/app/modules/microscopic_expert.py patent/server/patent/stage2_controls.py patent/server/patent/rerank_service.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_microscopic_expert.py fastQA/tests/test_stage2_hot_connection_runtime.py patent/tests/test_patent_stage2_controls.py patent/tests/test_patent_rerank_service.py patent/tests/test_patent_retrieval_service.py
git commit -m "refactor: unify rerank config and hardcode rerank flow"
```

---

## Task 5: Unified Embedding Config

**Files:**
- Modify: `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
- Modify: `fastQA/app/modules/microscopic_expert.py`
- Modify: `fastQA/app/modules/microscopic_runtime/bootstrap.py`
- Modify: `fastQA/app/modules/microscopic_runtime/embedding_client.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `highThinkingQA/config.py`
- Modify: `highThinkingQA/ingest/embedder.py`
- Test: `fastQA/tests/test_embedding_client.py`
- Test: `fastQA/tests/test_generation_runtime_bootstrap.py`
- Test: `fastQA/tests/test_microscopic_expert.py`
- Test: `patent/tests/test_env_loader.py`
- Test: `highThinkingQA/tests/test_config_runtime_defaults.py`
- Test: `highThinkingQA/tests/test_api_key_validation.py`

- [ ] **Step 1: Update tests for embedding target namespaces**

fastQA/patent tests use:

```env
EMBEDDING_API_KEY
EMBEDDING_BASE_URL
EMBEDDING_MODEL
EMBEDDING_MODEL_TYPE
EMBEDDING_API_URL
EMBEDDING_API_MODEL
EMBEDDING_API_TIMEOUT_SECONDS
EMBEDDING_MODEL_PATH
```

highThinkingQA tests use:

```env
HIGHTHINKINGQA_EMBEDDING_API_KEY
HIGHTHINKINGQA_EMBEDDING_BASE_URL
HIGHTHINKINGQA_EMBEDDING_MODEL
HIGHTHINKINGQA_EMBEDDING_DIMENSIONS
HIGHTHINKINGQA_EMBEDDING_BATCH_SIZE
HIGHTHINKINGQA_EMBEDDING_API_RPM
HIGHTHINKINGQA_EMBEDDING_API_TPM
HIGHTHINKINGQA_EMBEDDING_CONCURRENCY
HIGHTHINKINGQA_EMBEDDING_MAX_CONCURRENT_REQUESTS
HIGHTHINKINGQA_EMBEDDING_MAX_INPUT_TOKENS
HIGHTHINKINGQA_EMBEDDING_MAX_RETRIES
HIGHTHINKINGQA_EMBEDDING_QUEUE_SIZE
```

Tests should prove highThinkingQA prefers `HIGHTHINKINGQA_EMBEDDING_*` during migration. The final "does not read generic `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBED_BATCH_SIZE`, or `EMBED_*`" assertion belongs in Task 8 after `resource/config/**` is cleaned.

- [ ] **Step 2: Run target tests and confirm failures**

Run:

```bash
pytest fastQA/tests/test_embedding_client.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_microscopic_expert.py patent/tests/test_env_loader.py highThinkingQA/tests/test_config_runtime_defaults.py highThinkingQA/tests/test_api_key_validation.py -q
```

Expected: tests added in Step 1 fail until readers are migrated.

- [ ] **Step 3: Migrate fastQA/patent embedding reads**

fastQA and patent should resolve embedding through shared target keys. During migration, use target-first fallback:

```python
api_url = os.getenv("EMBEDDING_API_URL") or os.getenv("EMBEDDING_BASE_URL")
api_model = os.getenv("EMBEDDING_API_MODEL") or os.getenv("EMBEDDING_MODEL")
timeout = os.getenv("EMBEDDING_API_TIMEOUT_SECONDS")
```

Patent should stop preferring:

```env
PATENT_EMBEDDING_BASE_URL
PATENT_EMBEDDING_MODEL
PATENT_EMBEDDING_MODEL_TYPE
PATENT_EMBEDDING_API_URL
PATENT_EMBEDDING_API_MODEL
PATENT_EMBEDDING_API_TIMEOUT_SECONDS
```

- [ ] **Step 4: Migrate highThinkingQA embedding reads**

In `highThinkingQA/config.py`, read `HIGHTHINKINGQA_EMBEDDING_*` first for highThinkingQA embedding. Keep a temporary fallback to the existing service-local generic names until Task 8 so the code works before `resource/config/services/highThinkingQA/config.shared.env` and secret examples are renamed:

```python
embedding_api_key = os.getenv("HIGHTHINKINGQA_EMBEDDING_API_KEY") or os.getenv("EMBEDDING_API_KEY", "")
embedding_base_url = os.getenv("HIGHTHINKINGQA_EMBEDDING_BASE_URL") or os.getenv("EMBEDDING_BASE_URL", "")
embedding_model = os.getenv("HIGHTHINKINGQA_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
embedding_dimensions = _get_int_from_names(
    "HIGHTHINKINGQA_EMBEDDING_DIMENSIONS",
    "EMBEDDING_DIMENSIONS",
    default=2048,
)
```

Apply the same target-first fallback pattern for batch size, RPM/TPM, concurrency, max concurrent requests, max input tokens, retries, and queue size.

Export compatibility module constants only if existing imports require them, but the source must be the new names:

```python
EMBEDDING_API_KEY = SETTINGS.highthinkingqa_embedding_api_key
EMBEDDING_BASE_URL = SETTINGS.highthinkingqa_embedding_base_url
EMBEDDING_MODEL = SETTINGS.highthinkingqa_embedding_model
EMBEDDING_DIMENSIONS = SETTINGS.highthinkingqa_embedding_dimensions
EMBED_BATCH_SIZE = SETTINGS.highthinkingqa_embedding_batch_size
```

Update `highThinkingQA/ingest/embedder.py` error messages to reference `HIGHTHINKINGQA_EMBEDDING_API_KEY`.

- [ ] **Step 5: Run target tests**

Run:

```bash
pytest fastQA/tests/test_embedding_client.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_microscopic_expert.py patent/tests/test_env_loader.py highThinkingQA/tests/test_config_runtime_defaults.py highThinkingQA/tests/test_api_key_validation.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/modules/generation_pipeline/runtime_bootstrap.py fastQA/app/modules/microscopic_expert.py fastQA/app/modules/microscopic_runtime/bootstrap.py fastQA/app/modules/microscopic_runtime/embedding_client.py patent/server/patent/runtime.py highThinkingQA/config.py highThinkingQA/ingest/embedder.py fastQA/tests/test_embedding_client.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_microscopic_expert.py patent/tests/test_env_loader.py highThinkingQA/tests/test_config_runtime_defaults.py highThinkingQA/tests/test_api_key_validation.py
git commit -m "refactor: unify embedding config namespaces"
```

---

## Task 6: Patent Fixed Runtime Switches And Warmup Removal

**Files:**
- Modify: `patent/config.py`
- Modify: `patent/server/patent/upstream_http.py`
- Modify: `patent/server/patent/planning_hot_pool.py`
- Modify: `patent/server/patent/upstream_gate.py`
- Modify: `patent/scripts/start.sh`
- Modify: `patent/scripts/start_gunicorn.sh`
- Test: `patent/tests/test_runtime_controls.py`
- Test: `patent/tests/test_redis_runtime.py`
- Test: `patent/tests/test_patent_upstream_config.py`
- Test: `patent/tests/test_patent_upstream_http.py`
- Test: `patent/tests/test_patent_planning_hot_pool.py`
- Test: `patent/tests/test_patent_upstream_gate.py`
- Test: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Update tests for fixed patent switches**

Disabled env values should no longer disable:

```env
PATENT_REDIS_ENABLED=false
PATENT_LLM_HTTP_SHARED_POOL_ENABLED=false
PATENT_PLANNING_HOT_POOL_ENABLED=false
PATENT_PLANNING_UPSTREAM_GATE_ENABLED=false
```

Warmup should remain disabled even if env tries to enable:

```env
PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED=true
```

Retained values should still read from env:

```env
PATENT_LLM_HTTP_CONNECT_TIMEOUT_SECONDS
PATENT_LLM_HTTP_READ_TIMEOUT_SECONDS
PATENT_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS
PATENT_LLM_HTTP_WRITE_TIMEOUT_SECONDS
PATENT_LLM_HTTP_POOL_TIMEOUT_SECONDS
PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS
PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS
PATENT_LLM_HTTP_MAX_CONNECTIONS
PATENT_PLANNING_HOT_POOL_LANE_COUNT
PATENT_PLANNING_HOT_POOL_LANE_DEGRADED_AFTER_SECONDS
PATENT_PLANNING_UPSTREAM_GATE_LIMIT
```

- [ ] **Step 2: Run target tests and confirm failures**

Run:

```bash
pytest patent/tests/test_runtime_controls.py patent/tests/test_redis_runtime.py patent/tests/test_patent_upstream_config.py patent/tests/test_patent_upstream_http.py patent/tests/test_patent_planning_hot_pool.py patent/tests/test_patent_upstream_gate.py patent/tests/fastapi_contract/test_health_contract.py -q
```

Expected: tests added in Step 1 fail until code is updated.

- [ ] **Step 3: Hardcode patent switches**

In `patent/config.py` and runtime helper modules:

```python
redis.enabled = True
shared_pool_enabled = True
planning_hot_pool.enabled = True
planning_hot_pool.warmup_enabled = False
planning_upstream_gate.enabled = True
```

Do not read retired enable/warmup env keys.

- [ ] **Step 4: Keep retained patent pool/gate values env-driven**

Ensure these still read env:

```env
PATENT_REDIS_KEY_PREFIX
PATENT_LLM_HTTP_*_TIMEOUT_SECONDS
PATENT_LLM_HTTP_MAX_CONNECTIONS
PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS
PATENT_PLANNING_HOT_POOL_LANE_COUNT
PATENT_PLANNING_HOT_POOL_LANE_DEGRADED_AFTER_SECONDS
PATENT_PLANNING_UPSTREAM_GATE_LIMIT
```

Do not keep warm interval/timeout/jitter/active-window env reads once warmup is hardcoded off.

- [ ] **Step 5: Update patent startup scripts**

In `patent/scripts/start.sh` and `patent/scripts/start_gunicorn.sh`, stop exporting `PATENT_REDIS_ENABLED` from env. Redis must be assumed enabled. Keep Redis URL/host/port/password/key prefix composition.

- [ ] **Step 6: Run target tests**

Run:

```bash
pytest patent/tests/test_runtime_controls.py patent/tests/test_redis_runtime.py patent/tests/test_patent_upstream_config.py patent/tests/test_patent_upstream_http.py patent/tests/test_patent_planning_hot_pool.py patent/tests/test_patent_upstream_gate.py patent/tests/fastapi_contract/test_health_contract.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add patent/config.py patent/server/patent/upstream_http.py patent/server/patent/planning_hot_pool.py patent/server/patent/upstream_gate.py patent/scripts/start.sh patent/scripts/start_gunicorn.sh patent/tests/test_runtime_controls.py patent/tests/test_redis_runtime.py patent/tests/test_patent_upstream_config.py patent/tests/test_patent_upstream_http.py patent/tests/test_patent_planning_hot_pool.py patent/tests/test_patent_upstream_gate.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "refactor: hardcode patent mandatory runtime switches"
```

---

## Task 7: Resource Config File Cleanup

**Files:**
- Modify: `resource/config/shared/infrastructure.shared.env`
- Modify: `resource/config/shared/infrastructure.secret.env.example`
- Modify: `resource/config/shared/model-endpoints.shared.env`
- Modify: `resource/config/shared/model-endpoints.secret.env.example`
- Modify: `resource/config/shared/graph.shared.env`
- Modify: `resource/config/shared/graph.secret.env.example`
- Modify: `resource/config/services/gateway/config.shared.env`
- Modify: `resource/config/services/gateway/config.secret.env.example`
- Modify: `resource/config/services/public-service/config.shared.env`
- Modify: `resource/config/services/public-service/config.secret.env.example`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Modify: `resource/config/services/fastQA/config.env.example`
- Modify: `resource/config/services/fastQA/config.secret.env.example`
- Modify: `resource/config/services/highThinkingQA/config.shared.env`
- Modify: `resource/config/services/highThinkingQA/config.env.example`
- Modify: `resource/config/services/highThinkingQA/config.secret.env.example`
- Modify: `resource/config/services/patent/config.shared.env`
- Modify: `patent/config.shared.env.example`
- Test: `fastQA/tests/test_env_loader.py`
- Test: `gateway/tests/test_config_env_loader.py`
- Test: `highThinkingQA/tests/test_env_loader.py`
- Test: `patent/tests/test_env_loader.py`
- Test: `patent/tests/test_patent_graph_kb_config.py`

- [ ] **Step 1: Update env-loader and config-file tests**

Update tests to expect the final config surface:

Retired keys absent:

```env
REDIS_ENABLED
MINIO_USE_PROXY
GATEWAY_ADMISSION_ENABLED
GATEWAY_ADMISSION_DISPATCHER_ENABLED
GATEWAY_ADMISSION_WORKER_ENABLED
OPENAI_*
DASHSCOPE_*
PATENT_OPENAI_*
PATENT_EMBEDDING_*
QA_RETRIEVAL_RERANK_* endpoint aliases
PATENT_STAGE2_RERANK_* endpoint aliases
OCR_*
*_WARMUP_ENABLED
*_WARM_INTERVAL_SECONDS
*_WARM_TIMEOUT_SECONDS
*_WARM_JITTER_SECONDS
*_WARM_ACTIVE_*
```

Retained keys present:

```env
LLM_*
RERANK_*
EMBEDDING_* / EMBEDDING_API_* for fastQA + patent
HIGHTHINKINGQA_EMBEDDING_* for highThinkingQA
MYSQL_*
MINIO_*
REDIS_* connection keys
NEO4J_* and service-prefixed graph keys
INTERACTIVE_* capacity keys
ASK_STREAM_MAX_CONCURRENT
PATENT_ASK_STREAM_MAX_CONCURRENT
```

- [ ] **Step 2: Run config tests and confirm failures**

Run:

```bash
pytest fastQA/tests/test_env_loader.py gateway/tests/test_config_env_loader.py highThinkingQA/tests/test_env_loader.py patent/tests/test_env_loader.py patent/tests/test_patent_graph_kb_config.py -q
```

Expected: tests added in Step 1 fail until config files are cleaned.

- [ ] **Step 3: Clean shared infrastructure config**

In `resource/config/shared/infrastructure.shared.env`, remove:

```env
REDIS_ENABLED
MINIO_USE_PROXY
```

Keep:

```env
*_HOST
*_PORT
*_BACKEND_BASE_URL
REDIS_HOST
REDIS_PORT
REDIS_DB
REDIS_SOCKET_CONNECT_TIMEOUT_SEC
REDIS_SOCKET_TIMEOUT_SEC
MYSQL_*
MINIO_BUCKET
MINIO_SECURE
MINIO_DOWNLOAD_EXPIRES
```

- [ ] **Step 4: Clean shared model endpoint config**

In `resource/config/shared/model-endpoints.shared.env`, keep only target namespaces:

```env
LLM_BASE_URL
LLM_MODEL
LLM_CONNECT_TIMEOUT_SECONDS
LLM_READ_TIMEOUT_SECONDS
LLM_STREAM_READ_TIMEOUT_SECONDS
LLM_WRITE_TIMEOUT_SECONDS
LLM_POOL_TIMEOUT_SECONDS
LLM_KEEPALIVE_EXPIRY_SECONDS
LLM_MAX_CONNECTIONS
LLM_MAX_KEEPALIVE_CONNECTIONS
EMBEDDING_BASE_URL
EMBEDDING_MODEL
EMBEDDING_MODEL_TYPE
EMBEDDING_API_URL
EMBEDDING_API_MODEL
EMBEDDING_API_TIMEOUT_SECONDS
RERANK_PROVIDER
RERANK_BASE_URL
RERANK_MODEL
RERANK_TIMEOUT_SECONDS
```

Remove aliases and OCR.

- [ ] **Step 5: Clean shared secret examples**

In `resource/config/shared/model-endpoints.secret.env.example`, keep:

```env
LLM_API_KEY
EMBEDDING_API_KEY
RERANK_API_KEY
```

Remove OpenAI/DashScope/rerank alias/OCR placeholders.

In `resource/config/shared/infrastructure.secret.env.example`, keep infra secret placeholders only.

- [ ] **Step 6: Clean service config files**

Apply the file-level layout from the spec:

- gateway: remove admission switches, keep admission capacity/TTL/poll keys.
- public-service: remove `UPLOAD_FILE_PROCESSING_ENABLED`, keep upload worker sizing.
- fastQA: remove warmup, PDF model alias, sidecar switch, rerank API key alias; keep sidecar endpoint/timeout and retrieval scale.
- highThinkingQA: remove service-local LLM model/thinking aliases, OCR, chat persist switches; rename embedding keys to `HIGHTHINKINGQA_EMBEDDING_*`; keep chat worker and auth/path/runtime/cache values.
- patent: remove fixed switches and old model/rerank/embedding aliases; keep retained pool/gate sizing and retrieval scale.

- [ ] **Step 7: Run config tests**

Run:

```bash
pytest fastQA/tests/test_env_loader.py gateway/tests/test_config_env_loader.py highThinkingQA/tests/test_env_loader.py patent/tests/test_env_loader.py patent/tests/test_patent_graph_kb_config.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add resource/config/shared/infrastructure.shared.env resource/config/shared/infrastructure.secret.env.example resource/config/shared/model-endpoints.shared.env resource/config/shared/model-endpoints.secret.env.example resource/config/shared/graph.shared.env resource/config/shared/graph.secret.env.example resource/config/services/gateway/config.shared.env resource/config/services/gateway/config.secret.env.example resource/config/services/public-service/config.shared.env resource/config/services/public-service/config.secret.env.example resource/config/services/fastQA/config.shared.env resource/config/services/fastQA/config.env.example resource/config/services/fastQA/config.secret.env.example resource/config/services/highThinkingQA/config.shared.env resource/config/services/highThinkingQA/config.env.example resource/config/services/highThinkingQA/config.secret.env.example resource/config/services/patent/config.shared.env patent/config.shared.env.example fastQA/tests/test_env_loader.py gateway/tests/test_config_env_loader.py highThinkingQA/tests/test_env_loader.py patent/tests/test_env_loader.py patent/tests/test_patent_graph_kb_config.py
git commit -m "chore: simplify resource config surface"
```

---

## Task 8: Remove Temporary Fallback Reads And Update Documentation

**Files:**
- Modify: all files touched in Tasks 3-5 that still read old aliases.
- Modify: docs that mention retired config keys where they are current guidance, not historical notes.
- Test: same focused test suites from Tasks 3-7.

- [ ] **Step 1: Grep old keys after config cleanup**

Run:

```bash
rg -n "OPENAI_|DASHSCOPE_|PATENT_OPENAI_|PATENT_EMBEDDING_|QA_RETRIEVAL_RERANK_(API_KEY|PROVIDER|BASE_URL|MODEL|TIMEOUT)|PATENT_STAGE2_RERANK_(API_KEY|PROVIDER|BASE_URL|MODEL|TIMEOUT_SECONDS|ENDPOINT_FAMILY)|OCR_|REDIS_ENABLED|PATENT_REDIS_ENABLED|MINIO_USE_PROXY|CHAT_PERSIST_ENABLED|CHAT_PERSIST_ASYNC=|UPLOAD_FILE_PROCESSING_ENABLED|UPLOAD_QA_USE_SIDECAR|GATEWAY_ADMISSION_(ENABLED|DISPATCHER_ENABLED|WORKER_ENABLED)|PATENT_LLM_HTTP_SHARED_POOL_ENABLED|PATENT_PLANNING_(HOT_POOL_ENABLED|UPSTREAM_GATE_ENABLED)|FASTQA_GRAPH_KB(_V2|_RAG_INJECTION)?_ENABLED|QA_RETRIEVAL_RERANK_ENABLED|PATENT_STAGE2_RERANK_ENABLED|WARMUP_ENABLED|WARM_INTERVAL|WARM_TIMEOUT|WARM_JITTER|WARM_ACTIVE|BOOTSTRAP_WARM" resource gateway public-service fastQA highThinkingQA patent scripts
```

Expected: production code/config should have no runtime dependency on retired keys. Tests may mention retired keys only when asserting they are ignored or absent.

Also grep for retired non-prefix aliases that the broad pattern above does not catch:

```bash
rg -n "LLM_PROVIDER|LLM_ENABLE_THINKING|DIRECT_ANSWER_ENABLE_THINKING|DECOMPOSE_ENABLE_THINKING|DECOMPOSE_MODEL|DIRECT_ANSWER_MODEL|SUB_ANSWER_MODEL|CHECKER_MODEL|QUERY_EXPANSION_MODEL|PDF_QA_USE_DEDICATED_LLM|PDF_QA_MODEL|EMBED_BATCH_SIZE|EMBED_API_RPM|EMBED_API_TPM|EMBED_CONCURRENCY|EMBED_MAX_CONCURRENT_REQUESTS|EMBED_MAX_INPUT_TOKENS|EMBED_MAX_RETRIES|EMBED_QUEUE_SIZE" resource gateway public-service fastQA highThinkingQA patent scripts
```

Expected: production code/config should have no runtime dependency on these retired aliases. Tests may mention them only when asserting they are ignored or absent.

- [ ] **Step 2: Remove model/rerank/embedding alias fallback reads**

After `resource/config/**` no longer uses old keys, remove fallback reads for:

```env
OPENAI_*
DASHSCOPE_*
PATENT_OPENAI_*
LLM_PROVIDER
LLM_ENABLE_THINKING
DIRECT_ANSWER_ENABLE_THINKING
DECOMPOSE_ENABLE_THINKING
DECOMPOSE_MODEL
DIRECT_ANSWER_MODEL
SUB_ANSWER_MODEL
CHECKER_MODEL
QUERY_EXPANSION_MODEL
PDF_QA_USE_DEDICATED_LLM
PDF_QA_MODEL
QA_RETRIEVAL_RERANK_* endpoint aliases
PATENT_STAGE2_RERANK_* endpoint aliases
PATENT_EMBEDDING_*
EMBEDDING_TIMEOUT_SECONDS
highThinkingQA legacy EMBED_* service-local aliases
```

Remove fastQA warmup-only reads from `fastQA/app/core/config.py` as part of final cleanup. This includes `FASTQA_STAGE2_CHAT_WARM_INTERVAL_SECONDS`, `FASTQA_STAGE2_RERANK_WARM_INTERVAL_SECONDS`, `FASTQA_STAGE2_CHAT_WARM_TIMEOUT_SECONDS`, `FASTQA_STAGE2_RERANK_WARM_TIMEOUT_SECONDS`, `FASTQA_STAGE2_WARM_JITTER_SECONDS`, `FASTQA_STAGE2_BOOTSTRAP_WARM_MAX_PARALLEL`, `FASTQA_STAGE2_BOOTSTRAP_WARM_JITTER_SECONDS`, `FASTQA_STAGE2_WARM_ACTIVE_START_HOUR`, and `FASTQA_STAGE2_WARM_ACTIVE_END_HOUR`.

Replace old patent single-timeout reads with `LLM_READ_TIMEOUT_SECONDS` for one-value client timeout call sites. Where code uses structured HTTP timeout values, keep `LLM_CONNECT_TIMEOUT_SECONDS`, `LLM_READ_TIMEOUT_SECONDS`, `LLM_STREAM_READ_TIMEOUT_SECONDS`, `LLM_WRITE_TIMEOUT_SECONDS`, and `LLM_POOL_TIMEOUT_SECONDS` as the only LLM timeout inputs. Do not retain `PATENT_OPENAI_TIMEOUT_SECONDS` after this step.

Remove highThinkingQA embedding fallback reads for the old generic service-local names, including `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, and `EMBEDDING_DIMENSIONS`, plus the legacy throughput aliases listed above. After this step, highThinkingQA embedding reads only `HIGHTHINKINGQA_EMBEDDING_*`.

Keep only target names from the spec.

- [ ] **Step 3: Update current docs**

Update docs that describe current config, not historical implementation notes. Do not rewrite historical design docs unless they are used as current operator guidance.

At minimum, update:

- [docs/config/2026-05-10-resource-config-code-map.md](/home/cqy/worktrees/highThinking/docs/config/2026-05-10-resource-config-code-map.md)
- [docs/superpowers/specs/2026-05-11-resource-config-simplification-spec.md](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-05-11-resource-config-simplification-spec.md), if implementation reveals a spec correction

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest gateway/tests/test_config.py gateway/tests/test_execution_admission.py gateway/tests/test_admission_worker_scripts.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_microscopic_expert.py fastQA/tests/test_qa_pdf_llm_factory.py fastQA/tests/test_embedding_client.py highThinkingQA/tests/test_config_runtime_defaults.py highThinkingQA/tests/test_api_key_validation.py highThinkingQA/tests/test_env_loader.py public-service/backend/tests/test_documents_module.py patent/tests/test_env_loader.py patent/tests/test_patent_stage2_controls.py patent/tests/test_patent_rerank_service.py patent/tests/test_patent_upstream_config.py patent/tests/test_patent_planning_hot_pool.py public-service/backend/tests/test_uploads_module.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Run startup/status sanity check**

Run:

```bash
bash scripts/status_all.sh
```

Expected: command exits successfully and does not require removed env switches.

- [ ] **Step 6: Commit**

```bash
git add gateway public-service fastQA highThinkingQA patent scripts resource/config docs
git commit -m "refactor: remove retired config fallback reads"
```

---

## Task 9: Final Full Verification

**Files:**
- No new source edits unless verification exposes a defect.

- [ ] **Step 1: Run final retired-key grep**

Run:

```bash
rg -n "OPENAI_|DASHSCOPE_|PATENT_OPENAI_|PATENT_EMBEDDING_|QA_RETRIEVAL_RERANK_(API_KEY|PROVIDER|BASE_URL|MODEL|TIMEOUT)|PATENT_STAGE2_RERANK_(API_KEY|PROVIDER|BASE_URL|MODEL|TIMEOUT_SECONDS|ENDPOINT_FAMILY)|OCR_|REDIS_ENABLED|PATENT_REDIS_ENABLED|MINIO_USE_PROXY|CHAT_PERSIST_ENABLED|CHAT_PERSIST_ASYNC=|UPLOAD_FILE_PROCESSING_ENABLED|UPLOAD_QA_USE_SIDECAR|GATEWAY_ADMISSION_(ENABLED|DISPATCHER_ENABLED|WORKER_ENABLED)|PATENT_LLM_HTTP_SHARED_POOL_ENABLED|PATENT_PLANNING_(HOT_POOL_ENABLED|UPSTREAM_GATE_ENABLED)|FASTQA_GRAPH_KB(_V2|_RAG_INJECTION)?_ENABLED|QA_RETRIEVAL_RERANK_ENABLED|PATENT_STAGE2_RERANK_ENABLED|WARMUP_ENABLED|WARM_INTERVAL|WARM_TIMEOUT|WARM_JITTER|WARM_ACTIVE|BOOTSTRAP_WARM" resource gateway public-service fastQA highThinkingQA patent scripts
```

Expected: no production code/config dependency on retired keys.

Run:

```bash
rg -n "LLM_PROVIDER|LLM_ENABLE_THINKING|DIRECT_ANSWER_ENABLE_THINKING|DECOMPOSE_ENABLE_THINKING|DECOMPOSE_MODEL|DIRECT_ANSWER_MODEL|SUB_ANSWER_MODEL|CHECKER_MODEL|QUERY_EXPANSION_MODEL|PDF_QA_USE_DEDICATED_LLM|PDF_QA_MODEL|EMBED_BATCH_SIZE|EMBED_API_RPM|EMBED_API_TPM|EMBED_CONCURRENCY|EMBED_MAX_CONCURRENT_REQUESTS|EMBED_MAX_INPUT_TOKENS|EMBED_MAX_RETRIES|EMBED_QUEUE_SIZE" resource gateway public-service fastQA highThinkingQA patent scripts
```

Expected: no production code/config dependency on retired non-prefix aliases.

- [ ] **Step 2: Run retained-key grep**

Run:

```bash
rg -n "LLM_BASE_URL|RERANK_BASE_URL|EMBEDDING_API_URL|HIGHTHINKINGQA_EMBEDDING_BASE_URL|MYSQL_HOST|MINIO_ENDPOINT|REDIS_HOST|NEO4J_URL|INTERACTIVE_EXECUTION_MAX_CONCURRENT|ASK_STREAM_MAX_CONCURRENT" resource/config
```

Expected: retained target keys exist.

- [ ] **Step 3: Run backend test suites**

Run:

```bash
pytest gateway/tests public-service/backend/tests fastQA/tests highThinkingQA/tests patent/tests -q
```

Expected: all pass, or unrelated pre-existing failures are documented with exact failing tests and output.

- [ ] **Step 4: Run status sanity**

Run:

```bash
bash scripts/status_all.sh
```

Expected: status command completes and no removed env switch is required.

- [ ] **Step 5: Record verification result**

Update the final PR/commit notes with:

1. commands run
2. pass/fail result
3. any skipped commands with reason
4. any remaining open decisions from the spec

- [ ] **Step 6: Commit final docs if needed**

```bash
git add docs
git commit -m "docs: record config simplification verification"
```

---

## Open Decisions Before Execution

Do not resolve these silently during implementation:

1. Whether `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED` should become mandatory and be removed from config.
2. Whether `PATENT_STAGE2_CONVERGENCE_ENABLED`, `PATENT_STAGE2_VALIDATION_ENABLED`, `PATENT_STAGE2_C_*`, `PATENT_DURABLE_MODE_ENABLED`, and `PATENT_DURABLE_AUTHORITY_ENABLED` are fixed mainline behavior or retained business switches.
3. Whether fastQA generation switches such as `QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED`, `QA_STAGE35_EVIDENCE_RERANK_ENABLED`, `QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED`, citation verification toggles, and structure mode toggles are fixed behavior or retained business switches.
4. Whether app/debug/CORS/logging/gunicorn values should move out of `resource/config` in a later pass.

If implementation reaches one of these decisions, stop and ask before deleting or hardcoding it.
