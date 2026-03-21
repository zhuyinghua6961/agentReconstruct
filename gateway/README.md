# Gateway

Standalone FastAPI gateway for the multi-mode QA architecture.

## Scope

This project is the single backend entrypoint for:
- public API proxying to the `public` backend role
- QA routing across `fast`, `thinking`, and future `patent` backends
- gateway-side file-context resolution and requested-mode to actual-backend decisions
- SSE passthrough for `ask_stream`
- pluggable conversation-file metadata providers

The canonical frontend now lives at the repository root: [`frontend-vue/`](/home/cqy/worktrees/highThinking/frontend-vue).

## Run

```bash
export PUBLIC_BACKEND_BASE_URL=http://127.0.0.1:8102
export FAST_BACKEND_BASE_URL=http://127.0.0.1:8008
export THINKING_BACKEND_BASE_URL=http://127.0.0.1:8009
export PATENT_BACKEND_BASE_URL=http://127.0.0.1:8010
export GATEWAY_CONVERSATION_FILE_PROVIDER=public_http
conda run --no-capture-output -n agent gunicorn -k uvicorn.workers.UvicornWorker app.main:app --chdir /home/cqy/worktrees/highThinking/gateway --bind 0.0.0.0:8101 --workers 1 --timeout 600
```

Frontend during local development:

```bash
cd /home/cqy/worktrees/highThinking/frontend-vue
npm install
npm run dev
```

## Environment

```bash
export PUBLIC_BACKEND_BASE_URL=http://127.0.0.1:8102
export FAST_BACKEND_BASE_URL=http://127.0.0.1:8008
export THINKING_BACKEND_BASE_URL=http://127.0.0.1:8009
export PATENT_BACKEND_BASE_URL=http://127.0.0.1:8010
export GATEWAY_CONVERSATION_FILE_PROVIDER=public_http
```

Provider options:
- `noop`: gateway ignores conversation file metadata
- `public_http`: gateway fetches `/api/conversations/{id}/files` from the public backend and reuses auth headers

## Tests

```bash
conda run --no-capture-output -n agent pytest -q tests -p no:cacheprovider
```

## Current Behavior

- `POST /api/ask` and `POST /api/ask_stream` remain compatibility aliases.
- `POST /api/{mode}/ask` and `POST /api/{mode}/ask_stream` are the preferred QA routes.
- Public infrastructure routes stay under `/api/...` and proxy through the `public` backend role.
- If a request is file-aware or mixed, the gateway can override the selected mode and route execution to `fast`.

## Protocol Docs

- `docs/gateway_forwarding_protocol.md`: frontend <-> gateway <-> backend forwarding contract
