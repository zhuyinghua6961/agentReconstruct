# Gateway And Public-Service Import Spec

## Scope

This document records the first consolidation step inside the current repository:

- copy `gateway` into [`gateway/`](/home/cqy/worktrees/highThinking/gateway)
- copy `public-service` into [`public-service/`](/home/cqy/worktrees/highThinking/public-service)
- keep current `highThinking` code at repository root for now

This is not yet the final monorepo layout. It is a staging step before splitting `highThinkingQA`, `fastQA`, `patent`, and `resource`.

## Imported Content

Copied into `gateway/`:

- `app/`
- `frontend-vue/`
- `tests/`
- `scripts/`
- `docs/`
- `pyproject.toml`
- `README.md`
- `AGENTS.md`

Copied into `public-service/`:

- `backend/`
- `scripts/`
- config templates
- migration and protocol documents
- `public-modules/`

Excluded during copy:

- `.git/`
- `.runtime/`
- `.pytest_cache/`
- `__pycache__/`
- `frontend-vue/node_modules/`
- `frontend-vue/dist/`
- `public-service/uploads/`
- `public-service/papers/`
- `public-service/vector_database/`
- `public-service/data/runtime/`

Reason:

- do not import machine-local runtime state
- do not import build artifacts
- do not import stale uploaded files or vector indexes

## Current Layout After Import

```text
/home/cqy/worktrees/highThinking
├── gateway/
├── public-service/
├── frontend-vue/
├── server_fastapi/
├── server/
├── agent_core/
└── docs/
```

Meaning:

- root-level `frontend-vue/` is now the promoted canonical frontend copied from the gateway project
- root-level backend code still represents the current `highThinking` backend
- `gateway/` and `public-service/` are now colocated source trees
- final monorepo restructuring is still pending

## Immediate Path Fixes Already Applied

- updated `gateway` runnable path examples to `/home/cqy/worktrees/highThinking/gateway`
- updated `public-service` runnable path examples to `/home/cqy/worktrees/highThinking/public-service`
- updated `PUBLIC_SERVICE_DATA_ROOT` examples to the new in-repo location

## Path Audit Findings

### Gateway

Low-risk items:

- start/stop scripts are already root-relative
- Python imports are local to the copied `gateway/` tree
- frontend source layout is self-contained

Still needs follow-up:

- tests currently assume `gateway/` itself is the Python project root; running `pytest` from repository root will fail unless the project root is added to `sys.path`
- `gateway` must stay an independent service; it cannot be merged into the current root `highThinking` FastAPI app because `/api/*` routes overlap
- docs and historical specs still reference old worktree paths
- backend URL defaults still point to placeholder ports and rely on env injection
- frontend install/build artifacts are intentionally absent and must be rebuilt locally
- frontend proxy env now belongs to the canonical root `frontend-vue/` and should be maintained there

### Public-Service

Low-risk items:

- gunicorn scripts are already root-relative
- backend module imports are local to `public-service/backend`
- config loader already supports an explicit data root

Still needs follow-up:

- many docs still reference old worktree paths
- config examples still use an absolute local data root pattern instead of future `resource/` roots
- runtime behavior still assumes service-owned local directories such as `uploads`, `papers`, `vector_database`
- secret file remains local-only and must not be pushed remotely
- historical runtime JSON data stores absolute `local_path` values from the old worktree; this data must not be reused blindly after the copy
- `public-service` still assumes `backend/` is the import root and must continue to be launched as an independent subproject

### Cross-Service

Structural issues not fixed in this step:

- current root `highThinking` code still owns some public APIs and state
- no unified `resource/` directory exists yet
- gateway and public-service are colocated, but not yet rewired to shared monorepo config roots
- duplicated frontend/backend operational docs now exist in multiple places
- `gateway`, `public-service`, and root `highThinking` still share overlapping environment variable names, so one shell-wide env block is unsafe

## Recommended Next-Step Structure

Target direction:

```text
/home/cqy/worktrees/highThinking
├── gateway/
├── public-service/
├── highThinkingQA/
├── fastQA/
├── patent/
├── resource/
├── docs/
└── ops/
```

Recommended meaning:

- `gateway/`: single ingress and frontend
- `public-service/`: auth, conversation, upload, storage, documents, quota
- `highThinkingQA/`: thinking backend only
- `fastQA/`: fast backend only
- `patent/`: patent backend only
- `resource/`: shared config, assets, state, runtime roots

## Resource Direction

Future shared roots should be explicit:

- `resource/config/`
- `resource/assets/`
- `resource/state/`
- `resource/runtime/`

Do not keep long-term shared state at repository root.

## Risk Checklist

- High: root `highThinking` and `public-service` still overlap in public capability ownership
- High: multiple services still rely on local filesystem semantics
- High: several config examples still encode host-specific absolute paths
- Medium: copied docs reference old worktrees and can mislead operators
- Medium: no single monorepo-level env contract exists yet
- Medium: frontend truth source is still not formally frozen

## Acceptance For This Step

This import step is considered complete when:

- `gateway/` and `public-service/` source trees exist in the current repository
- runtime/build garbage was not copied in
- runnable path examples point to the current repository
- import/path risks are documented for the next restructuring phase
