from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import httpx

from server.patent.pdf_contract import is_summary_question
from server.patent.summary_formatting import LITERATURE_SUMMARY_NOTE
from server.patent.upstream_transport import (
    build_patent_request_timeout,
    describe_patent_transport,
    record_patent_dispatch_error,
    record_patent_dispatch_success,
)


_HYBRID_SYNTHESIS_SYSTEM_MESSAGE = (
    "You are a patent hybrid synthesis assistant. Use file evidence first and treat KB only as supporting validation."
)
HYBRID_SYNTHESIS_PROMPT_VERSION = "patent-hybrid-synthesis-v1"
_LOGGER = logging.getLogger("patent.hybrid_synthesis")


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return str(default or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name) or default).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name) or default).strip())
    except Exception:
        return float(default)


def _clean_line(raw_line: str) -> str:
    line = str(raw_line or "").strip()
    if not line:
        return ""
    if line.startswith("匹配工作表:"):
        return ""
    if line.startswith("执行操作:"):
        return ""
    if "source_scope=" in line:
        return ""
    if line.startswith("真实 PDF 总结："):
        line = line.removeprefix("真实 PDF 总结：").strip()
    if line.startswith("真实表格总结："):
        line = line.removeprefix("真实表格总结：").strip()
    if line.startswith("知识库补充："):
        line = line.removeprefix("知识库补充：").strip()
    return line


