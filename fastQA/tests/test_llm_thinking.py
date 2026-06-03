from __future__ import annotations

from app.integrations.llm.thinking import (
    LLM_STAGE_CONTROL,
    LLM_STAGE_STAGE4_FINAL_ANSWER,
    apply_openai_compatible_thinking,
    auth_headers,
    local_sdk_api_key,
    resolve_auth_mode,
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
    assert auth_headers("Bearer token")["Authorization"] == "Bearer token"
    assert auth_headers("bearer token")["Authorization"] == "Bearer token"
    assert local_sdk_api_key("") == "local-openai-compatible"
    assert local_sdk_api_key("real") == "real"
    assert local_sdk_api_key("Bearer real") == "real"


def test_auth_headers_supports_configurable_auth_modes(monkeypatch):
    monkeypatch.setenv("LLM_AUTH_MODE", "authorization")
    assert resolve_auth_mode() == "authorization"
    assert auth_headers("Bearer token")["Authorization"] == "token"

    assert auth_headers("Bearer token", auth_mode="bearer")["Authorization"] == "Bearer token"
    assert auth_headers("Bearer token", auth_mode="x-api-key")["X-API-Key"] == "token"
    assert "Authorization" not in auth_headers("Bearer token", auth_mode="none")
    assert "X-API-Key" not in auth_headers("Bearer token", auth_mode="none")
