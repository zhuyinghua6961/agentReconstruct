# Single-Folder Independent Services Review

## Goal

Target shape:

```text
/home/cqy/worktrees/highThinking
├── gateway/
├── public-service/
├── highThinkingQA/
├── fastQA/
├── patent/
└── resource/
```

This is a single repository layout, not a single process layout.

Rules:

- each service remains independently runnable
- each service keeps its own port, startup script, and Python import root
- services may share one repository, but must not share mutable runtime state implicitly

## Current Status

Already colocated:

- [`gateway/`](/home/cqy/worktrees/highThinking/gateway)
- [`public-service/`](/home/cqy/worktrees/highThinking/public-service)

Still at repository root and not yet isolated as a service directory:

- current `highThinking` backend and frontend

Still external and only a source baseline:

- `/home/cqy/worktrees/fastapi-version`

## Service-by-Service Review

### Gateway

Current state:

- already works as an independent subproject
- uses its own `pyproject.toml`
- uses its own startup scripts under `gateway/scripts/`
- now has a local test import shim in [`gateway/tests/conftest.py`](/home/cqy/worktrees/highThinking/gateway/tests/conftest.py)

Must stay true:

- independent process on `8101`
- canonical repository-root frontend under `frontend-vue/` that targets the gateway
- independent `.runtime`

Do not do:

- do not merge `gateway` routes into root `highThinking` FastAPI

Reason:

- `gateway` and root `highThinking` both occupy `/api/*`

Open issues:

- backend URL env is still shell-injected
- frontend startup docs must consistently point to the repository-root `frontend-vue/`
- placeholder `thinking/patent` backend URLs still require explicit configuration

### Public-Service

Current state:

- already works as an independent subproject
- startup assumes `backend/` is the import root
- config supports explicit `PUBLIC_SERVICE_DATA_ROOT`

Must stay true:

- independent process on `8102`
- independent `backend/` import root
- independent env loading
- independent data root

Do not do:

- do not run it as if it were part of root `highThinking/server_fastapi`

Open issues:

- historical JSON/runtime data contains old absolute `local_path`
- env names still overlap with root `highThinking`
- docs still contain old worktree references

### Current Root HighThinking

Current state:

- still occupies repository root directly
- owns backend code, frontend code, runtime directories, and env loader
- still exposes public APIs and QA APIs in the same service surface

Future target:

- move it into `highThinkingQA/`

Minimum move unit:

- `server_fastapi/`
- `server/`
- `agent_core/`
- `ingest/`
- `retriever/`
- `prompts/`
- `tests/`
- `scripts/`
- config files and env loader
- its own frontend only if you still want a service-local frontend during transition

Main risk:

- current config and env loader assume repository root
- current runtime directories are still at repository root: `uploads/`, `papers/`, `vectordb/`, `.runtime/`, `cache/`

### Fastapi-Version -> FastQA

Current state:

- still a mixed monolith baseline
- contains public capability code plus ask gateway plus QA execution plus frontend plus runtime state

Future target:

- extract a QA-only `fastQA/`

Minimum move unit:

- `backend/app/modules/ask_gateway`
- `backend/app/modules/qa_kb`
- `backend/app/modules/qa_pdf`
- `backend/app/modules/qa_tabular`
- `backend/app/modules/file_context`
- required shared execution/runtime modules

Do not carry over:

- monolith public APIs as the source of truth
- old uploads/papers/vector_database/runtime state

Main risk:

- deep coupling to public-service-like capabilities
- many root-path and `WORKSPACE_DIR` assumptions
- scripts still inject old absolute paths and extra `PYTHONPATH`

## Colocation Rules

### Allowed to share

- repository
- docs
- ops scripts
- read-only assets
- protocol definitions

### Not allowed to share implicitly

- uploads directory
- conversations JSON directory
- vector database directory
- translation cache
- `.runtime` or `.run`
- logs
- local fallback storage

### Required isolation

- each service has its own startup entry
- each service has its own env contract
- each service has its own runtime directory
- each service has its own mutable state root

## Recommended Resource Layout

```text
resource/
  config/
  assets/
  state/
  runtime/
```

Usage:

- `resource/config`: shared templates and service-local env templates
- `resource/assets`: read-only corpora, models, templates
- `resource/state`: durable mutable state, separated by service
- `resource/runtime`: pid/log/temp files, separated by service

## Migration Order

### P0

1. Keep `gateway` and `public-service` independent.
2. Do not attempt process-level merging.
3. Freeze service ports and env boundaries.
4. Freeze service-owned runtime roots.

### P1

1. Create `resource/` contract.
2. Move current root `highThinking` into `highThinkingQA/`.
3. Rewrite root-bound config assumptions in `highThinkingQA`.

### P2

1. Extract `fastQA` from `fastapi-version`.
2. Keep only QA execution responsibilities in `fastQA`.
3. Reuse `public-service` for auth/conversation/upload/document truth data.

### P3

1. Add `patent/`.
2. Put all final service docs and runbooks under unified `docs/` and `ops/`.

## Risk Checklist

- High: route collision if `gateway` is merged into root FastAPI
- High: old absolute paths inside `public-service` runtime data
- High: root `highThinking` still mixes public APIs and QA APIs
- High: mutable runtime directories still live at repository root
- Medium: env variable names overlap across services
- Medium: `fastapi-version` still depends on monolith-style root paths and injected `PYTHONPATH`
- Medium: duplicated frontends can confuse operators about the real entrypoint

## Practical Conclusion

Putting all services into one folder is feasible.

The correct model is:

- same repository
- separate services
- separate startup
- separate runtime roots
- shared docs and resource contracts

The incorrect model is:

- same repository
- same FastAPI process
- same `/api/*` surface
- same mutable directories
