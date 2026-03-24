# Gateway And Public-Service Import Tasks

## Status

Completed in this step:

- copied `gateway/` source tree into the current repository
- copied `public-service/` source tree into the current repository
- excluded runtime, cache, build artifacts, uploaded files, and vector indexes
- corrected copied runnable path examples
- corrected `gateway` frontend proxy example
- added a local `gateway` test import shim

## P0

### T0.1 Freeze the operating model

- keep `gateway` as an independent service on `8101`
- keep `public-service` as an independent service on `8102`
- do not merge either one into the root `highThinking` FastAPI process

Why:

- route overlap with root `highThinking` is currently blocking
- copied projects still assume independent package/import roots

### T0.2 Freeze path ownership

- `gateway/` owns only its own `.runtime`
- `public-service/` owns only its own data root and `.runtime`
- root `highThinking` keeps its current runtime paths temporarily
- do not reuse copied runtime state from old worktrees

### T0.3 Freeze env boundaries

- `gateway` uses only gateway env files or shell exports
- `public-service` uses only public-service env files
- do not share one shell-wide config block with root `highThinking`

Why:

- variable names still overlap: `APP_ENV`, `UPLOAD_DIR`, `PAPERS_DIR`, MySQL, Redis, MinIO

### T0.4 Freeze test and import entrypoints

- run `gateway` tests from `gateway/`
- run `public-service` tests from `public-service/`
- treat `gateway/` as the Python root for `gateway`
- treat `public-service/backend/` as the Python root for `public-service`

Why:

- copied subprojects still assume their own import roots

## P1

### T1.1 Introduce `resource/` roots

- create `resource/config`
- create `resource/assets`
- create `resource/state`
- create `resource/runtime`

Goal:

- stop relying on repository-root state directories

### T1.2 Move `public-service` toward explicit state roots

- replace example roots with future `resource/state/...`
- define one durable owner for uploads, papers, translation cache, vector db
- avoid reusing old `data/runtime` blindly

### T1.3 Keep `gateway` stateless

- no uploads
- no vector db
- no conversation truth data
- runtime only

## P2

### T2.1 Split root `highThinking` into `highThinkingQA`

- move current root service code into a dedicated QA backend directory
- remove duplicated public routes over time

### T2.2 Add `fastQA`

- start from the future gateway contract
- keep it QA-only

### T2.3 Add `patent`

- keep it QA-only
- reuse public-service for shared auth/conversation/file/document flows

## Known Path Problems Still Open

- `public-service` historical JSON data contains absolute `local_path`
- `public-service` still assumes `backend` as import root
- `gateway` and root frontend still share similarly named env vars
- `gateway` and root `highThinking` cannot share the same `/api/*` FastAPI surface in one process
- many historical docs still reference old worktree paths
- no monorepo-level env launcher exists yet

## Validation Checklist

- `cd gateway && conda run -n agent pytest -q tests -p no:cacheprovider`
- `cd public-service && conda run -n agent pytest backend/tests/test_config_independence.py -q`
- verify `gateway` README and env examples point to the current repository
- verify `public-service` README and env examples point to the current repository
- verify no copied runtime or build garbage exists under `gateway/` or `public-service/`
