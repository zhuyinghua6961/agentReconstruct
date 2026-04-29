# Config Layer Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate shared infrastructure/model/graph configuration, retire stable migration-era feature flags by making core capabilities always-on, and keep only service-specific behavior knobs in service config files.

**Architecture:** Use `resource/config/shared` as the single source for public infrastructure, model endpoint, and graph connection configuration. Keep service configs under `resource/config/services/<service>` for behavior, capacity, paths, cache/stage tuning, and service-local secrets only. Preserve a compatibility window for legacy variable names while tests prove each service resolves the new layered config correctly.

**Tech Stack:** Bash env loading, Python dataclass settings modules, FastAPI services (`gateway`, `public-service`, `fastQA`, `highThinkingQA`, `patent`), pytest.

---

## Source Spec

- Primary checklist: `docs/config/2026-04-29-config-cleanup-checklist.md`
- Relevant loaders/config files:
  - `scripts/env_file_loader.sh`
  - `gateway/app/core/config.py`
  - `public-service/backend/app/core/env_loader.py`
  - `public-service/backend/app/core/config.py`
  - `fastQA/app/core/env_loader.py`
  - `fastQA/app/core/config.py`
  - `fastQA/app/core/runtime.py`
  - `fastQA/app/routers/qa.py`
  - `fastQA/app/routers/health.py`
  - `highThinkingQA/env_loader.py`
  - `highThinkingQA/config.py`
  - `patent/config.py`

## Implementation Policy

- Do not print or commit real secret values.
- Do not remove legacy env compatibility in the first implementation pass unless a task explicitly says so.
- Env files must be loaded from low precedence to high precedence because the current dotenv merge style lets later file values win. Required effective precedence is:
  1. process environment and explicit `*_ENV_FILES` values,
  2. resource service local override: `resource/config/services/<service>/config.env`,
  3. resource service secrets and local `.env`,
  4. resource service shared config,
  5. shared infrastructure/model/graph secret config,
  6. shared infrastructure/model/graph public config,
  7. legacy root/service-dir fallback files.
- Implementation detail: return legacy files first, then shared files, then resource service files, with `config.env` last if it is intended as local override. Add tests proving legacy files cannot override resource config.
- Treat `*.secret.env.example` files as commit-safe templates.
- Treat real `*.secret.env` and `.env` files as local-only. Do not include them in `git add` commands in this plan. If a currently tracked real secret file needs local cleanup, document it as a manual local step and do not commit it unless the user explicitly authorizes that exact file.
- If tests hang or need service/network access, run them escalated rather than repeatedly retrying in sandbox.

## Variable Migration Table

| Old key / location | New owner | Compatibility alias | Removal phase |
| --- | --- | --- | --- |
| `APP_PORT`, `BACKEND_PORT`, `FASTAPI_PORT` for fastQA | `resource/config/shared/infrastructure.shared.env` as `FASTQA_PORT` / `FASTQA_FASTAPI_PORT` | Keep old names for one release. | Remove from service examples after health/startup tests pass. |
| `APP_PORT` for highThinkingQA | `resource/config/shared/infrastructure.shared.env` as `HIGHTHINKINGQA_PORT` | Keep `APP_PORT` fallback. | Remove from service examples after startup tests pass. |
| `NEO4J_URL`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | `resource/config/shared/graph.shared.env` and local `graph.secret.env` as service-namespaced keys | Keep legacy global keys as fallback. | Remove global keys after all consumers use namespaced settings. |
| `PATENT_OPENAI_*`, fastQA `OPENAI_*` / `DASHSCOPE_*`, highThinkingQA `LLM_*` duplicates | `resource/config/shared/model-endpoints.shared.env` / `model-endpoints.secret.env` | Keep service-specific override aliases. | Remove service duplicates only after Task 8 tests prove shared resolution. |
| `FASTQA_GRAPH_KB_ENABLED`, `FASTQA_GRAPH_KB_V2_ENABLED`, `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED` | Code default always-on; no env owner | Deprecated aliases ignored for runtime disable and may log warning. | Remove from env examples immediately after code default changes. |
| `PATENT_GRAPH_KB_ENABLED`, `PATENT_GRAPH_KB_V2_ENABLED`, `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED` | Code default always-on; no env owner | Deprecated aliases ignored for runtime disable and may log warning. | Remove from env examples immediately after code default changes. |

## Target File Structure

### Shared Config Files

- Modify: `resource/config/shared/infrastructure.shared.env`
  - Add service ports, gateway backend URLs, and non-secret infrastructure defaults.
- Create: `resource/config/shared/model-endpoints.secret.env.example`
  - Document model API key names without values.
- Modify: `resource/config/shared/model-endpoints.shared.env`
  - Normalize unified `LLM_*`, `EMBEDDING_*`, `RERANK_*`, `OCR_*` endpoint/model/timeout keys.
- Create: `resource/config/shared/graph.shared.env`
  - Add non-secret graph connection keys for fastQA, patent, and public-service.
- Create: `resource/config/shared/graph.secret.env.example`
  - Document graph password keys without values.
- Modify: `resource/config/shared/README.md`
  - Explain layer responsibility and secret policy.

### Service Config Files

- Modify: `resource/config/services/gateway/config.shared.env`
  - Keep gateway behavior, admission, route classifier, and capacity config.
- Create or modify: `resource/config/services/gateway/config.secret.env.example`
  - Document gateway secret placeholders only.
- Local-only manual note: `resource/config/services/gateway/config.secret.env` currently contains non-secret behavior toggles, but this plan must not commit changes to the real secret file.
- Modify: `resource/config/services/fastQA/config.shared.env`
  - Keep fastQA stage/cache/vector/file-qa behavior config.
  - Remove public model endpoint duplication after code supports shared `LLM_*`.
- Modify: `resource/config/services/fastQA/config.env.example`
  - Make example match always-on graph defaults and remove obsolete toggles.
- Modify: `resource/config/services/fastQA/config.secret.env.example`
  - Remove non-secret graph toggles; keep only secret placeholders.
- Modify: `resource/config/services/highThinkingQA/config.shared.env`
  - Keep thinking-specific chunk/retrieval/cache/concurrency/path config.
  - Remove duplicated public model endpoint defaults after shared model config is active.
- Modify: `resource/config/services/highThinkingQA/config.secret.env.example`
  - Keep service-local secrets only if any remain.
- Modify: `resource/config/services/patent/config.shared.env`
  - Add patent graph behavior tuning, tabular/hybrid/stage4 evidence knobs.
  - Keep patent behavior knobs, not model endpoint secrets.
- Modify: `patent/config.shared.env.example`
  - Bring example into the same shape as resource service config.
- Create: `resource/config/services/public-service/config.shared.env`
  - Move commit-safe public-service behavior config from root `public-service/config.shared.env`.
- Create: `resource/config/services/public-service/config.secret.env.example`
  - Add public-service secret template.
- Modify: `public-service/config.shared.env`
  - Mark as legacy or reduce to comments after resource config works.

### Loader And Settings Files

- Modify: `fastQA/app/core/env_loader.py`
  - Load new shared files: `model-endpoints.secret.env`, `graph.shared.env`, `graph.secret.env`.
- Modify: `highThinkingQA/env_loader.py`
  - Same shared file list as fastQA.
- Modify: `patent/config.py`
  - Add resource-aware layered env loading equivalent to fastQA/highThinkingQA.
  - Preserve existing `patent/.env` local override.
