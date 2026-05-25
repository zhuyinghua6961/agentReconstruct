# LLM Thinking Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the five-key LLM thinking contract so local OpenAI-compatible deployments default to non-thinking, while Stage4 final answers can opt into thinking for thinking-capable models.

**Architecture:** Add small service-local thinking helpers, then wire them into each service's existing LLM call sites. Raw HTTP clients omit auth when the key is blank; OpenAI SDK clients use a local placeholder key. Stage4 final answer call sites pass `thinking.type=enabled` only when both thinking booleans are true; all other LLM calls pass disabled thinking for thinking-capable models.

**Tech Stack:** Python 3, FastAPI service modules, OpenAI-compatible ChatCompletions payloads, OpenAI Python SDK, pytest.

---

## Reference Spec

Implement against:

- [docs/superpowers/specs/2026-05-25-llm-thinking-control-design.md](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-05-25-llm-thinking-control-design.md)

Do not change `LLM_MODEL` defaults or introduce provider selection. Do not touch VLM/OCR ingestion paths that use `VLM_*`.

---

## Implementation Slices

This work is deliberately split by service boundary:

1. `fastQA`: shared raw HTTP/OpenAI-compatible transport plus fastQA generation, PDF, and tabular final-answer paths.
2. `highThinkingQA`: SDK client, runtime config, agent graph stage gating, and retired document service LLM calls.
3. `patent`: raw HTTP/SDK helper, staged planning/retrieval controls, final-answer clients, and streaming handling.
4. `public-service` and deployment config: document translation/summary controls plus env/compose passthrough.
5. Cross-service verification: targeted pytest commands and config scan.

Use TDD per task: write or update failing tests first, run the targeted test and confirm the expected failure, implement the minimal code, then rerun.

---

### Task 1: `fastQA` Thinking Helper And Transport

**Files:**

- Create: `fastQA/app/integrations/llm/thinking.py`
- Modify: `fastQA/app/integrations/llm/__init__.py`
- Modify: `fastQA/app/integrations/llm/openai_compat.py`
- Test: `fastQA/tests/test_llm_thinking.py`
- Test: `fastQA/tests/test_llm_openai_compat.py`

- [ ] **Step 1: Write failing helper tests**

Create `fastQA/tests/test_llm_thinking.py` with tests for the target helper API:

```python
from app.integrations.llm.thinking import (
    LLM_STAGE_CONTROL,
    LLM_STAGE_STAGE4_FINAL_ANSWER,
    apply_openai_compatible_thinking,
    auth_headers,
    local_sdk_api_key,
    resolve_thinking_controls,
)


def test_non_thinking_model_returns_no_controls():
    controls = resolve_thinking_controls(
        is_thinking_model=False,
        thinking_enabled=True,
        stage=LLM_STAGE_STAGE4_FINAL_ANSWER,
        max_tokens=4000,
        stream=True,
    )
    assert controls.extra_body is None
    assert controls.raw_payload_fields == {}
    assert controls.reasoning_effort is None
    assert controls.max_tokens == 4000
    assert controls.enabled is False


def test_thinking_model_control_stage_disables_thinking():
    controls = resolve_thinking_controls(
        is_thinking_model=True,
        thinking_enabled=True,
        stage=LLM_STAGE_CONTROL,
        max_tokens=1200,
        stream=False,
    )
    assert controls.extra_body == {"thinking": {"type": "disabled"}}
    assert controls.raw_payload_fields == {"thinking": {"type": "disabled"}}
    assert controls.reasoning_effort is None
    assert controls.max_tokens == 1200
    assert controls.enabled is False


def test_stage4_enabled_expands_tokens_and_omits_sampling():
    payload = {
        "temperature": 0.2,
        "top_p": 0.9,
        "presence_penalty": 0,
        "frequency_penalty": 0,
        "max_tokens": 4000,
    }
    controls = resolve_thinking_controls(
        is_thinking_model=True,
        thinking_enabled=True,
        stage=LLM_STAGE_STAGE4_FINAL_ANSWER,
        max_tokens=4000,
        stream=True,
    )
    apply_openai_compatible_thinking(payload, controls)
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"
    assert payload["max_tokens"] == 8192
    assert "temperature" not in payload
    assert "top_p" not in payload
    assert "presence_penalty" not in payload
    assert "frequency_penalty" not in payload


def test_stage4_enabled_does_not_invent_missing_max_tokens():
    controls = resolve_thinking_controls(
        is_thinking_model=True,
        thinking_enabled=True,
        stage=LLM_STAGE_STAGE4_FINAL_ANSWER,
        max_tokens=None,
        stream=True,
    )
    assert controls.max_tokens is None


def test_blank_auth_and_sdk_placeholder():
    assert "Authorization" not in auth_headers("")
    assert auth_headers("token")["Authorization"] == "Bearer token"
    assert local_sdk_api_key("") == "local-openai-compatible"
    assert local_sdk_api_key("real") == "real"
```

