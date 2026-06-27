from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from server.patent.model_call_logging import (
    auth_mode_label,
    log_model_call_failed,
    log_model_call_start,
    log_model_call_success,
    message_chars,
)
from server.patent.prompt_loader import load_patent_prompt_template
from server.patent.retrieval_models import PatentEvidence, PatentRetrievalOutcome, PatentTableSupplement
from server.patent.thinking import (
    LLM_STAGE_STAGE4_FINAL_ANSWER,
    apply_openai_compatible_thinking,
    auth_headers,
    resolve_thinking_controls,
)
from server.patent.upstream_transport import (
    build_patent_request_timeout,
    describe_patent_transport,
    record_patent_dispatch_error,
    record_patent_dispatch_success,
)
from server.utils.upstream_errors import UpstreamCallError, status_code_from_exception

_LOGGER = logging.getLogger("patent.answering")
_PATENT_ID_CITATION_RE = re.compile(r"\(\s*patent_id\s*=\s*([A-Za-z0-9._/\-]+)\s*\)", re.IGNORECASE)
_BACKTICK_CODE_SPAN_RE = re.compile(r"`(?P<body>[^`\n]{1,200})`")
_PATENT_CITATION_LIST_ITEM_RE = re.compile(r"^(?:patent_id\s*=\s*)?([A-Za-z0-9._/\-]+)$", re.IGNORECASE)
_CLAUSE_BOUNDARIES = "\n。！？!?；;，,"
_STREAM_CITATION_TAIL_HOLD = 160
DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE = load_patent_prompt_template("stage4_answer_user.txt")
DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT = load_patent_prompt_template("stage4_answer_system.txt")


class _PromptFormatDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _render_prompt_template(template: str, **values: object) -> str:
    return str(template or "").format_map(
        _PromptFormatDict({key: str(value or "") for key, value in values.items()})
    )


def _escape_prompt_value(value: object) -> str:
    return str(value or "").replace("{", "{{").replace("}", "}}")


def _build_patent_stage4_doping_warning(user_question: str) -> str:
    user_question_lower = str(user_question or "").lower()
    doping_elements: list[tuple[str, str]] = []
    element_patterns = [
        (r"钛[掺杂]?", "Ti", "钛"),
        (r"ti[ doped]?", "Ti", "钛"),
        (r"镁[掺杂]?", "Mg", "镁"),
        (r"mg[ doped]?", "Mg", "镁"),
        (r"锰[掺杂]?", "Mn", "锰"),
        (r"mn[ doped]?", "Mn", "锰"),
        (r"锌[掺杂]?", "Zn", "锌"),
        (r"zn[ doped]?", "Zn", "锌"),
        (r"钒[掺杂]?", "V", "钒"),
        (r"v[ +]doping", "V", "钒"),
        (r"氟[掺杂]?", "F", "氟"),
        (r"f[ -]doping", "F", "氟"),
    ]
    for pattern, eng, chi in element_patterns:
        del eng
        if re.search(pattern, user_question_lower):
            doping_elements.append(("", chi))
    if not doping_elements:
        return ""
    elements_str = "、".join([item[1] for item in doping_elements])
    return f"""
## ⚠️ 重要验证：专利证据元素匹配（必须遵守！）

用户问题涉及：**{elements_str}掺杂**

**你必须只引用标注为"✅ 包含核心元素"的专利证据！**

证据文档中每件专利都标注了状态：
- ✅ 包含核心元素 - 该专利包含用户问题中提到的掺杂元素，可以引用
- ❌ 不含核心元素（可能不相关！） - 该专利不包含用户问题中提到的掺杂元素，**绝对禁止引用**！

**绝对禁止**：
- 禁止引用标注为"❌ 不含核心元素"的专利证据
- 禁止在答案中声称这些专利研究了这些元素的掺杂效果
- 禁止基于这些专利证据进行推理

**正确做法**：
- 如果没有"✅ 包含核心元素"的专利证据，必须明确说"当前证据未直接覆盖{elements_str}掺杂"
- 只能引用标注为"✅ 包含核心元素"的专利证据

例如：用户问"钛掺杂"，证据中只有"氟掺杂"专利（标注为❌），就必须说"当前证据未直接覆盖钛掺杂"！
"""


def _build_patent_stage4_facts_based_warning() -> str:
    return """
## 🚨 单阶段模式：证据优先于预回答！

**用户原问题**是锚点。支持性专利证据中的内容**优先于**用户消息里的专家初稿。
初稿仅作角度与结构参考：**禁止**用初稿中的具体数据、结论充当「已引用专利证据」的论述；无证据支撑的句子不得写成肯定性技术结论。

## 预回答（专家初稿）的统一规则

- **用户原问题**：全文必须直接回应，避免答非所问。
- **预回答**：只用于**结构、讨论维度、段落衔接**，**不是**事实与数据的来源。
- **开篇引言（推荐保留）**：可在答案最前写 **1 段**（可多句），风格可与预回答首段类似：**问题意义或背景** → **对用户问题中的核心术语作界定**（如压实密度与振实密度须与用户问题用词一致，勿混用）→ **预告下文将从哪些方面展开**。该段为**引导**，**不需要**标注 `(patent_id=公开号)`；其中**具体数值、比例、工艺参数**仍须来自当轮专利证据，**禁止**照搬预回答中无证据支撑的数字。
- **正文中的具体数值、比例、工艺参数、性能数据、机理断言**（引言之后的论述部分）：须能在当轮「支持性专利证据」中找到依据；找不到则不要写，或明确写「当前专利证据未直接给出」。
- 预回答与专利证据不一致时，**以专利证据为准**。
"""


