from __future__ import annotations

import logging
from typing import Any

import config

_LOGGER = logging.getLogger(__name__)
_LLM_HTTP_SETTINGS_LOGGED = False


def llm_http_settings_snapshot() -> dict[str, float | int]:
    http = config.LLM_HTTP_SETTINGS
    return {
        "connect_timeout_seconds": float(http.connect_timeout_seconds),
        "read_timeout_seconds": float(http.read_timeout_seconds),
        "stream_read_timeout_seconds": float(http.stream_read_timeout_seconds),
        "write_timeout_seconds": float(http.write_timeout_seconds),
        "pool_timeout_seconds": float(http.pool_timeout_seconds),
        "ask_timeout_seconds": int(config.ASK_TIMEOUT_SECONDS),
        "gunicorn_timeout_seconds": int(config.GUNICORN_TIMEOUT),
    }


def log_llm_http_runtime_settings(*, logger: logging.Logger | None = None, force: bool = False) -> dict[str, float | int]:
    global _LLM_HTTP_SETTINGS_LOGGED
    active_logger = logger or _LOGGER
    snapshot = llm_http_settings_snapshot()
    if _LLM_HTTP_SETTINGS_LOGGED and not force:
        return snapshot
    _LLM_HTTP_SETTINGS_LOGGED = True
    active_logger.info(
        "llm http runtime settings service=highThinkingQA "
        "connect_timeout_seconds=%s read_timeout_seconds=%s stream_read_timeout_seconds=%s "
        "write_timeout_seconds=%s pool_timeout_seconds=%s ask_timeout_seconds=%s gunicorn_timeout_seconds=%s",
        snapshot["connect_timeout_seconds"],
        snapshot["read_timeout_seconds"],
        snapshot["stream_read_timeout_seconds"],
        snapshot["write_timeout_seconds"],
        snapshot["pool_timeout_seconds"],
        snapshot["ask_timeout_seconds"],
        snapshot["gunicorn_timeout_seconds"],
    )
    return snapshot


def resolve_request_timeout_seconds(*, timeout: Any | None, stream: bool) -> float:
    if timeout is not None:
        return float(timeout)
    if stream:
        return float(config.LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS)
    return float(config.LLM_HTTP_READ_TIMEOUT_SECONDS)


def log_llm_request_prepared(
    *,
    stage: str,
    stream: bool,
    model: str,
    timeout_seconds: float,
    message_count: int | None = None,
    message_chars: int | None = None,
    prompt_chars: int | None = None,
    logger: logging.Logger | None = None,
) -> None:
    active_logger = logger or _LOGGER
    fields = [
        "llm request prepared service=highThinkingQA",
        f"stage={stage}",
        f"stream={'true' if stream else 'false'}",
        f"model={model}",
        f"timeout_seconds={timeout_seconds}",
    ]
    if message_count is not None:
        fields.append(f"message_count={message_count}")
    if message_chars is not None:
        fields.append(f"message_chars={message_chars}")
    if prompt_chars is not None:
        fields.append(f"prompt_chars={prompt_chars}")
    active_logger.info(" ".join(fields))
