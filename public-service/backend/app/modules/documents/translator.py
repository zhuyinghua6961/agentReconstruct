#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smart translator for documents."""

from __future__ import annotations

import os
from typing import Any

from .translation_cache_impl import TranslationCache

DEFAULT_LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return default


class SmartTranslator:
    def __init__(
        self,
        openai_client_cls: Any,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or _first_env("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")
        self.base_url = base_url or _first_env(
            "LLM_BASE_URL",
            "OPENAI_BASE_URL",
            "DASHSCOPE_BASE_URL",
            default=DEFAULT_LLM_BASE_URL,
        )
        self.model = model or _first_env("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="deepseek-v3.1")

        if not self.api_key:
            self.client = None
        else:
            self.client = openai_client_cls(api_key=self.api_key, base_url=self.base_url)

        self.cache = TranslationCache()

    @property
    def enabled(self) -> bool:
        return self.client is not None

    @property
    def provider(self) -> str:
        return "openai-compatible"

    def translate(self, text, show_progress: bool = True) -> str:
        _ = show_progress
        if isinstance(text, list):
            text = " ".join(str(item) for item in text)
        elif text is None:
            text = ""
        elif not isinstance(text, str):
            text = str(text)

        if not text or not text.strip():
            return ""

        cached = self.cache.get(text)
        if cached:
            return cached

        if not self.client:
            return "❌ 翻译功能未启用（缺少API密钥）"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是专业的学术论文翻译专家。请将英文文献翻译成准确、流畅的中文，保持专业术语的准确性。",
                    },
                    {
                        "role": "user",
                        "content": (
                            f"请将以下英文翻译成中文：\n\n{text}\n\n要求：\n"
                            "1. 只输出翻译结果，不要添加任何说明、注释或解释\n"
                            "2. 不要输出关于翻译规范、翻译特点的说明\n"
                            "3. 保持专业术语准确，译文通顺"
                        ),
                    },
                ],
                temperature=0.3,
            )

            translation = str(response.choices[0].message.content or "").strip()
            self.cache.set(text, translation)
            return translation
        except Exception as exc:
            return f"❌ 翻译失败: {str(exc)}"
