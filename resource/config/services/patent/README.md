# Patent Config

Shared infrastructure and model endpoint defaults come from `resource/config/shared/` and
are loaded before patent service-local files.

The patent service should own only patent QA execution configuration here,
including patent-specific behavior, capacity, durable mode, and graph tuning.

Patent owns:

- worker/thread/timeouts, ask concurrency, and executor sizing
- patent QA citation/retrieval behavior and durable-mode flags
- `PATENT_REDIS_KEY_PREFIX=patent`
- patent route/runtime behavior flags and patent LLM pool behavior

Shared config owns service ports, Redis/MySQL/MinIO defaults, model endpoints, embedding
endpoints, and graph endpoints. Use service-specific `PATENT_OPENAI_*` only as explicit
overrides, not as duplicated defaults.

`resource/config/services/patent/config.shared.env` is local/operator-owned in this
workspace. Put local overrides in `config.env`; do not commit local secrets or
deployment-specific patent env files.
