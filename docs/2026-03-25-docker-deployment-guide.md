# Docker Deployment Guide

## Goal

This guide defines a portable Docker deployment approach for the current monorepo so the bundle can be moved to another machine and started with Docker Compose, without relying on local Conda environments or source-code bind mounts.

The intended deliverable is:

- application images for `gateway`, `public-service`, `fastQA`, and `highThinkingQA`
- a dedicated `gateway-admission-worker` container or Compose service using the same gateway image with a worker-role entrypoint
- infrastructure containers for `mysql`, `redis`, and `minio`
- initialization assets for MySQL schema, MinIO bucket setup, and vector-database data
- one deployment env file that can be edited per target environment

## Current Conclusion

Portable Docker deployment is feasible, but not by packaging only the Python services.

The deployment bundle must include:

- service images
- MySQL schema initialization
- MinIO bucket initialization
- vector-database data
- required state assets such as `papers` where the current service logic expects them

Without those assets, the services may start, but retrieval, storage, or persistence paths will be incomplete.

## Services

- `gateway`
  - port `8101`
  - routes requests to `public-service`, `fastQA`, and `highThinkingQA`
- `public-service`
  - port `8102`
  - depends on MySQL, Redis, and MinIO
- `fastQA`
  - port `8008`
  - depends on Redis
  - can also use MinIO-backed file materialization and internal calls to `public-service`
- `highThinkingQA`
  - port `8009`
  - depends on MySQL, Redis, and MinIO

## Packaging Principle

For portability, package code and dependencies into images, and package mutable data separately.

Recommended split:

- application images
  - code
  - Python dependencies
  - default bundled prompts and static assets
- deployment data bundle
  - MySQL schema SQL
  - vector-database directories
  - required paper/document assets
  - MinIO initialization script

Do not rely on:

- local Conda environments
- host-side source code mounts
- current `start_*.sh` scripts as container entrypoints

The existing start scripts background Gunicorn with `nohup ... &`, which is unsuitable for container PID 1 operation.

## Required Initialization Assets

### MySQL

The current repository does not provide a complete, portable, empty-database bootstrap path for all services.

Observed state:

- `highThinkingQA` has SQL migration files under `highThinkingQA/server/database/migrations/`
- `public-service` does not currently expose an equivalent migration directory in this worktree
- `public-service` still contains repository logic that probes live schema compatibility at runtime

Deployment requirement:

- provide a schema-only SQL snapshot that can initialize the required tables from an empty database
- mount it into MySQL init, for example through `docker-entrypoint-initdb.d`

### MinIO

The deployment must create the expected bucket before traffic begins.

Minimum requirement:

- create bucket `agentcode`, or the bucket configured through deployment env

Recommended approach:

- add a one-shot `minio-init` container that waits for MinIO and creates the bucket

### Vector Databases And Retrieval Assets

The following data should be treated as deployment data, not as ephemeral runtime output:

- `public-service` vector database under `VECTOR_DB_PATH`
- `highThinkingQA` Chroma persistence directory under `CHROMA_PERSIST_DIR`
- `fastQA` vector directories such as `VECTOR_DB_PATH`, `VECTOR_DB_PDF_PATH`, `VECTOR_DB_MD_PATH`, and related state roots when retrieval must work immediately
- required `papers` directories used by retrieval or document services

Recommendation:

- package these directories into a data archive or a dedicated seed image
- copy them into named volumes on first start

Do not bake large vector data directly into the main application images unless image size is acceptable and updates are infrequent.

Current worktree note:

- `fastQA` primary corpus and vector assets are under `resource/fastqa/`
- `highThinkingQA` primary papers and vectordb are under `resource/highThinkingQA/`
- the smaller `resource/state/dev/...` directories are partial runtime state and should be treated as fallback sources, not the preferred portable deployment source
- to avoid duplicate packaging, `fastQA` papers should be shipped through MinIO object seed, not copied a second time into `seed-data/fastQA/`

## Recommended Deployment Layout

Suggested deployment bundle:

- `docker-compose.yml`
- `.env.example`
- `mysql-init/001_schema.sql`
- `minio-init/init.sh`
- `seed-data/`
  - `public-service/vector_database/`
  - `highThinkingQA/vectordb/`
  - `fastQA/...`
  - required `papers/`
