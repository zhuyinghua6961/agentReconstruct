# LLM Thinking Control Design Spec

**Date:** 2026-05-25

## Summary

This spec defines the target contract for controlling thinking-mode LLM calls across `fastQA`, `highThinkingQA`, `patent`, and document LLM utilities.

The configuration surface is intentionally small: deployment only needs to provide `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_IS_THINKING_MODEL`, and `LLM_THINKING_ENABLED`. The default runtime behavior is non-thinking. If a model is marked as a thinking-capable model, every LLM call must explicitly disable thinking except final answer generation in Stage4, where `LLM_THINKING_ENABLED=true` may enable thinking.

The system uses local OpenAI-compatible model endpoints. No provider-specific configuration should be required for this feature.

Official DeepSeek references checked on 2026-05-25:

- DeepSeek Thinking Mode: https://api-docs.deepseek.com/guides/thinking_mode
- DeepSeek Change Log, 2026-04-24 DeepSeek-V4: https://api-docs.deepseek.com/updates/

Relevant documented behavior:

- DeepSeek V4-Pro and V4-Flash are available through the OpenAI ChatCompletions interface.
- Official model names are `deepseek-v4-pro` and `deepseek-v4-flash`.
- Thinking toggle uses `{"thinking": {"type": "enabled"}}` or `{"thinking": {"type": "disabled"}}`.
- The thinking toggle defaults to enabled for DeepSeek thinking-mode requests.
- OpenAI SDK callers pass `thinking` through `extra_body`; `reasoning_effort` is a top-level request parameter.
- Thinking output is returned through `reasoning_content`, separate from final answer `content`.

---

## Goals

1. Keep LLM configuration simple and local-deployment friendly.
2. Avoid accidentally enabling thinking in JSON, planning, retrieval, translation, extraction, and other control-plane calls.
3. Allow Stage4 final answer generation to use thinking when explicitly enabled.
4. Prevent `reasoning_content` from leaking to users or SSE callbacks.
5. Support blank `LLM_API_KEY` for local OpenAI-compatible servers.
6. Avoid breaking non-thinking models by sending unsupported thinking parameters.

## Non-Goals

1. Do not introduce provider selection such as `provider=deepseek`.
2. Do not auto-detect thinking models by model name.
3. Do not change the configured `LLM_MODEL` value in this work.
4. Do not expose reasoning text in logs, events, API responses, persisted chat history, or frontend state.
5. Do not redesign prompts, routing, retrieval, or answer formatting.
6. Do not add `LLM_THINKING_PARAM_MODE`; the two booleans are enough.
7. Do not change VLM/OCR document ingestion paths that use separate `VLM_*` configuration; those are not LLM chat calls under this spec.

---

## Configuration Contract

Required deployment keys:

```env
LLM_BASE_URL=http://your-local-openai-compatible/v1
LLM_API_KEY=
LLM_MODEL=deepseek-v3.1

LLM_IS_THINKING_MODEL=false
LLM_THINKING_ENABLED=false
```

Semantics:

| Configuration | Request behavior | Intended use |
| --- | --- | --- |
| `LLM_IS_THINKING_MODEL=false` | Never send `thinking` or `reasoning_effort`. | Non-thinking models such as current `deepseek-v3.1` deployments. |
| `LLM_IS_THINKING_MODEL=true`, `LLM_THINKING_ENABLED=false` | Send `thinking.type=disabled` on every LLM call. | Thinking-capable models with thinking globally disabled. |
| `LLM_IS_THINKING_MODEL=true`, `LLM_THINKING_ENABLED=true`, non-Stage4 call | Send `thinking.type=disabled`. | Planning, retrieval, translation, JSON, extraction, and utility calls remain non-thinking. |
| `LLM_IS_THINKING_MODEL=true`, `LLM_THINKING_ENABLED=true`, Stage4 final answer call | Send `thinking.type=enabled` and `reasoning_effort=high`. | Final answer quality mode. |

Important operator rule:

- For DeepSeek V4-Pro or V4-Flash, set `LLM_IS_THINKING_MODEL=true`.
- If DeepSeek V4 is deployed but `LLM_IS_THINKING_MODEL=false`, the application will intentionally send no thinking parameter. Because DeepSeek documents the toggle default as enabled, that misconfiguration may still allow upstream thinking. This is expected under the explicit-config contract.

Blank API key behavior:

- Raw HTTP clients must omit the `Authorization` header when `LLM_API_KEY` is blank.
- OpenAI SDK clients must pass a local placeholder key, for example `local-openai-compatible`, so SDK initialization does not fail.
- Blank key support applies only to LLM chat clients. Embedding, rerank, auth, and other non-LLM clients keep their existing credential rules unless separately changed.

---

## Thinking Request Format

Raw OpenAI-compatible HTTP payload:

```json
{
  "model": "deepseek-v4-pro",
  "messages": [],
  "stream": true,
  "max_tokens": 8192,
  "thinking": {"type": "enabled"},
  "reasoning_effort": "high"
}
```

OpenAI SDK request:

```python
client.chat.completions.create(
    model=model,
    messages=messages,
    stream=True,
    max_tokens=8192,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}},
)
```

Disabled thinking:

```python
extra_body={"thinking": {"type": "disabled"}}
```

For raw HTTP, the equivalent is:

```json
"thinking": {"type": "disabled"}
```

When the model is not marked as a thinking model, neither `thinking` nor `reasoning_effort` may appear in the request.

Temperature/top-p note:

- DeepSeek documents that thinking mode does not support `temperature`, `top_p`, `presence_penalty`, or `frequency_penalty`; compatibility behavior may ignore them rather than error.
- When a call resolves to `thinking.type=enabled`, request builders must omit `temperature`, `top_p`, `presence_penalty`, and `frequency_penalty` to stay compatible with stricter DeepSeek-compatible endpoints.
- Disabled-thinking calls and non-thinking-model calls may keep their existing sampling parameters.

---

## Stage Policy

Only final answer generation is allowed to enable thinking.

| Service | Call family | Stage policy |
| --- | --- | --- |
| `fastQA` | Stage1 planning / pre-answer JSON | Always disabled when `LLM_IS_THINKING_MODEL=true`. |
| `fastQA` | Stage2 retrieval query generation | Always disabled. |
| `fastQA` | intent detect, query expansion, comparison retrieval profile | Always disabled. |
| `fastQA` | Stage4 fact extraction / JSON fact cards | Always disabled. |
| `fastQA` | Stage4 final synthesis stream | May enable only when both booleans are true. |
| `fastQA` | PDF QA / tabular QA final file answer via shared LLM adapter | Treat as final answer; may enable only if the call is the user-visible final answer. |
| `highThinkingQA` | direct answer, decomposition, sub-answer, checker, reviser, intent detect | Always disabled. |
| `highThinkingQA` | synthesis draft answer / answer stream | Stage4 final answer; may enable only when both booleans are true. |
| `highThinkingQA` | document translation and summarization | Always disabled. |
| `patent` | Stage1 planning / Stage2 retrieval query / intent / query expansion | Always disabled. |
| `patent` | KB answer final synthesis | Stage4 final answer; may enable only when both booleans are true. |
| `patent` | PDF answer final synthesis | Stage4 final answer; may enable only when both booleans are true. |
| `patent` | tabular answer final synthesis | Stage4 final answer; may enable only when both booleans are true. |
| `patent` | hybrid answer final synthesis | Stage4 final answer; may enable only when both booleans are true. |
| `public-service` | translation, document summary, document extraction LLM calls | Always disabled. |

If a call produces JSON, routing decisions, retrieval queries, tags, summaries for intermediate processing, or internal checks, it is not Stage4 final answer generation.

---

## Stream Handling Contract

For streaming final answer calls:

1. Read both `delta.content` and `delta.reasoning_content` if present.
2. Append and emit only `content`.
3. Discard `reasoning_content` after counting its length for observability.
4. Do not include reasoning text in logs.
5. Do not forward reasoning chunks to SSE, callbacks, postprocessors, citation sanitizers, or persisted chat messages.
6. If a chunk has `reasoning_content` but no `content`, it must not produce visible output.
7. Existing citation and Markdown post-processing should operate only on final answer content.

Recommended logging fields:

```text
thinking_model=true thinking_enabled=true stage=stage4 reasoning_chars=1234 content_chars=5678
```

Forbidden logging:

```text
reasoning_content=...
```

Tool-call caveat:

- This spec assumes QA final answer calls do not use tool calls.
- DeepSeek documents that reasoning content must be preserved across tool-call sub-turns. If Stage4 later adds tool calls, this spec must be revised before enabling thinking in that path.

---

## Max Token Policy

Thinking consumes output tokens before final content, so Stage4 thinking-enabled calls need a larger output budget.

