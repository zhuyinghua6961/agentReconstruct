# HighThinkingQA

Independent `thinking` backend workspace inside the single-repository layout.

## Status Notice

This service is not functionally complete yet.
It is still in migration and isolation work, and should be treated as an in-progress thinking-mode extraction rather than a final fully closed replacement.

## Current Phase

This directory now contains a first-phase copied closure from the root `highThinking` backend:

- `server_fastapi/`
- `server/`
- `agent_core/`
- `ingest/`
- `retriever/`
- `prompts/`
- `tests/`
- `scripts/`
- `config.py`
- `env_loader.py`
- env templates

The former root-level implementation has been archived under `archive/root-highthinking-legacy-2026-03-23/` as the rollback baseline. Active highThinkingQA papers and vectordb now live under `resource/highThinkingQA/`.

## Scope

Planned long-term role:

- thinking-mode QA execution only
- independent backend process
- no long-term ownership of public auth/conversation/upload/document truth data

Current short-term role:

- copied execution closure for migration and isolation work

## Run

From this directory:

```bash
bash scripts/start_fastapi_gunicorn.sh
```

Default monorepo behavior:

- env files load from `resource/config/services/highThinkingQA/` when that directory exists
- mutable state writes under `resource/state/dev/highThinkingQA/`
- runtime pid/log files write under `resource/runtime/dev/highThinkingQA/`
- prompts still fall back to `highThinkingQA/prompts/` until assets are migrated

## Config

Compatibility copies still exist locally:

- `config.env.example`
- `config.shared.env`
- `config.secret.env.example`
- `config.secret.env`

Preferred monorepo env contract:

- `HIGHTHINKINGQA_SERVICE_CONFIG_ROOT`
- `HIGHTHINKINGQA_SERVICE_STATE_ROOT`
- `HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT`
- `HIGHTHINKINGQA_SERVICE_ASSET_ROOT`

Legacy variables remain valid:

- `PAPERS_DIR`
- `UPLOAD_DIR`
- `CHAT_JSON_BASE_DIR`
- `CHROMA_PERSIST_DIR`
- `PROMPTS_DIR`

### Intent fast-tag (`qwen3-8b` 等)

与 fastQA、patent 相同套路：可选地在主流程 Step1（直接回答与分解并行）之前，用轻量 `chat.completions` 调用从固定 snake_case tag 中选一个主轴类型，并把一段中文提示前缀到 **effective_question** 再交给分解、直接回答、子问题预回答、综合与 Checker/Reviser。默认关闭；在同一 OpenAI 兼容网关下通常需登记轻量模型名（默认 **`qwen3-8b`**）。

- **`QA_INTENT_DETECT_ENABLED`** / **`HT_QA_INTENT_DETECT_ENABLED`**：任一为 truthy 则开启。
- **`QA_INTENT_DETECT_MODEL`** / **`HT_QA_INTENT_DETECT_MODEL`**：模型 ID；后者优先。

详见仓库内 `config.shared.env` 注释。

## Notes

- Runtime path resolution is now decoupled from the old root layout.
- Root-level baseline code is still kept for rollback.
- The next migration step is shrinking this service down to thinking-only ownership.