- image tarballs or published image references
- this deployment guide

## Container Runtime Rules

### Entrypoints

Run services in the foreground.

Recommended foreground commands:

- `gateway`
  - preferred: `bash /app/gateway/scripts/run_gunicorn_foreground.sh`
  - equivalent raw command: `gunicorn -k uvicorn.workers.UvicornWorker app.main:app --chdir /app/gateway --bind 0.0.0.0:8101 --workers 8 --timeout 600`
- `gateway-admission-worker`
  - preferred: `bash /app/gateway/scripts/run_admission_worker_foreground.sh`
  - equivalent raw command: `python -m app.services.execution_admission`
- `public-service`
  - `gunicorn -k uvicorn.workers.UvicornWorker app.main:app --chdir /app/public-service/backend --bind 0.0.0.0:8102 --workers 8 --timeout 600`
- `fastQA`
  - `gunicorn -k uvicorn.workers.UvicornWorker app.main:app --chdir /app/fastQA --bind 0.0.0.0:8008 --workers 8 --timeout 600`
- `highThinkingQA`
  - `gunicorn server_fastapi.asgi:app --chdir /app/highThinkingQA -c /app/highThinkingQA/server_fastapi/gunicorn.conf.py`

### Images

Prefer:

- one shared Python base image with the merged runtime dependency set
- four thin service images inheriting from that base

This is more robust than trying to derive exact per-service dependency manifests from the current repository state.

## Deployment Configuration Model

The clean deployment target is:

- all runtime config is supplied by Docker Compose `environment` or `env_file`
- secrets are not stored in the image
- all service-to-service URLs use container DNS names
- all model access uses remote URLs, not local model files
- the Docker bridge network can be pinned explicitly to avoid overlap with host or VPN routes

For interactive admission specifically:

- `gateway-web` and `gateway-admission-worker` should share the same base image and mostly the same env file
- role selection must come from environment variables or command selection, not from a single container running both roles
- admission concurrency and retention settings must be operator-configurable through Compose env, not baked into the image

Recommended compose network defaults for this bundle:

- `DOCKER_BRIDGE_SUBNET=172.20.0.0/24`
- `DOCKER_BRIDGE_GATEWAY=172.20.0.1`

Avoid reusing host-conflicting ranges such as any Docker or VPN segments already occupying `172.18.*` or `172.19.*`.

## Configuration Inventory

### Already Environment-Configurable

These items are already driven by environment variables in current code and are suitable for Docker Compose configuration.

#### Gateway

- backend routing URLs
  - `PUBLIC_BACKEND_BASE_URL`
  - `FAST_BACKEND_BASE_URL`
  - `THINKING_BACKEND_BASE_URL`
  - `PATENT_BACKEND_BASE_URL`
- gateway runtime
  - `GATEWAY_PORT`
  - `GATEWAY_HOST`
  - `GATEWAY_REQUEST_TIMEOUT_SECONDS`
  - `GATEWAY_SSE_TIMEOUT_SECONDS`
  - `GATEWAY_CONVERSATION_FILE_PROVIDER`
- gateway interactive admission
  - `INTERACTIVE_EXECUTION_MAX_CONCURRENT`
  - `INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT`
  - `INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT`
  - `INTERACTIVE_QUEUED_TTL_SECONDS`
  - `INTERACTIVE_POST_ADMIT_ATTACH_TTL_SECONDS`
  - `GATEWAY_ADMISSION_ENABLED`
  - `GATEWAY_ADMISSION_DISPATCHER_ENABLED`
  - `GATEWAY_RUNTIME_ROLE`
  - `GATEWAY_ADMISSION_CONTROL_TOKEN`
  - `REDIS_ENABLED`
  - `REDIS_URL`
  - `REDIS_HOST`
  - `REDIS_PORT`
  - `REDIS_USERNAME`
  - `REDIS_PASSWORD`
  - `REDIS_DB`
  - `REDIS_KEY_PREFIX`

Recommended deployment rule for these admission settings:

- keep them in Compose `environment` or shared `env_file`
- let `gateway-web` and `gateway-admission-worker` read the same values
- override only role-selection variables per service