- Modify: `public-service/backend/app/core/env_loader.py`
  - Add resource-aware layered env loading.
  - Preserve explicit `PUBLIC_SERVICE_ENV_FILES` as highest-precedence explicit path behavior.
- Modify: `gateway/app/core/config.py`
  - Add env loading or ensure scripts always inject layered config before settings are read.
  - Prefer adding `gateway/app/core/env_loader.py` if tests show direct import currently misses resource env files.
- Modify: service start scripts as needed:
  - `gateway/scripts/start_gunicorn.sh`
  - `gateway/scripts/start_admission_worker.sh`
  - `public-service/scripts/start_gunicorn.sh`
  - `patent/scripts/start_gunicorn.sh`
  - `fastQA/scripts/start_gunicorn.sh`
  - `highThinkingQA/scripts/start_fastapi_gunicorn.sh`
  - `scripts/_service_common.sh`

### Tests

- Modify/Create:
  - `fastQA/tests/test_env_loader.py`
  - `fastQA/tests/test_graph_kb_runtime.py`
  - `fastQA/tests/test_fastqa_kb_graph_integration.py`
  - `gateway/tests/test_config.py` or existing gateway config tests
  - `public-service/backend/tests/test_env_loader.py`
  - `public-service/backend/tests/test_config_independence.py`
  - `highThinkingQA/tests/test_config_runtime_defaults.py`
  - `patent/tests/test_patent_graph_kb_config.py`
  - `patent/tests/fastapi_contract/test_health_contract.py`

---

## Task 1: Add Failing Tests For Shared Config Layering

**Files:**
- Modify: `fastQA/tests/test_env_loader.py`
- Create or modify: `highThinkingQA/tests/test_env_loader.py`
- Create or modify: `patent/tests/test_env_loader.py`
- Create or modify: `public-service/backend/tests/test_env_loader.py`
- Create or modify: `gateway/tests/test_config_env_loader.py`
- Modify: `highThinkingQA/tests/test_stage_cache_ttl_contract.py`
- Modify: `highThinkingQA/tests/test_stage_cache_runtime.py`
- Modify: `public-service/backend/tests/test_quota_module.py`
- Modify: `public-service/backend/tests/test_conversation_module.py`

- [ ] **Step 1: Add fastQA shared file order test**

Add a test that creates a temporary resource tree with:

```text
resource/config/shared/infrastructure.shared.env
resource/config/shared/model-endpoints.shared.env
resource/config/shared/model-endpoints.secret.env
resource/config/shared/graph.shared.env
resource/config/shared/graph.secret.env
resource/config/services/fastQA/config.shared.env
resource/config/services/fastQA/config.secret.env
```

Assert `iter_workspace_env_files()` returns files in this order:

```python
[
    "<legacy>/config.shared.env",
    "<legacy>/config.secret.env",
    "resource/config/shared/infrastructure.shared.env",
    "resource/config/shared/model-endpoints.shared.env",
    "resource/config/shared/infrastructure.secret.env",
    "resource/config/shared/model-endpoints.secret.env",
    "resource/config/shared/graph.shared.env",
    "resource/config/shared/graph.secret.env",
    "resource/config/services/fastQA/config.shared.env",
    "resource/config/services/fastQA/config.secret.env",
    "resource/config/services/fastQA/.env",
    "resource/config/services/fastQA/config.env",
]
```

Expected failure before implementation: new shared files are missing from loader order and legacy files may still be last.

- [ ] **Step 1b: Add precedence regression test**

In the same test, set the same key in three files:

```env
# legacy config.secret.env
FASTQA_GRAPH_KB_TIMEOUT_MS=111

# resource/config/shared/graph.shared.env
FASTQA_GRAPH_KB_TIMEOUT_MS=222

# resource/config/services/fastQA/config.env
FASTQA_GRAPH_KB_TIMEOUT_MS=333
```

Call `load_workspace_env(override_existing=False)` and assert the effective value is `333`. Then set process env `FASTQA_GRAPH_KB_TIMEOUT_MS=444` before loading and assert the effective value remains `444`.

Expected failure before implementation: legacy may override resource, or process env precedence may be unclear.

- [ ] **Step 2: Add highThinkingQA shared file order test**

Mirror the fastQA test for `highThinkingQA/env_loader.py`.

Expected failure before implementation: new shared files are missing from loader order.

- [ ] **Step 2b: Add highThinkingQA precedence regression test**

Mirror Step 1b with `RETRIEVAL_TOP_K` or `HT_QA_CACHE_EPOCH`.

Expected failure before implementation: legacy/service precedence is not explicitly protected.

- [ ] **Step 3: Add patent resource config load test**

Create a temp repo/resource layout and assert `patent/config.py` can load:

```env
# resource/config/shared/infrastructure.shared.env
PATENT_PORT=19010

# resource/config/shared/model-endpoints.shared.env
LLM_BASE_URL=http://llm.test/v1
LLM_MODEL=shared-model

# resource/config/shared/graph.shared.env
PATENT_NEO4J_URL=bolt://graph.test:8687
PATENT_NEO4J_DATABASE=neo4j

# resource/config/services/patent/config.shared.env
PATENT_STAGE4_MIN_CITATIONS=9
```

Assert:

```python
settings.http.port == 19010
settings.graph_kb.neo4j_url == "bolt://graph.test:8687"
```

Expected failure before implementation: patent only loads files from `patent/`.

- [ ] **Step 4: Add public-service resource config load test**

Set `RESOURCE_ROOT` to a temp resource tree. Assert public-service loads:

```env
PUBLIC_SERVICE_PORT=18102
REDIS_KEY_PREFIX=public_service_test
```

from `resource/config/services/public-service/config.shared.env` without requiring `PUBLIC_SERVICE_ENV_FILES`.

Expected failure before implementation: public-service defaults to no dotenv loading.

- [ ] **Step 5: Add gateway direct settings load test**

Assert importing `GatewaySettings.from_env()` after setting `RESOURCE_ROOT` can see:

```env
GATEWAY_PORT=18101
FAST_BACKEND_BASE_URL=http://127.0.0.1:18008
```

from resource config.

Expected failure before implementation: gateway has no Python env loader.

- [ ] **Step 5b: Add cache-default tests**

Add or update targeted tests proving cache is default-on via TTL/version knobs, not via hidden disabled flags:

- fastQA: `fastQA/tests/test_qa_cache_stage1.py`, `fastQA/tests/test_qa_cache_stage2.py`, `fastQA/tests/test_qa_cache_stage25.py`, `fastQA/tests/test_qa_cache_stage3.py`
  - assert default TTL values are positive;
  - assert `QA_CACHE_EPOCH`, `QA_STAGE1_GRAPH_CACHE_VERSION`, and `QA_STAGE2_GRAPH_CACHE_VERSION` participate in cache keys.
- highThinkingQA: update `highThinkingQA/tests/test_stage_cache_ttl_contract.py` and `highThinkingQA/tests/test_stage_cache_runtime.py` for `HT_QA_CACHE_EPOCH`, `HT_QA_*_CACHE_TTL_SECONDS`, and `HT_QA_CACHE_LOCK_ENABLED`.
- public-service: update `public-service/backend/tests/test_quota_module.py` and `public-service/backend/tests/test_conversation_module.py` for `QUOTA_*_CACHE_TTL_SECONDS` and `CONVERSATION_*_CACHE_TTL_SECONDS` defaults.

