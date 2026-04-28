# Shared Config

Shared env files provide repository-wide defaults loaded before service-local env files.

Load order target:

1. `resource/config/shared/infrastructure.shared.env`
2. `resource/config/shared/model-endpoints.shared.env`
3. optional local `resource/config/shared/infrastructure.secret.env`
4. service-local `config.env`
5. service-local `config.shared.env`
6. service-local `config.secret.env`
7. service-local `.env`

Service-local files override shared defaults. Process environment variables override all env files.

Shared files should contain common defaults only. Service files continue to own ports, worker counts,
Redis key prefixes, feature flags, and route behavior.

Do not commit real secrets. Use `infrastructure.secret.env.example` as the template for a
local `infrastructure.secret.env`, and keep deployment-specific credentials in local secret
env files.
