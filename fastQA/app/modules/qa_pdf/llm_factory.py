#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Initialization helpers for PDF QA LLM dependencies."""

from __future__ import annotations

import os
from typing import Any

from app.integrations.llm import build_chat_adapter, should_use_dashscope_native


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


def init_llm(logger) -> Any:
    """Initialize PDF QA fallback LLM, preferring DashScope native/OpenAI-compatible transport."""
    dashscope_api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
    dashscope_base_url = os.getenv(
        "DASHSCOPE_BASE_URL",
        os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )
    model = os.getenv(
        "PDF_QA_MODEL",
        os.getenv("DASHSCOPE_MODEL", os.getenv("OPENAI_MODEL", "deepseek-v3.1")),
    )
    temperature = _env_float("PDF_QA_TEMPERATURE", 0.5)
    top_p = _env_float("PDF_QA_TOP_P", 0.95)
    max_tokens = max(1, _env_int("PDF_QA_MAX_TOKENS", 2500))
    timeout = max(1.0, _env_float("PDF_QA_TIMEOUT_SECONDS", 60.0))
    max_retries = max(0, _env_int("PDF_QA_MAX_RETRIES", 3))

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
            )
            logger.info("LLM初始化成功，使用OpenAI兼容/百炼协议模型: %s", model)
            return llm

        try:
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(
                model=model,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                timeout=timeout,
                max_retries=max_retries,
                api_key=dashscope_api_key,
                base_url=dashscope_base_url,
            )
            logger.info("LLM初始化成功，使用LangChain兼容模型: %s", model)
            return llm
        except Exception:
            llm = build_chat_adapter(
                api_key=dashscope_api_key,
                base_url=dashscope_base_url,
                model=model,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                logger=logger,
            )
            logger.info("LLM初始化成功，回退OpenAI兼容适配器: %s", model)
            return llm

    raise ValueError("请设置DASHSCOPE_API_KEY或OPENAI_API_KEY环境变量")
