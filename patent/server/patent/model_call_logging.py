from __future__ import annotations

import time
from typing import Any, Mapping

from server.patent.thinking import resolve_auth_mode


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def message_chars(messages: Any) -> int:
    if not isinstance(messages, list):
        return 0
    total = 0
    for item in messages:
        if isinstance(item, Mapping):
            total += len(str(item.get("content") or ""))
    return total


def auth_mode_label(auth_mode: str | None = None) -> str:
    return resolve_auth_mode(auth_mode)


def log_model_call_start(
    logger: Any,
    *,
    service: str = "patent",
    component: str,
    model: str,
    endpoint: str,
    auth_mode: str | None = None,
    stream: bool | None = None,
    message_count: int | None = None,
    message_chars_value: int | None = None,
    input_count: int | None = None,
    input_chars: int | None = None,
    timeout_seconds: Any | None = None,
    key_present: bool | None = None,
) -> float:
    started_at = time.perf_counter()
    fields = [
        "model_call start",
        f"service={service}",
        f"component={component}",
        f"model={model}",
        f"endpoint={endpoint}",
        f"auth_mode={auth_mode_label(auth_mode)}",
    ]
    if stream is not None:
        fields.append(f"stream={bool_text(stream)}")
    if message_count is not None:
        fields.append(f"message_count={int(message_count)}")
    if message_chars_value is not None:
        fields.append(f"message_chars={int(message_chars_value)}")
    if input_count is not None:
        fields.append(f"input_count={int(input_count)}")
    if input_chars is not None:
        fields.append(f"input_chars={int(input_chars)}")
    if timeout_seconds is not None:
        fields.append(f"timeout_seconds={timeout_seconds}")
    if key_present is not None:
        fields.append(f"key_present={bool(key_present)}")
    logger.info(" ".join(fields))
    return started_at


def log_model_call_success(
    logger: Any,
    *,
    service: str = "patent",
    component: str,
    model: str,
    endpoint: str,
    started_at: float,
    auth_mode: str | None = None,
    status_code: Any = None,
    stream: bool | None = None,
    answer_chars: int | None = None,
    chunk_count: int | None = None,
    embedding_count: int | None = None,
    embedding_dim: int | None = None,
    fallback: bool | None = None,
) -> None:
    fields = [
        "model_call success",
        f"service={service}",
        f"component={component}",
        f"model={model}",
        f"endpoint={endpoint}",
        f"auth_mode={auth_mode_label(auth_mode)}",
    ]
    if status_code is not None:
        fields.append(f"status_code={status_code}")
    if stream is not None:
        fields.append(f"stream={bool_text(stream)}")
    if answer_chars is not None:
        fields.append(f"answer_chars={int(answer_chars)}")
    if chunk_count is not None:
        fields.append(f"chunk_count={int(chunk_count)}")
    if embedding_count is not None:
        fields.append(f"embedding_count={int(embedding_count)}")
    if embedding_dim is not None:
        fields.append(f"embedding_dim={int(embedding_dim)}")
    if fallback is not None:
        fields.append(f"fallback={bool_text(fallback)}")
    fields.append(f"elapsed_ms={(time.perf_counter() - started_at) * 1000.0:.2f}")
    logger.info(" ".join(fields))


def log_model_call_failed(
    logger: Any,
    *,
    service: str = "patent",
    component: str,
    model: str,
    endpoint: str,
    started_at: float,
    exc: Exception,
    auth_mode: str | None = None,
    status_code: Any = None,
    stream: bool | None = None,
    fallback: bool | None = None,
    reason: str | None = None,
) -> None:
    fields = [
        "model_call failed",
        f"service={service}",
        f"component={component}",
        f"model={model}",
        f"endpoint={endpoint}",
        f"auth_mode={auth_mode_label(auth_mode)}",
    ]
    if status_code is not None:
        fields.append(f"status_code={status_code}")
    if stream is not None:
        fields.append(f"stream={bool_text(stream)}")
    if fallback is not None:
        fields.append(f"fallback={bool_text(fallback)}")
    if reason:
        fields.append(f"reason={reason}")
    fields.extend(
        [
            f"elapsed_ms={(time.perf_counter() - started_at) * 1000.0:.2f}",
            f"error_type={type(exc).__name__}",
        ]
    )
    logger.warning(" ".join(fields))
