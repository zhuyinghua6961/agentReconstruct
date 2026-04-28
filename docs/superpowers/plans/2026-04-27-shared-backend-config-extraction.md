# Shared Backend Config Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract duplicated backend infrastructure and model endpoint settings from per-service env files into repository-level shared config files while preserving service-specific overrides and existing runtime behavior.

**Architecture:** Add shared env files under `resource/config/shared/` and load them before each service-local `config.env/config.shared.env/config.secret.env`, so shared defaults can be overridden by service files. Keep service-specific names such as `REDIS_KEY_PREFIX`, ports, worker counts, cache TTLs, and route behavior in service configs. Migrate only duplicate infrastructure/model defaults that are common across services; secrets stay in a shared secret env file or service secret env files depending on ownership.

**Tech Stack:** Bash service launchers, Python dotenv loaders, pytest, resource config env files

---

## Current Findings

Relevant config roots:

- `resource/config/shared/`: currently README/template only; intended for repository-wide commit-safe config.
- `resource/config/services/fastQA/`: real service env files used by `fastQA`.
- `resource/config/services/highThinkingQA/`: real service env files used by `highThinkingQA`.
- `resource/config/services/gateway/`: real service env files used by gateway scripts.
- `resource/config/services/patent/`: real service env files used by patent scripts.
- `resource/config/services/public-service/`: README only; current public-service runtime still loads `public-service/config.shared.env` and `public-service/config.secret.env`.

Observed duplicated or near-duplicated config:

