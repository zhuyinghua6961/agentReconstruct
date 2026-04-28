# FastQA Config

Service-level env templates for the `fastQA` backend live here.

Shared infrastructure and model endpoint defaults come from `resource/config/shared/` and
are loaded before these service-local files. Keep service-local overrides here only when
fastQA intentionally differs from the shared default.

Expected runtime contract:

- `FASTQA_SERVICE_CONFIG_ROOT`
- `FASTQA_SERVICE_STATE_ROOT`
- `FASTQA_SERVICE_RUNTIME_ROOT`
- `FASTQA_SERVICE_ASSET_ROOT`

fastQA owns:

- service ports and Gunicorn worker counts
- QA, graph KB, file-QA, SSE, cache, and retrieval feature flags
- service-specific LLM model names such as `OPENAI_MODEL`, `DASHSCOPE_MODEL`,
  `QUERY_EXPANSION_MODEL`, and `PDF_QA_MODEL`
- fastQA vector database, paper, prompt, JSON, cache, and runtime paths
- `REDIS_KEY_PREFIX=fastqa`
- fastQA-specific rerank candidates, API key, and warmup behavior

Shared config owns common defaults for Redis host/port/timeouts, DashScope-compatible
base URLs, local embedding endpoint aliases, and local rerank endpoint defaults.

Runtime expectations:

- trust gateway-normalized `route`
- do not require conversation/upload/document modules to boot