- [ ] **Step 2: Run helper tests and confirm RED**

Run:

```bash
pytest fastQA/tests/test_llm_thinking.py -q
```

Expected: import failure because `app.integrations.llm.thinking` does not exist.

- [ ] **Step 3: Add the `fastQA` helper**

Create `fastQA/app/integrations/llm/thinking.py` with:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

LLM_STAGE_CONTROL = "control"
LLM_STAGE_STAGE4_FINAL_ANSWER = "stage4_final_answer"
LLM_STAGE_TRANSLATION = "translation"
LLM_STAGE_DOCUMENT_SUMMARY = "document_summary"
LOCAL_OPENAI_COMPATIBLE_API_KEY = "local-openai-compatible"
_SAMPLING_KEYS = ("temperature", "top_p", "presence_penalty", "frequency_penalty")


@dataclass(frozen=True)
class ThinkingControls:
    extra_body: dict[str, Any] | None
    raw_payload_fields: dict[str, Any]
    reasoning_effort: str | None
    max_tokens: int | None
    enabled: bool


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def llm_is_thinking_model() -> bool:
    return env_bool("LLM_IS_THINKING_MODEL", False)


def llm_thinking_enabled() -> bool:
    return env_bool("LLM_THINKING_ENABLED", False)


def local_sdk_api_key(api_key: str | None) -> str:
    return str(api_key or "").strip() or LOCAL_OPENAI_COMPATIBLE_API_KEY


def auth_headers(api_key: str | None, *, accept: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if accept:
        headers["Accept"] = accept
    key = str(api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def resolve_thinking_controls(
    *,
    is_thinking_model: bool | None = None,
    thinking_enabled: bool | None = None,
    stage: str,
    max_tokens: int | None,
    stream: bool,
) -> ThinkingControls:
    del stream
    model_supports_thinking = llm_is_thinking_model() if is_thinking_model is None else bool(is_thinking_model)
    requested = llm_thinking_enabled() if thinking_enabled is None else bool(thinking_enabled)
    if not model_supports_thinking:
        return ThinkingControls(None, {}, None, max_tokens, False)
    enabled = bool(stage == LLM_STAGE_STAGE4_FINAL_ANSWER and requested)
    thinking_type = "enabled" if enabled else "disabled"
    fields: dict[str, Any] = {"thinking": {"type": thinking_type}}
    reasoning_effort = None
    effective_max = max_tokens
    if enabled:
        reasoning_effort = "high"
        fields["reasoning_effort"] = reasoning_effort
        if max_tokens is not None:
            effective_max = min(max(int(max_tokens) * 2, 8192), 32768)
    return ThinkingControls(
        extra_body={"thinking": {"type": thinking_type}},
        raw_payload_fields=fields,
        reasoning_effort=reasoning_effort,
        max_tokens=effective_max,
        enabled=enabled,
    )


def merge_extra_body(existing: Mapping[str, Any] | None, controls: ThinkingControls) -> dict[str, Any] | None:
    merged = dict(existing or {})
    if controls.extra_body:
        merged.update(controls.extra_body)
    if controls.reasoning_effort:
        merged["reasoning_effort"] = controls.reasoning_effort
    return merged or None


def apply_openai_compatible_thinking(payload: dict[str, Any], controls: ThinkingControls) -> None:
    if controls.max_tokens is not None:
        payload["max_tokens"] = controls.max_tokens
    if controls.enabled:
        for key in _SAMPLING_KEYS:
            payload.pop(key, None)
    payload.update(controls.raw_payload_fields)
```

Export helper symbols from `fastQA/app/integrations/llm/__init__.py` only after tests import them.

- [ ] **Step 4: Run helper tests and confirm GREEN**

Run:

```bash
pytest fastQA/tests/test_llm_thinking.py -q
```

Expected: all new helper tests pass.

- [ ] **Step 5: Add failing transport tests**

Update `fastQA/tests/test_llm_openai_compat.py`:

```python
def test_openai_compat_omits_authorization_when_api_key_blank():
    post_response = _FakeResponse(payload={"choices": [{"message": {"content": "answer"}}]})
    stream_response = _FakeResponse(lines=["data: [DONE]"])
    fake_client = _FakeClient(post_response=post_response, stream_response=stream_response)
    fake_httpx = _FakeHttpx(client=fake_client)
    client = OpenAICompatClient(
        httpx_module=fake_httpx,
        endpoint="https://example.com/v1/chat/completions",
        api_key="",
    )

    client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])

    headers = fake_client.calls[0][2]["headers"]
    assert "Authorization" not in headers