Recommended baseline values for the first admission rollout:

- `INTERACTIVE_EXECUTION_MAX_CONCURRENT=10`
- `INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT=10`
- `INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT=2`
- `INTERACTIVE_QUEUED_TTL_SECONDS=900`
- `INTERACTIVE_POST_ADMIT_ATTACH_TTL_SECONDS=600`
- `GATEWAY_ADMISSION_ENABLED=1`
- `GATEWAY_ADMISSION_DISPATCHER_ENABLED=1`

### Gateway Compose Pattern

Use one shared image and env file, but run separate Compose services for the two gateway roles.

```yaml
services:
  gateway-web:
    image: your-registry/gateway:latest
    command: ["bash", "/app/gateway/scripts/run_gunicorn_foreground.sh"]
    env_file:
      - ./gateway.env
    environment:
      GATEWAY_RUNTIME_ROLE: web
      GATEWAY_ADMISSION_ENABLED: "1"
      GATEWAY_ADMISSION_DISPATCHER_ENABLED: "1"
    depends_on:
      redis:
        condition: service_healthy
      public-service:
        condition: service_started
      fastqa:
        condition: service_started
      highthinkingqa:
        condition: service_started
    ports:
      - "8101:8101"

  gateway-admission-worker:
    image: your-registry/gateway:latest
    command: ["bash", "/app/gateway/scripts/run_admission_worker_foreground.sh"]
    restart: unless-stopped
    env_file:
      - ./gateway.env
    environment:
      GATEWAY_RUNTIME_ROLE: admission_worker
      GATEWAY_ADMISSION_ENABLED: "1"
      GATEWAY_ADMISSION_DISPATCHER_ENABLED: "1"
    depends_on:
      redis:
        condition: service_healthy
```

Operational rule:

- `gateway-web` can scale horizontally behind one service endpoint, but all replicas must share the same Redis admission state
- `gateway-admission-worker` should run as a dedicated worker deployment and may also scale later if the dispatcher logic is designed for multi-consumer coordination
- `gunicorn` worker counts do not replace `INTERACTIVE_EXECUTION_MAX_CONCURRENT`; the Redis admission ceiling remains the cluster-wide source of truth
- if the admission worker is configured fail-closed on missing Redis, Compose should use Redis healthchecks plus a worker restart policy so cold-start ordering does not leave the worker permanently exited

#### public-service

- app and data roots
  - `PUBLIC_SERVICE_HOST`
  - `PUBLIC_SERVICE_PORT`
  - `PUBLIC_SERVICE_DATA_ROOT`
  - `UPLOAD_DIR`
  - `PAPERS_DIR`
  - `CHAT_JSON_BASE_DIR`
  - `VECTOR_DB_PATH`
  - `TRANSLATION_CACHE_DIR`
  - `PUBLIC_SERVICE_LOGS_DIR`
  - `LOCAL_STORAGE_ROOT`
- MySQL
  - `MYSQL_HOST`
  - `MYSQL_PORT`
  - `MYSQL_USER`
  - `MYSQL_PASSWORD`
  - `MYSQL_DATABASE`
- Redis
  - `REDIS_ENABLED`
  - `REDIS_URL`
  - `REDIS_HOST`
  - `REDIS_PORT`
  - `REDIS_USERNAME`
  - `REDIS_PASSWORD`
  - `REDIS_DB`
  - `REDIS_KEY_PREFIX`
- MinIO
  - `MINIO_ENDPOINT`
  - `MINIO_ACCESS_KEY`
  - `MINIO_SECRET_KEY`
  - `MINIO_BUCKET`
  - `MINIO_SECURE`
  - `MINIO_REGION`
  - `MINIO_USE_PROXY`
  - `MINIO_DOWNLOAD_EXPIRES`
- document translation and retrieval
  - `OPENAI_API_KEY`
  - `OPENAI_BASE_URL`
  - `DASHSCOPE_API_KEY`
  - `NEO4J_URL`
  - `NEO4J_USERNAME`
  - `NEO4J_PASSWORD`

#### highThinkingQA

