# HighThinkingQA Config

Service-level env files for the copied `highThinkingQA` backend live in this directory.

Load order for the service process:
- explicit env files via `HIGHTHINKINGQA_ENV_FILE(S)` or `SERVICE_ENV_FILE(S)`
- otherwise this service config root: `resource/config/services/highThinkingQA/config.env`, `config.shared.env`, `config.secret.env`, `.env`
- workspace fallback is only used when no service config root is active

Runtime/state/assets should resolve via the `resource/` contract when the service runs from this monorepo.
