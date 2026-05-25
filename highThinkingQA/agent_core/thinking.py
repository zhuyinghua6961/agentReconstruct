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


def local_sdk_api_key(api_key: str | None) -> str:
    return str(api_key or "").strip() or LOCAL_OPENAI_COMPATIBLE_API_KEY


def resolve_thinking_controls(
    *,
    is_thinking_model: bool | None = None,
    thinking_enabled: bool | None = None,
    stage: str,
    max_tokens: int | None,
    stream: bool,
) -> ThinkingControls:
    del stream
    model_supports_thinking = env_bool("LLM_IS_THINKING_MODEL", False) if is_thinking_model is None else bool(is_thinking_model)
    requested = env_bool("LLM_THINKING_ENABLED", False) if thinking_enabled is None else bool(thinking_enabled)
    if not model_supports_thinking:
        return ThinkingControls(
            extra_body=None,
            raw_payload_fields={},
            reasoning_effort=None,
            max_tokens=max_tokens,
            enabled=False,
        )

    enabled = bool(stage == LLM_STAGE_STAGE4_FINAL_ANSWER and requested)
    thinking_type = "enabled" if enabled else "disabled"
    fields: dict[str, Any] = {"thinking": {"type": thinking_type}}
    reasoning_effort = None
    effective_max_tokens = max_tokens
    if enabled:
        reasoning_effort = "high"
        fields["reasoning_effort"] = reasoning_effort
        if max_tokens is not None:
            effective_max_tokens = min(max(int(max_tokens) * 2, 8192), 32768)

    return ThinkingControls(
        extra_body={"thinking": {"type": thinking_type}},
        raw_payload_fields=fields,
        reasoning_effort=reasoning_effort,
        max_tokens=effective_max_tokens,
        enabled=enabled,
    )


def merge_extra_body(existing: Mapping[str, Any] | None, controls: ThinkingControls) -> dict[str, Any] | None:
    merged = dict(existing or {})
    if controls.extra_body:
        merged.update(controls.extra_body)
    return merged or None


def apply_openai_compatible_thinking(payload: dict[str, Any], controls: ThinkingControls) -> None:
    if controls.max_tokens is not None:
        payload["max_tokens"] = controls.max_tokens
    if controls.enabled:
        for key in _SAMPLING_KEYS:
            payload.pop(key, None)
    payload.update(controls.raw_payload_fields)