Expected failure before implementation only if existing code has hidden cache-disable defaults or missing TTL/version coverage.

- [ ] **Step 6: Run tests and verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_env_loader.py -q
conda run -n agent pytest highThinkingQA/tests/test_env_loader.py -q
conda run -n agent pytest patent/tests/test_env_loader.py -q
conda run -n agent pytest public-service/backend/tests/test_env_loader.py -q
conda run -n agent pytest gateway/tests/test_config_env_loader.py -q
conda run -n agent pytest fastQA/tests/test_qa_cache_stage1.py fastQA/tests/test_qa_cache_stage2.py fastQA/tests/test_qa_cache_stage25.py fastQA/tests/test_qa_cache_stage3.py -q
conda run -n agent pytest highThinkingQA/tests/test_stage_cache_ttl_contract.py highThinkingQA/tests/test_stage_cache_runtime.py -q
conda run -n agent pytest public-service/backend/tests/test_quota_module.py public-service/backend/tests/test_conversation_module.py -q
```

Expected: tests fail for the new behavior only.

- [ ] **Step 7: Commit tests**

```bash
git add fastQA/tests/test_env_loader.py highThinkingQA/tests/test_env_loader.py patent/tests/test_env_loader.py public-service/backend/tests/test_env_loader.py gateway/tests/test_config_env_loader.py fastQA/tests/test_qa_cache_stage1.py fastQA/tests/test_qa_cache_stage2.py fastQA/tests/test_qa_cache_stage25.py fastQA/tests/test_qa_cache_stage3.py highThinkingQA/tests/test_stage_cache_ttl_contract.py highThinkingQA/tests/test_stage_cache_runtime.py public-service/backend/tests/test_quota_module.py public-service/backend/tests/test_conversation_module.py
git commit -m "test: define unified config layering"
```

---

## Task 2: Implement Unified Resource-Aware Env Loading

**Files:**
- Modify: `fastQA/app/core/env_loader.py`
- Modify: `highThinkingQA/env_loader.py`
- Modify: `patent/config.py`
- Modify: `public-service/backend/app/core/env_loader.py`
- Create: `gateway/app/core/env_loader.py`
- Modify: `gateway/app/core/config.py`

- [ ] **Step 1: Extend fastQA shared file list**

Change `SHARED_CONFIG_FILENAMES` in `fastQA/app/core/env_loader.py` to:

```python
SHARED_CONFIG_FILENAMES = (
    "infrastructure.shared.env",
    "model-endpoints.shared.env",
    "infrastructure.secret.env",
    "model-endpoints.secret.env",
    "graph.shared.env",
    "graph.secret.env",
)
```

- [ ] **Step 2: Extend highThinkingQA shared file list**

Make the same change in `highThinkingQA/env_loader.py`.

- [ ] **Step 3: Add patent resource-aware loading**

In `patent/config.py`, replace `_DEFAULT_ENV_FILES`-only loading with a small resource-aware resolver that:

- preserves process env precedence using `_INITIAL_ENV_KEYS`;
- returns file paths from low precedence to high precedence;
- loads legacy local `patent/config.shared.env`, `patent/config.secret.env`, `patent/.env` first as fallback only;
- then loads `resource/config/shared` public and secret files;
- then loads `resource/config/services/patent/config.shared.env`, `config.secret.env`, `.env`;
- loads `resource/config/services/patent/config.env` last as the local override file;
- keeps explicit process env highest priority.

Keep `_load_env_file()` semantics: do not override process env keys.

- [ ] **Step 4: Add public-service resource-aware loading**

In `public-service/backend/app/core/env_loader.py`:

- keep `PUBLIC_SERVICE_ENV_FILE` / `PUBLIC_SERVICE_ENV_FILES` as explicit override mode;
- if explicit env files are absent, resolve `RESOURCE_ROOT` or repo `resource`;
- return file paths from low precedence to high precedence;
- load legacy `public-service/config.shared.env` and `public-service/config.secret.env` first as fallback only;
- load shared files next;
- load `resource/config/services/public-service/config.shared.env`, `config.secret.env`, `.env`;
- load `resource/config/services/public-service/config.env` last as local override.

- [ ] **Step 5: Add gateway env loader**

Create `gateway/app/core/env_loader.py` using the same pattern:

```python
SERVICE_CODE = "GATEWAY"
SERVICE_NAME = "gateway"
DEFAULT_ENV_FILENAMES = ("config.env", "config.shared.env", "config.secret.env", ".env")
SHARED_CONFIG_FILENAMES = (
    "infrastructure.shared.env",
    "model-endpoints.shared.env",
    "infrastructure.secret.env",
    "model-endpoints.secret.env",
    "graph.shared.env",
    "graph.secret.env",
)
```

Support explicit `GATEWAY_ENV_FILE`, `GATEWAY_ENV_FILES`, `SERVICE_ENV_FILE`, `SERVICE_ENV_FILES`.

- [ ] **Step 6: Load gateway env before settings**

At the top of `gateway/app/core/config.py`, import and call:

```python
from gateway.app.core.env_loader import load_workspace_env

load_workspace_env(override_existing=False)
```

If import package path differs in tests, use the local project import pattern already used in gateway.

- [ ] **Step 7: Run config loader tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_env_loader.py highThinkingQA/tests/test_env_loader.py patent/tests/test_env_loader.py public-service/backend/tests/test_env_loader.py gateway/tests/test_config_env_loader.py -q
```

Expected: PASS, including precedence tests proving process env wins, resource service config beats legacy fallback, and `config.env` is the highest-precedence env-file override.

- [ ] **Step 8: Commit loader changes**

```bash
git add fastQA/app/core/env_loader.py highThinkingQA/env_loader.py patent/config.py public-service/backend/app/core/env_loader.py gateway/app/core/env_loader.py gateway/app/core/config.py fastQA/tests/test_env_loader.py highThinkingQA/tests/test_env_loader.py patent/tests/test_env_loader.py public-service/backend/tests/test_env_loader.py gateway/tests/test_config_env_loader.py
git commit -m "refactor: unify service env loading"
```

---

## Task 3: Add Shared Config Files And Secret Templates

**Files:**
- Modify: `resource/config/shared/infrastructure.shared.env`
- Modify: `resource/config/shared/model-endpoints.shared.env`
- Create: `resource/config/shared/model-endpoints.secret.env.example`
- Create: `resource/config/shared/graph.shared.env`
- Create: `resource/config/shared/graph.secret.env.example`
- Modify: `resource/config/shared/README.md`

- [ ] **Step 1: Add service ports to infrastructure shared**

Add:

```env
# Service ports
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=8101
PUBLIC_SERVICE_HOST=0.0.0.0
PUBLIC_SERVICE_PORT=8102
FASTQA_HOST=0.0.0.0
FASTQA_PORT=8008
FASTQA_FASTAPI_PORT=8008
HIGHTHINKINGQA_HOST=0.0.0.0
HIGHTHINKINGQA_PORT=8009
PATENT_HOST=0.0.0.0
PATENT_PORT=8010

# Gateway backend targets
PUBLIC_BACKEND_BASE_URL=http://127.0.0.1:8102
FAST_BACKEND_BASE_URL=http://127.0.0.1:8008
THINKING_BACKEND_BASE_URL=http://127.0.0.1:8009
PATENT_BACKEND_BASE_URL=http://127.0.0.1:8010
```