- LLM and embedding
  - `LLM_API_KEY`
  - `LLM_BASE_URL`
  - `LLM_MODEL`
  - `EMBEDDING_API_KEY`
  - `EMBEDDING_BASE_URL`
  - `EMBEDDING_MODEL`
  - `OCR_API_KEY`
  - `OCR_BASE_URL`
  - `OCR_MODEL`
  - `DASHSCOPE_API_KEY`
  - `DASHSCOPE_BASE_URL`
- service roots and runtime paths
  - `HIGHTHINKINGQA_SERVICE_CONFIG_ROOT`
  - `HIGHTHINKINGQA_SERVICE_STATE_ROOT`
  - `HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT`
  - `HIGHTHINKINGQA_SERVICE_ASSET_ROOT`
  - `PAPERS_DIR`
  - `CHROMA_PERSIST_DIR`
  - `PROMPTS_DIR`
  - `UPLOAD_DIR`
  - `CHAT_JSON_BASE_DIR`
- HTTP and Gunicorn
  - `APP_HOST`
  - `APP_PORT`
  - `GUNICORN_WORKERS`
  - `GUNICORN_THREADS`
  - `GUNICORN_TIMEOUT`
- MySQL
  - `MYSQL_HOST`
  - `MYSQL_PORT`
  - `MYSQL_USER`
  - `MYSQL_PASSWORD`
  - `MYSQL_DATABASE`
- Redis
  - `REDIS_ENABLED`
  - `REDIS_URL`
  - `REDIS_HOST`
  - `REDIS_PORT`
  - `REDIS_PASSWORD`
  - `REDIS_DB`
  - `REDIS_KEY_PREFIX`
- MinIO
  - `MINIO_ENDPOINT`
  - `MINIO_ACCESS_KEY`
  - `MINIO_SECRET_KEY`
  - `MINIO_BUCKET`
  - `MINIO_SECURE`
  - `MINIO_REGION`
  - `MINIO_USE_PROXY`
  - `MINIO_DOWNLOAD_EXPIRES`
- internal service URL
  - `PUBLIC_SERVICE_INTERNAL_BASE_URL`

#### fastQA

- generation runtime
  - `OPENAI_API_KEY`
  - `OPENAI_BASE_URL`
  - `DASHSCOPE_API_KEY`
  - `DASHSCOPE_BASE_URL`
- remote embedding behavior
  - `EMBEDDING_MODEL_TYPE`
  - `EMBEDDING_API_URL`
  - `EMBEDDING_API_MODEL`
  - `EMBEDDING_MODEL_NAME`
- service roots and data paths
  - `FASTQA_SERVICE_CONFIG_ROOT`
  - `FASTQA_SERVICE_STATE_ROOT`
  - `FASTQA_SERVICE_RUNTIME_ROOT`
  - `FASTQA_SERVICE_ASSET_ROOT`
  - `VECTOR_DB_PATH`
  - `VECTOR_DB_PDF_PATH`
  - `VECTOR_DB_MD_PATH`
  - `VECTOR_DB_SUMMARY_PATH`
  - `VECTOR_DB_COMMUNITY_PATH`
  - `PAPERS_DIR`
  - `CHAT_JSON_BASE_DIR`
- Redis
  - `REDIS_ENABLED`
  - `REDIS_URL`
  - `REDIS_HOST`
  - `REDIS_PORT`
  - `REDIS_PASSWORD`
  - `REDIS_DB`
  - `REDIS_KEY_PREFIX`
- MinIO
  - `MINIO_ENDPOINT`
  - `MINIO_ACCESS_KEY`
  - `MINIO_SECRET_KEY`
  - `MINIO_BUCKET`
  - `MINIO_SECURE`
- internal service URL
  - `PUBLIC_SERVICE_INTERNAL_BASE_URL`
  - `PDFQA_SIDECAR_BASE_URL_INTERNAL`

### Already Configurable But Not Unified

These items are already configurable, but current naming is inconsistent across services.

#### LLM Base URL

- `highThinkingQA`
  - `LLM_BASE_URL`
- `fastQA`
  - `OPENAI_BASE_URL`
  - `DASHSCOPE_BASE_URL`
- `public-service`
  - `OPENAI_BASE_URL`

Deployment impact:

- easy to configure in Docker now
- not yet a single canonical variable name across services

#### LLM API Key

