# Public-Service Config

Public-service config templates belong here.

Shared infrastructure and model endpoint defaults come from `resource/config/shared/`.
The canonical public-service service config is `resource/config/services/public-service`.
`public-service/config.shared.env` is a legacy shim only.

Public-service owns:

- data root mapping
- public-service API prefix, docs/OpenAPI URLs, CORS, and worker count
- `REDIS_KEY_PREFIX=public_service`
- auth, quota, cache, conversation, upload, outbox, and cleanup behavior
- document, translation, vector collection, and local storage defaults
- upload and outbox worker settings
- public-service-specific route and legacy fallback behavior

Shared config owns service host/port, common Redis/MySQL/MinIO infrastructure defaults,
MinIO proxy/download defaults, model endpoint aliases, and graph endpoint aliases.
Credentials remain in local secret env files or shared secret env files.
