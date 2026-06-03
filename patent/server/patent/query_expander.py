from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from server.patent.thinking import LLM_STAGE_CONTROL, auth_headers, merge_extra_body, resolve_thinking_controls


_LOGGER = logging.getLogger("patent.query_expander")
DEFAULT_LLM_COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

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


def _chat_completions_url(base_url: str | None) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        value = DEFAULT_LLM_COMPATIBLE_BASE_URL
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    if not value.endswith("/v1"):
        value = value.rstrip("/") + "/v1"
    return value.rstrip("/") + "/chat/completions"


def _timeout_seconds() -> float:
    raw = _first_env("QUERY_EXPANSION_TIMEOUT_SECONDS", "INTENT_MODEL_TIMEOUT_SECONDS", default="30")
    try:
        return max(float(raw), 1.0)
    except Exception:
        return 30.0


class QueryExpander:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        http_client: Any | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else _first_env("LLM_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")
        self.base_url = base_url if base_url is not None else _first_env("LLM_BASE_URL", "DASHSCOPE_BASE_URL", "OPENAI_BASE_URL")
        self.model = model if model is not None else _first_env("QUERY_EXPANSION_MODEL", default="qwen3-8b")
        self._client = client
        self._http_client = http_client
        self._owns_http_client = http_client is None

    def _get_client(self) -> Any | None:
        return self._client

    def _get_http_client(self) -> Any:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=_timeout_seconds(), http2=False)
        return self._http_client

    def close(self) -> None:
        if not self._owns_http_client:
            return
        close = getattr(self._http_client, "close", None)
        if callable(close):
            close()

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
        controls = resolve_thinking_controls(
            stage=LLM_STAGE_CONTROL,
            max_tokens=100,
            stream=False,
            thinking_enabled=False,
        )
        extra_body = merge_extra_body(None, controls)
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一个专利检索助手，只输出扩展后的查询，不要任何解释。"
                        "压实密度仅对应compaction density，振实密度仅对应tap density，二者不可混淆。"
                    ),
                },
                {"role": "user", "content": EXPANSION_PROMPT.format(query=normalized_query)},
            ]
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": controls.max_tokens,
                "stream": False,
            }
            if extra_body:
                payload.update(extra_body)
            if client is not None:
                kwargs = dict(payload)
                if extra_body:
                    for key in extra_body:
                        kwargs.pop(key, None)
                    kwargs["extra_body"] = extra_body
                response = client.chat.completions.create(**kwargs)
                expanded = str(response.choices[0].message.content or "").strip()
            else:
                response = self._get_http_client().post(
                    _chat_completions_url(self.base_url),
                    headers=auth_headers(self.api_key, accept="application/json"),
                    json=payload,
                    timeout=_timeout_seconds(),
                )
                response.raise_for_status()
                body = response.json()
                expanded = str((body.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
            if expanded and len(expanded) > 5:
                return self._filter_density_confusion(normalized_query, expanded)
        except Exception:
            _LOGGER.warning("patent query expansion failed; using original query", exc_info=True)
        return normalized_query
