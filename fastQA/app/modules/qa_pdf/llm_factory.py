#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Initialization helpers for PDF QA LLM dependencies."""

from __future__ import annotations

import os
from typing import Any

from app.integrations.llm import SharedHttpPoolConfig, build_chat_adapter, should_use_dashscope_native


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


def _build_transport_timeout(*, httpx_module: Any, transport_config: SharedHttpPoolConfig, read_timeout_seconds: float) -> Any:
    return httpx_module.Timeout(
        connect=transport_config.connect_timeout_seconds,
        read=read_timeout_seconds,
        write=transport_config.write_timeout_seconds,
        pool=transport_config.pool_timeout_seconds,
    )


def _build_private_http_client(*, httpx_module: Any, transport_config: SharedHttpPoolConfig, read_timeout_seconds: float) -> Any:
    timeout = _build_transport_timeout(
        httpx_module=httpx_module,
        transport_config=transport_config,
        read_timeout_seconds=read_timeout_seconds,
    )
    limits = httpx_module.Limits(
        max_connections=transport_config.max_connections,
        max_keepalive_connections=transport_config.max_keepalive_connections,
        keepalive_expiry=transport_config.keepalive_expiry_seconds,
    )
    return httpx_module.Client(timeout=timeout, limits=limits, http2=False)


def init_llm(logger, *, http_client: Any | None = None) -> Any:
    """Initialize PDF QA fallback LLM, preferring DashScope native/OpenAI-compatible transport."""
    dashscope_api_key = os.getenv("LLM_API_KEY")
    dashscope_base_url = os.getenv("LLM_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = os.getenv("LLM_MODEL") or "deepseek-v3.1"
    temperature = _env_float("PDF_QA_TEMPERATURE", 0.5)
    top_p = _env_float("PDF_QA_TOP_P", 0.95)
    max_tokens = max(1, _env_int("PDF_QA_MAX_TOKENS", 2500))
    max_retries = max(0, _env_int("PDF_QA_MAX_RETRIES", 3))
    transport_config = SharedHttpPoolConfig.from_env()

    if dashscope_api_key:
        if should_use_dashscope_native(api_key=dashscope_api_key, base_url=dashscope_base_url):
            llm = build_chat_adapter(
                api_key=dashscope_api_key,
                base_url=dashscope_base_url,
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
            logger.info("LLM初始化成功，使用OpenAI兼容/百炼协议模型: %s", model)
            return llm

        private_langchain_http_client = None
        try:
            from langchain_openai import ChatOpenAI
            import httpx

            langchain_timeout = _build_transport_timeout(
                httpx_module=httpx,
                transport_config=transport_config,
                read_timeout_seconds=transport_config.stream_read_timeout_seconds,
            )
            langchain_http_client = http_client
            if langchain_http_client is None:
                private_langchain_http_client = _build_private_http_client(
                    httpx_module=httpx,
                    transport_config=transport_config,
                    read_timeout_seconds=transport_config.stream_read_timeout_seconds,
                )
                langchain_http_client = private_langchain_http_client

            llm = ChatOpenAI(
                model=model,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                timeout=langchain_timeout,
                max_retries=max_retries,
                api_key=dashscope_api_key,
                base_url=dashscope_base_url,
                http_client=langchain_http_client,
            )
            logger.info("LLM初始化成功，使用LangChain兼容模型: %s", model)
            return llm
        except Exception:
            close_fn = getattr(private_langchain_http_client, "close", None)
            if callable(close_fn):
                close_fn()
            llm = build_chat_adapter(
                api_key=dashscope_api_key,
                base_url=dashscope_base_url,
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
            logger.info("LLM初始化成功，回退OpenAI兼容适配器: %s", model)
            return llm

    llm = build_chat_adapter(
        api_key="",
        base_url=dashscope_base_url,
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
    logger.info("LLM初始化成功，使用本地OpenAI兼容模型: %s", model)
    return llm