```

Add another test for request-level `reasoning_effort` and sampling omission:

```python
def test_openai_compat_accepts_reasoning_effort_and_omits_sampling_when_requested():
    post_response = _FakeResponse(payload={"choices": [{"message": {"content": "answer"}}]})
    stream_response = _FakeResponse(lines=["data: [DONE]"])
    fake_client = _FakeClient(post_response=post_response, stream_response=stream_response)
    fake_httpx = _FakeHttpx(client=fake_client)
    client = OpenAICompatClient(
        httpx_module=fake_httpx,
        endpoint="https://example.com/v1/chat/completions",
        api_key="token",
    )

    client.chat.completions.create(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.5,
        top_p=0.9,
        max_tokens=4000,
        extra_body={"thinking": {"type": "enabled"}},
        reasoning_effort="high",
        omit_sampling_parameters=True,
    )

    payload = fake_client.calls[0][2]["json"]
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"
    assert "temperature" not in payload
    assert "top_p" not in payload
```

- [ ] **Step 6: Run transport tests and confirm RED**

Run:

```bash
pytest fastQA/tests/test_llm_openai_compat.py::test_openai_compat_omits_authorization_when_api_key_blank fastQA/tests/test_llm_openai_compat.py::test_openai_compat_accepts_reasoning_effort_and_omits_sampling_when_requested -q
```

Expected: first test fails because auth is always sent; second fails because `reasoning_effort` and `omit_sampling_parameters` are ignored.

- [ ] **Step 7: Update `openai_compat.py` minimally**

In `fastQA/app/integrations/llm/openai_compat.py`:

- Change `_headers()` to include `Authorization` only when `self._cfg.api_key.strip()` is non-empty.
- Add optional `_build_payload(..., reasoning_effort: str | None = None, omit_sampling_parameters: bool = False)`.
- When `omit_sampling_parameters` is true, skip `temperature` and `top_p`.
- Include `reasoning_effort` top-level if provided.
- Add `reasoning_effort` and `omit_sampling_parameters` parameters to `_CompatCompletions.create`, `_invoke`, and `_stream`, and pass them through to `_build_payload`.

- [ ] **Step 8: Run Task 1 tests**

Run:

```bash
pytest fastQA/tests/test_llm_thinking.py fastQA/tests/test_llm_openai_compat.py -q
```

Expected: pass.

---

### Task 2: `fastQA` Call Sites And File Final Answers

**Files:**

- Modify: `fastQA/app/modules/generation_pipeline/stage1_planning.py`
- Modify: `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`
- Modify: `fastQA/app/modules/generation_pipeline/query_expander.py`
- Modify: `fastQA/app/modules/generation_pipeline/intent_detect.py`
- Modify: `fastQA/app/modules/qa_kb/comparison_intent.py`
- Modify: `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`
- Modify: `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/services/file_route_service.py`
- Modify: `fastQA/app/modules/qa_pdf/llm_factory.py`
- Test: `fastQA/tests/test_generation_runtime_bootstrap.py`
- Test: `fastQA/tests/test_generation_stage1_planning.py`
- Test: `fastQA/tests/test_generation_stage2_retrieval.py`
- Test: `fastQA/tests/test_generation_stage4_synthesis.py`
- Test: `fastQA/tests/test_intent_detect.py`
- Test: `fastQA/tests/test_file_route_service.py`
- Test: `fastQA/tests/test_qa_pdf_llm_factory.py`

- [ ] **Step 1: Add failing Stage1 and Stage4 tests**

In `fastQA/tests/test_generation_stage1_planning.py`, add:

```python
def test_stage1_disables_thinking_for_thinking_model(monkeypatch):
    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")
    client = _FakeClient('{"deep_answer":"answer","retrieval_claims":[]}')

    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    assert result["success"] is True
    assert client.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
```

In `fastQA/tests/test_generation_stage4_synthesis.py`, add a helper:

```python
def _reasoning_chunk(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content=text))])
```

Add:

```python
def test_stage4_synthesis_enables_thinking_and_drops_reasoning(monkeypatch):
    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FakeClient([_reasoning_chunk("secret reasoning"), _chunk("结论"), _chunk(" (doi=10.1/a)")])

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert outputs[0] == "结论"
    assert outputs[1] == " (doi=10.1/a)"
    call = client.calls[0]
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["reasoning_effort"] == "high"
    assert call["max_tokens"] == 8192
    assert "temperature" not in call