Keep existing MySQL/Redis/MinIO non-secret settings.

- [ ] **Step 2: Normalize model endpoint shared config**

In `resource/config/shared/model-endpoints.shared.env`, add unified names:

```env
LLM_PROVIDER=openai-compatible
LLM_BASE_URL=http://127.0.0.1:18000/v1
LLM_MODEL=deepseek-v3.1
LLM_ENABLE_THINKING=0
LLM_CONNECT_TIMEOUT_SECONDS=15
LLM_READ_TIMEOUT_SECONDS=180
LLM_STREAM_READ_TIMEOUT_SECONDS=600
LLM_WRITE_TIMEOUT_SECONDS=180
LLM_POOL_TIMEOUT_SECONDS=30
LLM_KEEPALIVE_EXPIRY_SECONDS=120
LLM_MAX_CONNECTIONS=160
LLM_MAX_KEEPALIVE_CONNECTIONS=64
```

Keep old `OPENAI_*` / `DASHSCOPE_*` aliases for compatibility, but write actual values rather than `${LLM_BASE_URL}` because current dotenv loaders do not expand variable references.

- [ ] **Step 3: Add model secret template**

Create `resource/config/shared/model-endpoints.secret.env.example`:

```env
LLM_API_KEY=
OPENAI_API_KEY=
DASHSCOPE_API_KEY=
EMBEDDING_API_KEY=
RERANK_API_KEY=
QA_RETRIEVAL_RERANK_API_KEY=
OCR_API_KEY=
```

- [ ] **Step 4: Add graph shared config**

Create `resource/config/shared/graph.shared.env`:

```env
FASTQA_NEO4J_URL=bolt://127.0.0.1:7688
FASTQA_NEO4J_USERNAME=neo4j
FASTQA_NEO4J_DATABASE=neo4j
PATENT_NEO4J_URL=bolt://127.0.0.1:8687
PATENT_NEO4J_USERNAME=neo4j
PATENT_NEO4J_DATABASE=neo4j
PUBLIC_SERVICE_NEO4J_URL=bolt://127.0.0.1:7688
PUBLIC_SERVICE_NEO4J_USERNAME=neo4j
PUBLIC_SERVICE_NEO4J_DATABASE=neo4j

# Legacy fallback for services not migrated yet.
NEO4J_URL=bolt://127.0.0.1:7688
NEO4J_USERNAME=neo4j
```

- [ ] **Step 5: Add graph secret template**

Create `resource/config/shared/graph.secret.env.example`:

```env
FASTQA_NEO4J_PASSWORD=
PATENT_NEO4J_PASSWORD=
PUBLIC_SERVICE_NEO4J_PASSWORD=

# Legacy fallback during migration.
NEO4J_PASSWORD=
```

- [ ] **Step 6: Update shared config README**

Document:

- `*.shared.env` is commit-safe;
- `*.secret.env` is local-only;
- secret examples are commit-safe;
- service ports live in `infrastructure.shared.env`;
- model endpoints live in `model-endpoints.shared.env`;
- graph endpoints live in `graph.shared.env`.

- [ ] **Step 7: Run smoke parser checks**

Run:

```bash
conda run -n agent python -c "from dotenv import dotenv_values; import pathlib; [dotenv_values(p) for p in pathlib.Path('resource/config/shared').glob('*.env')]; print('ok')"
```

Expected: `ok`.

- [ ] **Step 8: Commit shared config templates**

```bash
git add resource/config/shared/infrastructure.shared.env resource/config/shared/model-endpoints.shared.env resource/config/shared/model-endpoints.secret.env.example resource/config/shared/graph.shared.env resource/config/shared/graph.secret.env.example resource/config/shared/README.md
git commit -m "chore: add shared config layers"
```

---

## Task 4: Move Service Ports And Backend URLs To Shared Infrastructure

**Files:**
- Modify: `resource/config/shared/infrastructure.shared.env`
- Modify: `resource/config/services/gateway/config.shared.env`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Modify: `resource/config/services/highThinkingQA/config.shared.env`
- Modify: `resource/config/services/patent/config.shared.env`
- Modify: `public-service/config.shared.env`
- Modify/Create: `resource/config/services/public-service/config.shared.env`
- Modify service config tests from Task 1.

- [ ] **Step 1: Update service settings to read shared port aliases**

Add compatibility reads:

- fastQA: `FASTQA_PORT` and `FASTQA_FASTAPI_PORT` should feed current `FASTAPI_PORT` fallback.
- highThinkingQA: `HIGHTHINKINGQA_PORT` should feed current `APP_PORT` fallback.
- patent: `PATENT_PORT` already exists.
- public-service: `PUBLIC_SERVICE_PORT` already exists.
- gateway: `GATEWAY_PORT` already exists.

For fastQA, update `fastQA/app/core/config.py`:

```python
raw_fastapi_port = str(
    os.getenv("FASTQA_FASTAPI_PORT")
    or os.getenv("FASTQA_PORT")
    or os.getenv("FASTAPI_PORT")
    or os.getenv("BACKEND_PORT")
    or "8012"
).strip()
```

Preserve existing `FASTAPI_PORT` compatibility.

- [ ] **Step 2: Remove duplicate service port definitions from service shared files**

Once shared infrastructure has ports, remove or comment service-local duplicates:

- `APP_PORT`, `BACKEND_PORT`, `FASTAPI_PORT` in fastQA should become compatibility-only or be removed after tests pass.
- `APP_PORT` in highThinkingQA should become compatibility-only or removed after `HIGHTHINKINGQA_PORT` works.
- `PATENT_PORT` may remain in patent service config until infrastructure shared is the canonical file for all services.

If removing immediately creates too much risk, keep comments pointing to `infrastructure.shared.env` and plan a second cleanup pass.

- [ ] **Step 3: Move gateway backend URLs**

Put:

```env
PUBLIC_BACKEND_BASE_URL=http://127.0.0.1:8102
FAST_BACKEND_BASE_URL=http://127.0.0.1:8008
THINKING_BACKEND_BASE_URL=http://127.0.0.1:8009
PATENT_BACKEND_BASE_URL=http://127.0.0.1:8010
```

in `infrastructure.shared.env`. Remove duplicates from gateway service secret/shared if present.

- [ ] **Step 4: Run config value tests**

Run:

```bash
conda run -n agent pytest gateway/tests/test_config_env_loader.py fastQA/tests/test_env_loader.py highThinkingQA/tests/test_env_loader.py patent/tests/test_env_loader.py public-service/backend/tests/test_env_loader.py -q
```

Expected: PASS; each service resolves its port from shared infrastructure when no service override exists.

- [ ] **Step 5: Commit port migration**

```bash
git add resource/config/shared/infrastructure.shared.env resource/config/services/gateway/config.shared.env resource/config/services/fastQA/config.shared.env resource/config/services/highThinkingQA/config.shared.env resource/config/services/patent/config.shared.env public-service/config.shared.env resource/config/services/public-service/config.shared.env fastQA/app/core/config.py fastQA/tests/test_env_loader.py highThinkingQA/tests/test_env_loader.py patent/tests/test_env_loader.py public-service/backend/tests/test_env_loader.py gateway/tests/test_config_env_loader.py
git commit -m "chore: centralize service ports"
```

