# Resource Config

Configuration contract layers:

- `shared/`: commit-safe shared defaults and naming conventions
- `services/<service>/`: commit-safe service-local templates
- `local/`: local environment overrides, not for shared commits
- `secrets/`: secrets, never for shared commits

Recommended root variables:

- `RESOURCE_ROOT`
- `SERVICE_CONFIG_ROOT`
- `SERVICE_ASSET_ROOT`
- `SERVICE_STATE_ROOT`
- `SERVICE_RUNTIME_ROOT`