```

- [ ] **Step 2: Run Stage1/Stage4 tests and confirm RED**

Run:

```bash
pytest fastQA/tests/test_generation_stage1_planning.py::test_stage1_disables_thinking_for_thinking_model fastQA/tests/test_generation_stage4_synthesis.py::test_stage4_synthesis_enables_thinking_and_drops_reasoning -q
```

Expected: failures because call sites do not pass DeepSeek thinking controls.

- [ ] **Step 3: Wire control-plane call sites**

Use `resolve_thinking_controls(...stage=LLM_STAGE_CONTROL...)` and `merge_extra_body(...)` in:

- `_create_stage1_completion()` in `stage1_planning.py`.
- Stage2 `active_client.chat.completions.create(...)` calls in `stage2_retrieval.py`.
- `QueryExpander.expand()` in `query_expander.py`; remove legacy `{"enable_thinking": False}`.
- `_create_intent_completion()` client path and `_create_dedicated_intent_completion()` raw payload in `intent_detect.py`.
- `generate_comparison_retrieval_profile()` in `qa_kb/comparison_intent.py`.
- `_extract_citable_facts_from_evidence()` in `synthesis_streaming.py`.

For raw intent headers, use the helper `auth_headers(api_key)` behavior so a blank key omits `Authorization`.

- [ ] **Step 4: Wire Stage4 final synthesis**

In `iter_stage4_synthesis_with_pdf_chunks()`:

- Resolve controls with `stage=LLM_STAGE_STAGE4_FINAL_ANSWER`, `max_tokens=4000`, `stream=True`.
- Pass `max_tokens=controls.max_tokens`.
- Pass `extra_body=controls.extra_body`.
- Pass `reasoning_effort=controls.reasoning_effort` when non-None.
- Pass `omit_sampling_parameters=controls.enabled`.
- Include `temperature=stream_temperature` only when `not controls.enabled`.
- In the stream loop, read `delta.reasoning_content` and count chars, but continue without yielding.
- Log only reasoning char count at stream completion.

- [ ] **Step 5: Allow blank LLM key in fastQA bootstrap and file adapter**

Update:

- `runtime_bootstrap.resolve_generation_runtime_inputs()`: keep blank `LLM_API_KEY` as valid.
- `app/core/runtime.bootstrap_generation_runtime()`: remove the `LLM_API_KEY is required` guard; still require `LLM_BASE_URL`.
- `app/core/runtime._warm_stage2_chat_lane()`: pass disabled thinking controls when `LLM_IS_THINKING_MODEL=true`; this warmup is a non-Stage4 call.
- `file_route_service.resolve_app_owned_llm()`: remove the `LLM_API_KEY is required for file QA` guard; still require base URL.
- `qa_pdf/llm_factory.init_llm()`: when `LLM_API_KEY` is blank, build and return `OpenAICompatChatAdapter` instead of raising `ValueError`.
- `OpenAICompatChatAdapter.invoke/stream`: treat adapter final-answer use as Stage4-equivalent by applying helper controls before raw request payload construction. Keep reasoning chunks content-only.

- [ ] **Step 6: Add failing blank-key and intent/query tests**

Update existing tests:

- `fastQA/tests/test_file_route_service.py`: replace the missing-key error expectation with a test that blank key still builds an adapter and omits auth.
- `fastQA/tests/test_qa_pdf_llm_factory.py`: update `test_init_llm_ignores_retired_llm_aliases` so blank `LLM_API_KEY` with `LLM_BASE_URL` and `LLM_MODEL` returns the internal adapter.
- `fastQA/tests/test_intent_detect.py`: update dedicated raw payload assertion from `enable_thinking is False` to `thinking == {"type": "disabled"}` when `LLM_IS_THINKING_MODEL=true`.
- `fastQA/tests/test_generation_stage2_retrieval.py`: assert retrieval query LLM calls include disabled thinking controls when both booleans are true.
- `fastQA/tests/test_generation_runtime_bootstrap.py`: add a bootstrap test where `LLM_API_KEY` is blank, `LLM_BASE_URL` is set, and runtime initialization does not raise `LLM_API_KEY is required`.
- `fastQA/tests/test_generation_runtime_bootstrap.py`: add a warmup helper test that calls `_warm_stage2_chat_lane()` with `LLM_IS_THINKING_MODEL=true` and asserts the completion call includes disabled thinking controls.

Run each new or modified test individually and confirm the expected RED before implementation.

- [ ] **Step 7: Run Task 2 targeted tests**

Run:

```bash
pytest fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_generation_stage4_synthesis.py fastQA/tests/test_intent_detect.py fastQA/tests/test_file_route_service.py fastQA/tests/test_qa_pdf_llm_factory.py -q
```

Expected: pass.

---

### Task 3: `highThinkingQA` Config, SDK Client, And Stage Gating

**Files:**

- Create: `highThinkingQA/agent_core/thinking.py`
- Modify: `highThinkingQA/config.py`
- Modify: `highThinkingQA/agent_core/llm_client.py`
- Modify: `highThinkingQA/agent_core/intent_detect.py`
- Modify: `highThinkingQA/agent_core/synthesizer.py`
- Modify: `highThinkingQA/agent_core/graph.py`
- Modify: `highThinkingQA/agent_core/sub_answerer.py`
- Modify: `highThinkingQA/server/services/documents_service.py`
- Test: `highThinkingQA/tests/test_llm_client.py`
- Test: `highThinkingQA/tests/test_intent_detect.py`
- Test: `highThinkingQA/tests/test_api_key_validation.py`
- Test: `highThinkingQA/tests/test_config_runtime_defaults.py`
- Test: `highThinkingQA/tests/test_env_loader.py`
- Test: `highThinkingQA/tests/test_run_agent_overlap.py`
- Test: `highThinkingQA/tests/test_stage_model_selection.py`

- [ ] **Step 1: Add failing highThinking helper/client tests**

Update `highThinkingQA/tests/test_llm_client.py`:

- Non-thinking model + `enable_thinking=True` sends no `extra_body`.
- Thinking model + control stage sends `extra_body={"thinking":{"type":"disabled"}}`.
- Thinking model + Stage4 stream sends `extra_body={"thinking":{"type":"enabled"}}`, `reasoning_effort="high"`, no `temperature`, expanded `max_tokens`, and yields only `content`.
- Blank key initializes OpenAI/AsyncOpenAI with `local-openai-compatible`.
- `highThinkingQA/agent_core/intent_detect.py` direct SDK calls send disabled thinking controls when `LLM_IS_THINKING_MODEL=true`.

Example Stage4 stream test:

```python
def test_stage4_stream_enables_deepseek_thinking_and_drops_reasoning(monkeypatch):
    monkeypatch.setattr("agent_core.llm_client.config.LLM_IS_THINKING_MODEL", True)
    monkeypatch.setattr("agent_core.llm_client.config.LLM_THINKING_ENABLED", True)
    completions = _FakeStreamCompletions([
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content="secret"))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", reasoning_content=None))]),
    ])
    client = _FakeClient(completions)

    result = "".join(chat_completion_stream(prompt="demo", client=client, enable_thinking=True, stage="stage4_final_answer", max_tokens=4096))

    assert result == "ok"
    call = completions.calls[0]
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["reasoning_effort"] == "high"
    assert call["max_tokens"] == 8192
    assert "temperature" not in call
