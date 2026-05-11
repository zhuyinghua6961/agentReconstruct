# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a multi-service QA system monorepo. The frontend talks to a single gateway, which routes requests to mode-specific backends.

| Service | Role | Default Port |
|---------|------|-------------|
| `frontend-vue/` | Vue 3 + Vite frontend | 5173 |
| `gateway/` | FastAPI gateway: routing, proxy, file-context resolution, quota precheck/finalize | 8101 |
| `public-service/` | Public capabilities backend (auth, conversations, uploads, documents, quota, admin) | 8102 |
| `fastQA/` | Fast-mode QA executor (`kb_qa`) | 8008 |
| `highThinkingQA/` | Thinking-mode QA executor (agent-core graph with decomposer/answerer/synthesizer) | 8009 |
| `patent/` | Patent-mode QA executor | 8010 |
| `resource/` | Shared config, runtime state, logs, assets, vector DBs | — |

## Common Commands

### Frontend
```bash
cd frontend-vue
npm install
npm run dev        # Vite dev server on 5173, proxies /api/* to gateway :8101
npm run build      # Production bundle; this is the frontend quality gate
npm run test       # Node built-in test runner
```

### Backend (all services)
```bash
# Start the full backend stack
bash scripts/start_all.sh
bash scripts/status_all.sh
bash scripts/stop_all.sh

# Control an individual service
bash scripts/_service_common.sh gateway:start
bash scripts/_service_common.sh public-service:start
bash scripts/_service_common.sh fastQA:start
bash scripts/_service_common.sh highThinkingQA:start
bash scripts/_service_common.sh patent:start
```

### Tests
Tests are service-local and use `pytest`. There is no top-level test runner.

```bash
# Gateway
cd gateway && conda run --no-capture-output -n agent pytest -q tests -p no:cacheprovider

# fastQA
cd fastQA && conda run -n agent pytest tests -q

# highThinkingQA
cd highThinkingQA && pytest tests -q

# patent
cd patent && pytest tests -q

# public-service
cd public-service/backend && pytest tests -q
```

Run a single test:
```bash
pytest -q tests/test_health.py::test_health_returns_runtime_payload
```

Most gateway and backend tests use `fastapi.testclient.TestClient` or direct route calls. The fastQA HTTP contract tests use direct SSE consumption because `httpx.ASGITransport` hangs in the local test stack.

## High-Level Architecture

### Gateway Routing and Proxying
The gateway is the single browser-facing backend entrypoint. It does not execute QA logic; it decides where to send requests and proxies them.

Key gateway responsibilities:
- **Mode routing**: `POST /api/{mode}/ask` and `POST /api/{mode}/ask_stream` where `mode ∈ {fast, thinking, patent}`. Legacy aliases `/api/ask` and `/api/ask_stream` still exist.
- **Public proxy**: most `/api/...` infrastructure routes (auth, conversations, uploads, documents, quota, admin) proxy through to `public-service`.
- **File-context resolution**: before forwarding a QA request, the gateway resolves conversation file metadata (via `ConversationFileService` / `public_http` provider) and decides whether the turn is plain KB, PDF, tabular, or hybrid.
- **Route decision**: `RouteDecisionService` produces `actual_mode`, `route`, `turn_mode`, `source_scope`, `needs_clarification`, etc. The gateway may override the requested mode (e.g., file-aware requests can be routed to `fast`).
- **Quota gating**: for `ask` and `ask_stream`, the gateway prechecks quota with `public-service`, obtains a `grant_id`, and finalizes it after the response/stream completes. Quota types: `ask_query` for plain KB, `file_qa` for file routes.
- **SSE passthrough**: streaming responses from backends are forwarded as SSE. The gateway parses frames (`metadata`, `step`, `content`, `done`, `error`), logs first-step and first-content timings, and injects a `quota` field into the final `done` frame.

Important gateway state stores (backed by Redis when enabled):
- `ExecutionQueueStatusStore`
- `ExecutionSlotLeaseStore`
- `ExecutionEventRelayStore`
- `DistributedLockManager`

Gateway environment variables to know:
- `PUBLIC_BACKEND_BASE_URL`, `FAST_BACKEND_BASE_URL`, `THINKING_BACKEND_BASE_URL`, `PATENT_BACKEND_BASE_URL`
- `GATEWAY_CONVERSATION_FILE_PROVIDER` (`noop` or `public_http`)
- `REDIS_ENABLED`, `REDIS_HOST`, `REDIS_PORT`
- `GATEWAY_ADMISSION_ENABLED`, `GATEWAY_ADMISSION_DISPATCHER_ENABLED`

### Backend Service Boundaries
Each backend is expected to be independent. They should not reach into each other’s source trees.

- **fastQA** owns only fast `kb_qa` execution. It must not perform auth, quota, conversation metadata lookup, or file-context parsing. The gateway sends normalized payloads.
- **highThinkingQA** owns thinking-mode execution via an agent graph (`agent_core/graph.py` with decomposer, sub-answerer, synthesizer, reviser, checker). It is still in migration from an older monolithic layout.
- **patent** owns patent-mode QA, including a graph-KB pipeline (`server_fastapi/routers/ask.py`) and tabular/PDF routes.
- **public-service** owns all shared infrastructure: auth, users, departments, conversations, uploads, documents, quota, system settings, admin.

### SSE Contract
All QA backends emit gateway-stable SSE frames:
- `metadata` — `type=metadata`, `query_mode`, `trace_id`
- `step` — `type=step`, `step`, `status`, `message`
- `content` — `type=content`, `content`
- `done` — `type=done`, `references`, `trace_id`, optional `timings` and `quota`
- `error` — `type=error`, `error`, `message`, `trace_id`, optional `code`

### Frontend
- Vue 3, Vite, Pinia, Vue Router.
- Dev proxy config lives in `vite.config.js`: `/api/*` -> `http://127.0.0.1:8101`.
- Optional env: `VITE_API_BASE_URL`, `VITE_PROXY_TARGET`.
- Key stores under `src/stores/`: `chatStore.js` manages streaming, session switching, and localStorage persistence.

## Code Conventions

- **Python**: 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes.
- **Vue/JS**: preserve existing component structure and naming.
- **Commits**: Conventional Commit prefixes (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`). Keep each commit scoped to one concern.
- **Secrets**: belong in `config.secret.env` or local `.env`, never in committed code.
- **Runtime directories** (`.runtime/`, `resource/runtime/`, `resource/state/`, `frontend-vue/dist/`, `archive/`) are ignored by Git.
