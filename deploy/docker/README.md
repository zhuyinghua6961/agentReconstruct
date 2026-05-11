# Docker Image Build Notes

## Build Order

Build the shared base image first from the repository root:

```bash
docker build -f deploy/docker/base.Dockerfile -t highthinking-python-base:latest .
```

Then build the service images:

```bash
docker build -f deploy/docker/Dockerfile.gateway -t ghcr.io/example/highthinking-gateway:latest .
docker build -f deploy/docker/Dockerfile.public-service -t ghcr.io/example/highthinking-public-service:latest .
docker build -f deploy/docker/Dockerfile.fastqa -t ghcr.io/example/highthinking-fastqa:latest .
docker build -f deploy/docker/Dockerfile.highthinkingqa -t ghcr.io/example/highthinking-highthinkingqa:latest .
docker build -f deploy/docker/Dockerfile.patent -t ghcr.io/example/highthinking-patent:latest .
cd frontend-vue && npm ci && npm run build && cd ..
docker build -f deploy/docker/Dockerfile.frontend-nginx -t ghcr.io/example/highthinking-frontend:latest .
```

Update `deploy/.env.example` or the real deployment `.env` so the image references match the tags you publish or load.

## Notes

- These Dockerfiles package the repository into the image and do not depend on host source mounts.
- The Python dependency set is intentionally broad because the current repository does not expose a single complete, normalized runtime manifest for all backend services.
- The frontend image serves the prebuilt `frontend-vue/dist` bundle through nginx and proxies `/api/` to the gateway service inside Docker.
- `deploy/docker-compose.yml` assumes the images already exist and focuses on runtime orchestration, initialization hooks, and persistent data volumes.