```

- [ ] **Step 2: Run highThinking client tests and confirm RED**

Run:

```bash
pytest highThinkingQA/tests/test_llm_client.py highThinkingQA/tests/test_intent_detect.py highThinkingQA/tests/test_api_key_validation.py -q
```

Expected: failures from old Qwen `enable_thinking` behavior and API-key-required assertions.

- [ ] **Step 3: Add helper and config fields**

Create `highThinkingQA/agent_core/thinking.py` with the same semantics as the fastQA helper.

Modify `highThinkingQA/config.py`:

- Add `llm_is_thinking_model: bool`.
- Add `llm_thinking_enabled: bool`.
- Read from `LLM_IS_THINKING_MODEL` and `LLM_THINKING_ENABLED`.
- Default both to false.
- Add module constants:

```python
LLM_IS_THINKING_MODEL = SETTINGS.llm_is_thinking_model
LLM_THINKING_ENABLED = SETTINGS.llm_thinking_enabled
MAIN_LLM_THINKING_ENABLED = SETTINGS.llm_thinking_enabled
DIRECT_STAGE_THINKING_ENABLED = False
DECOMPOSE_STAGE_THINKING_ENABLED = False
```

Update tests that expected `MAIN_LLM_THINKING_ENABLED is True` to expect false by default or true only when `LLM_THINKING_ENABLED=true`.

- [ ] **Step 4: Update SDK client behavior**

In `highThinkingQA/agent_core/llm_client.py`:

- Replace `_require_api_key()` for LLM with `_local_api_key()`.
- Add optional `stage: str = LLM_STAGE_CONTROL` to `chat_completion()` and `chat_completion_stream()`.
- `_build_kwargs()` uses helper controls.
- If controls enabled:
  - omit `temperature`,
  - set expanded `max_tokens`,
  - set `extra_body={"thinking":{"type":"enabled"}}`,
  - set `reasoning_effort="high"`.
- If controls disabled for a thinking model:
  - set `extra_body={"thinking":{"type":"disabled"}}`,
  - keep existing `temperature` and original `max_tokens`.
- If not a thinking model:
  - do not pass `extra_body` or `reasoning_effort`.
- In streaming, count `reasoning_content` chars and never yield or log the text.

- [ ] **Step 5: Gate Stage4 only in the graph**

In `highThinkingQA/agent_core/synthesizer.py`:

- Pass `stage=LLM_STAGE_STAGE4_FINAL_ANSWER` to `chat_completion()` and `chat_completion_stream()`.

In `highThinkingQA/agent_core/graph.py`:

- `resolved_enable_thinking` remains the Stage4 synthesis request flag.
- `resolved_stream_synthesis_enable_thinking` must no longer force false when streaming; Stage4 stream can enable thinking.
- `resolved_direct_answer_enable_thinking` must always resolve false unless a future explicit direct-stage flag is introduced.
- `resolved_decompose_enable_thinking` must always resolve false.

In `highThinkingQA/agent_core/sub_answerer.py`:

- Replace legacy `extra_body={"enable_thinking": False}` with helper disabled controls.

In `highThinkingQA/agent_core/intent_detect.py`:

- Import the helper.
- Resolve non-Stage4 controls with `stage=LLM_STAGE_CONTROL`.
- Pass disabled controls to `client.chat.completions.create(...)` when `LLM_IS_THINKING_MODEL=true`.

- [ ] **Step 6: Update retired document LLM calls**

In `highThinkingQA/server/services/documents_service.py`:

- `_openai_client()` returns `OpenAI(api_key=local_sdk_api_key(api_key), base_url=...)` when base URL/model exists, even if key is blank.
- Translation and summary calls pass disabled thinking controls when `LLM_IS_THINKING_MODEL=true`.

- [ ] **Step 7: Run highThinking targeted tests**

Run:

```bash
pytest highThinkingQA/tests/test_llm_client.py highThinkingQA/tests/test_intent_detect.py highThinkingQA/tests/test_api_key_validation.py highThinkingQA/tests/test_config_runtime_defaults.py highThinkingQA/tests/test_env_loader.py highThinkingQA/tests/test_run_agent_overlap.py highThinkingQA/tests/test_stage_model_selection.py -q
```

Expected: pass.

---

### Task 4: `patent` Thinking Helper, Control Calls, And Final Answers

**Files:**

- Create: `patent/server/patent/thinking.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/stages/planning.py`
- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/server/patent/intent_detect.py`
- Modify: `patent/server/patent/query_expander.py`
- Modify: `patent/server/patent/answering.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/server/patent/hybrid_synthesis.py`
- Test: `patent/tests/test_patent_stage1_planning.py`
- Test: `patent/tests/test_patent_pdf_contract.py`
- Test: `patent/tests/test_patent_kb_service.py`
- Test: `patent/tests/test_patent_tabular_service.py`
- Test: `patent/tests/test_patent_hybrid_synthesis.py`
- Test: `patent/tests/test_patent_intent_detect.py`