def _sanitize_context(value: object) -> str:
    lines = []
    for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = _clean_line(raw_line)
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def _normalize_sources(value: list[str] | tuple[str, ...] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for item in list(value or []):
        source = str(item or "").strip()
        if not source or source in seen:
            continue
        seen.add(source)
        normalized.append(source)
    return normalized


def build_patent_hybrid_synthesis_contract(
    *,
    question: str,
    source_scope: str,
    pdf_answer: str = "",
    tabular_answer: str = "",
    kb_answer: str = "",
    pdf_evidence_context: str = "",
    table_execution_context: str = "",
    include_kb: bool = False,
    kb_evidence_context: str = "",
    kb_reference_instruction: str = "",
    pdf_synthesis_context: str = "",
    table_synthesis_context: str = "",
    kb_synthesis_context: str = "",
    available_sources: list[str] | None = None,
    source_answer_modes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inferred_sources = list(available_sources or [])
    if not inferred_sources:
        if str(pdf_answer or "").strip() or str(pdf_synthesis_context or "").strip() or str(pdf_evidence_context or "").strip():
            inferred_sources.append("pdf")
        if str(tabular_answer or "").strip() or str(table_synthesis_context or "").strip() or str(table_execution_context or "").strip():
            inferred_sources.append("table")
        if bool(include_kb) or str(kb_answer or "").strip() or str(kb_synthesis_context or "").strip() or str(kb_evidence_context or "").strip():
            inferred_sources.append("kb")
    return {
        "question": str(question or "").strip(),
        "source_scope": str(source_scope or "").strip(),
        "pdf_answer": str(pdf_answer or "").strip(),
        "tabular_answer": str(tabular_answer or "").strip(),
        "kb_answer": str(kb_answer or "").strip(),
        "pdf_evidence_context": str(pdf_evidence_context or "").strip(),
        "table_execution_context": str(table_execution_context or "").strip(),
        "kb_evidence_context": str(kb_evidence_context or "").strip(),
        "kb_reference_instruction": str(kb_reference_instruction or "").strip(),
        "pdf_synthesis_context": _sanitize_context(pdf_synthesis_context),
        "table_synthesis_context": _sanitize_context(table_synthesis_context),
        "kb_synthesis_context": _sanitize_context(kb_synthesis_context),
        "include_kb": bool(include_kb),
        "file_precedence": "file_over_kb",
        "available_sources": _normalize_sources(inferred_sources),
        "source_answer_modes": {
            str(key): str(value or "").strip()
            for key, value in dict(source_answer_modes or {}).items()
            if str(key).strip()
        },
        "synthesis_prompt_version": HYBRID_SYNTHESIS_PROMPT_VERSION,
    }


def build_patent_hybrid_synthesis_prompt(*, synthesis_contract: dict[str, Any]) -> str:
    contract = dict(synthesis_contract or {})
    question = str(contract.get("question") or "").strip()
    summary_mode = is_summary_question(question)
    available_sources = ", ".join(_normalize_sources(contract.get("available_sources") or [])) or "pdf, table"
    answer_modes = ", ".join(
        f"{key}:{value}"
        for key, value in sorted(dict(contract.get("source_answer_modes") or {}).items(), key=lambda item: str(item[0]))
        if str(value or "").strip()
    )
    sections = [
        "你是一位 patent 文件统一合成助手。",
        "文件证据优先：PDF 与表格中的文件证据优先于知识库结论。",
        "知识库只能作为补充验证或背景说明，不能改写成 PDF 或表格原文事实。",
        "如果文件证据与知识库冲突，必须明确指出冲突，并坚持文件证据优先。",
        "不要输出表格执行标记、内部 source 标签或测试中间壳子。",
        f"当前可用来源: {available_sources}",
    ]
    if answer_modes:
        sections.append(f"来源答案模式: {answer_modes}")
    if str(contract.get("kb_reference_instruction") or "").strip():
        sections.append(str(contract.get("kb_reference_instruction") or "").strip())
    sections.extend(
        [
            "",
            "用户问题:",
            question,
            "",
            "PDF 子答案:",
            str(contract.get("pdf_answer") or "").strip(),
            "",
            "PDF 合成证据:",
            _sanitize_context(contract.get("pdf_synthesis_context") or contract.get("pdf_evidence_context") or ""),
            "",
            "表格子答案:",
            str(contract.get("tabular_answer") or "").strip(),
            "",
            "表格合成证据:",
            _sanitize_context(contract.get("table_synthesis_context") or contract.get("table_execution_context") or ""),
            "",
            "知识库子答案:",
            str(contract.get("kb_answer") or "").strip(),
            "",
            "知识库补充证据:",
            _sanitize_context(contract.get("kb_synthesis_context") or contract.get("kb_evidence_context") or ""),
            "",
        ]
    )
    if summary_mode:
        sections.extend(
            [
                "请输出五段文献总结结构：",
                "## 研究目的和背景",
                "## 研究方法/实验设计",
                "## 主要发现和结果",
                "## 结论和意义",
                "## 局限性",
                LITERATURE_SUMMARY_NOTE,
                "- 文件证据优先进入背景、方法、结果和结论；知识库只能补充验证，不能替代文件结论。",
                "- 若某章节证据不足，明确写出证据不足，不要补写通用知识。",
                "- 表格证据优先进入主要发现和结果，除非表格本身明确提供方法信息。",
            ]
        )
    else:
        sections.extend(
            [
                "请按以下 Markdown 结构回答：",
                "## 结论",
                "## 证据",
                "## 对比",
                "## 限制",
                "- 先直接回答用户问题。",
                "- 证据需按来源覆盖 PDF、表格和知识库，并明确知识库只是补充验证。",
                "- 对比需要指出来源之间的一致点、差异点或冲突点。",
                "- 限制必须说明证据边界、缺失信息或无法确定的部分。",
            ]
        )
    prompt = "\n".join(part for part in sections if part is not None).strip()
    prompt = re.sub(r"\n{3,}", "\n\n", prompt)
    return prompt


class PatentHybridSynthesisClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        top_p: float = 0.95,
        max_tokens: int = 3000,
        http_client: Any | None = None,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self._base_url = str(base_url or "").strip()
        self._model = str(model or "").strip()
        self._timeout_seconds = float(timeout_seconds)
        self._top_p = float(top_p)
        self._max_tokens = max(1, int(max_tokens))
        self._owns_http_client = http_client is None
        self._client = http_client or httpx.Client(timeout=self._timeout_seconds)
        transport = describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client)
        _LOGGER.info(
            "patent hybrid synthesis client initialized model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s",
            self._model,
            self._base_url,
            self._timeout_seconds,
            transport.get("client_owner"),
            transport.get("shared_client_id"),
        )

    @classmethod
    def from_env(cls, *, http_client: Any | None = None) -> "PatentHybridSynthesisClient | None":
        api_key = _first_env("LLM_API_KEY")
        base_url = _first_env("LLM_BASE_URL")
        model = _first_env("LLM_MODEL")
        if not api_key or not base_url or not model:
            return None
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=_env_float("LLM_READ_TIMEOUT_SECONDS", 30.0),
            top_p=0.95,
            max_tokens=max(1024, _env_int("PATENT_HYBRID_MAX_TOKENS", 3000)),
            http_client=http_client,
        )

    def runtime_signature(self) -> dict[str, Any]:
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "top_p": self._top_p,
            "timeout_seconds": self._timeout_seconds,
            "prompt_version": HYBRID_SYNTHESIS_PROMPT_VERSION,
        }

    def answer(self, *, synthesis_contract: dict[str, Any]) -> str:
        prompt = build_patent_hybrid_synthesis_prompt(synthesis_contract=synthesis_contract)
        transport = describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client)
        _LOGGER.info(
            "patent hybrid synthesis request start model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s source_scope=%s available_sources=%s",
            self._model,
            self._base_url,
            self._timeout_seconds,
            transport.get("client_owner"),
            transport.get("shared_client_id"),
            str(synthesis_contract.get("source_scope") or ""),
            ",".join(_normalize_sources(synthesis_contract.get("available_sources") or [])),
        )
        request_timeout = build_patent_request_timeout(
            http_client=self._client,
            timeout_seconds=self._timeout_seconds,
        )
        dispatch_started = time.perf_counter()
        try:
            response = self._client.post(
                f"{self._base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "temperature": 0.2,
                    "top_p": self._top_p,
                    "max_tokens": self._max_tokens,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": _HYBRID_SYNTHESIS_SYSTEM_MESSAGE},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=request_timeout,
            )
        except Exception as exc:
            record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
            raise
        record_patent_dispatch_success(http_client=self._client, started_at=dispatch_started)
        response.raise_for_status()
        payload = response.json()
        choices = list(payload.get("choices") or [])
        message = dict((choices[0] or {}).get("message") or {}) if choices else {}
        return str(message.get("content") or "").strip()

    def close(self) -> None:
        if self._owns_http_client:
            self._client.close()
