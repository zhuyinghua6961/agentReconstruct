# Deployment Bundle README

Chinese version: [`README.zh-CN.md`](README.zh-CN.md)

## Goal

This directory is the portable Docker deployment bundle for:

- `gateway`
- `public-service`
- `fastQA`
- `highThinkingQA`
- `patentQA`
- `frontend nginx`
- `mysql`
- `redis`
- `minio`

The deployment target is:

- images can be moved to another machine
- runtime config is provided from Docker env
- embedding and LLM access use remote URLs
- no dependency on local Conda or local model files

## Directory Layout

- `docker-compose.yml`
  - runtime orchestration
- `.env.example`
  - minimal deployment env template
- `.env.production.example`
  - formal production deployment template
- `docker/`
  - image build Dockerfiles
- `mysql-init/001_schema.sql`
  - MySQL schema initialization SQL
- `minio-init/init.sh`
  - MinIO bucket initialization
- `minio-seed/`
  - MinIO object seeds such as `papers/`
- `seed-data/`
  - vector DB and state seed data to preload into volumes
- `scripts/collect_seed_data.sh`
  - collect deployment seed-data from the current worktree
- `scripts/collect_minio_seed.sh`
  - collect MinIO object seeds from the current worktree
- `scripts/preflight_check.sh`
  - validate env, assets, and compose wiring before release
- `scripts/export_images.sh`
  - export service and infrastructure images into one tarball

## Existing Docker Config Files

The Docker deployment config already exists in this directory:

- `deploy/docker-compose.yml`
  - starts MySQL, Redis, MinIO, one-shot init jobs, backend services, and the frontend nginx container
- `deploy/.env.production.example`
  - production-oriented template for image names, ports, passwords, model endpoints, and API keys
- `deploy/.env.example`
  - shorter example template
- `deploy/.env`
  - local working deployment env; do not treat placeholder values as final secrets

Runtime values are injected from `deploy/.env` into containers by Compose. The
`resource/config` files inside the image are fallback/default config only; an
environment variable injected by Compose takes precedence over the same key in
`resource/config`.

## Important Deployment Rule

This deployment bundle assumes model access is URL-based:

- `fastQA`, `highThinkingQA`, `patentQA`, and `public-service` use the shared `LLM_BASE_URL`, `LLM_MODEL`, and optional `LLM_API_KEY`
- `fastQA` and `patentQA` share the existing BGE-compatible embedding endpoint through `QA_EMBEDDING_BASE_URL`, `QA_EMBEDDING_MODEL`, and optional `QA_EMBEDDING_API_KEY`
- `highThinkingQA` embedding uses `HIGHTHINKINGQA_EMBEDDING_BASE_URL`
- `fastQA` and `patentQA` share `RERANK_PROVIDER`, `RERANK_BASE_URL`, `RERANK_MODEL`, and optional `RERANK_API_KEY`

Do not deploy with in-process local BGE model paths for this bundle. If the
model is deployed inside the customer's offline network, still use `remote`
mode and point the URL variables at that internal HTTP service.

## 1. Prepare Deployment Env

Recommended starting point:

```bash
cp deploy/.env.production.example deploy/.env
```

Then edit `deploy/.env` and set at minimum:

- image names and tags
- MySQL, Redis, and MinIO passwords
- `LLM_BASE_URL` and `LLM_MODEL`
- `QA_EMBEDDING_BASE_URL` and `QA_EMBEDDING_MODEL`
- `HIGHTHINKINGQA_EMBEDDING_BASE_URL` and `HIGHTHINKINGQA_EMBEDDING_MODEL`
- `RERANK_PROVIDER`; if it is not `none`, also set `RERANK_BASE_URL`
- API keys if the target model service requires them
- published ports
- Docker bridge subnet and gateway if the target host has network conflicts

## 2. Build Images

Build from the repository root:

```bash
docker build -f deploy/docker/base.Dockerfile -t highthinking-python-base:latest .
docker build -f deploy/docker/Dockerfile.gateway -t ghcr.io/example/highthinking-gateway:latest .
docker build -f deploy/docker/Dockerfile.public-service -t ghcr.io/example/highthinking-public-service:latest .
docker build -f deploy/docker/Dockerfile.fastqa -t ghcr.io/example/highthinking-fastqa:latest .
docker build -f deploy/docker/Dockerfile.highthinkingqa -t ghcr.io/example/highthinking-highthinkingqa:latest .
docker build -f deploy/docker/Dockerfile.patent -t ghcr.io/example/highthinking-patent:latest .

cd frontend-vue && npm ci && npm run build
cd ..
docker build -f deploy/docker/Dockerfile.frontend-nginx -t ghcr.io/example/highthinking-frontend:latest .
```

## 3. Collect Seed Data

If you want the target machine to start with existing vector DB and retrieval assets, run:

```bash
bash deploy/scripts/collect_seed_data.sh --clean
```

This copies current local retrieval data into `deploy/seed-data/`.

## 4. Current Seed-Data Sources In This Worktree

The helper script defaults to these source roots:

- `public-service`
  - `/home/cqy/worktrees/highThinking/public-service/data/runtime/vector_database`
  - `/home/cqy/worktrees/highThinking/public-service/data/runtime/papers`
  - `/home/cqy/worktrees/highThinking/public-service/data/runtime/storage`
  - `/home/cqy/worktrees/highThinking/public-service/data/runtime/translation_cache`
