# HighThinkingQA Config

Service-level env files for the copied `highThinkingQA` backend live in this directory.

Shared infrastructure and model endpoint defaults come from `resource/config/shared/` and
are loaded before these service-local files. Keep service-local overrides here only when
highThinkingQA intentionally differs from the shared default.

Load order for the service process:

- explicit env files via `HIGHTHINKINGQA_ENV_FILE(S)` or `SERVICE_ENV_FILE(S)`
- otherwise legacy fallback, shared files, then this service config root:
  `config.shared.env`, `config.secret.env`, `.env`, `config.env`
- workspace fallback is only used when no service config root is active

highThinkingQA owns:

- Gunicorn worker/thread/timeouts, CORS, SSE, and ask concurrency
- thinking-mode model choice through `LLM_MODEL`
- highThinkingQA-specific embedding endpoint, model, dimensions, and ingestion limits
- chunking, retrieval, ingestion, cache, and conversation persistence capacity
- local paper, prompt, Chroma, upload, and conversation paths
- `REDIS_KEY_PREFIX=highthinkingqa`

Shared config owns service ports, common Redis/MySQL/MinIO infrastructure defaults, model
endpoints, and graph endpoints. Use `config.env` or process env for local overrides;
do not commit filled secret files.

Runtime/state/assets should resolve via the `resource/` contract when the service runs from this monorepo.
