from __future__ import annotations

import csv
import logging
import os
import posixpath
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET

import httpx

from server.patent.pdf_contract import is_summary_question
from server.patent.file_models import PatentFileContract
from server.patent.object_reader import ObjectReader
from server.patent.streaming import emit_text_chunks, iter_text_output
from server.patent.tabular_context import build_compare_tabular_context_bundle, build_tabular_context_bundle
from server.patent.tabular.executor import execute_compare_plan, execute_tabular_plan
from server.patent.tabular.planner import plan_tabular_query
from server.patent.tabular.renderer import has_usable_tabular_result
from server.patent.tabular.schema_profiler import profile_workbook
from server.patent.tabular.workbook_loader import load_workbook_cached, load_workbook_from_execution_file
from server.patent.upstream_transport import (
    build_patent_request_timeout,
    describe_patent_transport,
    record_patent_dispatch_error,
    record_patent_dispatch_success,
)
from server.services.mode_profiles import get_patent_mode_profile

_XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_LOGGER = logging.getLogger("patent.tabular_service")
_TABULAR_QA_SYSTEM_MESSAGE = "You are a patent table QA assistant. Answer strictly from the provided table evidence."
_PATENT_TABULAR_PROMPT_VERSION = "patent-tabular-answer-v1"


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


def _collapse_whitespace(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _truncate(value: object, limit: int) -> str:
    text = _collapse_whitespace(value)
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _find_table_support_points(text: str, *, max_items: int = 3) -> list[str]:
    points: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = _collapse_whitespace(re.sub(r"^[#>\-\*\d\.\)\s]+", "", raw_line))
        if len(line) < 12:
            continue
        if line.startswith("文件:"):
            continue
        if line in points:
            continue
        points.append(_truncate(line, 220))
        if len(points) >= max_items:
            break
    return points


def _has_fastqa_markdown_sections(text: str) -> bool:
    normalized = str(text or "")
    last_end = -1
    for label in ("结论", "证据", "对比", "限制"):
        matched = re.search(rf"(^|\n)\s*(?:#{{1,6}}\s*)?{label}\s*[：:]?", normalized, flags=re.MULTILINE)
        if matched is None or matched.start() <= last_end:
            return False
        last_end = matched.start()
    return True


def _has_literature_summary_sections(text: str) -> bool:
    normalized = str(text or "")
    last_end = -1
    for label in ("研究目的和背景", "研究方法/实验设计", "主要发现和结果", "结论和意义"):
        matched = re.search(rf"(^|\n)\s*(?:#{{1,6}}\s*)?{re.escape(label)}\s*[：:]?", normalized, flags=re.MULTILINE)
        if matched is None or matched.start() <= last_end:
            return False
        last_end = matched.start()
    return True


def _ensure_fastqa_table_summary_structure(
    *,
    answer: str,
    table_text: str,
    include_kb: bool,
    route_hint: str = "tabular_qa",
    source_scope: str = "table",
) -> str:
    normalized_answer = str(answer or "").strip()
    if not normalized_answer:
        return normalized_answer
    if _has_fastqa_markdown_sections(normalized_answer):
        return normalized_answer

    evidence_points = _find_table_support_points(table_text, max_items=3)
    if not evidence_points:
        evidence_points = _find_table_support_points(normalized_answer, max_items=3)
    if not evidence_points:
        evidence_points = ["当前可读表格证据有限，仅能保留主结论。"]
    hybrid_mode = str(route_hint or "tabular_qa").strip().lower() == "hybrid_qa"
    normalized_scope = str(source_scope or "table").strip() or "table"

    sections = [
        "## 结论",
        normalized_answer,
        "",
        "## 证据",
        *[f"- {item}" for item in evidence_points],
        "",
        "## 对比",
        *(
            [
                "- 当前为混合问答中的表格证据子结论；可用于后续与 PDF 或知识库交叉验证，不能单独覆盖其他文件或知识库结论。",
                f"- 当前 source_scope={normalized_scope}；这里仅保留表格结果能够直接支持的对照点。",
            ]
            if hybrid_mode
            else ["- 当前问题主要基于单个表格文件，未提供可直接对照的第二份文件证据。"]
        ),
        "",
        "## 限制",
        *(
            [
                "- 当前结论受表格抽取范围与命中字段限制影响，仍需与其他已选文件或知识库证据综合判断。",
                (
                    "- 知识库若参与，仅可用于验证，不应覆盖当前表格执行结果。"
                    if include_kb
                    else "- 当前未引入知识库补充；若后续纳入其他来源，综合结论可能继续收敛。"
                ),
            ]
            if hybrid_mode
            else [
                "- 当前结论受表格抽取范围与命中字段限制影响，未命中的列不会被补写为确定结论。",
                (
                    "- 知识库若参与，仅可用于验证，不应覆盖当前表格执行结果。"
                    if include_kb
                    else "- 当前未引入知识库补充，本回答不代表跨来源统一结论。"
                ),
            ]
        ),
    ]
    return "\n".join(sections).strip()


def _is_summary_question(question: str) -> bool:
    return is_summary_question(question)


_LITERATURE_SUMMARY_NOTE = "注*：所有总结内容均严格基于文件原文中明确提到的信息，未添加任何通用知识或推测内容。"


def _pick_points(points: list[str], *, start: int, count: int) -> list[str]:
    return [item for item in points[start : start + count] if str(item or "").strip()]


def _build_literature_section(title: str, points: list[str], fallback: str) -> list[str]:
    lines = [f"## {title}"]
    if points:
        lines.extend(f"- {point}" for point in points)
    else:
        lines.append(f"- {fallback}")
    lines.append("")
    return lines


def _point_contains_keyword(point: str, keywords: tuple[str, ...]) -> bool:
    normalized = str(point or "").strip().lower()
    return bool(normalized) and any(keyword in normalized for keyword in keywords)


def _is_table_structure_point(point: str) -> bool:
    normalized = str(point or "").strip().lower()
    if not normalized:
        return False
    markers = ("工作表:", "sheet", "列:", "字段", "数据行数", "column", "columns", "row count")
    return any(marker in normalized for marker in markers)


def _select_literature_points(
    points: list[str],
    *,
    keywords: tuple[str, ...],
    max_items: int,
    allow_numeric: bool = False,
    exclude_structure: bool = False,
) -> list[str]:
    selected: list[str] = []
    for point in points:
        normalized = str(point or "").strip()
        if not normalized:
            continue
        if exclude_structure and _is_table_structure_point(normalized):
            continue
        if not _point_contains_keyword(normalized, keywords):
            if not (allow_numeric and re.search(r"\d", normalized)):
                continue
        if normalized in selected:
            continue
        selected.append(normalized)
        if len(selected) >= max_items:
            break
    return selected


def _ensure_literature_table_summary_structure(*, answer: str, table_text: str) -> str:
    normalized_answer = str(answer or "").strip()
    if not normalized_answer:
        return normalized_answer
    if _has_literature_summary_sections(normalized_answer):
        if _LITERATURE_SUMMARY_NOTE in normalized_answer:
            return normalized_answer
        return f"{normalized_answer}\n\n{_LITERATURE_SUMMARY_NOTE}".strip()

    table_points = _find_table_support_points(table_text, max_items=8)
    answer_points = _find_table_support_points(normalized_answer, max_items=4)
    all_points: list[str] = []
    for item in [*answer_points, *table_points]:
        if item and item not in all_points:
            all_points.append(item)

    background_points = _select_literature_points(
        answer_points,
        keywords=("研究背景", "背景", "目的", "研究", "focus", "aim", "objective"),
        max_items=1,
    )
    method_points = _select_literature_points(
        table_points,
        keywords=("工作表", "sheet", "列:", "字段", "数据行数", "row", "rows", "column"),
        max_items=2,
    )
    result_points = _select_literature_points(
        all_points,
        keywords=("mah", "capacity", "效率", "retention", "capacity_mah", "material=", "note=", "结果"),
        max_items=3,
        allow_numeric=True,
        exclude_structure=True,
    )
    conclusion_points = _select_literature_points(
        answer_points,
        keywords=("结论", "意义", "总结", "说明", "表明", "conclusion", "summary"),
        max_items=2,
    )

    sections = [
        *_build_literature_section("研究目的和背景", background_points, "表格中未提供足够的研究背景或研究目的信息。"),
        *_build_literature_section("研究方法/实验设计", method_points, "表格中未提供足够的研究方法、实验设计或字段定义信息。"),
        *_build_literature_section("主要发现和结果", result_points, "表格中未提供足够的主要发现、关键指标或结果数据。"),
        *_build_literature_section("结论和意义", conclusion_points, "表格中未提供足够的结论或研究意义描述。"),
        _LITERATURE_SUMMARY_NOTE,
    ]
    return "\n".join(sections).strip()


def _tokenize(value: object) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./+-]+|[\u4e00-\u9fff]{2,8}", str(value or "").lower()):
        clean = token.strip()
        if len(clean) > 1:
            tokens.add(clean)
    return tokens


