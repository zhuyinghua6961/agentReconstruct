# Deployment Bundle README

Chinese version: [`README.zh-CN.md`](README.zh-CN.md)

## Goal

`deploy/` is the portable Docker bundle for the gateway, four backend services,
frontend nginx, MySQL, Redis, MinIO, two Neo4j graph stores, and one-shot seed
jobs.

The delivery shape is intentionally mixed:

- Runtime images: service images, frontend image, MySQL, Redis, MinIO, Neo4j,
  `minio/mc`, and `lifeo4agent/seed-tools`.
- Versioned data packages under `deploy/data/*.tar.zst`.
- `docker compose up -d` automatically seeds MinIO originals, reference vector
  data, and Neo4j dumps into Docker named volumes.
- MySQL initialization creates the schema and seeds only the reference
  department tree. Users, personnel records, conversations, and quota usage are
  not included.

The deployment machine does not need `mc`, `zstd`, or `neo4j-admin` installed on
the host.

## Runtime Configuration

Start from:

```bash
cp deploy/.env.production.example deploy/.env
```

The customer-facing env surface is limited to:

- HTTPS/HTTP edge ports, frontend debug port, and MySQL, Redis, and MinIO
  published ports
- `HTTPS_SERVER_NAME` and `HTTPS_REDIRECT_HOST`
- MySQL, Redis, and MinIO credentials
- `DATA_PACKAGE_VERSION` and `DATA_SEED_FORCE`
- `JWT_SECRET` and `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN`
- LLM endpoint/model/key
- fastQA and patentQA intent model endpoint/model/key
- fastQA and patentQA embedding endpoint/model/key
- highThinkingQA embedding endpoint/model/key
- rerank provider/endpoint/model/key

Neo4j is internal to Compose. Backends connect to `neo4j-literature` or
`neo4j-patent` by service name, so customers do not configure Neo4j URLs.

## HTTPS Edge

Compose includes an `edge` nginx service as the public HTTPS entrypoint:

- `HTTP_PUBLISH_PORT` redirects to HTTPS.
- `HTTPS_PUBLISH_PORT` proxies to the internal `frontend:80` service.
- `frontend` still has `FRONTEND_PUBLISH_PORT`, but it is bound to
  `127.0.0.1` by default and is intended for local debugging. Normal access
  should use the HTTPS edge.

The deployment party supplies certificates at:

```text
deploy/certs/fullchain.pem
deploy/certs/privkey.pem
```

The certificate SAN must match `HTTPS_SERVER_NAME`. `HTTPS_REDIRECT_HOST` is
used for HTTP-to-HTTPS redirects; for standard port 443 it is usually the same
domain, while non-standard local testing can use `domain:port`.

For local testing, generate an internal test certificate:

```bash
bash deploy/scripts/generate_dev_tls_cert.sh lifeo4.agent.test 172.19.14.204
```

Then point `lifeo4.agent.test` to the deployment host IP via hosts or internal
DNS.

## Build Images

Build from the repository root:

```bash
docker build -f deploy/docker/base.Dockerfile -t lifeo4agent/python-base:latest .
docker build -f deploy/docker/Dockerfile.seed-tools -t lifeo4agent/seed-tools:latest .
docker build -f deploy/docker/Dockerfile.gateway -t lifeo4agent/gateway:latest .
docker build -f deploy/docker/Dockerfile.public-service -t lifeo4agent/public-service:latest .
docker build -f deploy/docker/Dockerfile.fastqa -t lifeo4agent/fastqa:latest .
docker build -f deploy/docker/Dockerfile.highthinkingqa -t lifeo4agent/highthinkingqa:latest .
docker build -f deploy/docker/Dockerfile.patent -t lifeo4agent/patent:latest .

cd frontend-vue && npm ci && npm run build
cd ..
docker build -f deploy/docker/Dockerfile.frontend-nginx -t lifeo4agent/frontend:latest .
```

Service images copy only their service code plus shared `resource/config` and
`resource/assets`; large resource data is delivered by data packages.

## Build Data Packages

First collect MinIO originals from local `resource/`:

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --clean
```

That command builds:

- `deploy/minio-seed/agentcode/papers/`
- `deploy/minio-seed/agentcode/patent/originals/`

Patent `*_tables.json` files are backfilled to
`patent/originals/<id>/structured/tables.json`, and the corresponding
`manifest.json` files are updated.

Then create consistent Neo4j dumps during a graph maintenance window and package
all data:

```bash
NEO4J_LITERATURE_DUMP_SRC=/path/to/literature.dump \
NEO4J_PATENT_DUMP_SRC=/path/to/patent.dump \
bash deploy/scripts/package_data.sh deploy/.env
```

Expected `deploy/data/` outputs:

- `manifest.json`
- `minio-originals.tar.zst`: `papers/` and `patent/originals/`
- `fastqa-ref.tar.zst`: fastQA vector DBs and topic index, no paper originals
- `highthinking-ref.tar.zst`: highThinkingQA `vectordb`, with an empty papers
  cache directory
- `patentqa-ref.tar.zst`: patent vector DBs and JSON-only patent archive, no
  PDFs or PNGs
- `public-service-ref.tar.zst`: public-service reference vector data
- `neo4j-literature.dump.zst`
- `neo4j-patent.dump.zst`

`manifest.json` records package version, sha256, byte size, build time, and key
counts.

## Export Images

```bash
bash deploy/scripts/export_images.sh deploy/.env deploy/lifeo4agent-images.tar
```

The export contains runtime images and official infrastructure images. Large
data packages stay in `deploy/data/` and are not embedded in the image tarball.

Legacy/debug scripts for the older MinIO originals data-image path are kept as
separate helpers, but they are not part of the recommended delivery flow.

## Preflight

Run before handoff or deployment:

```bash
bash deploy/scripts/preflight_check.sh deploy/.env
```

The preflight checks required env values, data package presence, manifest
sha256, image availability when Docker is reachable, and Compose expansion.

## Run

On the target machine:

```bash
docker load -i deploy/lifeo4agent-images.tar
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

Seed jobs write markers:

- MinIO: `_deploy/data-seed/<package>/<version>.done`
- Reference volumes: `.deploy/data-seed/<package>/<version>.done`
- Neo4j volumes: `/data/.deploy/data-seed/<package>/<version>.done`

The same version is skipped on later starts. Set `DATA_SEED_FORCE=1` to reimport
the current version.