- Redis defaults: `REDIS_ENABLED`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_SOCKET_CONNECT_TIMEOUT_SEC`, `REDIS_SOCKET_TIMEOUT_SEC` repeat across `fastQA`, `gateway`, `highThinkingQA`, and public-service. `REDIS_KEY_PREFIX` is intentionally service-specific.
- LLM upstream: DashScope/OpenAI compatible base URL repeats across `fastQA`, `highThinkingQA`, public-service, and patent, but variable names differ (`OPENAI_BASE_URL`, `LLM_BASE_URL`, `PATENT_OPENAI_BASE_URL`).
- Embedding endpoint: `fastQA` and patent share local embedding endpoint/model defaults; `highThinkingQA` uses DashScope embedding defaults and should not be forced onto the local endpoint.
- Rerank endpoint: currently `fastQA` only, but it belongs with local model endpoint defaults if future services consume it.
- MySQL and MinIO: `highThinkingQA` and public-service share the same infrastructure class. Some common safe defaults can be shared, but credentials are secrets.
- Shared secret candidates: `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN`, `DASHSCOPE_API_KEY`/`OPENAI_API_KEY`, Redis password, MySQL credentials, MinIO credentials.

Important loader facts:

- `fastQA/scripts/start_gunicorn.sh` loads `FASTQA_ENV_FILES`, defaulting to service-local files only.
- `fastQA/app/core/env_loader.py` also resolves service-local files only unless explicit `FASTQA_ENV_FILES`/`SERVICE_ENV_FILES` is set.
- `highThinkingQA/scripts/start_fastapi_gunicorn.sh` relies on Python `highThinkingQA/env_loader.py`, which currently resolves service-local files only.
- Gateway scripts load `GATEWAY_ENV_FILES`, defaulting to service-local files only.
- Patent scripts already load service-local files plus root `config.shared.env`, `config.secret.env`, and public-service env files. This should be simplified after shared config exists.
- Public-service scripts currently load env files from `public-service/`, not `resource/config/services/public-service`.

## Locked Decisions

1. Shared config files are additive and loaded before service-local files.
2. Service-local config wins over shared config.
3. Process environment variables win over all env files.
4. Do not move secrets into commit-safe files.
5. Do not remove service-specific prefixes such as `REDIS_KEY_PREFIX`, `PATENT_REDIS_KEY_PREFIX`, app ports, worker counts, route limits, cache TTLs, or service-specific model choices.
6. Do not migrate public-service into `resource/config/services/public-service` in the same task unless explicitly called out. First make it able to consume `resource/config/shared/*` while preserving its current local files.
7. Keep variable aliases when services use different names for the same shared endpoint. Shared files may define canonical values plus compatibility aliases, but implementation must not require a large app-code rename.
8. Every migration step must include a test proving load order and override behavior.
9. Shell launchers must not overwrite an already-exported process env value when loading env files.
10. Real `config.secret.env` and service-local ignored config files are not committed in this plan. Changes to ignored local files are either documented as local operations or represented through tracked `.example` files.
11. Legacy root service env files such as `highThinkingQA/config.shared.env`, `fastQA/config.shared.env`, and root `config.shared.env` are out of scope unless a launcher still explicitly loads them for compatibility.

## Proposed Shared Files

Create these files:

- `resource/config/shared/infrastructure.shared.env`
  - Commit-safe defaults for Redis, MySQL non-secret defaults, MinIO non-secret defaults, and common object storage behavior.
- `resource/config/shared/model-endpoints.shared.env`
  - Commit-safe defaults for LLM base URLs, local embedding endpoint, local rerank endpoint, and common timeout defaults.
- `resource/config/shared/infrastructure.secret.env.example`
  - Template keys for shared secrets. No real values.

Do not commit a real shared secret file in this plan. Local deployments may create:

- `resource/config/shared/infrastructure.secret.env`

## Initial Shared Env Contents

`resource/config/shared/infrastructure.shared.env` should start with:

```env
# Shared backend infrastructure defaults. Service-local env files may override.

REDIS_ENABLED=1
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_SOCKET_CONNECT_TIMEOUT_SEC=2
REDIS_SOCKET_TIMEOUT_SEC=2

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=agentcode
MYSQL_CONNECT_TIMEOUT_SECONDS=5
MYSQL_READ_TIMEOUT_SECONDS=30
MYSQL_WRITE_TIMEOUT_SECONDS=30
MYSQL_CONNECT_RETRIES=2
MYSQL_CONNECT_RETRY_DELAY_SECONDS=0.15
MYSQL_QUERY_RETRIES=2
MYSQL_QUERY_RETRY_DELAY_SECONDS=0.05

MINIO_BUCKET=agentcode
MINIO_SECURE=0
MINIO_USE_PROXY=1
MINIO_DOWNLOAD_EXPIRES=3600
```

`resource/config/shared/model-endpoints.shared.env` should start with:

```env
# Shared model endpoint defaults. Service-local env files may override.

DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
PATENT_OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

OPENAI_CONNECT_TIMEOUT_SECONDS=15
OPENAI_READ_TIMEOUT_SECONDS=180
OPENAI_WRITE_TIMEOUT_SECONDS=180
OPENAI_POOL_TIMEOUT_SECONDS=30

EMBEDDING_MODEL_TYPE=remote
EMBEDDING_API_URL=http://127.0.0.1:8001/v1/embeddings
EMBEDDING_API_MODEL=bge-local
EMBEDDING_API_TIMEOUT_SECONDS=120

PATENT_EMBEDDING_MODEL_TYPE=remote
PATENT_EMBEDDING_API_URL=http://127.0.0.1:8001/v1/embeddings
PATENT_EMBEDDING_API_MODEL=bge-local
PATENT_EMBEDDING_API_TIMEOUT_SECONDS=20

QA_RETRIEVAL_RERANK_PROVIDER=local
QA_RETRIEVAL_RERANK_BASE_URL=http://localhost:8084
QA_RETRIEVAL_RERANK_MODEL=qwen3-vl-rerank
QA_RETRIEVAL_RERANK_TIMEOUT=20
```

`resource/config/shared/infrastructure.secret.env.example` should start with:

```env
# Copy to infrastructure.secret.env for local shared secrets.

PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN=

DASHSCOPE_API_KEY=
OPENAI_API_KEY=
LLM_API_KEY=
EMBEDDING_API_KEY=

REDIS_PASSWORD=
REDIS_USERNAME=
REDIS_URL=

MYSQL_USER=
MYSQL_PASSWORD=

MINIO_ENDPOINT=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_REGION=
```

## Task 1: Add Shared Config Files And Documentation

**Files:**
- Create: `resource/config/shared/infrastructure.shared.env`
- Create: `resource/config/shared/model-endpoints.shared.env`
- Create: `resource/config/shared/infrastructure.secret.env.example`
- Modify: `resource/config/shared/README.md`
- Test: none for this task; load-order tests come in Tasks 2-5.

- [ ] **Step 1: Create shared env files**

Add the exact starting contents from "Initial Shared Env Contents" above.

- [ ] **Step 2: Update shared README**

Update `resource/config/shared/README.md` to document:

```markdown
# Shared Config

Shared env files provide repository-wide defaults loaded before service-local env files.

Load order target:
1. `resource/config/shared/infrastructure.shared.env`
2. `resource/config/shared/model-endpoints.shared.env`
3. optional local `resource/config/shared/infrastructure.secret.env`
4. service-local `config.env`
5. service-local `config.shared.env`
6. service-local `config.secret.env`
7. service-local `.env`

Service-local files override shared defaults. Process environment variables override all env files.
```

- [ ] **Step 3: Commit**

```bash
git add resource/config/shared/infrastructure.shared.env resource/config/shared/model-endpoints.shared.env resource/config/shared/infrastructure.secret.env.example resource/config/shared/README.md
git commit -m "chore: add shared backend config templates"
```

## Task 2: Add Shared Env Loading To fastQA

**Files:**
- Modify: `fastQA/app/core/env_loader.py`
- Modify: `fastQA/scripts/start_gunicorn.sh`
- Test: `fastQA/tests/test_env_loader.py`

- [ ] **Step 1: Write failing Python loader test**

Add a test to `fastQA/tests/test_env_loader.py` proving shared files precede service files:

```python
def test_iter_workspace_env_files_includes_resource_shared_before_service_files(tmp_path, monkeypatch):
    resource_root = tmp_path / "resource"
    shared_root = resource_root / "config" / "shared"
    config_root = resource_root / "config" / "services" / "fastQA"
    shared_root.mkdir(parents=True)
    config_root.mkdir(parents=True)
    for name in ("infrastructure.shared.env", "model-endpoints.shared.env", "infrastructure.secret.env"):
        (shared_root / name).write_text(f"{name}=1\n", encoding="utf-8")
    for name in ("config.env", "config.shared.env", "config.secret.env", ".env"):
        (config_root / name).write_text(f"{name}=1\n", encoding="utf-8")

    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))
    monkeypatch.delenv("FASTQA_ENV_FILE", raising=False)
    monkeypatch.delenv("FASTQA_ENV_FILES", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILE", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILES", raising=False)

    import app.core.env_loader as env_loader

    result = env_loader.iter_workspace_env_files()

    assert result[:7] == (
        (shared_root / "infrastructure.shared.env").resolve(),
        (shared_root / "model-endpoints.shared.env").resolve(),
        (shared_root / "infrastructure.secret.env").resolve(),
        (config_root / "config.env").resolve(),
        (config_root / "config.shared.env").resolve(),
        (config_root / "config.secret.env").resolve(),
        (config_root / ".env").resolve(),
    )
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
cd fastQA && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_env_loader.py::test_iter_workspace_env_files_includes_resource_shared_before_service_files -q
```

Expected: FAIL because shared env files are not included yet.

- [ ] **Step 3: Implement shared file discovery in Python loader**

In `fastQA/app/core/env_loader.py`, add:

```python
SHARED_CONFIG_FILENAMES = (
    "infrastructure.shared.env",
    "model-endpoints.shared.env",
    "infrastructure.secret.env",
)


def _iter_resource_shared_env_files() -> tuple[Path, ...]:
    resource_root = resolve_resource_root()
    if resource_root is None:
        return ()
    shared_root = resource_root / "config" / "shared"
    return tuple((shared_root / filename).resolve() for filename in SHARED_CONFIG_FILENAMES)
```

Then in `iter_workspace_env_files()`, when `config_root` is resolved, prepend these files before service candidates while keeping dedupe behavior.

- [ ] **Step 4: Add shared files to fastQA start script**

In `fastQA/scripts/start_gunicorn.sh`, introduce:

```bash
SHARED_CONFIG_DIR_DEFAULT=""
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  SHARED_CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/shared"
fi
export FASTQA_SHARED_ENV_FILES="${FASTQA_SHARED_ENV_FILES:-$SHARED_CONFIG_DIR_DEFAULT/infrastructure.shared.env:$SHARED_CONFIG_DIR_DEFAULT/model-endpoints.shared.env:$SHARED_CONFIG_DIR_DEFAULT/infrastructure.secret.env}"
export FASTQA_ENV_FILES="${FASTQA_ENV_FILES:-$FASTQA_SHARED_ENV_FILES:$FASTQA_SERVICE_CONFIG_ROOT/config.env:$FASTQA_SERVICE_CONFIG_ROOT/config.shared.env:$FASTQA_SERVICE_CONFIG_ROOT/config.secret.env:$PROJECT_ROOT/.env}"
```

If `RESOURCE_DIR` is absent, `FASTQA_SHARED_ENV_FILES` should be empty or point to non-existing files that the loader skips. Do not break workspace-local fallback.

- [ ] **Step 5: Run fastQA env loader tests**

Run:

```bash
cd fastQA && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_env_loader.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/core/env_loader.py fastQA/scripts/start_gunicorn.sh fastQA/tests/test_env_loader.py
git commit -m "chore: load shared backend config in fastqa"
```

## Task 3: Add Shared Env Loading To highThinkingQA

**Files:**
- Modify: `highThinkingQA/env_loader.py`
- Modify: `highThinkingQA/scripts/start_fastapi_gunicorn.sh`
- Test: `highThinkingQA/tests/test_env_loader.py`

- [ ] **Step 1: Write failing Python loader test**

Add an equivalent test to `highThinkingQA/tests/test_env_loader.py`, using service path `resource/config/services/highThinkingQA`.

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
cd highThinkingQA && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_env_loader.py::test_iter_workspace_env_files_includes_resource_shared_before_service_files -q
```

Expected: FAIL because shared env files are not included yet.

- [ ] **Step 3: Implement shared file discovery**

Mirror the fastQA helper in `highThinkingQA/env_loader.py`:

```python
SHARED_CONFIG_FILENAMES = (
    "infrastructure.shared.env",
    "model-endpoints.shared.env",
    "infrastructure.secret.env",
)
```

Prepend resource shared files before service-local files in `iter_workspace_env_files()`.

- [ ] **Step 4: Add shared env awareness to start script**

In `highThinkingQA/scripts/start_fastapi_gunicorn.sh`, export:

```bash
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  export HIGHTHINKINGQA_SHARED_ENV_FILES="${HIGHTHINKINGQA_SHARED_ENV_FILES:-$RESOURCE_DIR/config/shared/infrastructure.shared.env:$RESOURCE_DIR/config/shared/model-endpoints.shared.env:$RESOURCE_DIR/config/shared/infrastructure.secret.env}"
  export HIGHTHINKINGQA_ENV_FILES="${HIGHTHINKINGQA_ENV_FILES:-$HIGHTHINKINGQA_SHARED_ENV_FILES:$HIGHTHINKINGQA_SERVICE_CONFIG_ROOT/config.env:$HIGHTHINKINGQA_SERVICE_CONFIG_ROOT/config.shared.env:$HIGHTHINKINGQA_SERVICE_CONFIG_ROOT/config.secret.env:$HIGHTHINKINGQA_SERVICE_CONFIG_ROOT/.env}"
fi
```

This is mostly a startup-script explicitness improvement; the Python loader is still the source of truth.

- [ ] **Step 5: Run highThinkingQA env loader tests**

Run:

```bash
cd highThinkingQA && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_env_loader.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add highThinkingQA/env_loader.py highThinkingQA/scripts/start_fastapi_gunicorn.sh highThinkingQA/tests/test_env_loader.py
git commit -m "chore: load shared backend config in highthinkingqa"
```

## Task 4: Add Shared Env Loading To Gateway, Patent, And Public-Service Scripts

**Files:**
- Modify: `gateway/scripts/start_gunicorn.sh`
- Modify: `gateway/scripts/start_admission_worker.sh`
- Modify: `gateway/scripts/run_gunicorn_foreground.sh`
- Modify: `gateway/scripts/run_admission_worker_foreground.sh`
- Modify: `scripts/_service_common.sh`
- Modify: `patent/scripts/start_gunicorn.sh`
- Modify: `patent/scripts/start.sh`
- Modify: `public-service/scripts/start_gunicorn.sh`
- Test: `gateway/tests/test_admission_worker_scripts.py`
- Create: `tests/test_shared_env_launchers.py`

- [ ] **Step 1: Add shared launcher test helpers**

Create `tests/test_shared_env_launchers.py` with helpers that run launcher scripts in a temporary copied repo tree. The test should not start real services. It should use script stubs for `conda`, `gunicorn`, or service entrypoints where necessary.

Helper outline:

```python
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_script_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in (
        "scripts",
        "gateway/scripts",
        "public-service/scripts",
        "patent/scripts",
        "resource/config/shared",
        "resource/config/services/gateway",
        "resource/config/services/patent",
    ):
        source = REPO_ROOT / rel
        target = root / rel
        if source.exists():
            shutil.copytree(source, target, dirs_exist_ok=True)
    return root


def _write_env(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
```

Tests in this file should execute shell snippets or scripts with harmless overrides, not launch production gunicorn.

- [ ] **Step 2: Add gateway script test for shared env order and process env precedence**

Extend gateway script tests to assert default `GATEWAY_ENV_FILES` order includes:

```text
resource/config/shared/infrastructure.shared.env
resource/config/shared/model-endpoints.shared.env
resource/config/shared/infrastructure.secret.env
resource/config/services/gateway/config.env
resource/config/services/gateway/config.shared.env
resource/config/services/gateway/config.secret.env
gateway/.env
```

Use a temporary repo/resource fixture and a lightweight script invocation pattern already present in `gateway/tests/test_admission_worker_scripts.py`.

Also add two precedence tests:

- Process env preservation: set `REDIS_HOST=process-value`, have shared env set `REDIS_HOST=shared-value`, service env set `REDIS_HOST=service-value`, source the script's `load_env_files` helper in a test shell, and assert the final value remains `process-value`.
- Service-local override: leave `REDIS_HOST` unset in process env, have shared env set `REDIS_HOST=shared-value`, service env set `REDIS_HOST=service-value`, and assert the final value is `service-value`.

- [ ] **Step 3: Add script helpers for shared env file list**

For gateway scripts, add:

```bash
SHARED_CONFIG_DIR_DEFAULT=""
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  SHARED_CONFIG_DIR_DEFAULT="$RESOURCE_DIR/config/shared"
fi
export GATEWAY_SHARED_ENV_FILES="${GATEWAY_SHARED_ENV_FILES:-$SHARED_CONFIG_DIR_DEFAULT/infrastructure.shared.env:$SHARED_CONFIG_DIR_DEFAULT/model-endpoints.shared.env:$SHARED_CONFIG_DIR_DEFAULT/infrastructure.secret.env}"
export GATEWAY_ENV_FILES="${GATEWAY_ENV_FILES:-$GATEWAY_SHARED_ENV_FILES:$CONFIG_DIR_DEFAULT/config.env:$CONFIG_DIR_DEFAULT/config.shared.env:$CONFIG_DIR_DEFAULT/config.secret.env:$PROJECT_ROOT/.env}"
```

Apply the same pattern to gateway web and admission-worker scripts.

- [ ] **Step 4: Fix shell env loaders to preserve process env**

For all touched shell `load_env_files()` implementations, change assignment behavior so env files only preserve keys that existed before env-file loading began. Later env files must still override earlier env-file values.

At the start of each script, capture initially exported names before script-owned default exports and before calling `load_env_files()`:

```bash
declare -A PROCESS_ENV_KEYS=()
while IFS='=' read -r key _; do
  [[ -n "${key:-}" ]] || continue
  PROCESS_ENV_KEYS["$key"]=1
done < <(env)
```

For manual parsers (`fastQA`, patent, public-service if present), replace unconditional export:

```bash
if [[ -z "${PROCESS_ENV_KEYS[$name]+x}" ]]; then
  export "${name}=${value}"
fi
```

This means:

- process env wins over all env files
- service-local env files still override shared env files
- later files in `*_ENV_FILES` still override earlier files for values introduced by env files
- script-owned defaults remain overrideable by env files unless the caller explicitly provided the variable in the original process environment

For source-based gateway loaders, avoid direct `source` into the current environment because it overwrites process env. Use a simple parser like the fastQA parser, or source into a temporary env and selectively export only keys not present in `PROCESS_ENV_KEYS`. The simpler implementation is to switch gateway loaders to the same parser pattern used by fastQA.

- [ ] **Step 5: Update patent env file order and tests**

Replace patent's default `PATENT_ENV_FILES` with shared resource files first, then patent service-local files, then public-service legacy files only if still needed:

```bash
PATENT_SHARED_ENV_FILES="$RESOURCE_DIR/config/shared/infrastructure.shared.env:$RESOURCE_DIR/config/shared/model-endpoints.shared.env:$RESOURCE_DIR/config/shared/infrastructure.secret.env"
PATENT_ENV_FILES="${PATENT_ENV_FILES:-$PATENT_SHARED_ENV_FILES:$CONFIG_DIR_DEFAULT/config.env:$CONFIG_DIR_DEFAULT/config.shared.env:$CONFIG_DIR_DEFAULT/config.secret.env:$PUBLIC_SERVICE_ROOT/config.shared.env:$PUBLIC_SERVICE_ROOT/config.secret.env:$PROJECT_ROOT/.env}"
```

Do not remove public-service legacy fallback in this task; some patent config still depends on it.

- [ ] **Step 6: Update public-service start script and top-level service common**

In `public-service/scripts/start_gunicorn.sh`, prepend resource shared files to `PUBLIC_SERVICE_ENV_FILES` while preserving current public-service local env files:

```bash
RESOURCE_DIR="$(cd "$PROJECT_ROOT/../resource" 2>/dev/null && pwd || true)"
PUBLIC_SERVICE_SHARED_ENV_FILES=""
if [[ -n "${RESOURCE_DIR:-}" ]]; then
  PUBLIC_SERVICE_SHARED_ENV_FILES="$RESOURCE_DIR/config/shared/infrastructure.shared.env:$RESOURCE_DIR/config/shared/model-endpoints.shared.env:$RESOURCE_DIR/config/shared/infrastructure.secret.env"
fi
export PUBLIC_SERVICE_ENV_FILES="${PUBLIC_SERVICE_ENV_FILES:-$PUBLIC_SERVICE_SHARED_ENV_FILES:$PROJECT_ROOT/config.shared.env:$PROJECT_ROOT/config.secret.env}"
```

Also update `scripts/_service_common.sh` so `public-service:start` does not pass a hard-coded `PUBLIC_SERVICE_ENV_FILES` that omits shared resource config. Replace:

```bash
PUBLIC_SERVICE_ENV_FILES="$ROOT_DIR/public-service/config.shared.env:$ROOT_DIR/public-service/config.secret.env" \
```

with:

```bash
PUBLIC_SERVICE_ENV_FILES="${PUBLIC_SERVICE_ENV_FILES:-$RESOURCE_DIR/config/shared/infrastructure.shared.env:$RESOURCE_DIR/config/shared/model-endpoints.shared.env:$RESOURCE_DIR/config/shared/infrastructure.secret.env:$ROOT_DIR/public-service/config.shared.env:$ROOT_DIR/public-service/config.secret.env}" \
```

Add a test in `tests/test_shared_env_launchers.py` that inspects or executes the public-service branch through `_service_common.sh` with a stubbed `public-service/scripts/start_gunicorn.sh`, proving the env list includes shared files before public-service local files.

- [ ] **Step 7: Add patent/public-service launcher tests**

Add tests in `tests/test_shared_env_launchers.py` proving the following through observable env output or safely stubbed launchers. Do not let tests start real services.

- patent default env list starts with resource shared files, then service-local patent files, then public-service legacy files.
- patent loader preserves process env values over shared/service env files.
- patent service-local values override shared values when process env is unset.
- public-service default env list starts with resource shared files.
- public-service loader preserves process env values.
- public-service local values override shared values when process env is unset.

- [ ] **Step 8: Run script tests**

Run available tests:

```bash
cd gateway && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_admission_worker_scripts.py -q
eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_shared_env_launchers.py -q
cd public-service/backend && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_config_independence.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add gateway/scripts/start_gunicorn.sh gateway/scripts/start_admission_worker.sh gateway/scripts/run_gunicorn_foreground.sh gateway/scripts/run_admission_worker_foreground.sh scripts/_service_common.sh patent/scripts/start_gunicorn.sh patent/scripts/start.sh public-service/scripts/start_gunicorn.sh gateway/tests/test_admission_worker_scripts.py tests/test_shared_env_launchers.py
git commit -m "chore: load shared backend config in service scripts"
```

## Task 5: Migrate Duplicate Non-Secret Values Out Of Service Configs

**Files:**
- Modify: `resource/config/services/fastQA/config.shared.env`
- Modify: `resource/config/services/highThinkingQA/config.shared.env`
- Modify: `resource/config/services/gateway/config.secret.env.example` if non-secret Redis defaults are currently documented there
- Do not modify ignored real `resource/config/services/patent/config.shared.env`; update tracked examples/docs or record local operator instructions instead.
- Modify: `public-service/config.shared.env`
- Test: targeted config/env tests from previous tasks

- [ ] **Step 1: Remove shared Redis defaults from tracked service files**

Remove these keys where values equal the shared default:

```text
REDIS_ENABLED
REDIS_HOST
REDIS_PORT
REDIS_DB
REDIS_SOCKET_CONNECT_TIMEOUT_SEC
REDIS_SOCKET_TIMEOUT_SEC
```

Keep service-specific prefixes:

```text
REDIS_KEY_PREFIX=fastqa
REDIS_KEY_PREFIX=highthinkingqa
REDIS_KEY_PREFIX=public_service
PATENT_REDIS_KEY_PREFIX=patent
```

For gateway, do not edit or commit real ignored `resource/config/services/gateway/config.secret.env`. If a tracked example file documents non-secret Redis defaults, update the example. Record a local follow-up note for operators to remove duplicate Redis defaults from real ignored gateway secret files after rollout. Keep `REDIS_PASSWORD` in a secret file.

- [ ] **Step 2: Remove shared LLM base URL aliases when safe**

Remove duplicate base URL values from service files only when shared aliases cover the same key name:

```text
DASHSCOPE_BASE_URL
OPENAI_BASE_URL
LLM_BASE_URL
PATENT_OPENAI_BASE_URL
```

Keep service model names when they differ:

```text
fastQA: OPENAI_MODEL=deepseek-v3.1 / DASHSCOPE_MODEL=deepseek-v3.1
highThinkingQA: LLM_MODEL=qwen3-max
patent: PATENT_OPENAI_MODEL=deepseek-v3.1
```

- [ ] **Step 3: Remove shared local embedding defaults from fastQA and patent**

For fastQA and patent only, remove values covered by shared `model-endpoints.shared.env` if exactly equal:

```text
EMBEDDING_MODEL_TYPE
EMBEDDING_API_URL
EMBEDDING_API_MODEL
EMBEDDING_API_TIMEOUT_SECONDS
PATENT_EMBEDDING_MODEL_TYPE
PATENT_EMBEDDING_API_URL
PATENT_EMBEDDING_API_MODEL
PATENT_EMBEDDING_API_TIMEOUT_SECONDS
```

Do not change highThinkingQA DashScope embedding settings.

- [ ] **Step 4: Remove shared rerank defaults from fastQA only if covered**

Remove from `resource/config/services/fastQA/config.shared.env` if shared `model-endpoints.shared.env` defines the same value:

```text
QA_RETRIEVAL_RERANK_PROVIDER
QA_RETRIEVAL_RERANK_BASE_URL
QA_RETRIEVAL_RERANK_MODEL
QA_RETRIEVAL_RERANK_TIMEOUT
```

Keep FastQA-specific knobs:

```text
QA_RETRIEVAL_RERANK_CANDIDATES=50
QA_RETRIEVAL_RERANK_API_KEY=
FASTQA_STAGE2_RERANK_WARMUP_ENABLED=false
```

- [ ] **Step 5: Keep MySQL/MinIO service values conservative**

Only remove non-secret MySQL/MinIO keys from highThinkingQA/public-service when shared values exactly match and service-specific behavior is not implied:

```text
MYSQL_HOST
MYSQL_PORT
MYSQL_DATABASE
MYSQL_CONNECT_TIMEOUT_SECONDS
MYSQL_READ_TIMEOUT_SECONDS
MYSQL_WRITE_TIMEOUT_SECONDS
MINIO_BUCKET
MINIO_SECURE
MINIO_USE_PROXY
MINIO_DOWNLOAD_EXPIRES
```

Do not move real `MYSQL_USER`, `MYSQL_PASSWORD`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, or `MINIO_ENDPOINT` into commit-safe shared files.

- [ ] **Step 6: Run config tests**

Run:

```bash
cd fastQA && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_env_loader.py tests/test_redis_runtime.py tests/test_graph_kb_runtime.py -q
cd highThinkingQA && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_env_loader.py -q
cd public-service/backend && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_config_independence.py -q
eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_shared_env_launchers.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit tracked config changes only**

```bash
git add resource/config/services/fastQA/config.shared.env resource/config/services/highThinkingQA/config.shared.env public-service/config.shared.env resource/config/services/*/*.example public-service/*.example
git commit -m "chore: deduplicate backend shared config values"
```

Do not use `git add -f` for ignored real secret/local env files.

## Task 6: Add Effective Config Verification Documentation

**Files:**
- Modify: `resource/config/README.md`
- Modify: `resource/config/services/fastQA/README.md`
- Modify: `resource/config/services/highThinkingQA/README.md`
- Modify: `resource/config/services/gateway/README.md`
- Modify: `resource/config/services/patent/README.md`
- Modify: `resource/config/services/public-service/README.md`

- [ ] **Step 1: Document shared-first service-local-override policy**

In `resource/config/README.md`, document the final load order and ownership rules:

```markdown
Shared files provide common defaults only. Service files own service identity, ports, prefixes, feature flags, and route behavior. Secret files must not be committed unless they are templates.
```

- [ ] **Step 2: Document per-service retained ownership**

For each service README, list which config remains service-owned:

```markdown
- port / worker counts
- Redis key prefix
- app-specific feature flags
- service-specific model names when they intentionally differ
```

- [ ] **Step 3: Document local secret setup**

Add a short note that deployments may create:

```text
resource/config/shared/infrastructure.secret.env
```

and that service-local `config.secret.env` overrides it.

- [ ] **Step 4: Commit**

```bash
git add resource/config/README.md resource/config/services/fastQA/README.md resource/config/services/highThinkingQA/README.md resource/config/services/gateway/README.md resource/config/services/patent/README.md resource/config/services/public-service/README.md
git commit -m "docs: document shared backend config ownership"
```

## Task 7: Final Verification

**Files:**
- All files touched in Tasks 1-6

- [ ] **Step 1: Run targeted env/config test suite**

Run:

```bash
cd fastQA && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_env_loader.py tests/test_redis_runtime.py tests/test_graph_kb_runtime.py -q
cd highThinkingQA && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_env_loader.py -q
cd gateway && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_admission_worker_scripts.py -q
eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_shared_env_launchers.py -q
cd public-service/backend && eval "$(conda shell.bash hook)" && conda activate agent && pytest tests/test_config_independence.py -q
```

Expected: PASS.

- [ ] **Step 2: Run service status smoke checks if local stack is running**

Do not start or stop services unless the human explicitly asks. If services are already running, inspect health:

```bash
bash scripts/status_all.sh
```

Expected: services that were already running remain healthy. If not running, skip and report that runtime smoke was not performed.

- [ ] **Step 3: Inspect duplicate keys**

Run:

```bash
awk -F= '/^[A-Za-z_][A-Za-z0-9_]*=/{count[$1]++; files[$1]=files[$1] " " FILENAME} END{for (k in count) if (count[k] > 1) print count[k] " " k files[k]}' resource/config/services/*/config*.env public-service/config.shared.env public-service/config.secret.env resource/config/shared/*.env | sort -nr
```

Expected: remaining duplicates are intentional aliases, service-specific values, or secret placeholders. Redis host/port/db and shared model endpoint defaults should no longer be duplicated across service-local commit-safe files.

- [ ] **Step 4: Review git diff**

Run:

```bash
git status --short
git diff --stat HEAD~6..HEAD
```

Expected: only config, loader/script, test, and README files from this plan changed.

## Rollout Notes

1. This refactor changes config loading, so deploy one service first in a staging/dev environment before applying to all services.
2. Shared files must be loaded before service-local files, otherwise service overrides will not work.
3. Do not remove a service-local value until there is a test proving the shared value is loaded for that service.
4. If a service uses a different variable name for the same endpoint, keep compatibility aliases in `model-endpoints.shared.env` until app code is intentionally unified.
5. Public-service resource config migration is a separate follow-up. This plan only prepends shared resource config to its current env stack.

## Out Of Scope

1. Renaming app code to use a single universal config key.
2. Moving real secrets into committed files.
3. Full public-service config-root migration into `resource/config/services/public-service`.
4. Changing service ports, worker counts, Redis key prefixes, or business feature flags.
5. Starting/stopping services during implementation without explicit human approval.