def _score_row(question: str, row_text: str, fallback_index: int) -> tuple[float, int]:
    q_tokens = _tokenize(question)
    row_tokens = _tokenize(row_text)
    overlap = len(q_tokens & row_tokens) if q_tokens and row_tokens else 0
    numeric_overlap = len(set(re.findall(r"\d+(?:\.\d+)?", question)) & set(re.findall(r"\d+(?:\.\d+)?", row_text)))
    return (overlap * 2.0 + numeric_overlap * 0.8, -fallback_index)


def _cell_reference_to_index(reference: str) -> int:
    letters = "".join(ch for ch in str(reference or "") if ch.isalpha()).upper()
    if not letters:
        return 0
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return max(0, index - 1)


def _normalize_row(values: list[str]) -> list[str]:
    normalized = [_collapse_whitespace(item) for item in values]
    while normalized and not normalized[-1]:
        normalized.pop()
    return normalized


def _table_fallback_answer(*, question: str, table_text: str) -> str:
    cleaned = str(table_text or "").strip()
    if not cleaned:
        return "当前未拿到可读的表格原始内容，无法生成基于表格的回答。"

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return "当前未拿到可读的表格原始内容，无法生成基于表格的回答。"

    candidates: list[tuple[tuple[float, int], str]] = []
    for index, line in enumerate(lines):
        if len(line) < 6:
            continue
        candidates.append((_score_row(question, line, index), _truncate(line, 220)))

    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = [item[1] for item in candidates[:4]] or [_truncate(line, 220) for line in lines[:4]]
    prefix = "基于表格原始内容提取，重点如下：" if _is_summary_question(question) else "基于表格原始内容，当前最相关的信息如下："
    return "\n".join([prefix, *[f"{index}. {item}" for index, item in enumerate(selected, start=1)]])