---

## Task 5: Normalize Graph Config And Namespaced Neo4j Variables

**Files:**
- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/tests/test_graph_kb_runtime.py`
- Modify: `patent/config.py`
- Modify: `patent/tests/test_patent_graph_kb_config.py`
- Modify: `public-service/backend/app/core/config.py`
- Modify: `public-service/backend/tests/test_config_independence.py`
- Modify: `resource/config/shared/graph.shared.env`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Modify: `resource/config/services/fastQA/config.secret.env.example`
- Modify: `resource/config/services/patent/config.shared.env`
- Modify: `patent/config.shared.env.example`

- [ ] **Step 1: Add fastQA Neo4j settings fields**

In `fastQA/app/core/config.py`, add settings fields:

```python
neo4j_url: str
neo4j_username: str
neo4j_password: str
neo4j_database: str
```

Resolve them using:

```python
neo4j_url=str(os.getenv("FASTQA_NEO4J_URL") or os.getenv("NEO4J_URL", "") or "").strip()
neo4j_username=str(os.getenv("FASTQA_NEO4J_USERNAME") or os.getenv("NEO4J_USERNAME", "neo4j") or "neo4j").strip()
neo4j_password=str(os.getenv("FASTQA_NEO4J_PASSWORD") or os.getenv("NEO4J_PASSWORD", "") or "")
neo4j_database=str(os.getenv("FASTQA_NEO4J_DATABASE") or os.getenv("NEO4J_DATABASE", "neo4j") or "neo4j").strip()
```

- [ ] **Step 2: Use fastQA settings in graph bootstrap**

In `fastQA/app/core/runtime.py`, update `bootstrap_graph_kb()` to use `settings.neo4j_url`, `settings.neo4j_username`, and `settings.neo4j_password` instead of direct `os.getenv("NEO4J_*")`.

Preserve fallback behavior through settings.

- [ ] **Step 3: Add fastQA graph config tests**

In `fastQA/tests/test_graph_kb_runtime.py`, assert:

```python
monkeypatch.setenv("FASTQA_NEO4J_URL", "bolt://fastqa:7688")
monkeypatch.setenv("FASTQA_NEO4J_USERNAME", "neo4j")
monkeypatch.setenv("FASTQA_NEO4J_PASSWORD", "pw")
settings = get_settings()
assert settings.neo4j_url == "bolt://fastqa:7688"
```

Also assert legacy `NEO4J_URL` still works when namespaced value is absent.

- [ ] **Step 4: Add public-service namespaced Neo4j fallback**

In `public-service/backend/app/core/config.py`, add namespaced graph fields if public-service still needs Neo4j:

```python
neo4j_url = os.getenv("PUBLIC_SERVICE_NEO4J_URL") or os.getenv("NEO4J_URL", "")
neo4j_username = os.getenv("PUBLIC_SERVICE_NEO4J_USERNAME") or os.getenv("NEO4J_USERNAME", "neo4j")
neo4j_password = os.getenv("PUBLIC_SERVICE_NEO4J_PASSWORD") or os.getenv("NEO4J_PASSWORD", "")
```

If no existing settings field exists, add it only if code consumes Neo4j from settings; otherwise update the direct consumer instead.

- [ ] **Step 5: Ensure patent uses namespaced graph config from shared layer**

Patent already uses `PATENT_NEO4J_*`. Add tests proving values can come from `resource/config/shared/graph.shared.env` and `graph.secret.env`.

- [ ] **Step 6: Move graph toggles out of secrets**

Remove graph feature toggles from secret templates:

- `FASTQA_GRAPH_KB_ENABLED`
- `FASTQA_GRAPH_KB_V2_ENABLED`
- `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED`
- `PATENT_GRAPH_KB_ENABLED`
- `PATENT_GRAPH_KB_V2_ENABLED`
- `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED`

Keep secret files for passwords only.

- [ ] **Step 7: Run graph config tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_runtime.py patent/tests/test_patent_graph_kb_config.py public-service/backend/tests/test_config_independence.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit graph config normalization**

```bash
git add fastQA/app/core/config.py fastQA/app/core/runtime.py fastQA/tests/test_graph_kb_runtime.py patent/config.py patent/tests/test_patent_graph_kb_config.py public-service/backend/app/core/config.py public-service/backend/tests/test_config_independence.py resource/config/shared/graph.shared.env resource/config/services/fastQA/config.shared.env resource/config/services/fastQA/config.secret.env.example resource/config/services/patent/config.shared.env patent/config.shared.env.example
git commit -m "refactor: namespace graph configuration"
```

---

## Task 6: Retire Stable Graph Feature Flags By Making Graph Always-On

**Files:**
- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/routers/qa.py`
- Modify: `fastQA/app/routers/health.py`
- Modify: `fastQA/tests/test_graph_kb_runtime.py`
- Modify: `fastQA/tests/test_fastqa_kb_graph_integration.py`
- Modify: `patent/config.py`
- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/tests/test_patent_kb_service.py`
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`
- Modify: service env examples.

- [ ] **Step 1: Change fastQA graph defaults to always-on**

In `fastQA/app/core/config.py`, change defaults:

```python
graph_kb_enabled=True
graph_kb_v2_enabled=True
graph_kb_rag_injection_enabled=True
graph_community_route_enabled=True
graph_precise_numeric_enabled=True
```

Decision for implementation: do not honor the deprecated fastQA graph disable values as runtime disable switches. They may be read only to emit a deprecation warning for one release. Tests must use missing/degraded Neo4j clients to cover fallback behavior instead of disabling graph through env.

- [ ] **Step 2: Update fastQA graph bootstrap behavior**

In `fastQA/app/core/runtime.py`, remove the early `graph kb disabled by config` branch for normal runtime. New behavior:

- graph bootstrap always attempts when a Neo4j URL exists;
- if URL is missing, component is `degraded` or `skipped` with a clear reason depending on environment;
- no graph secret means graph is `degraded`, not silently off.

- [ ] **Step 3: Update fastQA QA router graph checks**

In `fastQA/app/routers/qa.py`, remove checks that skip graph solely because `graph_kb_enabled` or `graph_kb_v2_enabled` is false.

Keep safe checks for:

- Neo4j client presence;
- graph readiness;
- graph route result mode;
- graph-to-RAG payload presence.

- [ ] **Step 4: Update fastQA health payload**

In `fastQA/app/routers/health.py`, replace `graph_kb_enabled` semantics with:

```json
{
  "graph_kb_configured": true,
  "graph_kb_ready": true|false
}
```

Keep legacy `graph_kb_enabled` if frontend/tests consume it, but make it always `true` during compatibility window.

- [ ] **Step 5: Update fastQA tests**

Rewrite tests that depend on disabling graph flags:

- `fastQA/tests/test_graph_kb_runtime.py`
- `fastQA/tests/test_fastqa_kb_graph_integration.py`
- `fastQA/tests/test_health.py`

Instead of testing disabled flags, test missing Neo4j/degraded fallback and unavailable graph client fallback.

- [ ] **Step 6: Change patent graph defaults to always-on**

In `patent/config.py`, change defaults:

```python
enabled=_read_bool("PATENT_GRAPH_KB_ENABLED", True)
v2_enabled=_read_bool("PATENT_GRAPH_KB_V2_ENABLED", True)
rag_injection_enabled=_read_bool("PATENT_GRAPH_KB_RAG_INJECTION_ENABLED", True)
```