- [ ] **Step 1: Add patent helper tests**

Create or extend a test file such as `patent/tests/test_patent_upstream_config.py`:

```python
from server.patent.thinking import LLM_STAGE_CONTROL, LLM_STAGE_STAGE4_FINAL_ANSWER, auth_headers, resolve_thinking_controls


def test_patent_thinking_helper_matches_stage_policy():
    disabled = resolve_thinking_controls(is_thinking_model=True, thinking_enabled=True, stage=LLM_STAGE_CONTROL, max_tokens=1000, stream=False)
    enabled = resolve_thinking_controls(is_thinking_model=True, thinking_enabled=True, stage=LLM_STAGE_STAGE4_FINAL_ANSWER, max_tokens=4000, stream=True)
    assert disabled.raw_payload_fields == {"thinking": {"type": "disabled"}}
    assert enabled.raw_payload_fields["thinking"] == {"type": "enabled"}
    assert enabled.raw_payload_fields["reasoning_effort"] == "high"
    assert enabled.max_tokens == 8192
    assert "Authorization" not in auth_headers("")
```

- [ ] **Step 2: Run patent helper test and confirm RED**

Run:

```bash
pytest patent/tests/test_patent_upstream_config.py::test_patent_thinking_helper_matches_stage_policy -q
```