def _build_patent_tabular_prompt(
    *,
    question: str,
    table_text: str,
    route_hint: str,
    source_scope: str,
    include_kb: bool,
    operation_hint: str = "",
) -> str:
    normalized_route = str(route_hint or "tabular_qa").strip().lower() or "tabular_qa"
    normalized_scope = str(source_scope or "table").strip() or "table"
    compare_mode = str(operation_hint or "").strip().lower() == "compare_tables"
    summary_mode = (not compare_mode) and (
        _is_summary_question(question) or "全表统计摘要:" in str(table_text or "")
    )
    if normalized_route == "hybrid_qa":
        if summary_mode:
            return "\n".join(
                [
                    "你是一位专利/文献表格证据分析助手。",
                    "当前任务属于 patent 混合文件问答中的表格证据分析环节。",
                    "表格执行结果来自当前专利/文献文件的真实提取或计算结果，必须作为当前子任务的主依据。",
                    f"当前 source_scope={normalized_scope}",
                    "知识库或其他文件只能用于后续交叉验证，不能覆盖这里的表格结论。",
                    "对于概览类问题，优先根据全表统计摘要作答，先总结整体分布、差异、异常，再引用少量代表性样例举例。不能把少量样例当成整体结论。",
                    "请先整理这份表格单独能够支持的文献概要，再为后续跨来源综合保留证据边界。",
                    "",
                    "用户问题:",
                    str(question or ""),
                    "",
                    "表格证据:",
                    str(table_text or ""),
                    "",
                    "请按以下 Markdown 结构回答：",
                    "## 研究目的和背景",
                    "## 研究方法/实验设计",
                    "## 主要发现和结果",
                    "## 结论和意义",
                    f"{_LITERATURE_SUMMARY_NOTE}",
                    "- 只允许使用当前表格中直接出现的字段、数值、样例行或统计结果",
                    "- 如果某个章节缺少表格证据，明确写出表格中未提供足够信息，不要补写通用知识",
                    "- 保留原始术语、字段名和数值单位",
                    "- 这仍然是混合问答里的表格子结论，不能把 PDF 或知识库内容写成当前表格事实",
                ]
            ).strip()
        return "\n".join(
            [
                    "你是一位专利/文献表格证据分析助手。",
                    "当前任务属于 patent 混合文件问答中的表格证据分析环节。",
                    "表格执行结果来自当前专利/文献文件的真实提取或计算结果，必须作为当前子任务的主依据。",
                    f"当前 source_scope={normalized_scope}",
                    "知识库或其他文件只能用于后续交叉验证，不能覆盖这里的表格结论。",
                    (
                        "当前证据是多表对比执行结果，优先总结不同表格之间的差异、相同点和异常，不要把对比结果写成单文件总结。"
                        if compare_mode
                        else "请先给出这份表格单独能够支持的判断，再指出可供后续跨来源比较的指标或差异。"
                    ),
                    "",
                    "用户问题:",
                    str(question or ""),
                "",
                "表格证据:",
                str(table_text or ""),
                "",
                "请按以下 Markdown 结构回答：",
                "## 结论",
                "## 证据",
                "## 对比",
                "## 限制",
                "- 结论只写当前表格能够直接支持的判断",
                "- 证据列出 2-4 条关键数据、字段或代表性行",
                "- 对比说明这些表格证据后续可与 PDF/知识库对照的点；若当前无对照对象，直接说明",
                "- 限制说明字段缺失、抽取范围限制或仍待其他来源验证的部分",
                "- 不要编造表格中不存在的列、数值或结论",
                (
                    "- 当前为多表对比执行结果，结论必须优先概括文件间差异和对照关系，不要退回单文件描述"
                    if compare_mode
                    else "- 对比段优先说明当前表格证据后续可与 PDF/知识库对照的点"
                ),
                (
                    "- 即使当前允许知识库参与，也只能在后续总结合成里交叉验证，不能把知识库结论写成当前表格事实"
                    if include_kb
                    else "- 当前未引入知识库补充，本轮回答仍需明确边界"
                ),
            ]
        ).strip()

    intro = "你是一位专利/文献表格分析助手。表格执行结果来自当前专利/文献文件的真实提取或计算结果，不允许编造。"
    if compare_mode:
        intro += " 当前证据是多表对比执行结果，回答必须优先总结不同文件之间的差异、共同点和异常，不要把多表对比改写成单文件总结。"
    if summary_mode:
        intro += " 对于概览类问题，优先根据全表统计摘要作答，先总结整体分布、差异、异常，再引用少量代表性样例举例。不能把少量样例当成整体结论。若背景或方法在表格里没有证据，明确说明信息不足。"
        return "\n".join(
            [
                intro,
                f"当前 route={normalized_route}，source_scope={normalized_scope}",
                "",
                "用户问题:",
                str(question or ""),
                "",
                "表格证据:",
                str(table_text or ""),
                "",
                "请按以下 Markdown 结构回答：",
                "## 研究目的和背景",
                "## 研究方法/实验设计",
                "## 主要发现和结果",
                "## 结论和意义",
                f"{_LITERATURE_SUMMARY_NOTE}",
                "- 只允许使用当前表格中能够直接支持的字段、数值、统计结果和代表性行",
                "- 先总结整体分布、差异、异常，再用少量代表性样例举例",
                "- 不能把少量代表性样例当成整体结论",
                "- 如果表格无法支持某个章节，明确写出表格中未提供足够信息",
                "- 保留原始字段名、单位和关键术语",
                "- 不要把字段查询类问题改写成超出表格证据边界的泛化结论",
            ]
        ).strip()
    intro += " 对于定向问题，只回答表格证据能够直接支持的内容；证据不足时要明确指出。"
    return "\n".join(
        [
            intro,
            f"当前 route={normalized_route}，source_scope={normalized_scope}",
            "",
            "用户问题:",
            str(question or ""),
            "",
            "表格证据:",
            str(table_text or ""),
            "",
            "请按以下 Markdown 结构回答：",
            "## 结论",
            "## 证据",
            "## 对比",
            "## 限制",
            "- 结论需要先回答用户最关心的判断",
            "- 证据列出关键字段、数值、样例行或统计摘要",
            (
                "- 对比优先总结不同表格之间的差异、数量级和异常点"
                if compare_mode
                else "- 对比说明当前是否缺少第二份表格或其他来源可用于对照"
            ),
            "- 限制说明抽取范围、字段缺失或原表未提及的部分",
        ]
    ).strip()


class PatentTabularAnswerClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        top_p: float = 0.95,
        max_tokens: int = 2500,
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
            "patent tabular answer client initialized model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s",
            self._model,
            self._base_url,
            self._timeout_seconds,
            transport.get("client_owner"),
            transport.get("shared_client_id"),
        )

    @classmethod
    def from_env(cls, *, http_client: Any | None = None) -> "PatentTabularAnswerClient | None":
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
            top_p=_env_float("PATENT_TABULAR_TOP_P", 0.95),
            max_tokens=max(1024, _env_int("PATENT_TABULAR_MAX_TOKENS", 2500)),
            http_client=http_client,
        )

    def runtime_signature(self) -> dict[str, Any]:
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "top_p": self._top_p,
            "timeout_seconds": self._timeout_seconds,
        }

    def answer(
        self,
        *,
        question: str,
        table_text: str,
        include_kb: bool,
        route_hint: str,
        source_scope: str,
        operation_hint: str = "",
    ) -> str:
        prompt = _build_patent_tabular_prompt(
            question=question,
            table_text=table_text,
            route_hint=route_hint,
            source_scope=source_scope,
            include_kb=include_kb,
            operation_hint=operation_hint,
        )
        transport = describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client)
        _LOGGER.info(
            "patent tabular answer client request start model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s route=%s source_scope=%s operation_hint=%s",
            self._model,
            self._base_url,
            self._timeout_seconds,
            transport.get("client_owner"),
            transport.get("shared_client_id"),
            route_hint,
            source_scope,
            operation_hint,
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
                        {"role": "system", "content": _TABULAR_QA_SYSTEM_MESSAGE},
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


class PatentTabularService:
    def __init__(
        self,
        *,
        extract_table_text_fn: Callable[..., str] | None = None,
        answer_question_fn: Callable[..., str] | None = None,
        answer_client: PatentTabularAnswerClient | Any | None = None,
        auto_answer_client: bool = True,
        max_rows_per_sheet: int = 8,
        max_sheets: int = 3,
        max_table_chars: int | None = None,
        object_reader: Any | None = None,
    ) -> None:
        self._extract_table_text_fn = extract_table_text_fn or self._extract_table_text
        self._answer_question_fn = answer_question_fn
        if answer_question_fn is not None:
            self._client = None
        elif answer_client is not None or not auto_answer_client:
            self._client = answer_client
        else:
            self._client = PatentTabularAnswerClient.from_env()
        self._max_rows_per_sheet = max(1, int(max_rows_per_sheet))
        self._max_sheets = max(1, int(max_sheets))
        resolved_max_table_chars = (
            _env_int("PATENT_TABULAR_MAX_CONTEXT_CHARS", 12000)
            if max_table_chars is None
            else int(max_table_chars)
        )
        self._max_table_chars = max(1000, int(resolved_max_table_chars))
        self._has_custom_extract_table_text_fn = extract_table_text_fn is not None
        self._object_reader = object_reader

    def answer_backend(self) -> str:
        if callable(self._answer_question_fn) or self._client is not None:
            return "llm"
        return "fallback"

    def prompt_version(self) -> str:
        return _PATENT_TABULAR_PROMPT_VERSION

    def runtime_signature(self) -> dict[str, Any]:
        client_runtime = getattr(self._client, "runtime_signature", None)
        return {
            "answer_backend": self.answer_backend(),
            "prompt_version": self.prompt_version(),
            "max_context_chars": self._max_table_chars,
            "answer_client_runtime_signature": dict(client_runtime() or {}) if callable(client_runtime) else {},
        }

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def _get_object_reader(self) -> Any:
        if self._object_reader is None:
            self._object_reader = ObjectReader()
        return self._object_reader

    @staticmethod
    def _strict_minio_only() -> bool:
        return _env_flag("PATENT_ORIGINAL_MINIO_ONLY", True)

    @staticmethod
    def _structured_context_bundle(
        *,
        status: str,
        compact_evidence_context: str = "",
        answer_context: str = "",
        synthesis_context: str = "",
        user_message: str = "",
        answer_mode: str = "",
        skip_file_route_cache: bool = False,
        log_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": str(status or "unreadable"),
            "compact_evidence_context": str(compact_evidence_context or "").strip(),
            "answer_context": str(answer_context or "").strip(),
            "synthesis_context": str(synthesis_context or "").strip(),
            "user_message": str(user_message or "").strip(),
            "answer_mode": str(answer_mode or "table_execution_unavailable").strip(),
            "_skip_file_route_cache": bool(skip_file_route_cache),
            "log_fields": dict(log_fields or {}),
        }

    @staticmethod
    def _is_compare_question(question: str) -> bool:
        text = str(question or "")
        return any(keyword in text for keyword in ("对比", "比较", "差异", "区别"))

    def _load_table_descriptors(self, *, contract: PatentFileContract) -> tuple[list[dict[str, Any]], bool]:
        descriptors: list[dict[str, Any]] = []
        skip_file_route_cache = False
        for item in contract.selected_execution_files:
            if item.family != "table":
                continue
            file_name = str(item.file_name or item.payload.get("file_name") or f"file:{item.file_id}")
            try:
                if self._strict_minio_only():
                    workbook = load_workbook_from_execution_file(
                        item=item,
                        reader=self._get_object_reader(),
                        max_sheets=self._max_sheets,
                    )
                    resolved: Path | str = ""
                else:
                    local_path = str(item.payload.get("local_path") or "").strip()
                    if not local_path:
                        continue
                    resolved_path = Path(local_path)
                    if not resolved_path.exists() or not resolved_path.is_file():
                        continue
                    resolved = resolved_path
                    workbook = load_workbook_cached(
                        path=str(resolved_path),
                        file_name=file_name or resolved_path.name,
                        file_type=str(item.file_type or resolved_path.suffix.lstrip(".")).lower(),
                        max_sheets=self._max_sheets,
                    )
                profile = profile_workbook(workbook)
            except Exception:
                skip_file_route_cache = True
                continue
            descriptors.append(
                {
                    "item": item,
                    "resolved_path": resolved,
                    "file_name": file_name,
                    "workbook": workbook,
                    "profile": profile,
                }
            )
        return descriptors, skip_file_route_cache

    @staticmethod
    def _build_compare_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
        item = descriptor.get("item")
        file_id = int(getattr(item, "file_id", 0) or 0)
        file_name = str(descriptor.get("file_name") or getattr(item, "file_name", "") or f"file:{file_id}")
        workbook = dict(descriptor.get("workbook") or {})
        profile = dict(descriptor.get("profile") or {})
        workbook["file_id"] = file_id
        workbook["file_name"] = file_name
        profile["file_id"] = file_id
        profile["file_name"] = file_name
        return {
            "file_id": file_id,
            "file_name": file_name,
            "workbook": workbook,
            "profile": profile,
        }

    def execute(
        self,
        *,
        contract: PatentFileContract,
        include_kb: bool,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        content_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        used_files = [item.as_payload() for item in contract.selected_execution_files if item.family == "table"]
        profile = get_patent_mode_profile(contract.route)
        steps: list[dict[str, Any]] = []

        self._record_step(
            steps,
            progress_callback=progress_callback,
            payload={
                "step": "tabular_load",
                "title": "读取表格内容",
                "message": f"📊 已匹配 {len(used_files)} 个表格文件，正在加载全表数据...",
                "status": "running",
                "data": {"count": len(used_files)},
            },
        )

        table_text = self._load_table_text(contract=contract) if self._has_custom_extract_table_text_fn else ""
        if self._has_custom_extract_table_text_fn:
            compact_context = _truncate(table_text, min(self._max_table_chars, 1200))
            answer_context = _truncate(table_text, self._max_table_chars)
            synthesis_context = _truncate(table_text, min(self._max_table_chars, 6000))
            skip_file_route_cache = False
            context_status = "ok" if answer_context else "unreadable"
            context_answer_mode = "table_execution_summary"
            context_user_message = ""
            operation_hint = ""
        else:
            context_bundle = self._load_table_context_bundle(contract=contract)
            context_status = str(context_bundle.get("status") or "unreadable")
            compact_context = str(context_bundle.get("compact_evidence_context") or "")
            answer_context = str(context_bundle.get("answer_context") or "")
            synthesis_context = str(context_bundle.get("synthesis_context") or "")
            context_user_message = str(context_bundle.get("user_message") or "")
            context_answer_mode = str(context_bundle.get("answer_mode") or "table_execution_unavailable")
            skip_file_route_cache = bool(context_bundle.get("_skip_file_route_cache"))
            operation_hint = str((context_bundle.get("log_fields") or {}).get("operation") or "")

        if context_status == "ok" and answer_context:
            self._record_step(
                steps,
                progress_callback=progress_callback,
                payload={
                    "step": "tabular_load",
                    "title": "读取表格内容",
                    "message": f"📊 已完成表格执行上下文构建，文件数 {len(used_files)}，chars={len(answer_context)}",
                    "status": "success",
                    "data": {"count": len(used_files), "chars": len(answer_context)},
                },
            )
            answer_text, answer_backend = self._build_answer(
                question=contract.question,
                table_text=answer_context,
                include_kb=include_kb,
                route_hint=contract.route,
                source_scope=contract.source_scope,
                operation_hint=operation_hint,
                content_callback=content_callback,
            )
            answer_mode = context_answer_mode or "table_execution_summary"
        elif context_status == "clarification":
            answer_text = context_user_message or "请进一步明确要分析的工作表、列名或对比维度。"
            answer_backend = "unavailable"
            self._record_step(
                steps,
                progress_callback=progress_callback,
                payload={
                    "step": "tabular_load",
                    "title": "读取表格内容",
                    "message": f"📊 已读取表格文件，但还需要用户补充信息后才能执行，文件数 {len(used_files)}",
                    "status": "success",
                    "data": {"count": len(used_files), "chars": 0},
                },
            )
            answer_mode = context_answer_mode or "table_execution_clarification"
        elif context_status == "execution_unavailable":
            answer_text = context_user_message or "当前已读取到表格文件，但未能生成可用的表格执行结果，请补充更明确的问题或筛选条件。"
            answer_backend = "unavailable"
            self._record_step(
                steps,
                progress_callback=progress_callback,
                payload={
                    "step": "tabular_load",
                    "title": "读取表格内容",
                    "message": f"📊 已读取表格文件，但当前未能生成可用执行结果，文件数 {len(used_files)}",
                    "status": "success",
                    "data": {"count": len(used_files), "chars": 0},
                },
            )
            answer_mode = context_answer_mode or "table_execution_unavailable"
        else:
            answer_text = context_user_message or "当前未拿到可读的表格原始内容，无法生成基于表格的回答。请稍后重试或检查文件处理状态。"
            answer_backend = "unavailable"
            self._record_step(
                steps,
                progress_callback=progress_callback,
                payload={
                    "step": "tabular_load",
                    "title": "读取表格内容",
                    "message": f"📊 未拿到可读的表格原始内容，当前选择文件数 {len(used_files)}",
                    "status": "success",
                    "data": {"count": len(used_files), "chars": 0},
                },
            )
            answer_mode = context_answer_mode or "table_execution_unavailable"

        self._record_step(
            steps,
            progress_callback=progress_callback,
            payload={
                "step": "tabular_answer",
                "title": "生成文件答案",
                "message": "✍️ 正在基于表格原始内容生成答案...",
                "status": "running",
            },
        )
        self._record_step(
            steps,
            progress_callback=progress_callback,
            payload={
                "step": "tabular_answer",
                "title": "生成文件答案",
                "message": "✍️ 已基于表格原始内容生成答案" if answer_context else "✍️ 已返回表格不可读的说明",
                "status": "success",
            },
        )

        return {
            "handler": "tabular",
            "answer_text": answer_text,
            "route": contract.route,
            "query_mode": profile.query_mode,
            "source_scope": contract.source_scope,
            "_table_synthesis_context": synthesis_context,
            "_skip_file_route_cache": bool(skip_file_route_cache or (context_status == "ok" and answer_context and answer_backend == "unavailable")),
            "steps": [dict(item) for item in steps],
            "metadata": {
                "handler": "tabular",
                "source_scope": contract.source_scope,
                "selected_file_count": len(used_files),
                "kb_enabled": bool(include_kb),
                "answer_mode": answer_mode,
                "answer_backend": answer_backend,
                "table_text_chars": len(answer_context),
                "table_answer_context_chars": len(answer_context),
                "table_synthesis_context_chars": len(synthesis_context),
                "table_evidence_context": compact_context,
            },
            "timings": {
                "patent_tabular_route_ms": 1,
            },
            "used_files": used_files,
            "selected_file_ids": list(contract.selected_file_ids),
            "file_selection": dict(contract.file_selection),
            "kb_enabled": bool(include_kb),
        }

    def _load_table_context_bundle(self, *, contract: PatentFileContract) -> dict[str, Any]:
        compact_sections: list[str] = []
        answer_sections: list[str] = []
        synthesis_sections: list[str] = []
        selected_table_items = [item for item in contract.selected_execution_files if item.family == "table"]
        descriptors, skip_file_route_cache = self._load_table_descriptors(contract=contract)
        if len(selected_table_items) >= 2 and self._is_compare_question(contract.question) and len(descriptors) < len(selected_table_items):
            return self._structured_context_bundle(
                status="unreadable",
                user_message="当前对比请求缺少足够的可读表格文件，无法生成完整对比结果。请检查所选表格是否都已处理完成。",
                answer_mode="table_execution_unavailable",
                skip_file_route_cache=skip_file_route_cache,
                log_fields={"operation": "compare_tables", "selected_table_count": len(selected_table_items), "readable_table_count": len(descriptors)},
            )

        if len(descriptors) >= 2:
            compare_descriptors = [self._build_compare_descriptor(descriptor) for descriptor in descriptors]
            compare_profiles = [dict(item.get("profile") or {}) for item in compare_descriptors]
            compare_plan = plan_tabular_query(
                question=contract.question,
                profile=compare_profiles[0],
                profiles=compare_profiles,
                workbook_count=len(compare_profiles),
            )
            clarification_reason = str(compare_plan.get("clarification_reason") or "")
            is_compare_flow = (
                str(compare_plan.get("operation") or "") == "compare_tables"
                or clarification_reason == "sheet_compare_ambiguous"
                or clarification_reason.startswith("compare_")
            )
            if is_compare_flow:
                if bool(compare_plan.get("needs_clarification")):
                    return self._structured_context_bundle(
                        status="clarification",
                        user_message=str(compare_plan.get("clarification_message") or "请进一步明确多表对比条件。"),
                        answer_mode="table_execution_clarification",
                        skip_file_route_cache=skip_file_route_cache,
                        log_fields={"operation": "compare_tables", "clarification_reason": clarification_reason},
                    )
                compare_result = execute_compare_plan(
                    workbooks=[dict(item.get("workbook") or {}) for item in compare_descriptors],
                    plan=compare_plan,
                )
                if not has_usable_tabular_result(compare_result):
                    return self._structured_context_bundle(
                        status="execution_unavailable",
                        user_message="当前已读取到表格文件，但未能生成可用的表格对比结果，请补充更明确的对比维度或检查目标工作表/列名。",
                        answer_mode="table_execution_compare_unavailable",
                        skip_file_route_cache=skip_file_route_cache,
                        log_fields={"operation": "compare_tables", "empty_reason": str(compare_result.get("empty_reason") or "")},
                    )
                compare_bundle = build_compare_tabular_context_bundle(
                    question=contract.question,
                    workbooks=[dict(item.get("workbook") or {}) for item in compare_descriptors],
                    plan=compare_plan,
                    result=compare_result,
                    compact_limit=min(self._max_table_chars, 1200),
                    answer_limit=self._max_table_chars,
                    synthesis_limit=min(self._max_table_chars, 6000),
                )
                return self._structured_context_bundle(
                    status="ok",
                    compact_evidence_context=str(compare_bundle.get("compact_evidence_context") or ""),
                    answer_context=str(compare_bundle.get("answer_context") or ""),
                    synthesis_context=str(compare_bundle.get("synthesis_context") or ""),
                    answer_mode="table_execution_compare",
                    skip_file_route_cache=skip_file_route_cache,
                    log_fields={"operation": "compare_tables"},
                )

        first_clarification_message = ""
        first_clarification_reason = ""
        saw_execution_unavailable = False
        for descriptor in descriptors:
            file_name = str(descriptor.get("file_name") or "")
            workbook = dict(descriptor.get("workbook") or {})
            profile = dict(descriptor.get("profile") or {})
            plan = plan_tabular_query(question=contract.question, profile=profile)
            if bool(plan.get("needs_clarification")):
                if not first_clarification_message:
                    first_clarification_message = str(plan.get("clarification_message") or "")
                    first_clarification_reason = str(plan.get("clarification_reason") or "")
                continue
            try:
                result = execute_tabular_plan(workbook=workbook, plan=plan)
            except Exception:
                skip_file_route_cache = True
                saw_execution_unavailable = True
                continue
            if not has_usable_tabular_result(result):
                saw_execution_unavailable = True
                continue
            bundle = build_tabular_context_bundle(
                question=contract.question,
                workbook=workbook,
                plan=plan,
                result=result,
                file_name=file_name,
                compact_limit=min(self._max_table_chars, 1200),
                answer_limit=self._max_table_chars,
                synthesis_limit=min(self._max_table_chars, 6000),
            )
            compact_context = str(bundle.get("compact_evidence_context") or "").strip()
            answer_context = str(bundle.get("answer_context") or "").strip()
            synthesis_context = str(bundle.get("synthesis_context") or "").strip()
            if compact_context:
                compact_sections.append(compact_context)
            if answer_context:
                answer_sections.append(answer_context)
            if synthesis_context:
                synthesis_sections.append(synthesis_context)

        compact_context = "\n\n".join(section for section in compact_sections if section).strip()
        answer_context = "\n\n".join(section for section in answer_sections if section).strip()
        synthesis_context = "\n\n".join(section for section in synthesis_sections if section).strip()
        if answer_context:
            return self._structured_context_bundle(
                status="ok",
                compact_evidence_context=compact_context,
                answer_context=answer_context,
                synthesis_context=synthesis_context,
                answer_mode="table_execution_summary",
                skip_file_route_cache=skip_file_route_cache,
                log_fields={"operation": "single_table"},
            )
        if first_clarification_message:
            return self._structured_context_bundle(
                status="clarification",
                user_message=first_clarification_message,
                answer_mode="table_execution_clarification",
                skip_file_route_cache=skip_file_route_cache,
                log_fields={"clarification_reason": first_clarification_reason},
            )
        if descriptors and saw_execution_unavailable:
            return self._structured_context_bundle(
                status="execution_unavailable",
                user_message="当前已读取到表格文件，但未能生成可用的表格执行结果，请补充更明确的问题、筛选条件或目标列名。",
                answer_mode="table_execution_unavailable",
                skip_file_route_cache=skip_file_route_cache,
                log_fields={"operation": "single_table"},
            )
        return self._structured_context_bundle(
            status="unreadable",
            user_message="当前未拿到可读的表格原始内容，无法生成基于表格的回答。请稍后重试或检查文件处理状态。",
            answer_mode="table_execution_unavailable",
            skip_file_route_cache=skip_file_route_cache,
            log_fields={"operation": "none"},
        )

    @staticmethod
    def _record_step(
        steps: list[dict[str, Any]],
        *,
        payload: dict[str, Any],
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        normalized = dict(payload or {})
        step_key = str(normalized.get("step") or "").strip()
        if step_key:
            for index, existing in enumerate(steps):
                if str(existing.get("step") or "").strip() == step_key:
                    merged = dict(existing)
                    merged.update(normalized)
                    steps[index] = merged
                    break
            else:
                steps.append(normalized)
        else:
            steps.append(normalized)
        if callable(progress_callback):
            progress_callback(dict(normalized))

    def _load_table_text(self, *, contract: PatentFileContract) -> str:
        sections: list[str] = []
        for item in contract.selected_execution_files:
            if item.family != "table":
                continue
            file_name = str(item.file_name or item.payload.get("file_name") or f"file:{item.file_id}")
            file_type = str(item.file_type or Path(file_name).suffix.lstrip(".")).lower()
            if self._strict_minio_only():
                storage_ref = str(item.payload.get("storage_ref") or "").strip()
                if not storage_ref:
                    continue
                try:
                    suffix = Path(file_name).suffix.lower()
                    if not suffix:
                        suffix = f".{file_type}" if file_type in {"csv", "xls", "xlsx", "xlsm"} else ".bin"
                    resolved = Path(self._get_object_reader().materialize_temp(storage_ref, suffix=suffix))
                except Exception:
                    continue
            else:
                local_path = str(item.payload.get("local_path") or "").strip()
                if not local_path:
                    continue
                resolved = Path(local_path)
                if not resolved.exists() or not resolved.is_file():
                    continue
            extracted = str(
                self._extract_table_text_fn(
                    str(resolved),
                    question=contract.question,
                    file_name=file_name or resolved.name,
                    file_type=file_type or resolved.suffix.lstrip(".").lower(),
                    max_rows_per_sheet=self._max_rows_per_sheet,
                    max_sheets=self._max_sheets,
                )
                or ""
            ).strip()
            if not extracted:
                continue
            label = str(file_name or resolved.name or f"file:{item.file_id}")
            sections.append(f"文件: {label}\n{_truncate(extracted, self._max_table_chars)}")
        return "\n\n".join(sections).strip()

    def _build_answer(
        self,
        *,
        question: str,
        table_text: str,
        include_kb: bool,
        route_hint: str,
        source_scope: str,
        operation_hint: str = "",
        content_callback: Callable[[str], None] | None = None,
    ) -> str:
        compare_mode = str(operation_hint or "").strip().lower() == "compare_tables"
        summary_mode = (not compare_mode) and (
            _is_summary_question(question) or "全表统计摘要:" in str(table_text or "")
        )
        route_name = str(route_hint or "tabular_qa").strip() or "tabular_qa"
        live_stream_possible = False
        prompt = _build_patent_tabular_prompt(
            question=question,
            table_text=table_text,
            route_hint=route_hint,
            source_scope=source_scope,
            include_kb=include_kb,
            operation_hint=operation_hint,
        )
        if callable(self._answer_question_fn):
            output = self._answer_question_fn(
                question=question,
                table_text=table_text,
                include_kb=include_kb,
                prompt=prompt,
                route_hint=route_hint,
                source_scope=source_scope,
                operation_hint=operation_hint,
            )
            if isinstance(output, (str, bytes)):
                answer = str(output or "").strip()
                if answer:
                    answer = (
                        _ensure_literature_table_summary_structure(answer=answer, table_text=table_text)
                        if summary_mode
                        else _ensure_fastqa_table_summary_structure(
                            answer=answer,
                            table_text=table_text,
                            include_kb=include_kb,
                            route_hint=route_hint,
                            source_scope=source_scope,
                        )
                    )
                    emitted = emit_text_chunks(answer, content_callback=content_callback)
                    _LOGGER.info(
                        "patent tabular answer route=%s source_scope=%s summary_mode=%s live_stream_possible=%s output_mode=callable_text emitted_chunks=%s answer_chars=%s",
                        route_name,
                        source_scope,
                        summary_mode,
                        live_stream_possible,
                        emitted,
                        len(answer),
                    )
                    return answer, "llm"
            else:
                answer_parts: list[str] = []
                for piece in iter_text_output(output):
                    text = str(piece or "")
                    if not text:
                        continue
                    answer_parts.append(text)
                answer = "".join(answer_parts).strip()
                if answer:
                    answer = (
                        _ensure_literature_table_summary_structure(answer=answer, table_text=table_text)
                        if summary_mode
                        else _ensure_fastqa_table_summary_structure(
                            answer=answer,
                            table_text=table_text,
                            include_kb=include_kb,
                            route_hint=route_hint,
                            source_scope=source_scope,
                        )
                    )
                    emitted = emit_text_chunks(answer, content_callback=content_callback)
                    _LOGGER.info(
                        "patent tabular answer route=%s source_scope=%s summary_mode=%s live_stream_possible=%s output_mode=callable_iter_buffered buffered_pieces=%s emitted_chunks=%s answer_chars=%s",
                        route_name,
                        source_scope,
                        summary_mode,
                        live_stream_possible,
                        len(answer_parts),
                        emitted,
                        len(answer),
                    )
                    return answer, "llm"
        elif self._client is not None:
            try:
                output = self._client.answer(
                    question=question,
                    table_text=table_text,
                    include_kb=include_kb,
                    route_hint=route_hint,
                    source_scope=source_scope,
                    operation_hint=operation_hint,
                )
                if isinstance(output, (str, bytes)):
                    answer = str(output or "").strip()
                else:
                    answer = "".join(str(piece or "") for piece in iter_text_output(output)).strip()
                if answer:
                    answer = (
                        _ensure_literature_table_summary_structure(answer=answer, table_text=table_text)
                        if summary_mode
                        else _ensure_fastqa_table_summary_structure(
                            answer=answer,
                            table_text=table_text,
                            include_kb=include_kb,
                            route_hint=route_hint,
                            source_scope=source_scope,
                        )
                    )
                    emitted = emit_text_chunks(answer, content_callback=content_callback)
                    _LOGGER.info(
                        "patent tabular answer route=%s source_scope=%s summary_mode=%s live_stream_possible=%s output_mode=client_text emitted_chunks=%s answer_chars=%s",
                        route_name,
                        source_scope,
                        summary_mode,
                        live_stream_possible,
                        emitted,
                        len(answer),
                    )
                    return answer, "llm"
            except Exception:
                _LOGGER.warning(
                    "patent tabular answer client failed; degrading to fallback route=%s source_scope=%s",
                    route_name,
                    source_scope,
                    exc_info=True,
                )
                fallback = _table_fallback_answer(question=question, table_text=table_text)
                answer = (
                    _ensure_literature_table_summary_structure(answer=fallback, table_text=table_text)
                    if summary_mode
                    else _ensure_fastqa_table_summary_structure(
                        answer=fallback,
                        table_text=table_text,
                        include_kb=include_kb,
                        route_hint=route_hint,
                        source_scope=source_scope,
                    )
                )
                emitted = emit_text_chunks(answer, content_callback=content_callback)
                _LOGGER.info(
                    "patent tabular answer route=%s source_scope=%s summary_mode=%s live_stream_possible=%s output_mode=client_error_fallback emitted_chunks=%s answer_chars=%s",
                    route_name,
                    source_scope,
                    summary_mode,
                    live_stream_possible,
                    emitted,
                    len(answer),
                )
                return answer, "unavailable"
        fallback = _table_fallback_answer(question=question, table_text=table_text)
        answer = (
            _ensure_literature_table_summary_structure(answer=fallback, table_text=table_text)
            if summary_mode
            else _ensure_fastqa_table_summary_structure(
                answer=fallback,
                table_text=table_text,
                include_kb=include_kb,
                route_hint=route_hint,
                source_scope=source_scope,
            )
        )
        emitted = emit_text_chunks(answer, content_callback=content_callback)
        _LOGGER.info(
            "patent tabular answer route=%s source_scope=%s summary_mode=%s live_stream_possible=%s output_mode=fallback emitted_chunks=%s answer_chars=%s",
            route_name,
            source_scope,
            summary_mode,
            live_stream_possible,
            emitted,
            len(answer),
        )
        return answer, "fallback"

    @staticmethod
    def _extract_table_text(
        table_path: str,
        *,
        question: str,
        file_name: str,
        file_type: str,
        max_rows_per_sheet: int = 8,
        max_sheets: int = 3,
    ) -> str:
        suffix = Path(table_path).suffix.lower() or Path(file_name).suffix.lower()
        normalized_type = str(file_type or suffix.lstrip(".")).lower()
        if normalized_type in {"csv"} or suffix == ".csv":
            rows = PatentTabularService._read_csv_rows(table_path)
            return PatentTabularService._summarize_sheet(
                sheet_name="Sheet1",
                rows=rows,
                question=question,
                max_rows=max_rows_per_sheet,
                file_name=file_name,
            )
        if normalized_type in {"xls"} or suffix == ".xls":
            sheets = PatentTabularService._read_legacy_excel_rows(table_path, max_sheets=max_sheets)
            parts = [
                PatentTabularService._summarize_sheet(
                    sheet_name=sheet_name,
                    rows=rows,
                    question=question,
                    max_rows=max_rows_per_sheet,
                    file_name=file_name,
                )
                for sheet_name, rows in sheets
            ]
            return "\n\n".join(part for part in parts if part).strip()
        if normalized_type in {"excel", "table", "xlsx", "xlsm"} or suffix in {".xlsx", ".xlsm"}:
            sheets = PatentTabularService._read_xlsx_rows(table_path, max_sheets=max_sheets)
            parts = [
                PatentTabularService._summarize_sheet(
                    sheet_name=sheet_name,
                    rows=rows,
                    question=question,
                    max_rows=max_rows_per_sheet,
                    file_name=file_name,
                )
                for sheet_name, rows in sheets
            ]
            return "\n\n".join(part for part in parts if part).strip()
        return ""

    @staticmethod
    def _read_csv_rows(table_path: str) -> list[list[str]]:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                with open(table_path, "r", encoding=encoding, newline="") as handle:
                    return [_normalize_row(row) for row in csv.reader(handle)]
            except UnicodeDecodeError:
                continue
        return []

    @staticmethod
    def _read_xlsx_rows(table_path: str, *, max_sheets: int) -> list[tuple[str, list[list[str]]]]:
        try:
            with zipfile.ZipFile(table_path) as archive:
                shared_strings = PatentTabularService._xlsx_shared_strings(archive)
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                rel_targets = {
                    str(rel.attrib.get("Id") or ""): str(rel.attrib.get("Target") or "")
                    for rel in relationships.findall("pkgrel:Relationship", _XML_NS)
                }
                sheets: list[tuple[str, list[list[str]]]] = []
                for sheet in workbook.findall("main:sheets/main:sheet", _XML_NS)[:max_sheets]:
                    sheet_name = str(sheet.attrib.get("name") or f"Sheet{len(sheets) + 1}")
                    rel_id = str(sheet.attrib.get(f"{{{_XML_NS['rel']}}}id") or "")
                    target = rel_targets.get(rel_id, "")
                    if not target:
                        continue
                    sheet_path = posixpath.normpath(
                        target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
                    )
                    rows = PatentTabularService._xlsx_sheet_rows(
                        archive.read(sheet_path),
                        shared_strings=shared_strings,
                    )
                    sheets.append((sheet_name, rows))
                return sheets
        except (KeyError, ET.ParseError, zipfile.BadZipFile):
            return []

    @staticmethod
    def _read_legacy_excel_rows(table_path: str, *, max_sheets: int) -> list[tuple[str, list[list[str]]]]:
        try:
            import pandas as pd  # type: ignore
        except Exception:
            return []
        try:
            workbook = pd.read_excel(table_path, sheet_name=None, header=None)
        except Exception:
            return []
        sheets: list[tuple[str, list[list[str]]]] = []
        for index, (sheet_name, frame) in enumerate(workbook.items()):
            if index >= max_sheets:
                break
            rows: list[list[str]] = []
            for raw_row in frame.fillna("").itertuples(index=False, name=None):
                rows.append(_normalize_row([str(value or "") for value in raw_row]))
            sheets.append((str(sheet_name or f"Sheet{index + 1}"), rows))
        return sheets

    @staticmethod
    def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        values: list[str] = []
        for item in root.findall("main:si", _XML_NS):
            values.append(_collapse_whitespace("".join(item.itertext())))
        return values

    @staticmethod
    def _xlsx_sheet_rows(payload: bytes, *, shared_strings: list[str]) -> list[list[str]]:
        root = ET.fromstring(payload)
        rows: list[list[str]] = []
        for row in root.findall("main:sheetData/main:row", _XML_NS):
            values: list[str] = []
            for cell in row.findall("main:c", _XML_NS):
                column_index = _cell_reference_to_index(cell.attrib.get("r", ""))
                while len(values) < column_index:
                    values.append("")
                values.append(PatentTabularService._xlsx_cell_value(cell, shared_strings))
            rows.append(_normalize_row(values))
        return rows

    @staticmethod
    def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
        cell_type = str(cell.attrib.get("t") or "").strip()
        if cell_type == "inlineStr":
            inline = cell.find("main:is", _XML_NS)
            return _collapse_whitespace("".join(inline.itertext())) if inline is not None else ""
        value_node = cell.find("main:v", _XML_NS)
        if value_node is None:
            return ""
        raw = str(value_node.text or "").strip()
        if cell_type == "s":
            try:
                return _collapse_whitespace(shared_strings[int(raw)])
            except (ValueError, IndexError):
                return raw
        return _collapse_whitespace(raw)

    @staticmethod
    def _summarize_sheet(
        *,
        sheet_name: str,
        rows: list[list[str]],
        question: str,
        max_rows: int,
        file_name: str,
    ) -> str:
        normalized_rows = [row for row in rows if any(str(cell or "").strip() for cell in row)]
        if not normalized_rows:
            return ""
        headers = normalized_rows[0]
        body_rows = normalized_rows[1:]
        header_names = [item for item in headers if item][:8]
        selected_rows = PatentTabularService._select_rows(question=question, headers=headers, rows=body_rows, max_rows=max_rows)
        parts = [f"工作表: {sheet_name}", f"文件: {file_name}"]
        if header_names:
            parts.append(f"列: {', '.join(header_names)}")
        parts.append(f"数据行数: {len(body_rows)}")
        if selected_rows:
            parts.append("代表性行:")
            parts.extend(f"- {row}" for row in selected_rows)
        return "\n".join(parts).strip()

    @staticmethod
    def _select_rows(*, question: str, headers: list[str], rows: list[list[str]], max_rows: int) -> list[str]:
        if not rows:
            return []
        labeled_rows: list[tuple[tuple[float, int], str]] = []
        for index, row in enumerate(rows):
            pairs: list[str] = []
            for column_index, value in enumerate(row):
                cell = _collapse_whitespace(value)
                if not cell:
                    continue
                header = _collapse_whitespace(headers[column_index]) if column_index < len(headers) else ""
                pairs.append(f"{header}={cell}" if header else cell)
            if not pairs:
                continue
            rendered = "; ".join(pairs[:8])
            labeled_rows.append((_score_row(question, rendered, index), _truncate(rendered, 220)))
        labeled_rows.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in labeled_rows[:max_rows]]