Decision for implementation: do not honor `PATENT_GRAPH_KB_ENABLED=0`, `PATENT_GRAPH_KB_V2_ENABLED=0`, or `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED=0` as runtime disable switches. Keep compatibility fields in settings for one release only if health/contracts need them, but set them to `True` and emit a deprecation warning if a disable value is present.

- [ ] **Step 7: Update patent graph tests**

Update tests that explicitly set graph disabled by default. Prefer testing:

- degraded client behavior;
- missing Neo4j password behavior;
- graph route fallback to RAG when graph returns `skip_graph`;
- graph-for-RAG injection always enabled by default.

- [ ] **Step 8: Remove obsolete graph flags from env files**

Remove from service env examples after tests pass:

- `FASTQA_GRAPH_KB_ENABLED`
- `FASTQA_GRAPH_KB_V2_ENABLED`
- `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED`
- `FASTQA_GRAPH_COMMUNITY_ROUTE_ENABLED`
- `FASTQA_GRAPH_PRECISE_NUMERIC_ENABLED`
- `PATENT_GRAPH_KB_ENABLED`
- `PATENT_GRAPH_KB_V2_ENABLED`
- `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED`

Keep:

- `FASTQA_GRAPH_KB_TIMEOUT_MS`
- `FASTQA_GRAPH_KB_MAX_ROWS`
- `FASTQA_GRAPH_KB_QUERY_LOGGING`
- `PATENT_GRAPH_KB_TIMEOUT_MS`
- `PATENT_GRAPH_KB_MAX_ROWS`
- `PATENT_GRAPH_KB_QUERY_LOGGING`

- [ ] **Step 9: Run graph behavior tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_runtime.py fastQA/tests/test_fastqa_kb_graph_integration.py fastQA/tests/test_health.py -q
conda run -n agent pytest patent/tests/test_patent_kb_service.py patent/tests/fastapi_contract/test_health_contract.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit always-on graph behavior**

```bash
git add fastQA/app/core/config.py fastQA/app/core/runtime.py fastQA/app/routers/qa.py fastQA/app/routers/health.py fastQA/tests/test_graph_kb_runtime.py fastQA/tests/test_fastqa_kb_graph_integration.py fastQA/tests/test_health.py patent/config.py patent/server/patent/kb_service.py patent/server_fastapi/app.py patent/tests/test_patent_kb_service.py patent/tests/fastapi_contract/test_health_contract.py resource/config/services/fastQA/config.shared.env resource/config/services/fastQA/config.env.example resource/config/services/patent/config.shared.env patent/config.shared.env.example
git commit -m "refactor: make graph qa always on"
```

---

## Task 7: Clean Service-Specific Config Files

**Files:**
- Modify: `resource/config/services/fastQA/config.shared.env`
- Modify: `resource/config/services/fastQA/config.env.example`
- Modify: `resource/config/services/fastQA/config.secret.env.example`
- Modify: `resource/config/services/highThinkingQA/config.shared.env`
- Modify: `resource/config/services/highThinkingQA/config.secret.env.example`
- Modify: `resource/config/services/patent/config.shared.env`
- Modify: `patent/config.shared.env.example`
- Create/Modify: `resource/config/services/public-service/config.shared.env`
- Create/Modify: `resource/config/services/public-service/config.secret.env.example`
- Modify: `resource/config/services/gateway/config.shared.env`
- Create/Modify: `resource/config/services/gateway/config.secret.env.example`

- [ ] **Step 1: fastQA service config cleanup**

Keep in fastQA service shared:

- ask/SSE behavior;
- vector/db/data paths;
- graph timeout/max rows/query logging;
- QA stage/cache/stage4 parameters;
- PDF/file QA behavior;
- Redis key prefix.

Remove in this task:

- obsolete graph enabled/v2/injection flags.

Do not remove model endpoint values in this task. Model endpoint cleanup happens in Task 8 after compatibility tests prove shared `LLM_*` resolution works.

- [ ] **Step 2: highThinkingQA service config cleanup**

Keep:

- chunking;
- retrieval topK;
- sub-question count;
- checker loops;
- ingestion concurrency;
- paths;
- ask/SSE;
- cache.

Do not remove `LLM_*`, `EMBEDDING_*`, or `OCR_*` endpoint/model defaults in this task. Model endpoint cleanup happens in Task 8 after compatibility tests pass.

- [ ] **Step 3: patent service config cleanup**

Keep:

- patent runtime capacity;
- durable/authority rollout;
- graph timeout/max rows/logging;
- stage4 reference/evidence parameters;
- tabular/hybrid context and output params;
- planning hot pool/gate;
- Redis key prefix.

Remove in this task:

- graph enabled/v2/injection flags.

Do not remove `PATENT_OPENAI_*` or `PATENT_EMBEDDING_*` endpoint/model defaults in this task. Model endpoint cleanup happens in Task 8 after compatibility tests pass.

- [ ] **Step 4: public-service resource config migration**

Move commit-safe values from `public-service/config.shared.env` to `resource/config/services/public-service/config.shared.env`.

Keep in public-service service shared:

- public-service API/docs/CORS;
- data root and storage paths;
- quota/conversation cache;
- upload processing;
- outbox;
- cleanup;
- reference preview.

Move out:

- `NEO4J_USERNAME` to graph shared;
- public infrastructure keys to infrastructure shared.

- [ ] **Step 5: gateway config cleanup**

Keep in gateway shared:

- gateway worker/capacity;
- request/SSE timeout;
- backend strict mode;
- admission behavior and limits;
- route classifier config;
- task events debug;
- backend URLs only if not centralized in infrastructure yet.

Document as local-only cleanup for `resource/config/services/gateway/config.secret.env`; do not commit the real secret file. Move these keys into `resource/config/services/gateway/config.shared.env` or shared infrastructure config:

- non-secret `GATEWAY_*_ENABLED` toggles;
- `REDIS_ENABLED`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_KEY_PREFIX`.

Keep in secret:

- internal token;
- admission control token;
- Redis password if not moved to infrastructure secret.

- [ ] **Step 6: Validate env files parse**

Run:

```bash
conda run -n agent python -c "from dotenv import dotenv_values; import pathlib; paths=list(pathlib.Path('resource/config').rglob('*.env'))+list(pathlib.Path('resource/config').rglob('*.env.example')); [dotenv_values(p) for p in paths]; print(len(paths))"
```

Expected: prints a positive file count and no parse errors.

- [ ] **Step 7: Commit service config cleanup**

```bash
git add resource/config/services/fastQA/config.shared.env resource/config/services/fastQA/config.env.example resource/config/services/fastQA/config.secret.env.example resource/config/services/highThinkingQA/config.shared.env resource/config/services/highThinkingQA/config.secret.env.example resource/config/services/patent/config.shared.env patent/config.shared.env.example resource/config/services/public-service/config.shared.env resource/config/services/public-service/config.secret.env.example resource/config/services/gateway/config.shared.env resource/config/services/gateway/config.secret.env.example
git diff --cached --name-only | rg '(^|/)config\\.secret\\.env$|(^|/)\\.env$' && exit 1 || true
git commit -m "chore: clean service config ownership"
```

---

## Task 8: Align Services To Unified LLM And Embedding Names

**Files:**
- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/integrations/llm/shared_http_pool.py`
- Modify: `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
- Modify: `fastQA/app/modules/generation_pipeline/query_expander.py`
- Modify: `fastQA/app/modules/qa_pdf/llm_factory.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/answering.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/server/patent/hybrid_synthesis.py`
- Modify: `highThinkingQA/config.py`
- Add/update tests for each service.

- [ ] **Step 1: Add unified LLM fallback helper**

For each service, introduce or reuse a small helper that resolves:

```text
service-specific override -> LLM_* -> OPENAI_* / DASHSCOPE_* legacy -> default
```

Example precedence for model:

```python
model = (
    os.getenv("FASTQA_LLM_MODEL")
    or os.getenv("LLM_MODEL")
    or os.getenv("OPENAI_MODEL")
    or os.getenv("DASHSCOPE_MODEL")
    or "deepseek-v3.1"
)
```

Do not introduce a new abstraction if an existing local bootstrap helper already centralizes this.

- [ ] **Step 2: fastQA uses unified LLM transport**

Update fastQA LLM-related settings so `LLM_*` values feed existing `FASTQA_LLM_HTTP_*` behavior.

Keep compatibility with `FASTQA_LLM_HTTP_*` during migration:

```text
FASTQA_LLM_HTTP_CONNECT_TIMEOUT_SECONDS -> LLM_CONNECT_TIMEOUT_SECONDS -> OPENAI_CONNECT_TIMEOUT_SECONDS
```

- [ ] **Step 3: patent uses unified LLM by default**

Change patent LLM resolution so `PATENT_OPENAI_USE_SHARED_ENV=1` is no longer needed for normal operation. The default should be:

```text
PATENT_* override -> LLM_* -> OPENAI_* / DASHSCOPE_* legacy
```

Keep `PATENT_OPENAI_*` as override aliases for compatibility.

- [ ] **Step 4: highThinkingQA uses unified LLM/embedding/OCR**

In `highThinkingQA/config.py`, resolve:

- `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`;
- `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`;
- `OCR_BASE_URL`, `OCR_MODEL`, `OCR_API_KEY`;

from shared model config. Keep existing env names as compatibility aliases.

- [ ] **Step 5: Add tests**

Tests should verify:

- shared `LLM_BASE_URL` and `LLM_MODEL` are used when service-specific values are absent;
- service-specific override still wins;
- API keys are not logged or exposed in settings repr/output.

- [ ] **Step 6: Remove service model endpoint duplication after compatibility passes**

Only after Step 5 tests pass, remove duplicated public endpoint/model values from service shared configs:

- fastQA: `OPENAI_MODEL`, `DASHSCOPE_MODEL`, `FASTQA_LLM_HTTP_*` if equivalent `LLM_*` values are active;
- highThinkingQA: duplicated public `LLM_*`, `EMBEDDING_*`, `OCR_*` only when shared model config provides the same values;
- patent: `PATENT_OPENAI_BASE_URL`, `PATENT_OPENAI_MODEL`, `PATENT_EMBEDDING_*` endpoint/model defaults, while retaining service-specific overrides if product behavior still needs them.

Do not remove API key placeholders from secret templates until `model-endpoints.secret.env.example` is committed and tests prove fallback aliases work.

- [ ] **Step 7: Run model config tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_qa_pdf_llm_factory.py -q
conda run -n agent pytest patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_hybrid_synthesis.py -q
conda run -n agent pytest highThinkingQA/tests/test_config_runtime_defaults.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit unified model endpoint support**

```bash
git add fastQA/app/core/config.py fastQA/app/integrations/llm/shared_http_pool.py fastQA/app/modules/generation_pipeline/runtime_bootstrap.py fastQA/app/modules/generation_pipeline/query_expander.py fastQA/app/modules/qa_pdf/llm_factory.py patent/server/patent/runtime.py patent/server/patent/answering.py patent/server/patent/pdf_service.py patent/server/patent/tabular_service.py patent/server/patent/hybrid_synthesis.py highThinkingQA/config.py fastQA/tests patent/tests highThinkingQA/tests
git commit -m "refactor: use unified model endpoint config"
```

---

## Task 9: Update Startup Scripts For New Shared Env Files

**Files:**
- Modify: `fastQA/scripts/start_gunicorn.sh`
- Modify: `highThinkingQA/scripts/start_fastapi_gunicorn.sh`
- Modify: `patent/scripts/start_gunicorn.sh`
- Modify: `gateway/scripts/start_gunicorn.sh`
- Modify: `gateway/scripts/start_admission_worker.sh`
- Modify: `public-service/scripts/start_gunicorn.sh`
- Modify: `scripts/_service_common.sh`
- Modify: `scripts/start_all.sh`
- Modify: `scripts/status_all.sh`

- [ ] **Step 1: Add shared env files to shell defaults**

Where scripts build `*_SHARED_ENV_FILES_DEFAULT`, include:

```text
infrastructure.shared.env
model-endpoints.shared.env
infrastructure.secret.env
model-endpoints.secret.env
graph.shared.env
graph.secret.env
```

Keep missing files non-fatal; `scripts/env_file_loader.sh` already skips absent files.

- [ ] **Step 2: Add service resource config paths**

Ensure each service uses:

```text
legacy service-dir/root config.shared.env
legacy service-dir/root config.secret.env
resource/config/shared/infrastructure.shared.env
resource/config/shared/model-endpoints.shared.env
resource/config/shared/infrastructure.secret.env
resource/config/shared/model-endpoints.secret.env
resource/config/shared/graph.shared.env
resource/config/shared/graph.secret.env
resource/config/services/<service>/config.shared.env
resource/config/services/<service>/config.secret.env
resource/config/services/<service>/.env
resource/config/services/<service>/config.env
```

in that order. The shell loader is later-file-wins, so legacy fallback must appear first and `resource/config/services/<service>/config.env` must appear last as the highest-precedence env-file override.

- [ ] **Step 3: Update top-level lifecycle common script**

Update `scripts/_service_common.sh` so stack-level `start_all`, `stop_all`, and `status_all` use the same shared env file list, service config roots, port variables, and health URLs as individual service scripts. This file currently owns much of the lifecycle wiring; do not rely on service scripts alone.

- [ ] **Step 4: Ensure process env still wins**

Do not change `scripts/env_file_loader.sh` precedence unless tests prove it is wrong. Process environment must continue to override env files.

- [ ] **Step 5: Add script smoke tests or manual commands**

If there are no shell tests, run:

```bash
bash -n fastQA/scripts/start_gunicorn.sh
bash -n highThinkingQA/scripts/start_fastapi_gunicorn.sh
bash -n patent/scripts/start_gunicorn.sh
bash -n gateway/scripts/start_gunicorn.sh
bash -n gateway/scripts/start_admission_worker.sh
bash -n public-service/scripts/start_gunicorn.sh
bash -n scripts/_service_common.sh
bash -n scripts/start_all.sh
```

Expected: no syntax errors.

- [ ] **Step 6: Commit startup script updates**

```bash
git add fastQA/scripts/start_gunicorn.sh highThinkingQA/scripts/start_fastapi_gunicorn.sh patent/scripts/start_gunicorn.sh gateway/scripts/start_gunicorn.sh gateway/scripts/start_admission_worker.sh public-service/scripts/start_gunicorn.sh scripts/_service_common.sh scripts/start_all.sh scripts/status_all.sh
git commit -m "chore: load unified env files in service scripts"
```

---

## Task 10: Update Documentation And Migration Notes

**Files:**
- Modify: `docs/config/2026-04-29-config-cleanup-checklist.md`
- Create: `docs/config/2026-04-29-config-layer-migration-guide.md`
- Modify: `resource/config/shared/README.md`
- Modify: service README files as needed:
  - `resource/config/services/fastQA/README.md`
  - `resource/config/services/highThinkingQA/README.md`
  - `resource/config/services/patent/README.md`
  - `resource/config/services/gateway/README.md`
  - `resource/config/services/public-service/README.md`

- [ ] **Step 1: Write migration guide**

Create `docs/config/2026-04-29-config-layer-migration-guide.md` with:

- new file hierarchy;
- variable precedence;
- secret policy;
- how to override locally;
- legacy variable compatibility table;
- removed always-on flags;
- service port table;
- rollback notes.

- [ ] **Step 2: Update shared README**

Add one concise section per shared file:

- infrastructure;
- model endpoints;
- graph;
- secret examples.

- [ ] **Step 3: Update service READMEs**

Each service README should state:

- service config lives in `resource/config/services/<service>`;
- common infrastructure/model/graph config comes from `resource/config/shared`;
- local overrides belong in `config.env` or process env;
- secrets do not belong in committed docs.

- [ ] **Step 4: Validate docs references**

Run:

```bash
rg -n "deprecated graph disable examples|secret files containing runtime toggles|plaintext Neo4j password" docs resource/config -S
```

Expected:

- no examples recommending graph disabled by default;
- no plaintext passwords in docs;
- any Neo4j password assignment is blank or in `.example`.

- [ ] **Step 5: Commit docs**

```bash
git add docs/config/2026-04-29-config-cleanup-checklist.md docs/config/2026-04-29-config-layer-migration-guide.md resource/config/shared/README.md resource/config/services/fastQA/README.md resource/config/services/highThinkingQA/README.md resource/config/services/patent/README.md resource/config/services/gateway/README.md resource/config/services/public-service/README.md
git commit -m "docs: document config layer migration"
```

---

## Task 11: End-To-End Verification

**Files:**
- No planned source edits unless verification exposes bugs.

- [ ] **Step 1: Run unit/config tests**

Run:

```bash
conda run -n agent pytest gateway/tests public-service/backend/tests fastQA/tests/test_env_loader.py fastQA/tests/test_graph_kb_runtime.py fastQA/tests/test_health.py highThinkingQA/tests patent/tests/test_patent_graph_kb_config.py patent/tests/fastapi_contract/test_health_contract.py -q
```

Expected: PASS.

- [ ] **Step 2: Run targeted graph tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_service.py fastQA/tests/test_fastqa_kb_graph_integration.py patent/tests/test_patent_graph_kb_service_v2.py patent/tests/test_patent_kb_service.py -q
```