Policy:

```text
if thinking is enabled for Stage4:
    effective_max_tokens = min(max(original_max_tokens * 2, 8192), 32768)
else:
    effective_max_tokens = original_max_tokens
```

Notes:

- The cap `32768` is the first implementation safety cap and can be made configurable later only if deployment proves a need.
- Do not expand `max_tokens` for non-thinking models.
- Do not expand `max_tokens` for thinking models when the resolved thinking type is disabled.
- Keep existing smaller budgets for JSON/control-plane calls.

---

## Helper Interface

Each service should have a small local helper, because import paths and packaging differ across services.

Suggested helper inputs:

```python
def build_thinking_controls(
    *,
    is_thinking_model: bool,
    thinking_enabled: bool,
    stage: str,
    max_tokens: int | None,
    stream: bool,
) -> ThinkingControls:
    ...
```

Suggested result shape:

```python
@dataclass(frozen=True)
class ThinkingControls:
    extra_body: dict[str, object] | None
    raw_payload_fields: dict[str, object]
    reasoning_effort: str | None
    max_tokens: int | None
    enabled: bool
```

Rules:

```python
is_stage4 = stage == "stage4_final_answer"

if not is_thinking_model:
    return no_extra_fields()

enabled = bool(is_stage4 and thinking_enabled)
thinking_type = "enabled" if enabled else "disabled"

controls.extra_body = {"thinking": {"type": thinking_type}}
controls.raw_payload_fields = {"thinking": {"type": thinking_type}}

if enabled:
    controls.reasoning_effort = "high"
    controls.raw_payload_fields["reasoning_effort"] = "high"
    controls.max_tokens = None if max_tokens is None else min(max(max_tokens * 2, 8192), 32768)
else:
    controls.reasoning_effort = None
    controls.max_tokens = max_tokens
```

When `max_tokens is None`, the helper must not invent an output budget. The caller may either pass no max token value through unchanged, or provide an explicit Stage4 default before calling the helper.

Environment parsing:

- Truthy: `1`, `true`, `yes`, `on`
- Falsy: `0`, `false`, `no`, `off`
- Default: false

Stage constants:

```python
LLM_STAGE_CONTROL = "control"
LLM_STAGE_STAGE4_FINAL_ANSWER = "stage4_final_answer"
LLM_STAGE_TRANSLATION = "translation"
LLM_STAGE_DOCUMENT_SUMMARY = "document_summary"
```

Only `stage4_final_answer` may resolve to enabled.

---

## Service Integration Map

### `fastQA`

Primary helper location:

- Create `fastQA/app/integrations/llm/thinking.py`
- Export helper functions from `fastQA/app/integrations/llm/__init__.py` only if multiple modules need stable imports.

Core transport:

- Modify `fastQA/app/integrations/llm/openai_compat.py`
  - Allow blank `api_key`.
  - Omit `Authorization` header when `api_key` is blank.
  - Keep `extra_body` merge behavior.
  - Preserve stream behavior that emits only content.

Call sites:

- `fastQA/app/modules/generation_pipeline/stage1_planning.py`
  - Stage1 JSON calls: disabled thinking when model is marked thinking-capable.
- `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`
  - Query generation calls: disabled thinking.
- `fastQA/app/modules/generation_pipeline/query_expander.py`
  - Replace legacy `enable_thinking=False` with DeepSeek-format disabled controls.
  - Do not skip local LLM solely because API key is blank.
- `fastQA/app/modules/generation_pipeline/intent_detect.py`
  - Dedicated raw HTTP path: omit auth header when blank and send disabled controls for thinking models.
  - Shared client path: send disabled controls.
- `fastQA/app/modules/qa_kb/comparison_intent.py`
  - Comparison profile JSON call: disabled thinking.
- `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`
  - Fact extraction: disabled thinking.
  - Final answer stream: Stage4 controls may enable thinking.
  - Stream loop: discard `reasoning_content`.
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
  - Allow blank `LLM_API_KEY`.
- `fastQA/app/services/file_route_service.py`
  - Allow blank `LLM_API_KEY`.
- `fastQA/app/modules/qa_pdf/llm_factory.py`
  - Allow blank `LLM_API_KEY` for local OpenAI-compatible adapters.
  - Prefer the internal adapter when key is blank because LangChain/OpenAI SDK may require a placeholder.
- `fastQA/app/modules/qa_pdf/engine.py`
  - Treat user-visible PDF answer generation through `OpenAICompatChatAdapter.invoke/stream` as Stage4-equivalent final answer generation.