Expected: import failure because `server.patent.thinking` does not exist.

- [ ] **Step 3: Add `patent/server/patent/thinking.py`**

Implement the same helper semantics as the fastQA helper, adjusted to import path `server.patent.thinking`.

Include:

- `auth_headers(api_key, accept=None)`
- `local_sdk_api_key(api_key)`
- `resolve_thinking_controls(...)`
- `apply_openai_compatible_thinking(payload, controls)`
- `merge_extra_body(...)`

- [ ] **Step 4: Add failing control-plane tests**

Update:

- `patent/tests/test_patent_stage1_planning.py`: Stage1 planning with both booleans true still sends disabled thinking.
- `patent/tests/test_patent_intent_detect.py`: dedicated intent raw payload uses `thinking.type=disabled` instead of legacy `enable_thinking=False` for thinking models.

Run:

```bash
pytest patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_intent_detect.py -q
```

Expected: failures until call sites are wired.

- [ ] **Step 5: Wire patent control-plane calls**

Use disabled controls in:

- `PatentPlanningClient` request payloads in `runtime.py`.
- `server/patent/stages/planning.py` completion calls.
- `server/patent/stages/retrieval.py` completion calls.
- `server/patent/intent_detect.py` raw and client paths.
- `server/patent/query_expander.py` SDK call; replace legacy `enable_thinking=False`.

Also:

- Allow blank LLM API key in `runtime.py` builders.
- Omit `Authorization` headers when key is blank.
- Use SDK placeholder for `query_expander.py`.

- [ ] **Step 6: Add failing final-answer tests**

Update:

- `patent/tests/test_patent_pdf_contract.py`: when both booleans true, `PatentPdfAnswerClient` final stream/raw payload has enabled thinking, `reasoning_effort=high`, expanded max tokens, no sampling params, and reasoning-only chunks are not emitted.
- `patent/tests/test_patent_kb_service.py` or `test_patent_answering_graph_context.py`: KB final answer payload can enable thinking.
- `patent/tests/test_patent_tabular_service.py`: tabular final answer payload can enable thinking.
- `patent/tests/test_patent_hybrid_synthesis.py`: hybrid final answer payload can enable thinking.

Run each specific test and confirm RED before implementation.

- [ ] **Step 7: Wire patent final-answer clients**

In:

- `answering.py`
- `pdf_service.py`
- `tabular_service.py`
- `hybrid_synthesis.py`

For user-visible final answer requests:

- Resolve controls with `stage=LLM_STAGE_STAGE4_FINAL_ANSWER`.
- Apply raw payload fields.
- Expand max tokens only when enabled.
- Omit sampling params when enabled.
- Omit Authorization if key blank.
- For streaming, drop `reasoning_content`, count chars, and emit only `content`.

Do not apply enabled thinking to intermediate JSON/planning/extraction calls inside those files.

- [ ] **Step 8: Run patent targeted tests**

Run:

```bash
pytest patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_intent_detect.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_kb_service.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_hybrid_synthesis.py -q
```

Expected: pass.

---

### Task 5: `public-service` Documents And Deployment Config

**Files:**

- Create: `public-service/backend/app/modules/documents/llm_thinking.py`
- Modify: `public-service/backend/app/modules/documents/translator.py`
- Modify: `public-service/backend/app/modules/documents/service.py`
- Modify: `deploy/.env`
- Modify: `deploy/.env.example`
- Modify: `deploy/.env.production.example`
- Modify: `deploy/docker-compose.yml`
- Modify: `resource/config/shared/model-endpoints.shared.env`
- Modify: `resource/config/shared/model-endpoints.secret.env.example`
- Modify: `resource/config/services/fastQA/config.secret.env.example`
- Modify: `resource/config/services/highThinkingQA/config.secret.env.example`
- Modify: `resource/config/services/highThinkingQA/config.env.example`
- Modify: `resource/config/services/public-service/config.shared.env`
- Modify: `public-service/config.env.example`
- Test: `public-service/backend/tests/test_documents_module.py`

- [ ] **Step 1: Add failing public-service document tests**

Update `public-service/backend/tests/test_documents_module.py`:

- `SmartTranslator` initializes with blank `LLM_API_KEY` when base URL and model exist.
- Translation with `LLM_IS_THINKING_MODEL=true` sends `extra_body={"thinking":{"type":"disabled"}}`.
- PDF/document summary call sends disabled thinking controls when model is thinking-capable.

