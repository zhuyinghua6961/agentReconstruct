#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Initialization helpers for PDF QA LLM dependencies."""

from __future__ import annotations

import os
from typing import Any

from app.integrations.llm import SharedHttpPoolConfig, build_chat_adapter


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        value = int(default)
    return value


def _build_openai_compatible_llm(
    *,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    logger: Any,
    transport_config: SharedHttpPoolConfig,
    http_client: Any | None,
) -> Any:
    return build_chat_adapter(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        logger=logger,
        connect_timeout_seconds=transport_config.connect_timeout_seconds,
        read_timeout_seconds=transport_config.read_timeout_seconds,
        stream_read_timeout_seconds=transport_config.stream_read_timeout_seconds,
        write_timeout_seconds=transport_config.write_timeout_seconds,
        pool_timeout_seconds=transport_config.pool_timeout_seconds,
        keepalive_expiry_seconds=transport_config.keepalive_expiry_seconds,
        max_connections=transport_config.max_connections,
        max_keepalive_connections=transport_config.max_keepalive_connections,
        http_client=http_client,
    )


def init_llm(logger, *, http_client: Any | None = None) -> Any:
    """Initialize PDF QA fallback LLM with the unified OpenAI-compatible HTTP transport."""
    dashscope_api_key = os.getenv("LLM_API_KEY")
    dashscope_base_url = os.getenv("LLM_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = os.getenv("LLM_MODEL") or "deepseek-v3.1"
    temperature = _env_float("PDF_QA_TEMPERATURE", 0.5)
    top_p = _env_float("PDF_QA_TOP_P", 0.95)
    max_tokens = max(1, _env_int("PDF_QA_MAX_TOKENS", 2500))
    transport_config = SharedHttpPoolConfig.from_env()

    if dashscope_api_key:
        llm = _build_openai_compatible_llm(
            api_key=dashscope_api_key,
            base_url=dashscope_base_url,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            logger=logger,
            transport_config=transport_config,
            http_client=http_client,
        )
        logger.info("LLM初始化成功，使用统一OpenAI兼容HTTP模型: %s", model)
        return llm

    llm = _build_openai_compatible_llm(
        api_key="",
        base_url=dashscope_base_url,
        model=model,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        logger=logger,
        transport_config=transport_config,
        http_client=http_client,
    )
    logger.info("LLM初始化成功，使用无鉴权OpenAI兼容HTTP模型: %s", model)
    return llm