def _build_patent_stage4_cite_depth_instruction(evidence_count: int) -> str:
    n_ev = max(0, int(evidence_count or 0))
    if n_ev == 0:
        return "本轮未提供专利证据正文时，**不要**在答案中编写 `(patent_id=公开号)`。"
    if n_ev <= 2:
        return (
            f"本轮专利证据正文仅 **{n_ev}** 件：最多只引用这 {n_ev} 件；有据可查的论断后可标 `(patent_id=公开号)`，"
            "**禁止**使用白名单外的公开号。"
        )
    if n_ev <= 8:
        return (
            f"本轮专利证据正文共 **{n_ev}** 件：建议引用 **{min(3, n_ev)}–{n_ev}** 件，"
            f"件数**不得超过 {n_ev}**；所有 patent_id/公开号须与用户消息白名单逐字一致。"
        )
    return (
        f"本轮专利证据正文共 **{n_ev}** 件：建议从中择优深入引用 **3–8** 件；"
        "所有 `(patent_id=公开号)` 必须来自白名单，禁止凭记忆补全。"
    )


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _truncate(value: str, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def _normalize_patent_id(value: object) -> str:
    return str(value or "").strip().upper()


def _normalize_patent_id_list(values: list[object] | tuple[object, ...] | None) -> list[str]:
    normalized: list[str] = []
    for item in list(values or []):
        patent_id = _normalize_patent_id(item)
        if patent_id and patent_id not in normalized:
            normalized.append(patent_id)
    return normalized


def _resolve_min_distinct_citations(*, context: dict[str, Any], allowed_patent_ids: list[str]) -> int:
    if not allowed_patent_ids:
        return 0

    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    raw_required = context.get("stage4_min_citations_required")
    raw_configured = context.get("stage4_min_citations_configured")
    configured_default = max(1, _coerce_int(raw_configured, 10))
    required = _coerce_int(raw_required, configured_default)
    return max(1, min(required, len(allowed_patent_ids)))


def _extract_content_fragments(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            fragments.extend(_extract_content_fragments(item))
        return fragments
    if isinstance(value, dict):
        fragments: list[str] = []
        text = value.get("text")
        if isinstance(text, str) and text:
            fragments.append(text)
        elif text is not None:
            fragments.extend(_extract_content_fragments(text))
        content = value.get("content")
        if content is not None and content is not value:
            fragments.extend(_extract_content_fragments(content))
        return fragments
    return []


def _extract_stream_fragments(payload: dict[str, Any]) -> list[str]:
    fragments: list[str] = []
    for choice in list(payload.get("choices") or []):
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict):
            fragments.extend(_extract_content_fragments(delta.get("content")))
        message = choice.get("message")
        if isinstance(message, dict):
            fragments.extend(_extract_content_fragments(message.get("content")))
    return [fragment for fragment in fragments if isinstance(fragment, str) and fragment]


def extract_cited_patent_ids(answer_text: str) -> list[str]:
    cited: list[str] = []
    for match in _PATENT_ID_CITATION_RE.finditer(str(answer_text or "")):
        patent_id = _normalize_patent_id(match.group(1))
        if patent_id and patent_id not in cited:
            cited.append(patent_id)
    return cited


def _unwrap_backticked_patent_citation_blocks(text: str, *, allowed_patent_ids: list[str] | None) -> str:
    allowed = set(_normalize_patent_id_list(allowed_patent_ids))
    if not allowed:
        return str(text or "")

    def _replace(match: re.Match[str]) -> str:
        body = str(match.group("body") or "").strip()
        if not (body.startswith("(") and body.endswith(")")):
            return match.group(0)
        inner = body[1:-1].strip()
        if not inner:
            return match.group(0)
        raw_parts = [part.strip() for part in re.split(r"\s*[,，、;；]\s*", inner) if str(part).strip()]
        if not raw_parts:
            return match.group(0)
        for raw_part in raw_parts:
            token_match = _PATENT_CITATION_LIST_ITEM_RE.fullmatch(raw_part)
            if token_match is None:
                return match.group(0)
            patent_id = _normalize_patent_id(token_match.group(1))
            if not patent_id or patent_id not in allowed:
                return match.group(0)
        return body

    return _BACKTICK_CODE_SPAN_RE.sub(_replace, str(text or ""))


def render_patent_citations_for_user(
    answer_text: str,
    *,
    allowed_patent_ids: list[str] | None,
    trim: bool = True,
) -> str:
    allowed = set(_normalize_patent_id_list(allowed_patent_ids))

    def _replace(match: re.Match[str]) -> str:
        patent_id = _normalize_patent_id(match.group(1))
        if patent_id and (not allowed or patent_id in allowed):
            return f"({patent_id})"
        return ""

    rendered = _unwrap_backticked_patent_citation_blocks(
        str(answer_text or ""),
        allowed_patent_ids=allowed_patent_ids,
    )
    rendered = _PATENT_ID_CITATION_RE.sub(_replace, rendered)
    rendered = re.sub(r"patent_id\s*=\s*", "", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"\(\s+\)", "", rendered)
    rendered = re.sub(r"\s+([，。！？；：,.;!?])", r"\1", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    if trim:
        rendered = re.sub(r"[ \t]{2,}", " ", rendered)
        return rendered.strip()
    return rendered


class PatentCitationStreamSanitizer:
    def __init__(self, *, allowed_patent_ids: list[str] | None) -> None:
        self._allowed_patent_ids = _normalize_patent_id_list(allowed_patent_ids)
        self._tail = ""

    def consume(self, chunk: str) -> str:
        text = str(chunk or "")
        if not text:
            return ""
        self._tail = f"{self._tail}{text}"
        if len(self._tail) <= _STREAM_CITATION_TAIL_HOLD:
            return ""
        flushable = self._tail[:-_STREAM_CITATION_TAIL_HOLD]
        self._tail = self._tail[-_STREAM_CITATION_TAIL_HOLD:]
        return render_patent_citations_for_user(
            flushable,
            allowed_patent_ids=self._allowed_patent_ids,
            trim=False,
        )

    def finalize(self) -> str:
        if not self._tail:
            return ""
        remaining = self._tail
        self._tail = ""
        return render_patent_citations_for_user(
            remaining,
            allowed_patent_ids=self._allowed_patent_ids,
            trim=False,
        )


def _remove_segment_around_marker(text: str, marker: str) -> str:
    updated = str(text or "")
    while marker in updated:
        index = updated.find(marker)
        start = index
        while start > 0 and updated[start - 1] not in _CLAUSE_BOUNDARIES:
            start -= 1
        if start > 0 and updated[start - 1] in "，,；;":
            start -= 1

        end = index + len(marker)
        while end < len(updated) and updated[end] not in _CLAUSE_BOUNDARIES:
            end += 1
        if end < len(updated) and updated[end] in _CLAUSE_BOUNDARIES:
            end += 1
        updated = (updated[:start] + updated[end:]).strip()
    return updated


def sanitize_patent_id_citations(answer_text: str, *, allowed_patent_ids: list[str] | None) -> tuple[str, list[str], list[str]]:
    allowed = set(_normalize_patent_id_list(allowed_patent_ids))
    cited: list[str] = []
    invalid: list[str] = []
    invalid_markers: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        patent_id = _normalize_patent_id(match.group(1))
        if patent_id and (not allowed or patent_id in allowed):
            if patent_id not in cited:
                cited.append(patent_id)
            return f"(patent_id={patent_id})"
        if patent_id and patent_id not in invalid:
            invalid.append(patent_id)
        marker = f"__INVALID_PATENT_CITATION_{patent_id}__"
        invalid_markers.append(marker)
        return marker

    sanitized = _PATENT_ID_CITATION_RE.sub(_replace, str(answer_text or ""))
    for marker in invalid_markers:
        sanitized = _remove_segment_around_marker(sanitized, marker)
    sanitized = re.sub(r"[ \t]+", " ", sanitized)
    sanitized = re.sub(r"\s+([，。！？；：,.;!?])", r"\1", sanitized)
    sanitized = re.sub(r"\(\s+\)", "", sanitized)
    sanitized = re.sub(r"[，,；;]\s*[。！？!?]", lambda m: str(m.group(0))[-1], sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip(), cited, invalid


def _table_summary(table: PatentTableSupplement) -> str:
    title = table.table_title or "未命名表格"
    header = " / ".join(table.columns[:6])
    rows = []
    for row in table.rows[:3]:
        pairs = [f"{key}={value}" for key, value in list(row.items())[:4]]
        if pairs:
            rows.append("；".join(pairs))
    row_text = " | ".join(rows)
    if header and row_text:
        return f"{title}（列: {header}）: {row_text}"
    if row_text:
        return f"{title}: {row_text}"
    return title


def _group_evidences_by_patent(evidences: list[PatentEvidence], *, max_patents: int | None = None) -> list[tuple[str, list[PatentEvidence]]]:
    grouped: list[tuple[str, list[PatentEvidence]]] = []
    by_patent: dict[str, list[PatentEvidence]] = {}
    for evidence in evidences:
        patent_id = str(evidence.canonical_patent_id or "").strip().upper()
        if not patent_id:
            continue
        if patent_id not in by_patent:
            if max_patents is not None and len(grouped) >= max_patents:
                continue
            by_patent[patent_id] = []
            grouped.append((patent_id, by_patent[patent_id]))
        by_patent[patent_id].append(evidence)
    return grouped


def _reference_summary(
    *,
    retrieval_outcome: PatentRetrievalOutcome,
    patent_id: str,
) -> str:
    patent_id = str(patent_id or "").strip().upper()
    if not patent_id:
        return ""
    original_link = next(
        (
            dict(item)
            for item in list(retrieval_outcome.original_links)
            if isinstance(item, dict) and str(item.get("canonical_patent_id") or "").strip().upper() == patent_id
        ),
        {},
    )
    reference_object = next(
        (
            dict(item)
            for item in list(retrieval_outcome.reference_objects)
            if isinstance(item, dict) and str(item.get("canonical_patent_id") or "").strip().upper() == patent_id
        ),
        {},
    )
    reference_link = next(
        (
            dict(item)
            for item in list(retrieval_outcome.reference_links)
            if isinstance(item, dict) and str(item.get("canonical_patent_id") or "").strip().upper() == patent_id
        ),
        {},
    )
    section = str(original_link.get("section") or "").strip().lower()
    claim_number = original_link.get("claim_number")
    paragraph_id = str(original_link.get("paragraph_id") or "").strip()
    if section == "claim" and claim_number is not None:
        anchor = f"Claim {claim_number}"
    elif paragraph_id:
        anchor = f"Paragraph {paragraph_id}"
    else:
        anchor = str(reference_object.get("section_label") or reference_link.get("label") or section or "原文定位").strip()
    viewer_uri = str(
        original_link.get("viewer_uri")
        or reference_object.get("viewer_uri")
        or reference_link.get("viewer_uri")
        or ""
    ).strip()
    if viewer_uri:
        return f"{anchor} | {viewer_uri}"
    return anchor


def _build_stage4_evidence_section(
    *,
    retrieval_outcome: PatentRetrievalOutcome,
    allowed_patent_ids: list[str],
) -> tuple[list[str], dict[str, int]]:
    allowed_set = set(_normalize_patent_id_list(allowed_patent_ids))
    filtered_evidences = [
        evidence
        for evidence in list(retrieval_outcome.evidences)
        if not allowed_set or _normalize_patent_id(evidence.canonical_patent_id) in allowed_set
    ]
    grouped_evidences = _group_evidences_by_patent(
        filtered_evidences,
        max_patents=len(allowed_patent_ids) if allowed_patent_ids else None,
    )
    snippets_per_patent = _env_int("PATENT_STAGE4_EVIDENCE_SNIPPETS_PER_PATENT", 3, minimum=1, maximum=10)
    snippet_max_chars = _env_int("PATENT_STAGE4_EVIDENCE_SNIPPET_MAX_CHARS", 600, minimum=80, maximum=5000)
    abstract_max_chars = _env_int("PATENT_STAGE4_EVIDENCE_ABSTRACT_MAX_CHARS", 400, minimum=80, maximum=5000)
    tables_per_patent = _env_int("PATENT_STAGE4_EVIDENCE_TABLES_PER_PATENT", 2, minimum=1, maximum=10)
    table_max_chars = _env_int("PATENT_STAGE4_EVIDENCE_TABLE_MAX_CHARS", 400, minimum=80, maximum=5000)

    lines = ["", "6. 检索证据（已按专利归并）："]
    snippet_count = 0
    table_count = 0
    for index, (_, patent_evidences) in enumerate(grouped_evidences, start=1):
        evidence = patent_evidences[0]
        lines.append(f"{index}. 专利: {evidence.title} ({evidence.canonical_patent_id})")
        if evidence.abstract_text:
            lines.append(f"   摘要: {_truncate(evidence.abstract_text, limit=abstract_max_chars)}")
        for snippet in patent_evidences[:snippets_per_patent]:
            if snippet.matched_section_label and snippet.matched_snippet:
                snippet_count += 1
                lines.append(
                    f"   命中片段[{snippet.matched_section_label}]: "
                    f"{_truncate(snippet.matched_snippet, limit=snippet_max_chars)}"
                )
        for table in evidence.table_supplements[:tables_per_patent]:
            table_count += 1
            lines.append(f"   表格: {_truncate(_table_summary(table), limit=table_max_chars)}")
        reference_summary = _reference_summary(retrieval_outcome=retrieval_outcome, patent_id=evidence.canonical_patent_id)
        if reference_summary:
            lines.append(f"   原文定位: {reference_summary}")

    evidence_text = "\n".join(lines)
    return lines, {
        "evidence_chars": len(evidence_text),
        "evidence_patent_count": len(grouped_evidences),
        "evidence_item_count": len(filtered_evidences),
        "snippet_count": snippet_count,
        "table_count": table_count,
    }


def _is_boilerplate_snippet(evidence: PatentEvidence) -> bool:
    section_type = str(evidence.matched_section_type or "").strip().lower()
    section_label = str(evidence.matched_section_label or "").strip().lower()
    snippet = str(evidence.matched_snippet or "").strip().lower()
    if section_type in {"background", "legal"}:
        return True
    if any(keyword in section_label for keyword in ("background", "legal", "背景", "法律")):
        return True
    boilerplate_markers = (
        "背景技术",
        "技术领域",
        "现有技术",
        "本发明旨在",
        "本发明提供",
        "本申请公开",
        "保护范围",
        "法律",
        "本领域技术人员",
    )
    return any(marker in snippet for marker in boilerplate_markers)


def build_fallback_patent_answer(
    *,
    question: str,
    retrieval_outcome: PatentRetrievalOutcome,
    context: dict[str, Any] | None = None,
) -> str:
    context = dict(context or {})
    stage1_deep_answer = str(context.get("stage1_deep_answer") or "").strip()
    if not retrieval_outcome.evidences:
        return stage1_deep_answer or "Patent retrieval found no matching results."
    grouped_evidences = _group_evidences_by_patent(list(retrieval_outcome.evidences))
    lines = [f"围绕“{question}”，当前检索命中了 {len(grouped_evidences)} 篇核心专利证据："]
    if stage1_deep_answer:
        lines.append(f"阶段1预分析：{stage1_deep_answer}")
    graph_kb = dict(context.get("graph_kb") or {})
    if graph_kb:
        lines.append("图谱辅助线索：以下结构化线索仅用于补充检索定位，不作为可引用证据。")
        graph_mode = str(graph_kb.get("mode") or "").strip()
        if graph_mode:
            lines.append(f"图谱模式：{graph_mode}")
        graph_candidates = _normalize_patent_id_list(graph_kb.get("stage4_graph_candidate_patent_ids"))
        if graph_candidates:
            lines.append(f"图谱候选专利（仅供定位，不作为引用）：{', '.join(graph_candidates)}")
        fact_block = " ".join(str(graph_kb.get("stage4_fact_block") or "").split()).strip()
        if fact_block:
            lines.append(f"图谱事实：{fact_block}")
    lines.append("证据口径：实质技术证据优先采用权利要求/说明书命中片段、摘要和同专利表格；背景/法律套话仅作背景说明，不作为核心结论依据。")
    for index, (_, patent_evidences) in enumerate(grouped_evidences, start=1):
        evidence = patent_evidences[0]
        segments = []
        boilerplate_labels: list[str] = []
        technical_snippets = [item for item in patent_evidences if not _is_boilerplate_snippet(item)]
        for snippet in technical_snippets[:2]:
            if snippet.matched_section_label and snippet.matched_snippet:
                segments.append(f"{snippet.matched_section_label}命中片段：{_truncate(snippet.matched_snippet)}")
        for snippet in patent_evidences:
            if not _is_boilerplate_snippet(snippet):
                continue
            label = str(snippet.matched_section_label or snippet.matched_section_type or "未命名片段").strip()
            if label and label not in boilerplate_labels:
                boilerplate_labels.append(label)
        if not segments and evidence.abstract_text:
            segments.append(f"摘要信号：{_truncate(evidence.abstract_text)}")
        if evidence.table_supplements:
            segments.append(f"表格补充：{_truncate(_table_summary(evidence.table_supplements[0]), limit=220)}")
        if boilerplate_labels:
            segments.append(f"背景/法律套话已降权：{', '.join(boilerplate_labels)}")
        if not segments:
            segments.append("当前仅命中专利元数据，尚未抽取到更具体的正文证据。")
        reference_summary = _reference_summary(retrieval_outcome=retrieval_outcome, patent_id=evidence.canonical_patent_id)
        if reference_summary:
            segments.append(f"原文定位：{reference_summary}")
        lines.append(
            f"{index}. 《{evidence.title}》(patent_id={evidence.canonical_patent_id})；实质技术证据：" + "；".join(segment for segment in segments if segment)
        )
    lines.append("综合判断：上述结论基于当前命中的说明书片段、摘要和同专利表格数据整理而成，适合先做技术替代方向筛查，再继续下钻原文核验。")
    return "\n".join(lines)


@dataclass
class PatentAnswerBuilder:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 30.0
    transport: httpx.BaseTransport | None = None
    http_client: Any | None = None
    _client: Any = field(init=False, repr=False)
    _owns_http_client: bool = field(init=False, repr=False, default=False)

    def __post_init__(self) -> None:
        if self.transport is not None and self.http_client is not None:
            raise ValueError("transport cannot be combined with http_client")
        self._owns_http_client = self.http_client is None
        self._client = self.http_client or httpx.Client(timeout=self.timeout_seconds, transport=self.transport)
        transport = describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client)
        _LOGGER.info(
            "patent answer builder initialized model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s",
            self.model,
            self.base_url,
            self.timeout_seconds,
            transport.get("client_owner"),
            transport.get("shared_client_id"),
        )

    def close(self) -> None:
        if self._owns_http_client:
            self._client.close()

    def __call__(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any] | None = None,
    ) -> str:
        context = dict(context or {})
        allowed_patent_ids = _normalize_patent_id_list(
            list(context.get("allowed_patent_ids") or []) or list(retrieval_outcome.references)
        )
        if not self.base_url or not self.model:
            _LOGGER.warning(
                "patent answer builder missing llm config api_key_set=%s base_url_set=%s model=%s; using fallback answer",
                bool(self.api_key),
                bool(self.base_url),
                self.model,
            )
            return self._build_sanitized_fallback_answer(
                question=question,
                retrieval_outcome=retrieval_outcome,
                context=context,
                allowed_patent_ids=allowed_patent_ids,
            )
        prompt, prompt_metadata = self._build_prompt_with_metadata(
            question=question,
            retrieval_outcome=retrieval_outcome,
            context=context,
        )
        _LOGGER.info(
            "patent answer builder prompt prepared prompt_chars=%s evidence_count=%s evidence_chars=%s allowed_patent_ids=%s",
            len(prompt),
            int(prompt_metadata.get("evidence_item_count", 0)),
            int(prompt_metadata.get("evidence_chars", 0)),
            allowed_patent_ids,
        )
        min_distinct_citations = _resolve_min_distinct_citations(context=context, allowed_patent_ids=allowed_patent_ids)
        _LOGGER.info(
            "patent answer builder request start model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s allowed_patent_ids=%s evidence_count=%s evidence_chars=%s prompt_chars=%s",
            self.model,
            self.base_url,
            self.timeout_seconds,
            describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client).get("client_owner"),
            describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client).get("shared_client_id"),
            allowed_patent_ids,
            int(prompt_metadata.get("evidence_item_count", 0)),
            int(prompt_metadata.get("evidence_chars", 0)),
            len(prompt),
        )
        request_url = f"{self.base_url.rstrip('/')}/chat/completions"
        model_call_started = time.perf_counter()
        response = None
        try:
            headers = auth_headers(self.api_key)
            payload = self._build_request_payload(
                prompt=prompt,
                question=question,
                evidence_count=int(prompt_metadata.get("evidence_patent_count", 0)),
                allowed_patent_ids=allowed_patent_ids,
                stream=False,
                min_distinct_citations=min_distinct_citations,
            )
            payload_body = json.dumps(payload, ensure_ascii=False)
            _LOGGER.info(
                "patent answer builder request payload ready model=%s stream=%s message_count=%s payload_chars=%s allowed_patent_ids=%s",
                self.model,
                False,
                len(payload.get("messages") or []),
                len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
                allowed_patent_ids,
            )
            model_call_started = log_model_call_start(
                _LOGGER,
                component="llm_patent_answer",
                model=self.model,
                endpoint=request_url,
                auth_mode=auth_mode_label(),
                stream=False,
                message_count=len(list(payload.get("messages") or [])),
                message_chars_value=message_chars(payload.get("messages")),
                timeout_seconds=self.timeout_seconds,
                key_present=bool(self.api_key),
            )
            request_started = time.perf_counter()
            request = None
            if hasattr(self._client, "build_request"):
                request = self._client.build_request(
                    "POST",
                    request_url,
                    headers=headers,
                    content=payload_body.encode("utf-8"),
                )
                _LOGGER.info(
                    "patent answer builder request object built method=%s url=%s elapsed_ms=%.3f content_length=%s",
                    getattr(request, "method", "POST"),
                    str(getattr(request, "url", request_url)),
                    (time.perf_counter() - request_started) * 1000,
                    str(getattr(request, "headers", {}).get("content-length") or ""),
                )
            _LOGGER.info(
                "patent answer builder request dispatch start timeout_seconds=%s elapsed_ms=%.3f transport=%s",
                self.timeout_seconds,
                (time.perf_counter() - request_started) * 1000,
                "send" if request is not None and hasattr(self._client, "send") else "post",
            )
            request_timeout = build_patent_request_timeout(
                http_client=self._client,
                timeout_seconds=self.timeout_seconds,
            )
            dispatch_started = time.perf_counter()
            if request is not None and hasattr(self._client, "send"):
                try:
                    response = self._client.send(request, stream=False, timeout=request_timeout)
                except TypeError as exc:
                    if "timeout" not in str(exc):
                        record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                        raise
                    response = self._client.send(request, stream=False)
                except Exception as exc:
                    record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                    raise
            else:
                try:
                    response = self._client.post(
                        request_url,
                        headers=headers,
                        content=payload_body.encode("utf-8"),
                        timeout=request_timeout,
                    )
                except TypeError as exc:
                    if "content" not in str(exc):
                        record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                        raise
                    response = self._client.post(
                        request_url,
                        headers=headers,
                        json=payload,
                        timeout=request_timeout,
                    )
                except Exception as exc:
                    record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                    raise
            record_patent_dispatch_success(http_client=self._client, started_at=dispatch_started)
            _LOGGER.info(
                "patent answer builder request dispatch returned status_code=%s elapsed_ms=%.3f",
                getattr(response, "status_code", ""),
                (time.perf_counter() - request_started) * 1000,
            )
            _LOGGER.info(
                "patent answer builder llm response headers received status_code=%s elapsed_ms=%.3f content_length=%s",
                getattr(response, "status_code", ""),
                (time.perf_counter() - request_started) * 1000,
                str(response.headers.get("content-length") or ""),
            )
            response.raise_for_status()
            payload = response.json()
            choices = list(payload.get("choices") or [])
            message = dict((choices[0] or {}).get("message") or {}) if choices else {}
            content = str(message.get("content") or "").strip()
            _LOGGER.info(
                "patent answer builder llm response body parsed response_chars=%s elapsed_ms=%.3f",
                len(content),
                (time.perf_counter() - request_started) * 1000,
            )
            if content:
                log_model_call_success(
                    _LOGGER,
                    component="llm_patent_answer",
                    model=self.model,
                    endpoint=request_url,
                    started_at=model_call_started,
                    auth_mode=auth_mode_label(),
                    status_code=getattr(response, "status_code", None),
                    stream=False,
                    answer_chars=len(content),
                    fallback=False,
                )
                _LOGGER.info(
                    "patent answer builder llm response received chars=%s elapsed_ms=%.3f",
                    len(content),
                    (time.perf_counter() - request_started) * 1000,
                )
                return sanitize_patent_id_citations(content, allowed_patent_ids=allowed_patent_ids)[0]
            _LOGGER.warning("patent answer builder returned empty content")
            log_model_call_success(
                _LOGGER,
                component="llm_patent_answer",
                model=self.model,
                endpoint=request_url,
                started_at=model_call_started,
                auth_mode=auth_mode_label(),
                status_code=getattr(response, "status_code", None),
                stream=False,
                answer_chars=0,
                fallback=True,
            )
            raise UpstreamCallError.llm_unavailable(
                stage="stage4",
                status_code=getattr(response, "status_code", None),
            )
        except UpstreamCallError:
            raise
        except Exception as exc:
            log_model_call_failed(
                _LOGGER,
                component="llm_patent_answer",
                model=self.model,
                endpoint=request_url,
                started_at=model_call_started,
                exc=exc,
                auth_mode=auth_mode_label(),
                status_code=getattr(response, "status_code", None),
                stream=False,
                fallback=True,
                reason="request_failed",
            )
            _LOGGER.warning("patent answer builder request failed: %s", exc)
            raise UpstreamCallError.llm_unavailable(
                stage="stage4",
                status_code=status_code_from_exception(exc),
            ) from exc

    def stream(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any] | None = None,
        should_cancel: Any | None = None,
    ) -> Iterator[str]:
        context = dict(context or {})
        allowed_patent_ids = _normalize_patent_id_list(
            list(context.get("allowed_patent_ids") or []) or list(retrieval_outcome.references)
        )
        if not self.base_url or not self.model:
            _LOGGER.warning(
                "patent answer builder missing llm config api_key_set=%s base_url_set=%s model=%s; using fallback streamed answer",
                bool(self.api_key),
                bool(self.base_url),
                self.model,
            )
            fallback = self._build_sanitized_fallback_answer(
                question=question,
                retrieval_outcome=retrieval_outcome,
                context=context,
                allowed_patent_ids=allowed_patent_ids,
            )
            if fallback:
                yield fallback
            return
        prompt, prompt_metadata = self._build_prompt_with_metadata(
            question=question,
            retrieval_outcome=retrieval_outcome,
            context=context,
        )
        _LOGGER.info(
            "patent answer builder stream prompt prepared prompt_chars=%s evidence_count=%s evidence_chars=%s allowed_patent_ids=%s",
            len(prompt),
            int(prompt_metadata.get("evidence_item_count", 0)),
            int(prompt_metadata.get("evidence_chars", 0)),
            allowed_patent_ids,
        )
        min_distinct_citations = _resolve_min_distinct_citations(context=context, allowed_patent_ids=allowed_patent_ids)
        _LOGGER.info(
            "patent answer builder stream start model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s allowed_patent_ids=%s evidence_count=%s evidence_chars=%s prompt_chars=%s",
            self.model,
            self.base_url,
            self.timeout_seconds,
            describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client).get("client_owner"),
            describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client).get("shared_client_id"),
            allowed_patent_ids,
            int(prompt_metadata.get("evidence_item_count", 0)),
            int(prompt_metadata.get("evidence_chars", 0)),
            len(prompt),
        )
        streamed_any = False
        stream_started = time.perf_counter()
        first_chunk_logged = False
        first_payload_logged = False
        first_line_logged = False
        chunk_count = 0
        answer_chars = 0
        request_url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = auth_headers(self.api_key)
        payload = self._build_request_payload(
            prompt=prompt,
            question=question,
            evidence_count=int(prompt_metadata.get("evidence_patent_count", 0)),
            allowed_patent_ids=allowed_patent_ids,
            stream=True,
            min_distinct_citations=min_distinct_citations,
        )
        payload_body = json.dumps(payload, ensure_ascii=False)
        _LOGGER.info(
            "patent answer builder stream request payload ready model=%s stream=%s message_count=%s payload_chars=%s allowed_patent_ids=%s",
            self.model,
            True,
            len(payload.get("messages") or []),
            len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            allowed_patent_ids,
        )
        model_call_started = log_model_call_start(
            _LOGGER,
            component="llm_patent_answer",
            model=self.model,
            endpoint=request_url,
            auth_mode=auth_mode_label(),
            stream=True,
            message_count=len(list(payload.get("messages") or [])),
            message_chars_value=message_chars(payload.get("messages")),
            timeout_seconds=self.timeout_seconds,
            key_present=bool(self.api_key),
        )
        response = None
        try:
            if callable(should_cancel) and should_cancel():
                _LOGGER.info("patent answer builder stream cancelled before request dispatch")
                return
            request = None
            if hasattr(self._client, "build_request"):
                request = self._client.build_request(
                    "POST",
                    request_url,
                    headers=headers,
                    content=payload_body.encode("utf-8"),
                )
                _LOGGER.info(
                    "patent answer builder stream request object built method=%s url=%s elapsed_ms=%.3f content_length=%s",
                    getattr(request, "method", "POST"),
                    str(getattr(request, "url", request_url)),
                    (time.perf_counter() - stream_started) * 1000,
                    str(getattr(request, "headers", {}).get("content-length") or ""),
                )
            _LOGGER.info(
                "patent answer builder stream request dispatch start timeout_seconds=%s elapsed_ms=%.3f transport=%s",
                self.timeout_seconds,
                (time.perf_counter() - stream_started) * 1000,
                "send" if request is not None and hasattr(self._client, "send") else "stream",
            )
            request_timeout = build_patent_request_timeout(
                http_client=self._client,
                timeout_seconds=self.timeout_seconds,
                stream=True,
            )
            dispatch_started = time.perf_counter()
            if request is not None and hasattr(self._client, "send"):
                try:
                    response_cm = self._client.send(request, stream=True, timeout=request_timeout)
                except TypeError as exc:
                    if "timeout" not in str(exc):
                        record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                        raise
                    response_cm = self._client.send(request, stream=True)
                except Exception as exc:
                    record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                    raise
                response_context = closing(response_cm)
            else:
                try:
                    response_cm = self._client.stream(
                        "POST",
                        request_url,
                        headers=headers,
                        content=payload_body.encode("utf-8"),
                        timeout=request_timeout,
                    )
                except TypeError as exc:
                    if "content" not in str(exc):
                        record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                        raise
                    response_cm = self._client.stream(
                        "POST",
                        request_url,
                        headers=headers,
                        json=payload,
                        timeout=request_timeout,
                    )
                except Exception as exc:
                    record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                    raise
                response_context = response_cm
            with response_context as response:
                record_patent_dispatch_success(http_client=self._client, started_at=dispatch_started)
                _LOGGER.info(
                    "patent answer builder stream request dispatch returned status_code=%s elapsed_ms=%.3f",
                    getattr(response, "status_code", ""),
                    (time.perf_counter() - stream_started) * 1000,
                )
                response.raise_for_status()
                _LOGGER.info(
                    "patent answer builder stream response headers received status_code=%s elapsed_ms=%.3f content_type=%s",
                    getattr(response, "status_code", ""),
                    (time.perf_counter() - stream_started) * 1000,
                    str(response.headers.get("content-type") or ""),
                )
                for line in response.iter_lines():
                    if callable(should_cancel) and should_cancel():
                        _LOGGER.info(
                            "patent answer builder stream cancelled chunk_count=%s answer_chars=%s elapsed_ms=%.3f",
                            chunk_count,
                            answer_chars,
                            (time.perf_counter() - stream_started) * 1000,
                        )
                        return
                    if line is None:
                        continue
                    line_text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
                    stripped = line_text.strip()
                    if not stripped:
                        continue
                    if not first_line_logged:
                        first_line_logged = True
                        _LOGGER.info(
                            "patent answer builder stream first response line received line_chars=%s elapsed_ms=%.3f",
                            len(stripped),
                            (time.perf_counter() - stream_started) * 1000,
                        )
                    if stripped.startswith("data:"):
                        stripped = stripped[5:].strip()
                    if not stripped:
                        continue
                    if stripped == "[DONE]":
                        break
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        _LOGGER.debug("patent answer builder ignored non-json stream line: %s", _truncate(stripped))
                        continue
                    if not first_payload_logged:
                        first_payload_logged = True
                        _LOGGER.info(
                            "patent answer builder stream first payload received payload_chars=%s elapsed_ms=%.3f",
                            len(stripped),
                            (time.perf_counter() - stream_started) * 1000,
                        )
                    for fragment in _extract_stream_fragments(payload):
                        if not fragment:
                            continue
                        streamed_any = True
                        chunk_count += 1
                        answer_chars += len(fragment)
                        if not first_chunk_logged:
                            first_chunk_logged = True
                            _LOGGER.info(
                                "patent answer builder stream first chunk chunk_chars=%s elapsed_ms=%.3f",
                                len(fragment),
                                (time.perf_counter() - stream_started) * 1000,
                            )
                        yield fragment
                        if callable(should_cancel) and should_cancel():
                            _LOGGER.info(
                                "patent answer builder stream cancelled chunk_count=%s answer_chars=%s elapsed_ms=%.3f",
                                chunk_count,
                                answer_chars,
                                (time.perf_counter() - stream_started) * 1000,
                            )
                            return
            if streamed_any:
                log_model_call_success(
                    _LOGGER,
                    component="llm_patent_answer",
                    model=self.model,
                    endpoint=request_url,
                    started_at=model_call_started,
                    auth_mode=auth_mode_label(),
                    status_code=getattr(response, "status_code", None),
                    stream=True,
                    answer_chars=answer_chars,
                    chunk_count=chunk_count,
                    fallback=False,
                )
                _LOGGER.info(
                    "patent answer builder stream completed chunk_count=%s answer_chars=%s elapsed_ms=%.3f",
                    chunk_count,
                    answer_chars,
                    (time.perf_counter() - stream_started) * 1000,
                )
                return
            _LOGGER.warning("patent answer builder stream returned no content")
            log_model_call_success(
                _LOGGER,
                component="llm_patent_answer",
                model=self.model,
                endpoint=request_url,
                started_at=model_call_started,
                auth_mode=auth_mode_label(),
                status_code=getattr(response, "status_code", None),
                stream=True,
                answer_chars=0,
                chunk_count=0,
                fallback=True,
            )
            raise UpstreamCallError.llm_unavailable(
                stage="stage4",
                status_code=getattr(response, "status_code", None),
            )
        except UpstreamCallError:
            raise
        except Exception as exc:
            if streamed_any:
                log_model_call_failed(
                    _LOGGER,
                    component="llm_patent_answer",
                    model=self.model,
                    endpoint=request_url,
                    started_at=model_call_started,
                    exc=exc,
                    auth_mode=auth_mode_label(),
                    status_code=getattr(response, "status_code", None),
                    stream=True,
                    fallback=False,
                    reason="partial_stream_failed",
                )
                _LOGGER.warning("patent answer builder stream failed after partial content: %s", exc)
                raise UpstreamCallError.stream_interrupted(
                    stage="stage4",
                    status_code=status_code_from_exception(exc),
                ) from exc
            log_model_call_failed(
                _LOGGER,
                component="llm_patent_answer",
                model=self.model,
                endpoint=request_url,
                started_at=model_call_started,
                exc=exc,
                auth_mode=auth_mode_label(),
                status_code=getattr(response, "status_code", None),
                stream=True,
                fallback=True,
                reason="request_failed",
            )
            _LOGGER.warning("patent answer builder stream failed: %s", exc)
            raise UpstreamCallError.llm_unavailable(
                stage="stage4",
                status_code=status_code_from_exception(exc),
            ) from exc

    @staticmethod
    def from_env(*, http_client: Any | None = None) -> "PatentAnswerBuilder":
        return PatentAnswerBuilder(
            api_key=str(
                os.getenv("LLM_API_KEY")
                or ""
            ).strip(),
            base_url=str(
                os.getenv("LLM_BASE_URL")
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).strip(),
            model=str(
                os.getenv("LLM_MODEL")
                or "deepseek-v3.1"
            ).strip(),
            timeout_seconds=float(str(os.getenv("LLM_READ_TIMEOUT_SECONDS") or "30").strip()),
            http_client=http_client,
        )

    def _build_prompt(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any] | None,
    ) -> str:
        return self._build_prompt_with_metadata(
            question=question,
            retrieval_outcome=retrieval_outcome,
            context=context,
        )[0]

    def _build_prompt_with_metadata(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any] | None,
    ) -> tuple[str, dict[str, int]]:
        context = dict(context or {})
        allowed_patent_ids = _normalize_patent_id_list(
            list(context.get("allowed_patent_ids") or []) or list(retrieval_outcome.references)
        )
        min_distinct_citations = _resolve_min_distinct_citations(
            context=context,
            allowed_patent_ids=allowed_patent_ids,
        )
        context_lines: list[str] = []
        summary = dict(context.get("summary_for_llm") or context.get("summary") or {})
        short_summary = str(summary.get("short_summary") or "").strip()
        if short_summary:
            context_lines.extend(["", f"2. 会话摘要（仅用于承接上文指代）：{short_summary}"])
        stage1_deep_answer = str(context.get("stage1_deep_answer") or "").strip()
        if stage1_deep_answer:
            context_lines.extend(
                [
                    "",
                    "3. 阶段1预分析（仅供结构、比较维度和核验线索参考，不能直接当作事实来源）：",
                    stage1_deep_answer,
                ]
            )
        turns_source = context.get("recent_turns_for_llm") or context.get("chat_history") or []
        turns = [str(item.get("content") or "").strip() for item in list(turns_source)[-4:] if isinstance(item, dict)]
        if turns:
            context_lines.extend(["", "4. 最近对话（仅用于承接当前问题，不可覆盖专利证据）："])
            context_lines.extend(f"- {item}" for item in turns if item)
        if allowed_patent_ids:
            top5_reference_list = (
                f"**【专利公开号白名单 — 强制】** 引用格式 `(patent_id=公开号)`，"
                f"须与下列反引号内**逐字相同**。\n"
                f"**禁止**编造下表以外的公开号；无证据支撑处省略标注。\n\n"
                f"【合法公开号 — 与「支持性专利证据」中第 1…第 {len(allowed_patent_ids)} 件专利对应】\n\n"
            )
            for index, patent_id in enumerate(allowed_patent_ids, 1):
                top5_reference_list += f"{index}. `{patent_id}`\n"
            top5_reference_list += (
                "\n⭐ **引用**：含容量、倍率、循环等**数值**时优先依据证据中的"
                "「结构化性能表（_tables.json）」；句末标注 `(patent_id=…)`。\n"
            )
        else:
            top5_reference_list = "【专利证据】本轮无正文片段，请勿编造公开号。\n"
        graph_kb = dict(context.get("graph_kb") or {})
        graph_context_lines: list[str] = []
        if graph_kb:
            graph_context_lines.extend(["6. 图谱结构化辅助线索（不可直接当作专利证据引用，也不能把这些候选专利当作引用白名单）："])
            graph_mode = str(graph_kb.get("mode") or "").strip()
            if graph_mode:
                graph_context_lines.append(f"- 图谱模式：{graph_mode}")
            graph_candidates = _normalize_patent_id_list(graph_kb.get("stage4_graph_candidate_patent_ids"))
            if graph_candidates:
                graph_context_lines.append(f"- 图谱候选专利（非引用白名单）：{', '.join(graph_candidates)}")
            fact_block = str(graph_kb.get("stage4_fact_block") or "").strip()
            if fact_block:
                graph_context_lines.append("- 图谱事实：")
                graph_context_lines.extend(f"  {line}" for line in str(fact_block).splitlines() if str(line).strip())
        evidence_lines, evidence_metadata = _build_stage4_evidence_section(
            retrieval_outcome=retrieval_outcome,
            allowed_patent_ids=allowed_patent_ids,
        )
        min_distinct_citations_clause = (
            f"最终答案至少引用 {min_distinct_citations} 个不同公开号。"
            if min_distinct_citations > 0
            else ""
        )
        final_coverage_clause = (
            f"整段答案必须覆盖至少 {min_distinct_citations} 个不同公开号。"
            if min_distinct_citations > 0
            else ""
        )
        prompt = _render_prompt_template(
            DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE,
            user_question=_escape_prompt_value(question),
            deep_answer=_escape_prompt_value(stage1_deep_answer),
            context_block="\n".join(context_lines).strip(),
            graph_context_block="\n".join(graph_context_lines).strip(),
            evidence_documents=_escape_prompt_value("\n".join(evidence_lines).strip()),
            min_distinct_citations_clause=min_distinct_citations_clause,
            final_coverage_clause=final_coverage_clause,
            top5_references=_escape_prompt_value(top5_reference_list),
        ).strip()
        return prompt, evidence_metadata

    def _build_request_payload(
        self,
        *,
        prompt: str,
        question: str = "",
        evidence_count: int = 0,
        allowed_patent_ids: list[str],
        stream: bool,
        min_distinct_citations: int,
    ) -> dict[str, Any]:
        del allowed_patent_ids, min_distinct_citations
        sys_cite_mid = "- 在答案中标注 patent_id/公开号；若某件专利不相关，直接跳过不引用即可，无需在答案中说明"
        sys_cite_correct = (
            "- 使用专利证据中的具体数值（如导电率、活化能、掺杂量、温度等），并说明其物理意义\n"
            "- 含数值或强结论的论断须可溯源；`(patent_id=公开号)` 优先标在**段落末或要点末**（同一逻辑块、同一件专利一般 **1 次**），必要时同段内至多 **2 处**，避免一句一标"
        )
        sys_cite_fine = (
            "- **可溯源与成组标注**：优先段落末/要点末；**禁止**同一 `(patent_id=公开号)` 在连续多个短句末尾机械重复；引用件数须符合上文约束，"
            "**禁止**为凑件数编造白名单外公开号"
        )
        sys_cite_format = (
            "## 📚 专利公开号引用格式\n"
            "- 使用 `(patent_id=公开号)` 格式，公开号须与用户消息中的白名单**完全一致**\n"
            "- 每处引用需伴随机理解释或定量数据；遵守上文「按件综述式」密度与反机械重复规则"
        )
        system_prompt = _render_prompt_template(
            DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT,
            split_body_prefix="",
            user_question=_escape_prompt_value(str(question or "")),
            doping_warning=_escape_prompt_value(_build_patent_stage4_doping_warning(str(question or ""))),
            facts_based_warning=_escape_prompt_value(_build_patent_stage4_facts_based_warning()),
            sys_cite_mid=_escape_prompt_value(sys_cite_mid),
            sys_cite_correct=_escape_prompt_value(sys_cite_correct),
            cite_depth_instruction=_escape_prompt_value(_build_patent_stage4_cite_depth_instruction(evidence_count)),
            sys_cite_fine=_escape_prompt_value(sys_cite_fine),
            sys_cite_format=_escape_prompt_value(sys_cite_format),
        )
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "stream": bool(stream),
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": prompt},
            ],
        }
        controls = resolve_thinking_controls(
            stage=LLM_STAGE_STAGE4_FINAL_ANSWER,
            max_tokens=4096,
            stream=stream,
        )
        apply_openai_compatible_thinking(payload, controls)
        return payload

    def _build_sanitized_fallback_answer(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any],
        allowed_patent_ids: list[str],
    ) -> str:
        fallback = build_fallback_patent_answer(question=question, retrieval_outcome=retrieval_outcome, context=context)
        return sanitize_patent_id_citations(fallback, allowed_patent_ids=allowed_patent_ids)[0]