Run:

```bash
pytest public-service/backend/tests/test_documents_module.py -q
```

Expected: failures until helper and call sites are updated.

- [ ] **Step 2: Add public-service helper**

Create `public-service/backend/app/modules/documents/llm_thinking.py` with the same SDK-focused pieces:

- `env_bool`
- `llm_is_thinking_model`
- `llm_thinking_enabled`
- `local_sdk_api_key`
- `resolve_thinking_controls`
- `merge_extra_body`

Only document translation/summary stages are needed here; they always call the helper as non-Stage4.

- [ ] **Step 3: Wire document translation and summary**

In `translator.py`:

- Do not disable translation solely because API key is blank when base URL/model exist.
- Initialize OpenAI SDK with `local_sdk_api_key(api_key)`.
- Pass disabled controls in `client.chat.completions.create(...)`.

In `service.py`:

- Initialize OpenAI SDK with placeholder key when local key is blank.
- Pass disabled controls to summary/document LLM calls.

- [ ] **Step 4: Update deployment config**

Add:

```env
LLM_IS_THINKING_MODEL=false
LLM_THINKING_ENABLED=false
```

to the active env/template files listed above.

In `deploy/docker-compose.yml`, pass both variables to:

- `fastqa`
- `highthinkingqa`
- `patent`
- `public-service`

Do not modify `LLM_MODEL`.

- [ ] **Step 5: Run public-service and config checks**

Run:

```bash
pytest public-service/backend/tests/test_documents_module.py -q
rg -n "LLM_IS_THINKING_MODEL|LLM_THINKING_ENABLED" deploy resource/config public-service/config.env.example highThinkingQA/config.env.example highThinkingQA/config.secret.env.example
```

Expected: tests pass and config scan shows the new keys in active deployment/template surfaces.

---

### Task 6: Cross-Service Regression And Final Review

**Files:**

- No new production files unless earlier tasks require small fixes.
- Test-only fixes allowed in the same service-specific test files already listed.

- [ ] **Step 1: Run targeted regression**

Run:

```bash
pytest fastQA/tests/test_llm_thinking.py fastQA/tests/test_llm_openai_compat.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_generation_stage4_synthesis.py fastQA/tests/test_intent_detect.py fastQA/tests/test_file_route_service.py fastQA/tests/test_qa_pdf_llm_factory.py -q
pytest highThinkingQA/tests/test_llm_client.py highThinkingQA/tests/test_intent_detect.py highThinkingQA/tests/test_api_key_validation.py highThinkingQA/tests/test_config_runtime_defaults.py highThinkingQA/tests/test_env_loader.py highThinkingQA/tests/test_run_agent_overlap.py highThinkingQA/tests/test_stage_model_selection.py -q
pytest patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_intent_detect.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_kb_service.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_hybrid_synthesis.py -q
pytest public-service/backend/tests/test_documents_module.py -q
```

Expected: all targeted tests pass.

- [ ] **Step 2: Scan for retired thinking parameter usage**

Run:

```bash
rg -n "enable_thinking|reasoning_content|reasoning_effort|LLM_IS_THINKING_MODEL|LLM_THINKING_ENABLED|extra_body" fastQA/app highThinkingQA patent/server public-service/backend/app deploy resource/config -g '!**/.venv/**'
```

Expected:

- No production LLM call should still send `enable_thinking`.
- `reasoning_content` appears only in stream parsing/drop logic.
- `reasoning_effort` appears only in enabled Stage4 thinking helper/call paths.
- New env keys appear in config and compose.

- [ ] **Step 3: Review diff for scope control**

Run:

```bash
git diff --stat
git diff -- fastQA highThinkingQA patent public-service deploy resource/config docs/superpowers
```

Expected:

- No `LLM_MODEL` value changes.
- No provider selection config.
- No VLM/OCR ingestion changes.
- No secret values added.

- [ ] **Step 4: Request code review before final handoff**

Use `superpowers:requesting-code-review` after implementation, not during this planning-only task.

Review context should include:

- Spec: `docs/superpowers/specs/2026-05-25-llm-thinking-control-design.md`
- Plan: `docs/superpowers/plans/2026-05-25-llm-thinking-control-implementation.md`
- Summary of test commands and results

---

## Rollback Notes

Runtime rollback is config-only:

```env
LLM_THINKING_ENABLED=false
```

If a deployed model rejects the `thinking` field:

```env
LLM_IS_THINKING_MODEL=false
```

This intentionally returns the system to "send no thinking parameters" behavior.