- `fastQA/app/modules/qa_tabular/service.py`
- `fastQA/app/modules/qa_tabular/renderer.py`
  - Treat user-visible tabular answer generation through `OpenAICompatChatAdapter.invoke/stream` as Stage4-equivalent final answer generation.

### `highThinkingQA`

Primary helper location:

- Create `highThinkingQA/agent_core/thinking.py`
  - Keep this service-local to avoid cross-service imports.

Configuration:

- Modify `highThinkingQA/config.py`
  - Add `llm_is_thinking_model`.
  - Add `llm_thinking_enabled`.
  - Default both to false.
  - Preserve old constants only as compatibility aliases if tests or callers still import them.

LLM client:

- Modify `highThinkingQA/agent_core/llm_client.py`
  - Replace API-key required behavior with placeholder key for SDK clients.
  - Add `stage` or `stage4` argument to `chat_completion` and `chat_completion_stream`.
  - Non-Stage4 calls must disable thinking for thinking models.
  - Stage4 calls may enable thinking when explicitly requested.
  - Log reasoning length only, never reasoning text.

Pipeline:

- Modify `highThinkingQA/agent_core/synthesizer.py`
  - Pass Stage4 final-answer stage to synthesis calls.
- Modify `highThinkingQA/agent_core/graph.py`
  - Profile `enable_thinking=True` may only affect synthesis.
  - Direct answer and decomposition must remain disabled even when the selected runtime profile is `thinking`.
- Modify `highThinkingQA/agent_core/sub_answerer.py`
  - Replace legacy `enable_thinking=False` with DeepSeek-format disabled controls.
- Modify checker/reviser/intent paths as needed so they call the helper with non-Stage4 policy.
- Modify `highThinkingQA/server/services/documents_service.py`
  - Translation and PDF summaries use disabled controls.
  - Blank API key uses SDK placeholder.

### `patent`

Primary helper location:

- Create `patent/server/patent/thinking.py`
  - Include raw HTTP header helper or expose a small `auth_headers(api_key)` function.

Raw HTTP clients:

- Omit `Authorization` when `LLM_API_KEY` is blank.
- Do not fail builder/from-env setup solely because `LLM_API_KEY` is blank.

Control-plane calls:

- `patent/server/patent/runtime.py`
- `patent/server/patent/stages/planning.py`
- `patent/server/patent/stages/retrieval.py`
- `patent/server/patent/intent_detect.py`
- `patent/server/patent/query_expander.py`

These must all disable thinking when `LLM_IS_THINKING_MODEL=true`.

Final answer calls:

- `patent/server/patent/answering.py`
- `patent/server/patent/pdf_service.py`
- `patent/server/patent/tabular_service.py`
- `patent/server/patent/hybrid_synthesis.py`
- `patent/server/patent/stages/synthesis.py` if it owns final synthesis dispatch parameters

These may enable thinking only for user-visible final answer generation.

Streaming:

- Patent streaming helpers must emit only final `content`.
- If upstream frames include `reasoning_content`, count and drop them.

### `public-service`

Primary helper location:

- Create `public-service/backend/app/modules/documents/llm_thinking.py`

Call sites:

- `public-service/backend/app/modules/documents/translator.py`
  - Translation always disables thinking.
  - Blank API key uses SDK placeholder.
- `public-service/backend/app/modules/documents/service.py`
  - Document summary/extraction LLM calls always disable thinking.
  - Blank API key uses SDK placeholder.

---

## Deployment Updates

Files to update during implementation:

- `deploy/.env`
- `deploy/docker-compose.yml`
- `resource/config/shared/model-endpoints.shared.env`
- `resource/config/shared/model-endpoints.secret.env.example`
- `resource/config/services/fastQA/config.secret.env.example`
- `resource/config/services/highThinkingQA/config.secret.env.example`
- `resource/config/services/highThinkingQA/config.env.example`
- `public-service/config.env.example`
- any active service-specific config templates that already define `LLM_*`

Add defaults:

```env
LLM_IS_THINKING_MODEL=false
LLM_THINKING_ENABLED=false
```

Compose passthrough:

- `fastqa`
- `highthinkingqa`
- `patent`
- `public-service`

Each service should receive both variables.

Do not change `LLM_MODEL` in this work. Switching to `deepseek-v4-pro` or `deepseek-v4-flash` is an operator deployment change.

