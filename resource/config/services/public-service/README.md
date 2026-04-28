# Public-Service Config

Public-service config templates belong here.

Shared infrastructure and model endpoint defaults come from `resource/config/shared/`.
Current public-service runtime still loads `public-service/config.shared.env` and
`public-service/config.secret.env`, with shared config prepended by launch scripts.
Fully moving public-service service-local env files into this directory is out of scope.

Public-service owns:

- data root mapping
- public-service app host, port, API prefix, docs/OpenAPI URLs, CORS, and worker count
- `REDIS_KEY_PREFIX=public_service`
- auth, quota, cache, conversation, upload, outbox, and cleanup behavior
- document, translation, vector collection, and local storage defaults
- upload and outbox worker settings
- public-service-specific route and legacy fallback behavior

Shared config owns common Redis/MySQL/MinIO infrastructure defaults, MinIO proxy/download
defaults, and shared model endpoint aliases. Credentials remain in local secret env files
or `resource/config/shared/infrastructure.secret.env`.
