#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smart translator for documents."""

from __future__ import annotations

import os
import logging
from typing import Any

from .llm_thinking import LLM_STAGE_TRANSLATION, local_sdk_api_key, merge_extra_body, resolve_thinking_controls
from .translation_cache_impl import TranslationCache
from app.modules.system.upstream_auth_logging import (
    log_upstream_auth_failure,
    log_upstream_auth_success_once,
)

DEFAULT_LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
logger = logging.getLogger(__name__)

DOCUMENT_SYSTEM_PROMPT = (
    "你是专业的学术论文翻译与 Markdown 排版专家。"
    "请将英文学术文献翻译成准确、流畅的中文，并输出结构清晰的 GitHub 风格 Markdown。"
)

SNIPPET_SYSTEM_PROMPT = (
    "你是专业的学术论文翻译专家。请将英文文献翻译成准确、流畅的中文，保持专业术语的准确性。"
)


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return default


def _normalize_profile(profile: str | None) -> str:
    normalized = str(profile or "snippet").strip().lower()
    return normalized if normalized in {"snippet", "document"} else "snippet"


def _build_document_user_prompt(
    text: str,
    *,
    chunk_index: int | None = None,
    chunk_count: int | None = None,
) -> str:
    chunk_context = ""
    if chunk_index is not None and chunk_count is not None and chunk_count > 0:
        chunk_context = (
            f"\n\n分块上下文：这是全文第 {chunk_index + 1}/{chunk_count} 段。"
            "不要输出整篇总标题；若本段开头会重复上一段已出现的同级章节标题，请省略重复标题并直接续写正文。"
        )

    return (
        f"请将以下学术文献片段翻译成中文，并输出 Markdown：\n\n{text}\n\n"
        "输出要求：\n"
        "1. 只输出译文 Markdown，不要添加说明、注释、译后记或翻译过程\n"
        "2. 识别章节并转为标题：Abstract→## 摘要，Introduction→## 引言，Methods/Methodology→## 方法，"
        "Results→## 结果，Discussion→## 讨论，Conclusion/Conclusions→## 结论，References→## 参考文献\n"
        "3. 段落之间保留一个空行；合并 PDF 断行造成的破碎句子\n"
        "4. 列表使用 `-` 或 `1.`；简单表格尽量使用 Markdown 表格\n"
        "5. 不要输出页眉、页脚、期刊名重复行、单独页码行\n"
        "6. 遇到 `[[PAGE:N]]` 占位符时，可删除或转为 `## 第 N 页`，不要保留英文页标记\n"
        "7. 遇到参考文献条目列表时，只保留 `## 参考文献` 标题，不要逐条翻译参考文献\n"
        "8. 保持专业术语准确，数字、单位、化学式、缩写尽量保留可识别形式"
        f"{chunk_context}"
    )


def _build_snippet_user_prompt(text: str) -> str:
    return (
        f"请将以下英文翻译成中文：\n\n{text}\n\n要求：\n"
        "1. 只输出翻译结果，不要添加任何说明、注释或解释\n"
        "2. 不要输出关于翻译规范、翻译特点的说明\n"
        "3. 保持专业术语准确，译文通顺"
    )


class SmartTranslator:
    def __init__(
        self,
        openai_client_cls: Any,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or _first_env("LLM_API_KEY")
        self.base_url = base_url or _first_env("LLM_BASE_URL", default=DEFAULT_LLM_BASE_URL)
        self.model = model or _first_env("LLM_MODEL", default="deepseek-v3.1")

        if not self.base_url or not self.model:
            self.client = None
        else:
            self.client = openai_client_cls(api_key=local_sdk_api_key(self.api_key), base_url=self.base_url)

        self.cache = TranslationCache()

    @property
    def enabled(self) -> bool:
        return self.client is not None

    @property
    def provider(self) -> str:
        return "openai-compatible"

    def translate(
        self,
        text,
        show_progress: bool = True,
        *,
        profile: str = "snippet",
        chunk_index: int | None = None,
        chunk_count: int | None = None,
    ) -> str:
        _ = show_progress
        if isinstance(text, list):
            text = " ".join(str(item) for item in text)
        elif text is None:
            text = ""
        elif not isinstance(text, str):
            text = str(text)

        if not text or not text.strip():
            return ""

        normalized_profile = _normalize_profile(profile)
        cached = self.cache.get(text, profile=normalized_profile)
        if cached:
            return cached

        if not self.client:
            return "❌ 翻译功能未启用（缺少模型连接配置）"

        try:
            max_output_tokens = min(8192, max(2048, len(text) * 2))
            controls = resolve_thinking_controls(
                stage=LLM_STAGE_TRANSLATION,
                max_tokens=max_output_tokens,
                stream=False,
                thinking_enabled=False,
            )
            if normalized_profile == "document":
                system_prompt = DOCUMENT_SYSTEM_PROMPT
                user_prompt = _build_document_user_prompt(
                    text,
                    chunk_index=chunk_index,
                    chunk_count=chunk_count,
                )
            else:
                system_prompt = SNIPPET_SYSTEM_PROMPT
                user_prompt = _build_snippet_user_prompt(text)

            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": max_output_tokens,
            }
            extra_body = merge_extra_body(None, controls)
            if extra_body:
                kwargs["extra_body"] = extra_body
            try:
                response = self.client.chat.completions.create(
                    **kwargs,
                )
            except Exception as exc:
                log_upstream_auth_failure(
                    logger=logger,
                    service="public-service",
                    endpoint="chat",
                    model=self.model,
                    base_url=self.base_url,
                    api_key=self.api_key,
                    exc=exc,
                )
                raise
            log_upstream_auth_success_once(
                logger=logger,
                service="public-service",
                endpoint="chat",
                model=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
            )

            translation = str(response.choices[0].message.content or "").strip()
            self.cache.set(text, translation, profile=normalized_profile)
            return translation
        except Exception as exc:
            return f"❌ 翻译失败: {str(exc)}"