---

## Acceptance Criteria

### Configuration

1. With `LLM_IS_THINKING_MODEL=false`, no LLM request contains `thinking` or `reasoning_effort`.
2. With `LLM_IS_THINKING_MODEL=true` and `LLM_THINKING_ENABLED=false`, every LLM request contains disabled thinking controls.
3. With both booleans true, only Stage4 final answer requests contain enabled thinking controls.
4. With blank `LLM_API_KEY`, raw HTTP chat clients send no `Authorization` header.
5. With blank `LLM_API_KEY`, OpenAI SDK clients initialize with a local placeholder key.

### Stage Safety

1. Stage1 JSON mode remains disabled even when global thinking is enabled.
2. Stage2 retrieval query generation remains disabled.
3. Intent detection remains disabled.
4. Query expansion remains disabled.
5. Translation remains disabled.
6. Document summary/extraction remains disabled.
7. Checker, reviser, sub-answer, direct answer, and decomposition remain disabled.

### Streaming

1. A stream chunk with only `reasoning_content` produces no user-visible output.
2. A mixed stream produces final answer text from `content` only.
3. Logs include reasoning character counts but not reasoning content.
4. Citation validation and post-processing receive content-only text.

### Compatibility

1. Current non-thinking local model deployment continues to work with default config.
2. Thinking-capable DeepSeek V4 deployment can be made non-thinking by setting:

```env
LLM_IS_THINKING_MODEL=true
LLM_THINKING_ENABLED=false
```

3. Thinking-capable DeepSeek V4 deployment can enable thinking only in final answer generation by setting:

```env
LLM_IS_THINKING_MODEL=true
LLM_THINKING_ENABLED=true
```

---

## Test Plan

Targeted unit tests:

```bash
pytest fastQA/tests/test_llm_openai_compat.py
pytest fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_generation_stage4_synthesis.py
pytest highThinkingQA/tests/test_llm_client.py highThinkingQA/tests/test_stage_model_selection.py
pytest patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_kb_service.py
pytest public-service/backend/tests/test_documents_module.py
```

Expected new or updated tests:

- Helper tests for non-thinking model: no thinking fields.
- Helper tests for thinking model disabled: disabled fields.
- Helper tests for Stage4 thinking enabled: enabled fields, `reasoning_effort=high`, expanded max tokens.
- Stage1 planning test: global thinking enabled still sends disabled controls.
- Stage4 stream test: reasoning-only chunk is discarded and content chunk is emitted.
- Blank API key test: raw HTTP omits auth and SDK uses placeholder.
- Translation/document summary tests: disabled controls are sent when model is thinking-capable.
- Enabled-thinking Stage4 test: request payload/SDK kwargs omit `temperature`, `top_p`, `presence_penalty`, and `frequency_penalty`.

Recommended broader regression after targeted tests pass:

```bash
pytest fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_generation_stage4_synthesis.py
pytest highThinkingQA/tests/test_llm_client.py highThinkingQA/tests/test_stage_model_selection.py
pytest patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_kb_service.py
pytest public-service/backend/tests
```

---

## Rollout Plan

1. Ship code with both new booleans defaulting to false.
2. Deploy with current non-thinking model unchanged.
3. Verify no payload contains thinking fields under default config.
4. Switch a staging deployment to a thinking-capable model with:

```env
LLM_MODEL=deepseek-v4-pro
LLM_IS_THINKING_MODEL=true
LLM_THINKING_ENABLED=false
```

5. Verify all requests explicitly disable thinking.
6. Enable Stage4 thinking in staging:

```env
LLM_THINKING_ENABLED=true
```

7. Verify only final answer generation enables thinking.
8. Monitor:
   - first token latency
   - total answer latency
   - output token usage
   - upstream 400/422 errors
   - stream completion rate
   - reasoning character count logs

Rollback:

```env
LLM_THINKING_ENABLED=false
```

If the upstream model rejects the thinking parameter entirely:

```env
LLM_IS_THINKING_MODEL=false
```

---

## Implementation Decisions

These decisions are part of the spec and do not require further clarification before implementation:

1. Treat all user-visible final answer generation as Stage4-equivalent, including PDF/tabular/hybrid final answers.
2. Keep `32768` hardcoded for the first implementation.
3. Preserve old highThinking constants as code aliases only, but make them read from the new booleans.
4. Add lightweight logs for final-answer thinking resolution and reasoning character counts only.