Expected: PASS.

- [ ] **Step 3: Run cache and vector/RAG regression tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_qa_cache.py fastQA/tests/test_qa_cache_stage1.py fastQA/tests/test_qa_cache_stage2.py fastQA/tests/test_qa_cache_stage25.py fastQA/tests/test_qa_cache_stage3.py -q
conda run -n agent pytest fastQA/tests/test_embedding_client.py fastQA/tests/test_retrieval_validation.py fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_generation_stage4_synthesis.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_rerank_service.py -q
conda run -n agent pytest highThinkingQA/tests/test_stage_cache_behavior.py highThinkingQA/tests/test_stage_cache_runtime.py highThinkingQA/tests/test_stage_cache_ttl_contract.py highThinkingQA/tests/test_prompt_and_retrieval_optimizations.py -q
conda run -n agent pytest patent/tests/test_patent_generation_orchestrator.py patent/tests/test_patent_retrieval_service.py patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_stage3_evidence_loading.py patent/tests/test_patent_stage4_synthesis.py -q
conda run -n agent pytest public-service/backend/tests/test_quota_module.py public-service/backend/tests/test_conversation_module.py -q
```

Expected: PASS. These tests protect cache default-on behavior, vector retrieval, embedding/rerank config, and patent/fastQA RAG orchestration after model endpoint ownership changes.

- [ ] **Step 4: Restart backend stack**

Run:

```bash
bash scripts/stop_all.sh
bash scripts/start_all.sh
bash scripts/status_all.sh
```

Expected:

- gateway running;
- public-service running;
- fastQA running and graph ready or clearly degraded if Neo4j is unavailable;
- highThinkingQA running;
- patent running and graph ready or clearly degraded if Neo4j is unavailable.

- [ ] **Step 5: Check health endpoints**

Run:

```bash
curl -s http://127.0.0.1:8101/api/health
curl -s http://127.0.0.1:8102/api/health
curl -s http://127.0.0.1:8008/api/health
curl -s http://127.0.0.1:8009/api/health
curl -s http://127.0.0.1:8010/api/health
```

Expected:

- HTTP 200 for healthy services;
- config-derived ports match `infrastructure.shared.env`;
- no secret values appear in responses.

- [ ] **Step 6: Run gateway smoke requests**

Use existing auth flow or known test token. Cover:

- fastQA KB graph direct question;
- fastQA graph-for-RAG question;
- patent KB graph direct question;
- patent graph-for-RAG question;
- public-service conversation list/detail;
- highThinkingQA basic question.

Expected:

- routes reach correct backends;
- graph paths are not skipped due to removed feature flags;
- vector/RAG logic still works.

- [ ] **Step 7: Inspect logs without printing secrets**

Check log lines for graph/config health terms, but do not print lines containing secret values. Prefer key-name-only scans:

```bash
rg -n "graph_kb|neo4j|config|degraded" resource/logs/dev fastQA/.runtime patent/.runtime gateway/.runtime public-service -S
rg -n "(password|api_key|secret|token)=\\S+" resource/logs/dev fastQA/.runtime patent/.runtime gateway/.runtime public-service -S
```

Expected:

- graph route logs present for graph requests;
- second command returns no plaintext secret assignments;
- no unexpected fallback to placeholder;
- no "disabled by config" for graph main path.

- [ ] **Step 8: Commit any verification fixes**

If fixes were needed:

```bash
git add <changed-files>
git commit -m "fix: stabilize unified config rollout"
```

---

## Rollback Strategy

If production startup fails after migration:

1. Set explicit `*_ENV_FILES` for the affected service to the previous known-good env file list.
2. Restore legacy `NEO4J_URL` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` variables in process env.
3. Keep graph always-on code, but let graph component degrade instead of blocking full service startup.
4. Revert only the failing service loader/config task, not the shared config files, unless parsing shared files is the root cause.

## Acceptance Criteria

- All five backend services can load config from `resource/config/shared` plus `resource/config/services/<service>`.
- Service ports live in shared infrastructure config.
- LLM/embedding/rerank/OCR endpoint config lives in shared model config.
- Graph endpoint config lives in shared graph config with namespaced Neo4j variables.
- Real secret values are not written to docs or example files.
- fastQA graph and patent graph are default-on main paths.
- Cache remains default-on with TTL/version knobs retained.
- Secret files no longer contain non-secret feature flags except during an explicitly documented compatibility window.
- Gateway health and service health endpoints do not expose secrets.
- fastQA vector/RAG and patent vector/RAG behavior are not regressed by graph always-on changes.