- `highThinkingQA`
  - `LLM_API_KEY`
  - fallback `DASHSCOPE_API_KEY`
- `fastQA`
  - `OPENAI_API_KEY`
  - fallback `DASHSCOPE_API_KEY`
- `public-service`
  - `OPENAI_API_KEY`
  - optional `DASHSCOPE_API_KEY`

Deployment impact:

- easy to configure now
- still service-specific in naming

#### Embedding Endpoint

- `highThinkingQA`
  - `EMBEDDING_BASE_URL`
- `fastQA`
  - `EMBEDDING_API_URL`
- `public-service`
  - no equivalent general embedding endpoint variable is exposed as a single canonical runtime contract

Deployment impact:

- configurable for current paths
- not unified as one deployment standard

#### Embedding Model Selection

- `highThinkingQA`
  - `EMBEDDING_MODEL`
- `fastQA`
  - `EMBEDDING_API_MODEL`
  - `EMBEDDING_MODEL_NAME`
- `public-service`
  - no equivalent general embedding-model variable for service-wide retrieval

Deployment impact:

- configurable in the service-specific paths that exist today
- not unified as one deployment standard

#### Embedding Deployment Rule

Deployment recommendation:

- for portable Docker deployment, use remote embedding by URL
- the current deployment template defaults `fastQA` to remote embedding mode
- use `EMBEDDING_API_URL` plus `EMBEDDING_API_MODEL`
- do not depend on local BGE model files inside the image or on the target host

Current state:

- `fastQA` can run in remote embedding mode with
  - `EMBEDDING_MODEL_TYPE=remote`
  - `EMBEDDING_API_URL=...`
  - `EMBEDDING_API_MODEL=...`
- `highThinkingQA` already uses remote embedding configuration through
  - `EMBEDDING_API_KEY`
  - `EMBEDDING_BASE_URL`
  - `EMBEDDING_MODEL`
- `public-service` does not require a separate deployment-wide embedding endpoint for the current runtime path

Deployment impact:

- the current Docker deployment can stay fully URL-based without business-code changes
- a later config-unification phase would still be needed if you want one canonical embedding variable set across all services

### Requires Code Changes To Truly Unify

The following are not blocked from Docker deployment, but they require code changes if the goal is a single clean, cross-service deployment contract.

- one canonical variable name for LLM endpoint across all services
  - for example `LLM_BASE_URL`
- one canonical variable name for LLM model across all services
  - for example `LLM_MODEL`
- one canonical variable name for embedding endpoint across all services
  - for example `EMBEDDING_BASE_URL`
- one canonical variable name for embedding model across all services
  - for example `EMBEDDING_MODEL`
- one canonical env-loading strategy across all services
  - today `public-service` relies on `PUBLIC_SERVICE_ENV_FILE` or `PUBLIC_SERVICE_ENV_FILES`
  - `fastQA` and `highThinkingQA` also support service-root based automatic env discovery

## Recommended Docker Env Strategy

For deployment, use one top-level `.env` only for Compose interpolation, and pass service-specific runtime env explicitly.

Recommended rule:

- Compose `.env`
  - image tags
  - published ports
  - secret placeholders
- service `environment`
  - runtime values that must be explicit inside containers
- optional service `env_file`
  - when a dedicated per-service deployment file is preferred

Do not rely on:

- repo-local dev env templates inside the image
- automatic discovery of `config.secret.env` from legacy local paths

## Minimal Portable Deployment Standard

A deployment bundle should be considered portable only if all of the following are true:

- services run without Conda
- services run without host source-code mounts
- MySQL schema initializes from an empty database
- MinIO bucket initializes automatically
- vector-database data is restored automatically
- required papers and retrieval assets are present
- runtime config is passed through Docker env, not hardcoded paths
- model access is provided through remote URLs

## Practical Recommendation

Near-term, the easiest path is:

- keep current per-service environment-variable contracts
- wire them through Compose now
- package MySQL schema and vector data as deployment assets
- standardize this deployment bundle on remote URL based LLM and embedding access
- avoid a cross-service config refactor in the first Docker milestone

If a second hardening phase is planned, then do:

- config naming unification
- env-loading unification
- empty-database official schema baseline for `public-service`
- formal data-seeding workflow for vector databases
