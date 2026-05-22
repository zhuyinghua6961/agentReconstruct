# Docker Image Build Notes

## Build Order

Build the shared Python dependency base first:

```bash
docker build -f deploy/docker/base.Dockerfile -t lifeo4agent/python-base:latest .
```

Build seed tools and runtime services:

```bash
docker build -f deploy/docker/Dockerfile.seed-tools -t lifeo4agent/seed-tools:latest .
docker build -f deploy/docker/Dockerfile.gateway -t lifeo4agent/gateway:latest .
docker build -f deploy/docker/Dockerfile.public-service -t lifeo4agent/public-service:latest .
docker build -f deploy/docker/Dockerfile.fastqa -t lifeo4agent/fastqa:latest .
docker build -f deploy/docker/Dockerfile.highthinkingqa -t lifeo4agent/highthinkingqa:latest .
docker build -f deploy/docker/Dockerfile.patent -t lifeo4agent/patent:latest .
cd frontend-vue && npm ci && npm run build && cd ..
docker build -f deploy/docker/Dockerfile.frontend-nginx -t lifeo4agent/frontend:latest .
```

The service Dockerfiles copy only their own source tree plus shared
`resource/config` and `resource/assets`. Large originals, vector DBs, and graph
data are delivered by `deploy/data/*.tar.zst` packages.

## Seed Tools

`Dockerfile.seed-tools` builds the small `lifeo4agent/seed-tools` image. It
contains:

- `tar`
- `zstd`
- `jq`
- `mc`
- seed entrypoint scripts under `/seed-tools`

Compose uses this image for MinIO originals, reference data volume seeding, and
Neo4j dump preparation.

## Legacy MinIO Originals Image

`Dockerfile.minio-originals` is kept only for legacy/debug builds of the older
large data-image approach. The recommended offline delivery now uses
`deploy/data/minio-originals.tar.zst`.
