from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

LLM_STAGE_CONTROL = "control"
LLM_STAGE_TRANSLATION = "translation"
LLM_STAGE_DOCUMENT_SUMMARY = "document_summary"
LOCAL_OPENAI_COMPATIBLE_API_KEY = "local-openai-compatible"


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


def normalize_bearer_api_key(api_key: str | None) -> str:
    value = str(api_key or "").strip()
    scheme, separator, token = value.partition(" ")
    if separator and scheme.lower() == "bearer":
        return token.strip()
    return value


def local_sdk_api_key(api_key: str | None) -> str:
    return normalize_bearer_api_key(api_key) or LOCAL_OPENAI_COMPATIBLE_API_KEY


def resolve_thinking_controls(
    *,
    is_thinking_model: bool | None = None,
    thinking_enabled: bool | None = None,
    stage: str,
    max_tokens: int | None,
    stream: bool,
) -> ThinkingControls:
    del stage, stream
    model_supports_thinking = llm_is_thinking_model() if is_thinking_model is None else bool(is_thinking_model)
    _ = llm_thinking_enabled() if thinking_enabled is None else bool(thinking_enabled)
    if not model_supports_thinking:
        return ThinkingControls(
            extra_body=None,
            raw_payload_fields={},
            reasoning_effort=None,
            max_tokens=max_tokens,
            enabled=False,
        )
    return ThinkingControls(
        extra_body={"thinking": {"type": "disabled"}},
        raw_payload_fields={"thinking": {"type": "disabled"}},
        reasoning_effort=None,
        max_tokens=max_tokens,
        enabled=False,
    )


def merge_extra_body(existing: Mapping[str, Any] | None, controls: ThinkingControls) -> dict[str, Any] | None:
    merged = dict(existing or {})
    if controls.extra_body:
        merged.update(controls.extra_body)
    return merged or None
