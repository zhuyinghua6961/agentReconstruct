# Resource Config

Configuration contract layers:

- `shared/`: commit-safe shared defaults and optional local shared secrets
- `services/<service>/`: commit-safe service-local templates
- `local/`: local environment overrides, not for shared commits
- `secrets/`: secrets, never for shared commits

Backend service load order:

1. `resource/config/shared/infrastructure.shared.env`
2. `resource/config/shared/model-endpoints.shared.env`
3. optional local `resource/config/shared/infrastructure.secret.env`
4. service-local `config.env`
5. service-local `config.shared.env`
6. service-local `config.secret.env`
7. service-local `.env`

Later env files override earlier env files. Service-local files therefore override shared defaults.
Variables already present in the original process environment override all env files.

Real secrets are not committed. Use checked-in `.example` files and local `config.secret.env`
or `resource/config/shared/infrastructure.secret.env` files for deployment-specific secrets.

Recommended root variables:

- `RESOURCE_ROOT`
- `SERVICE_CONFIG_ROOT`
- `SERVICE_ASSET_ROOT`
- `SERVICE_STATE_ROOT`
- `SERVICE_RUNTIME_ROOT`
