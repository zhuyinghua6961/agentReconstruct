# Patent Config

Shared infrastructure and model endpoint defaults come from `resource/config/shared/` and
are loaded before patent service-local files.

The patent service should own only patent QA execution configuration here,
including whether it uses local embedding, patent-specific LLM credentials, and
durable mode settings.

Patent owns:

- `PATENT_PORT`, worker/thread/timeouts, ask concurrency, and executor sizing
- patent QA citation/retrieval behavior and durable-mode flags
- `PATENT_REDIS_KEY_PREFIX=patent`
- patent-specific model choices such as `PATENT_OPENAI_MODEL`
- patent route/runtime behavior flags and patent LLM pool behavior

Shared config owns common Redis infrastructure defaults, DashScope-compatible base URL
aliases, and local embedding endpoint aliases such as `PATENT_EMBEDDING_API_URL`.

`resource/config/services/patent/config.shared.env` is local/operator-owned in this
workspace and ignored by Git. Tracked examples and this README describe the intended
ownership; do not commit local secrets or deployment-specific patent env files.