- `fastQA`
  - prefer `/home/cqy/worktrees/highThinking/resource/fastqa/vector_database`
  - prefer `/home/cqy/worktrees/highThinking/resource/fastqa/vector_database_md`
  - prefer `/home/cqy/worktrees/highThinking/resource/fastqa/community_vector_database`
  - prefer `/home/cqy/worktrees/highThinking/resource/fastqa/vector_db_topic_index.json`
  - fallback `/home/cqy/worktrees/highThinking/resource/state/dev/fastQA/vector_database`
  - fallback `/home/cqy/worktrees/highThinking/resource/state/dev/fastQA/vector_database_local`
- `highThinkingQA`
  - prefer `/home/cqy/worktrees/highThinking/resource/highThinkingQA/vectordb`
  - prefer `/home/cqy/worktrees/highThinking/resource/highThinkingQA/papers`
  - fallback `/home/cqy/worktrees/highThinking/resource/state/dev/highThinkingQA/vectordb`
  - fallback `/home/cqy/worktrees/highThinking/resource/state/dev/highThinkingQA/papers`
- `patentQA`
  - `/home/cqy/worktrees/highThinking/resource/patentQA/vector_db_patent_abstracts`
  - `/home/cqy/worktrees/highThinking/resource/patentQA/vector_db_patent_chunks`

You can override the source roots with environment variables when needed.

## 5. Collect MinIO Object Seed

If you want deployment to auto-import papers and patent originals into MinIO, run:

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --clean
```

This populates:

- `deploy/minio-seed/agentcode/papers/`
- `deploy/minio-seed/agentcode/patent/originals/`

In the current worktree, this command resolves to `resource/fastqa/papers`
as the primary corpus source, which is the large 7000+ paper set. `fastQA`
itself does not copy that corpus into `seed-data/fastQA`; the papers are packed
once through MinIO object seed only.

Patent originals are converted into the runtime MinIO object layout, for
example:

- `patent/originals/<patent_id>/manifest.json`
- `patent/originals/<patent_id>/structured/claims.json`
- `patent/originals/<patent_id>/structured/description.json`
- `patent/originals/<patent_id>/structured/bibliography.json`
- `patent/originals/<patent_id>/fulltext/original.pdf`
- `patent/originals/<patent_id>/figures/...`

To collect only patent originals:

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --patent-only
```

## 6. Run Preflight Check

Before packaging or deployment, run:

```bash
bash deploy/scripts/preflight_check.sh deploy/.env
```

This checks:

- required deployment files exist
- required env variables are present
- shared QA embedding mode is `remote`
- rerank provider is valid, and rerank URL is present when rerank is enabled
- seed-data directories are not silently empty
- MinIO object seed directories are not silently empty
- `docker compose config` resolves correctly

## 7. Export Or Push Images

If the target machine has no registry access, export tarballs:

```bash
bash deploy/scripts/export_images.sh deploy/.env deploy/highthinking-images.tar
```

The export includes the service images plus runtime infrastructure images:

- `gateway`, `public-service`, `fastQA`, `highThinkingQA`, `patentQA`, `frontend`
- `mysql`, `redis`, `minio/minio`, `minio/mc`, `alpine`, `nginx`

On the target machine:

```bash
docker load -i deploy/highthinking-images.tar
```

## 8. Required Initialization Assets

Before first start, confirm these assets are present:

- `deploy/mysql-init/001_schema.sql`
- `deploy/minio-init/init.sh`
- `deploy/minio-seed/<bucket>/`
- `deploy/seed-data/public-service/`
- `deploy/seed-data/fastQA/`
- `deploy/seed-data/highThinkingQA/`
- `deploy/seed-data/patentQA/`

`seed-data/` should contain the vector DB and retrieval state that must exist immediately after startup.
`minio-seed/` should contain object prefixes such as `papers/` that should be restored into MinIO automatically.

## 9. Start The Stack

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

## 10. Verify

Check container status:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

Check logs for one service:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f gateway
```

Expected ports:

- frontend nginx: `8080` by default
- gateway: `8101`
- public-service: `8102`
- fastQA: `8008`
- highThinkingQA: `8009`
- patentQA: `8010`
- mysql: `3306`
- redis: `6379`
- minio api: `9000`
- minio console: `9001`

Default Docker internal bridge network for this bundle:

- subnet: `172.20.0.0/24`
- gateway: `172.20.0.1`

If the target machine has Docker/VPN overlap, change:

- `DOCKER_BRIDGE_SUBNET`
- `DOCKER_BRIDGE_GATEWAY`

in [`deploy/.env`](/home/cqy/worktrees/highThinking/deploy/.env) before first startup.

## 11. First-Start Behavior

On first startup:

- MySQL loads `mysql-init/001_schema.sql`
- MinIO bucket is created by `minio-init`
- `minio-seed` imports object seeds such as `papers/` into MinIO
- `seed-data/` is copied into Docker named volumes by `init-data`

If named volumes already exist, first-start initialization will not overwrite existing persistent data.

## Notes

- `mysql-init/001_schema.sql` was exported from the live local `agentcode` schema and validated by test import.
- The current SQL targets MySQL 8.x.
- The current deployment contract is service-specific, not fully unified across all services.
- Portable deployment is already feasible without modifying business code.
