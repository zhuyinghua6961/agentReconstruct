from __future__ import annotations

import logging
import os
import re
from typing import Any


_LOGGER = logging.getLogger("patent.query_expander")

EXPANSION_PROMPT = """你是一个专利检索助手。任务：对给定的检索查询进行扩展，补充中英文同义词和领域术语变体，以提升专利向量检索召回率。

规则：
1. 保留原有关键词
2. 为专业术语补充英文/中文对应词（如 过充→overcharge、钛掺杂→Ti doping）
3. 输出为空格分隔的关键词列表，40-80字
4. 不添加与检索无关的内容

【重要】压实密度与振实密度不可混淆：
- 压实密度（电极片辊压后密度）↔ compaction density，仅扩展此对应，不要添加 tap density
- 振实密度（粉末振实后密度）↔ tap density，仅扩展此对应，不要添加 compaction density
二者为不同概念，扩展时禁止互相替代或同时添加。

输入：{query}
输出（仅输出扩展后的查询，不要解释）："""


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        raw = str(os.getenv(name, "") or "").strip()
        if raw:
            return raw
    return default


class QueryExpander:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else _first_env("DASHSCOPE_API_KEY", "OPENAI_API_KEY")
        self.base_url = base_url if base_url is not None else _first_env("DASHSCOPE_BASE_URL", "OPENAI_BASE_URL")
        self.model = model if model is not None else _first_env("QUERY_EXPANSION_MODEL", default="qwen3-8b")
        self._client = client

    def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if not self.api_key:
            return None
        try:
            from openai import OpenAI
        except ImportError:
            _LOGGER.warning("openai package unavailable; patent query expansion disabled")
            return None
        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url or None)
        return self._client

    @staticmethod
    def _filter_density_confusion(original_query: str, expanded: str) -> str:
        has_compaction = "压实密度" in original_query or "compaction" in original_query.lower()
        has_tap = "振实密度" in original_query or "tap" in original_query.lower()
        if has_compaction and not has_tap:
            expanded = re.sub(r"\btap\s*density\b", "", expanded, flags=re.IGNORECASE)
            expanded = re.sub(r"振实密度", "", expanded)
        elif has_tap and not has_compaction:
            expanded = re.sub(r"\bcompaction\s*density\b", "", expanded, flags=re.IGNORECASE)
            expanded = re.sub(r"压实密度", "", expanded)
        normalized = " ".join(expanded.split())
        return normalized or original_query

    def expand(self, query: str) -> str:
        normalized_query = " ".join(str(query or "").split()).strip()
        if not normalized_query:
            return query
        client = self._get_client()
        if client is None:
            return normalized_query
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个专利检索助手，只输出扩展后的查询，不要任何解释。"
                            "压实密度仅对应compaction density，振实密度仅对应tap density，二者不可混淆。"
                        ),
                    },
                    {"role": "user", "content": EXPANSION_PROMPT.format(query=normalized_query)},
                ],
                temperature=0.2,
                max_tokens=100,
                extra_body={"enable_thinking": False},
            )
            expanded = str(response.choices[0].message.content or "").strip()
            if expanded and len(expanded) > 5:
                return self._filter_density_confusion(normalized_query, expanded)
        except Exception:
            _LOGGER.warning("patent query expansion failed; using original query", exc_info=True)
        return normalized_query
