from __future__ import annotations

import logging
import os

from app.integrations.llm import build_chat_completions_client

logger = logging.getLogger(__name__)

_EXPANSION_PROMPT = """你是一个学术文献检索助手。任务：对给定的检索查询进行扩展，补充中英文同义词和领域术语变体，以提升文献检索召回率。

规则：
1. 保留原有关键词
2. 为专业术语补充英文/中文对应词
3. 输出为空格分隔的关键词列表，40-80字
4. 不添加与检索无关的内容

输入：{query}
输出（仅输出扩展后的查询，不要解释）："""


class QueryExpander:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("DASHSCOPE_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        self.model = model or os.getenv("QUERY_EXPANSION_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL") or "qwen-plus"
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            return None
        self._client = build_chat_completions_client(api_key=self.api_key, base_url=self.base_url, logger=logger)
        return self._client

    def expand(self, query: str) -> str:
        text = str(query or "").strip()
        if not text:
            return text
        client = self._get_client()
        if client is None:
            return text
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个学术文献检索助手，只输出扩展后的查询，不要任何解释。"},
                    {"role": "user", "content": _EXPANSION_PROMPT.format(query=text)},
                ],
                temperature=0.2,
                max_tokens=120,
                extra_body={"enable_thinking": False},
            )
            expanded = str(response.choices[0].message.content or "").strip()
            if len(expanded) >= 6:
                logger.info("stage2 query expanded original=%s expanded=%s", text[:80], expanded[:80])
                return expanded
        except Exception as exc:
            logger.warning("stage2 query expansion failed, fallback to original query: %s", exc)
        return text
