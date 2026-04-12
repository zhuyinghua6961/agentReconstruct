from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import Any, Callable

import httpx

from server.patent.pdf_contract import (
    CompareBudgetError,
    MULTI_DOC_HEADER_PATTERN,
    PDF_QA_SYSTEM_MESSAGE,
    build_compare_failure_message,
    build_extractive_fallback_summary,
    build_kb_section,
    build_patent_pdf_answer_prompt,
    detect_targeted_document_index,
    format_multi_pdf_sections,
    is_compare_question,
    is_summary_question,
    smart_truncate_pdf_content,
    validate_compare_context,
)
from server.patent.file_models import PatentFileContract
from server.patent.summary_formatting import (
    LITERATURE_SUMMARY_NOTE,
    PRIMARY_SUMMARY_HEADINGS,
    classify_summary_answer,
    extract_support_points,
)
from server.patent.streaming import emit_text_chunks, iter_text_output
from server.services.mode_profiles import get_patent_mode_profile

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover - dependency guard
    fitz = None


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


_WHITESPACE_PATTERN = re.compile(r"\s+")
_KB_BOUNDARY_PLACEHOLDER = "当前无额外知识库验证结果。"
_MAX_COMPARE_DOCUMENTS = 4


def _collapse_whitespace(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", str(value or "")).strip()


def _truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _find_markdown_support_points(text: str, *, max_items: int = 3, min_chars: int = 18) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw_items = re.split(r"(?<=[。！？.!?])\s+|\n+", normalized)
    points: list[str] = []
    for item in raw_items:
        line = _collapse_whitespace(re.sub(r"^[#>\-\*\d\.\)\s]+", "", item))
        if len(line) < min_chars:
            continue
        if line in points:
            continue
        points.append(_truncate(line, 220))
        if len(points) >= max_items:
            break
    return points


def _find_section_position(text: str, patterns: tuple[str, ...], *, last_end: int) -> int:
    normalized = str(text or "")
    best_position = -1
    best_end = -1
    for pattern in patterns:
        matched = re.search(pattern, normalized, flags=re.MULTILINE | re.IGNORECASE)
        if matched is None:
            continue
        if matched.start() <= last_end:
            continue
        if best_position < 0 or matched.start() < best_position:
            best_position = matched.start()
            best_end = matched.end()
    return best_position if best_end >= 0 else -1


def _has_four_block_sections(text: str) -> bool:
    normalized = str(text or "")
    patterns = (
        (r"(^|\n)\s*(?:#{1,6}\s*)?结论\s*[：:]?",),
        (r"(^|\n)\s*(?:#{1,6}\s*)?证据\s*[：:]?",),
        (r"(^|\n)\s*(?:#{1,6}\s*)?对比\s*[：:]?",),
        (r"(^|\n)\s*(?:#{1,6}\s*)?限制\s*[：:]?",),
    )
    last_end = -1
    for group in patterns:
        position = _find_section_position(normalized, group, last_end=last_end)
        if position < 0:
            return False
        last_end = position
    return True


def _has_literature_summary_sections(text: str) -> bool:
    normalized = str(text or "")
    patterns = (
        (r"(^|\n)\s*(?:#{1,6}\s*)?研究目的和背景\s*[：:]?",),
        (r"(^|\n)\s*(?:#{1,6}\s*)?研究方法/实验设计\s*[：:]?",),
        (r"(^|\n)\s*(?:#{1,6}\s*)?主要发现和结果\s*[：:]?",),
        (r"(^|\n)\s*(?:#{1,6}\s*)?结论和意义\s*[：:]?",),
    )
    last_end = -1
    for group in patterns:
        position = _find_section_position(normalized, group, last_end=last_end)
        if position < 0:
            return False
        last_end = position
    return True


def _ensure_four_block_pdf_answer_structure(
    *,
    answer: str,
    prepared_pdf_text: str,
    include_kb: bool,
    route_hint: str = "pdf_qa",
    source_scope: str = "pdf",
) -> str:
    normalized_answer = str(answer or "").strip()
    if not normalized_answer:
        return normalized_answer
    if _has_four_block_sections(normalized_answer):
        return normalized_answer

    evidence_points = _find_markdown_support_points(prepared_pdf_text, max_items=3)
    if not evidence_points:
        evidence_points = _find_markdown_support_points(normalized_answer, max_items=3, min_chars=10)
    if not evidence_points:
        evidence_points = ["当前可读原文证据有限，仅能保留模型回答中的主结论。"]

    hybrid_mode = str(route_hint or "pdf_qa").strip().lower() == "hybrid_qa"
    normalized_scope = str(source_scope or "pdf").strip() or "pdf"
    comparison_lines = (
        [
            "- 当前为混合问答中的 PDF 证据子结论；可用于后续与表格或知识库交叉验证，不能单独替代全局综合结论。",
            f"- 当前 source_scope={normalized_scope}；本段只描述这份 PDF 原文能够直接支持的对照点。",
        ]
        if hybrid_mode
        else ["- PDF中未提供跨文献对比对象；当前回答仅基于单篇文件证据。"]
    )
    limitation_lines = (
        [
            "- 当前结论仅基于本次上传 PDF 的可读原文整理，仍需与其他已选文件或知识库证据综合判断。",
            (
                "- 知识库若参与，仅可用于验证已在 PDF 中出现的内容，不能补充新的文件结论。"
                if include_kb
                else "- 当前未引入知识库补充；若后续纳入其他来源，综合结论可能继续收敛。"
            ),
        ]
        if hybrid_mode
        else [
            "- 当前结论仅基于本次上传 PDF 的可读原文整理，未引入文件外新证据。",
            (
                "- 知识库若参与，仅可用于验证已在 PDF 中出现的内容，不能补充新的文件结论。"
                if include_kb
                else "- 当前未引入知识库补充，本回答不代表跨来源统一结论。"
            ),
        ]
    )

    sections = [
        "## 结论",
        normalized_answer,
        "",
        "## 证据",
        *[f"- {item}" for item in evidence_points],
        "",
        "## 对比",
        *comparison_lines,
        "",
        "## 限制",
        *limitation_lines,
    ]
    return "\n".join(sections).strip()


_LITERATURE_SUMMARY_NOTE = LITERATURE_SUMMARY_NOTE
_SUMMARY_LIMITATIONS_HEADING = "局限性"
_SUMMARY_SECTION_ORDER = (*PRIMARY_SUMMARY_HEADINGS, _SUMMARY_LIMITATIONS_HEADING)
_SUMMARY_SELECTION_ORDER = (
    "研究目的和背景",
    "研究方法/实验设计",
    "主要发现和结果",
    "局限性",
    "结论和意义",
)
_SUMMARY_SECTION_ALIASES = {
    "研究目的和背景": "研究目的和背景",
    "研究方法/实验设计": "研究方法/实验设计",
    "主要发现和结果": "主要发现和结果",
    "结论和意义": "结论和意义",
    "局限性": "局限性",
    "结论": "结论和意义",
    "证据": "主要发现和结果",
    "限制": "局限性",
}
_SUMMARY_SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "研究目的和背景": (
        "背景",
        "目的",
        "动机",
        "研究",
        "problem",
        "background",
        "motivation",
        "objective",
        "aim",
        "study",
    ),
    "研究方法/实验设计": (
        "方法",
        "实验",
        "流程",
        "设计",
        "采用",
        "对比",
        "测量",
        "表征",
        "method",
        "methods",
        "experimental",
        "experiment",
        "setup",
        "compare",
        "measure",
        "evaluate",
    ),
    "主要发现和结果": (
        "结果",
        "发现",
        "提升",
        "降低",
        "改善",
        "提高",
        "show",
        "result",
        "results",
        "improve",
        "improved",
        "gain",
        "lower",
        "higher",
        "better",
    ),
    "结论和意义": (
        "结论",
        "意义",
        "说明",
        "表明",
        "证明",
        "conclusion",
        "significance",
        "indicate",
        "suggest",
        "demonstrate",
        "help",
    ),
    "局限性": (
        "局限",
        "不足",
        "仍有限",
        "有限",
        "未来",
        "后续",
        "需要进一步",
        "有待",
        "future",
        "limit",
        "limited",
        "limitation",
        "further",
    ),
}
_SUMMARY_SECTION_FALLBACKS = {
    "研究目的和背景": "PDF中未提及足够的研究背景或研究目的信息。",
    "研究方法/实验设计": "PDF中未提及足够的研究方法或实验设计细节。",
    "主要发现和结果": "PDF中未提及足够的主要发现或结果数据。",
    "结论和意义": "PDF中未提及足够的结论或研究意义描述。",
    "局限性": "PDF中未提及明确的局限性或后续工作说明。",
}


def _is_aligned_pdf_summary_request(*, route_hint: str, source_scope: str) -> bool:
    normalized_route = str(route_hint or "").strip().lower()
    normalized_scope = str(source_scope or "").strip().lower()
    return normalized_route == "pdf_qa" and normalized_scope == "pdf"


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


def _select_literature_points(
    points: list[str],
    *,
    keywords: tuple[str, ...],
    max_items: int,
    allow_numeric: bool = False,
) -> list[str]:
    selected: list[str] = []
    for point in points:
        normalized = str(point or "").strip()
        if not normalized:
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


def _strip_multi_doc_headers(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines = [line for line in normalized.split("\n") if not MULTI_DOC_HEADER_PATTERN.fullmatch(line.strip())]
    return "\n".join(cleaned_lines).strip()


def _match_summary_heading(line: str) -> str | None:
    normalized = str(line or "").strip()
    if not normalized:
        return None
    normalized = re.sub(r"^#{1,6}\s*", "", normalized).strip()
    normalized = re.sub(r"\s*[：:]\s*$", "", normalized).strip()
    return _SUMMARY_SECTION_ALIASES.get(normalized)


def _normalize_summary_body(body: str) -> str:
    lines = str(body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(line.rstrip() for line in lines).strip()


def _extract_summary_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_heading: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        nonlocal current_heading, current_lines
        if current_heading is None:
            current_lines = []
            return
        body = _normalize_summary_body("\n".join(current_lines))
        if body:
            existing = sections.get(current_heading, "")
            sections[current_heading] = body if not existing else f"{existing}\n{body}".strip()
        current_lines = []

    for raw_line in _strip_multi_doc_headers(text).splitlines():
        if raw_line.lstrip().startswith("注*："):
            continue
        heading = _match_summary_heading(raw_line)
        if heading is not None:
            _flush()
            current_heading = heading
            continue
        if current_heading is not None:
            current_lines.append(raw_line)
    _flush()
    return sections


def _body_has_support(body: str) -> bool:
    normalized = _normalize_summary_body(body)
    if not normalized:
        return False
    if "PDF中未提及" in normalized or "原文证据不足" in normalized:
        return True
    return bool(extract_support_points(normalized, max_items=8, min_chars=10))


def _clean_summary_source_text(text: str) -> str:
    normalized = _strip_multi_doc_headers(text)
    cleaned_lines = []
    for raw_line in normalized.splitlines():
        if raw_line.lstrip().startswith("注*："):
            continue
        if _match_summary_heading(raw_line) is not None:
            continue
        cleaned_lines.append(raw_line)
    return "\n".join(cleaned_lines).strip()


def _collect_summary_points(text: str, *, max_items: int, min_chars: int) -> list[str]:
    return extract_support_points(_clean_summary_source_text(text), max_items=max_items, min_chars=min_chars)


def _point_matches_heading(point: str, heading: str) -> bool:
    normalized = str(point or "").strip().lower()
    return bool(normalized) and any(keyword in normalized for keyword in _SUMMARY_SECTION_KEYWORDS.get(heading, ()))


def _pick_summary_points(
    *,
    heading: str,
    answer_points: list[str],
    prepared_points: list[str],
    used_points: set[str],
    max_items: int,
    allow_general_fallback: bool,
) -> list[str]:
    selected: list[str] = []

    def _try_add(point: str) -> bool:
        normalized = str(point or "").strip()
        if not normalized or normalized in used_points or normalized in selected:
            return False
        selected.append(normalized)
        used_points.add(normalized)
        return len(selected) >= max_items

    for pool in (answer_points, prepared_points):
        for point in pool:
            if not _point_matches_heading(point, heading):
                continue
            if _try_add(point):
                return selected

    if not allow_general_fallback:
        return selected

    if heading == "研究目的和背景":
        for point in [*answer_points, *prepared_points]:
            if _try_add(point):
                return selected
    elif heading == "结论和意义":
        for point in [*reversed(answer_points), *reversed(prepared_points)]:
            if _try_add(point):
                return selected

    return selected


def _build_summary_section_body(
    *,
    heading: str,
    sections: dict[str, str],
    answer_points: list[str],
    prepared_points: list[str],
    used_points: set[str],
    allow_general_fallback: bool,
) -> str:
    body = _normalize_summary_body(sections.get(heading, ""))
    if _body_has_support(body):
        return body
    points = _pick_summary_points(
        heading=heading,
        answer_points=answer_points,
        prepared_points=prepared_points,
        used_points=used_points,
        max_items=2 if heading != "主要发现和结果" else 3,
        allow_general_fallback=allow_general_fallback,
    )
    if points:
        return "\n".join(f"- {point}" for point in points)
    return f"- {_SUMMARY_SECTION_FALLBACKS[heading]}"


def _build_repaired_literature_summary(
    *,
    answer: str,
    prepared_pdf_text: str,
    use_model_content: bool,
    allow_general_fallback: bool,
) -> str:
    sections = _extract_summary_sections(answer) if use_model_content else {}
    answer_points = _collect_summary_points(answer, max_items=10, min_chars=10) if use_model_content else []
    prepared_points = _collect_summary_points(prepared_pdf_text, max_items=10, min_chars=12)
    used_points: set[str] = set()
    section_bodies: dict[str, str] = {}
    for heading in _SUMMARY_SELECTION_ORDER:
        section_bodies[heading] = _build_summary_section_body(
            heading=heading,
            sections=sections,
            answer_points=answer_points,
            prepared_points=prepared_points,
            used_points=used_points,
            allow_general_fallback=allow_general_fallback,
        )
    lines: list[str] = []
    for heading in _SUMMARY_SECTION_ORDER:
        lines.append(f"## {heading}")
        lines.append(section_bodies[heading])
        lines.append("")
    lines.append(_LITERATURE_SUMMARY_NOTE)
    return "\n".join(lines).strip()


def _append_summary_tail_sections(
    *,
    answer: str,
    prepared_pdf_text: str,
) -> str:
    normalized_answer = str(answer or "").strip()
    if not normalized_answer:
        return _build_repaired_literature_summary(
            answer=answer,
            prepared_pdf_text=prepared_pdf_text,
            use_model_content=False,
            allow_general_fallback=False,
        )
    sections = _extract_summary_sections(normalized_answer)
    answer_points = _collect_summary_points(normalized_answer, max_items=10, min_chars=10)
    prepared_points = _collect_summary_points(prepared_pdf_text, max_items=10, min_chars=12)
    used_points: set[str] = set()
    extra_blocks: list[str] = []
    for heading in _SUMMARY_SECTION_ORDER:
        if heading in sections and _body_has_support(sections[heading]):
            continue
        extra_blocks.extend(
            [
                f"## {heading}",
                _build_summary_section_body(
                    heading=heading,
                sections=sections,
                answer_points=answer_points,
                prepared_points=prepared_points,
                used_points=used_points,
                allow_general_fallback=True,
            ),
                "",
            ]
        )
    cleaned = normalized_answer
    if _LITERATURE_SUMMARY_NOTE in cleaned:
        cleaned = cleaned.replace(_LITERATURE_SUMMARY_NOTE, "").rstrip()
    if extra_blocks:
        extra_text = "\n".join(extra_blocks).strip()
        cleaned = f"{cleaned}\n\n{extra_text}".strip()
    return f"{cleaned}\n\n{_LITERATURE_SUMMARY_NOTE}".strip()


def _ensure_legacy_literature_summary_structure(
    *,
    answer: str,
    prepared_pdf_text: str,
) -> str:
    normalized_answer = str(answer or "").strip()
    if not normalized_answer:
        return normalized_answer
    if _has_literature_summary_sections(normalized_answer):
        if _LITERATURE_SUMMARY_NOTE in normalized_answer:
            return normalized_answer
        return f"{normalized_answer}\n\n{_LITERATURE_SUMMARY_NOTE}".strip()

    prepared_points = _find_markdown_support_points(prepared_pdf_text, max_items=8, min_chars=12)
    answer_points = _find_markdown_support_points(normalized_answer, max_items=4, min_chars=10)
    all_points: list[str] = []
    for item in [*answer_points, *prepared_points]:
        if item and item not in all_points:
            all_points.append(item)

    background_points = _select_literature_points(
        all_points,
        keywords=("研究背景", "背景", "目的", "aim", "objective", "motivation", "introduc", "study", "studies", "investigate"),
        max_items=2,
    )
    method_points = _select_literature_points(
        all_points,
        keywords=("方法", "实验", "采用", "通过", "制备", "表征", "测试", "xrd", "tof-sims", "s-cells", "method", "methods", "experimental", "measure"),
        max_items=2,
    )
    result_points = _select_literature_points(
        all_points,
        keywords=("结果", "发现", "提升", "改善", "show", "shows", "result", "results", "retention", "efficiency", "ocv", "峰"),
        max_items=3,
        allow_numeric=True,
    )
    conclusion_points = _select_literature_points(
        answer_points or all_points,
        keywords=("结论", "意义", "表明", "说明", "证明", "suggest", "indicate", "conclusion", "conclusions"),
        max_items=2,
    )

    sections = [
        *_build_literature_section("研究目的和背景", background_points, "PDF中未提及足够的研究背景或研究目的信息。"),
        *_build_literature_section("研究方法/实验设计", method_points, "PDF中未提及足够的研究方法或实验设计细节。"),
        *_build_literature_section("主要发现和结果", result_points, "PDF中未提及足够的主要发现或结果数据。"),
        *_build_literature_section("结论和意义", conclusion_points, "PDF中未提及足够的结论或研究意义描述。"),
        _LITERATURE_SUMMARY_NOTE,
    ]
    return "\n".join(sections).strip()


def _ensure_literature_summary_structure(
    *,
    answer: str,
    prepared_pdf_text: str,
    route_hint: str = "pdf_qa",
    source_scope: str = "pdf",
) -> str:
    normalized_answer = str(answer or "").strip()
    if not _is_aligned_pdf_summary_request(route_hint=route_hint, source_scope=source_scope):
        return _ensure_legacy_literature_summary_structure(answer=normalized_answer, prepared_pdf_text=prepared_pdf_text)
    prepared_source_text = _strip_multi_doc_headers(prepared_pdf_text)
    mode = classify_summary_answer(normalized_answer, prepared_text=prepared_source_text)
    if mode == "preserve":
        return _append_summary_tail_sections(answer=normalized_answer, prepared_pdf_text=prepared_source_text)
    if mode == "light_repair":
        return _build_repaired_literature_summary(
            answer=normalized_answer,
            prepared_pdf_text=prepared_source_text,
            use_model_content=True,
            allow_general_fallback=True,
        )
    if mode == "conservative_repair":
        return _build_repaired_literature_summary(
            answer=normalized_answer,
            prepared_pdf_text=prepared_source_text,
            use_model_content=True,
            allow_general_fallback=False,
        )
    return _build_repaired_literature_summary(
        answer="",
        prepared_pdf_text=prepared_source_text,
        use_model_content=False,
        allow_general_fallback=False,
    )


class _NoopLogger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None


class PatentPdfAnswerClient:
    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_seconds: float = 30.0) -> None:
        self._api_key = str(api_key or "").strip()
        self._base_url = str(base_url or "").strip()
        self._model = str(model or "").strip()
        self._timeout_seconds = float(timeout_seconds)
        self._client = httpx.Client(timeout=self._timeout_seconds)

    @classmethod
    def from_env(cls) -> "PatentPdfAnswerClient | None":
        use_shared_env = _env_flag("PATENT_OPENAI_USE_SHARED_ENV", default=False)
        api_key = _first_env(
            "PATENT_OPENAI_API_KEY",
            default=(os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")) if use_shared_env else "",
        )
        base_url = _first_env(
            "PATENT_OPENAI_BASE_URL",
            default=(os.getenv("OPENAI_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL")) if use_shared_env else "",
        )
        model = _first_env(
            "PATENT_OPENAI_MODEL",
            default=(os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL")) if use_shared_env else "",
        )
        if not api_key or not base_url or not model:
            return None
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=float(str(os.getenv("PATENT_OPENAI_TIMEOUT_SECONDS") or "30").strip()),
        )

    def close(self) -> None:
        self._client.close()

    def _build_request_payload(
        self,
        *,
        question: str,
        pdf_text: str,
        file_name: str,
        include_kb: bool,
        stream: bool,
        selected_file_labels: list[str] | None = None,
        route_hint: str = "pdf_qa",
        source_scope: str = "pdf",
    ) -> dict[str, Any]:
        labels = [str(item).strip() for item in list(selected_file_labels or []) if str(item).strip()]
        compare_mode = is_compare_question(question, selected_pdf_count=len(labels) or 1)
        summary_mode = is_summary_question(question) and not compare_mode
        kb_section = build_kb_section({"kb_answer": _KB_BOUNDARY_PLACEHOLDER}) if include_kb else ""
        prompt = build_patent_pdf_answer_prompt(
            question=question,
            pdf_content=pdf_text,
            kb_section=kb_section,
            is_summary=summary_mode,
            is_compare=compare_mode,
            selected_file_labels=labels or [str(file_name or "").strip() or "unknown.pdf"],
            route_hint=route_hint,
            source_scope=source_scope,
        )
        return {
            "model": self._model,
            "temperature": 0.2,
            "stream": bool(stream),
            "messages": [
                {
                    "role": "system",
                    "content": PDF_QA_SYSTEM_MESSAGE,
                },
                {"role": "user", "content": prompt},
            ],
        }

    @staticmethod
    def _extract_delta_text(payload: dict[str, Any]) -> str:
        choices = list(payload.get("choices") or [])
        pieces: list[str] = []
        for choice in choices:
            delta = dict((choice or {}).get("delta") or {})
            content = delta.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = str(item.get("text") or "")
                        if text:
                            pieces.append(text)
                continue
            text = str(content or "")
            if text:
                pieces.append(text)
        return "".join(pieces)

    def stream_answer(
        self,
        *,
        question: str,
        pdf_text: str,
        file_name: str,
        include_kb: bool,
        selected_file_labels: list[str] | None = None,
        route_hint: str = "pdf_qa",
        source_scope: str = "pdf",
    ) -> Any:
        request_payload = self._build_request_payload(
            question=question,
            pdf_text=pdf_text,
            file_name=file_name,
            include_kb=include_kb,
            stream=True,
            selected_file_labels=selected_file_labels,
            route_hint=route_hint,
            source_scope=source_scope,
        )
        with self._client.stream(
            "POST",
            f"{self._base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=request_payload,
        ) as response:
            response.raise_for_status()
            for raw_line in response.iter_lines():
                line = str(raw_line or "").strip()
                if not line or not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if not body or body == "[DONE]":
                    continue
                payload = json.loads(body)
                if isinstance(payload, dict) and payload.get("error"):
                    message = str(dict(payload.get("error") or {}).get("message") or "patent_pdf_stream_error").strip()
                    raise RuntimeError(message)
                if not isinstance(payload, dict):
                    continue
                text = self._extract_delta_text(payload)
                if text:
                    yield text

    def answer(
        self,
        *,
        question: str,
        pdf_text: str,
        file_name: str,
        include_kb: bool,
        selected_file_labels: list[str] | None = None,
        route_hint: str = "pdf_qa",
        source_scope: str = "pdf",
    ) -> str:
        response = self._client.post(
            f"{self._base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=self._build_request_payload(
                question=question,
                pdf_text=pdf_text,
                file_name=file_name,
                include_kb=include_kb,
                stream=False,
                selected_file_labels=selected_file_labels,
                route_hint=route_hint,
                source_scope=source_scope,
            ),
        )
        response.raise_for_status()
        payload = response.json()
        choices = list(payload.get("choices") or [])
        message = dict((choices[0] or {}).get("message") or {}) if choices else {}
        return str(message.get("content") or "").strip()


class PatentPdfService:
    def __init__(
        self,
        *,
        extract_pdf_text_fn: Callable[..., str] | None = None,
        answer_question_fn: Callable[..., str] | None = None,
        max_pdf_pages: int = 10,
        max_pdf_chars: int = 12000,
    ) -> None:
        self._extract_pdf_text_fn = extract_pdf_text_fn or self._extract_pdf_text
        self._answer_question_fn = answer_question_fn
        self._client = None if answer_question_fn is not None else PatentPdfAnswerClient.from_env()
        self._max_pdf_pages = max(1, int(max_pdf_pages))
        self._max_pdf_chars = max(1000, int(max_pdf_chars))

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def execute(
        self,
        *,
        contract: PatentFileContract,
        include_kb: bool,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        content_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        pdf_execution_files = [item for item in contract.selected_execution_files if item.family == "pdf"]
        used_files = [item.as_payload() for item in pdf_execution_files]
        profile = get_patent_mode_profile(contract.route)
        selected_labels = [
            str(item.get("file_name") or f"file:{item.get('file_id') or 'unknown'}").strip()
            for item in used_files
        ]
        compare_mode = is_compare_question(contract.question, selected_pdf_count=len(selected_labels))
        targeted_doc_index = None if compare_mode else detect_targeted_document_index(
            contract.question,
            selected_pdf_count=len(selected_labels),
            selected_file_labels=selected_labels,
        )
        candidate_pdf_files = (
            self._select_targeted_execution_files(pdf_execution_files=pdf_execution_files, target_index=targeted_doc_index)
            if targeted_doc_index is not None
            else list(pdf_execution_files)
        )
        steps: list[dict[str, Any]] = []
        prepared_for_generation = ""

        self._record_step(
            steps,
            progress_callback=progress_callback,
            payload={
                "step": "pdf_extract",
                "title": "分析 PDF 原文",
                "message": "📄 正在分析上传的PDF文献...",
                "status": "running",
                "data": {"count": len(used_files)},
            },
        )

        pdf_documents = self._load_pdf_documents(execution_files=candidate_pdf_files)
        if targeted_doc_index is not None:
            selected_labels = self._select_targeted_labels(selected_labels=selected_labels, target_index=targeted_doc_index)
        pdf_text = format_multi_pdf_sections(pdf_documents)
        available_labels = [str(item.get("label") or "").strip() for item in pdf_documents if str(item.get("label") or "").strip()]
        if pdf_text:
            self._record_step(
                steps,
                progress_callback=progress_callback,
                payload={
                    "step": "pdf_extract",
                    "title": "分析 PDF 原文",
                    "message": f"📄 已完成 PDF 原文提取，共 {len(used_files)} 个文件，正文 chars={len(pdf_text)}",
                    "status": "success",
                    "data": {"count": len(used_files), "chars": len(pdf_text)},
                },
            )
            if compare_mode:
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "multi_pdf_compare",
                        "title": "准备多文献比较",
                        "message": f"🔍 已识别多文献比较请求，正在准备 {len(available_labels)} 篇文献证据...",
                        "status": "running",
                        "data": {"count": len(available_labels)},
                    },
                )
            prepared = self._prepare_answer_input(
                question=contract.question,
                pdf_text=pdf_text,
                pdf_documents=pdf_documents,
                selected_file_labels=selected_labels,
                available_file_labels=available_labels,
                compare_mode=compare_mode,
            )
            prepared_for_generation = str(prepared.get("prepared_pdf_text") or "")
            if compare_mode:
                compare_status = "success" if prepared["ok"] else "error"
                compare_message = (
                    f"🔍 已完成多文献比较证据准备，共 {len(available_labels)} 篇文献"
                    if prepared["ok"]
                    else f"🔍 多文献比较准备失败：{prepared['failure_reason']}"
                )
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "multi_pdf_compare",
                        "title": "准备多文献比较",
                        "message": compare_message,
                        "status": compare_status,
                        "error": None if prepared["ok"] else str(prepared["failure_reason"]),
                        "data": {"count": len(available_labels)},
                    },
                )
            if prepared["ok"]:
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_answer",
                        "title": "生成文件答案",
                        "message": "✍️ 正在基于 PDF 原文生成比较答案..." if compare_mode else "✍️ 正在基于 PDF 原文生成答案...",
                        "status": "running",
                    },
                )
                rendered = self._render_answer(
                    question=contract.question,
                    prepared_pdf_text=str(prepared["prepared_pdf_text"]),
                    file_name=", ".join(selected_labels) if len(selected_labels) > 1 else (selected_labels[0] if selected_labels else "unknown.pdf"),
                    selected_file_labels=selected_labels,
                    available_file_labels=available_labels,
                    include_kb=include_kb,
                    compare_mode=compare_mode,
                    route_hint=contract.route,
                    source_scope=contract.source_scope,
                    content_callback=content_callback,
                )
                answer_text = str(rendered["answer_text"])
                answer_mode = str(rendered["answer_mode"])
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_answer",
                        "title": "生成文件答案",
                        "message": (
                            "✍️ 已基于 PDF 原文生成比较答案"
                            if rendered["ok"] and compare_mode
                            else "✍️ 已基于 PDF 原文生成答案"
                            if rendered["ok"]
                            else "✍️ 多文献比较失败，已返回明确失败说明"
                            if compare_mode
                            else "✍️ 文件答案生成失败"
                        ),
                        "status": "success" if rendered["ok"] else "error",
                        "error": None if rendered["ok"] else str(rendered["failure_reason"]),
                    },
                )
                if rendered["ok"] and rendered.get("emit_after_steps") and callable(content_callback):
                    emit_text_chunks(answer_text, content_callback=content_callback)
                if not rendered["ok"] and rendered.get("stream_after_steps") and callable(content_callback):
                    emit_text_chunks(answer_text, content_callback=content_callback)
            else:
                answer_text = str(prepared["answer_text"])
                answer_mode = str(prepared["answer_mode"])
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_answer",
                        "title": "生成文件答案",
                        "message": "✍️ 多文献比较失败，已返回明确失败说明" if compare_mode else "✍️ 文件答案生成失败",
                        "status": "error",
                        "error": str(prepared["failure_reason"]),
                    },
                )
                if callable(content_callback):
                    emit_text_chunks(answer_text, content_callback=content_callback)
        else:
            answer_mode = "pdf_compare_unavailable" if compare_mode else "pdf_text_unavailable"
            answer_text = (
                build_compare_failure_message(
                    question=contract.question,
                    available_docs=[],
                    missing_docs=selected_labels,
                    reason="当前未拿到可读的 PDF 原文内容",
                )
                if compare_mode
                else "当前未拿到可读的 PDF 原文内容，无法生成基于正文的总结。请稍后重试或检查文件处理状态。"
            )
            if compare_mode:
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_extract",
                        "title": "分析 PDF 原文",
                        "message": f"📄 未拿到可读的 PDF 原文内容，当前选择文件数 {len(used_files)}",
                        "status": "error",
                        "error": "当前未拿到可读的 PDF 原文内容",
                        "data": {"count": len(used_files), "chars": 0},
                    },
                )
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "multi_pdf_compare",
                        "title": "准备多文献比较",
                        "message": f"🔍 已识别多文献比较请求，正在准备 {len(used_files)} 篇文献证据...",
                        "status": "running",
                        "data": {"count": len(used_files)},
                    },
                )
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "multi_pdf_compare",
                        "title": "准备多文献比较",
                        "message": "🔍 多文献比较准备失败：当前未拿到可读的 PDF 原文内容",
                        "status": "error",
                        "error": "当前未拿到可读的 PDF 原文内容",
                        "data": {"count": len(used_files)},
                    },
                )
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_answer",
                        "title": "生成文件答案",
                        "message": "✍️ 多文献比较失败，已返回明确失败说明",
                        "status": "error",
                        "error": "当前未拿到可读的 PDF 原文内容",
                    },
                )
                if callable(content_callback):
                    emit_text_chunks(answer_text, content_callback=content_callback)
            else:
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_extract",
                        "title": "分析 PDF 原文",
                        "message": f"📄 未拿到可读的 PDF 原文内容，当前选择文件数 {len(used_files)}",
                        "status": "success",
                        "data": {"count": len(used_files), "chars": 0},
                    },
                )
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_answer",
                        "title": "生成文件答案",
                        "message": "✍️ 已返回文件不可读的说明",
                        "status": "success",
                    },
                )
                if callable(content_callback):
                    emit_text_chunks(answer_text, content_callback=content_callback)
        return {
            "handler": "pdf",
            "answer_text": answer_text,
            "route": contract.route,
            "query_mode": profile.query_mode,
            "source_scope": contract.source_scope,
            "steps": [dict(item) for item in steps],
            "metadata": {
                "handler": "pdf",
                "source_scope": contract.source_scope,
                "selected_file_count": len(used_files),
                "kb_enabled": bool(include_kb),
                "answer_mode": answer_mode,
                "pdf_text_chars": len(pdf_text),
                "pdf_evidence_context": str(prepared_for_generation or pdf_text or "")[:1200],
                "prepared_pdf_text": str(prepared_for_generation or ""),
                "steps": [dict(item) for item in steps],
            },
            "timings": {
                "patent_pdf_route_ms": 1,
            },
            "used_files": used_files,
            "selected_file_ids": list(contract.selected_file_ids),
            "file_selection": dict(contract.file_selection),
            "kb_enabled": bool(include_kb),
        }

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

    def _load_pdf_documents(self, *, execution_files: list[Any]) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        for item in execution_files:
            local_path = str(item.payload.get("local_path") or "").strip()
            if not local_path:
                continue
            resolved = Path(local_path)
            if not resolved.exists() or not resolved.is_file():
                continue
            extracted = str(
                self._extract_pdf_text_fn(
                    str(resolved),
                    max_pages=self._max_pdf_pages,
                )
                or ""
            ).strip()
            if not extracted:
                continue
            label = str(item.file_name or resolved.name or f"file:{item.file_id}")
            sections.append({"label": label, "text": extracted})
        return sections

    def _prepare_answer_input(
        self,
        *,
        question: str,
        pdf_text: str,
        pdf_documents: list[dict[str, str]],
        selected_file_labels: list[str],
        available_file_labels: list[str],
        compare_mode: bool,
    ) -> dict[str, Any]:
        summary_mode = is_summary_question(question) and not compare_mode
        missing_labels = [label for label in selected_file_labels if label not in set(available_file_labels)]

        if compare_mode and len(selected_file_labels) > _MAX_COMPARE_DOCUMENTS:
            message = build_compare_failure_message(
                question=question,
                available_docs=available_file_labels,
                missing_docs=missing_labels,
                reason=f"当前比较已超过 {_MAX_COMPARE_DOCUMENTS} 篇文献，请缩小比较范围后重试",
            )
            return {
                "ok": False,
                "prepared_pdf_text": "",
                "answer_text": message,
                "answer_mode": "pdf_compare_unavailable",
                "failure_reason": f"超过 {_MAX_COMPARE_DOCUMENTS} 篇文献，无法生成结构化比较",
            }

        if compare_mode and (len(available_file_labels) < 2 or missing_labels):
            message = build_compare_failure_message(
                question=question,
                available_docs=available_file_labels,
                missing_docs=missing_labels,
                reason="参与比较的文献正文不完整",
            )
            return {
                "ok": False,
                "prepared_pdf_text": "",
                "answer_text": message,
                "answer_mode": "pdf_compare_unavailable",
                "failure_reason": "参与比较的文献正文不完整",
            }

        try:
            prepared_pdf_text = smart_truncate_pdf_content(
                pdf_text,
                self._max_pdf_chars,
                logger=_NoopLogger(),
                is_summary=summary_mode,
                question=question,
                is_compare=compare_mode,
            )
        except CompareBudgetError as exc:
            message = build_compare_failure_message(
                question=question,
                available_docs=available_file_labels,
                missing_docs=missing_labels,
                reason=str(exc),
            )
            return {
                "ok": False,
                "prepared_pdf_text": "",
                "answer_text": message,
                "answer_mode": "pdf_compare_unavailable",
                "failure_reason": str(exc),
            }

        if compare_mode:
            try:
                validate_compare_context(prepared_pdf_text, pdf_documents)
            except CompareBudgetError as exc:
                message = build_compare_failure_message(
                    question=question,
                    available_docs=available_file_labels,
                    missing_docs=missing_labels,
                    reason=str(exc),
                )
                return {
                    "ok": False,
                    "prepared_pdf_text": "",
                    "answer_text": message,
                    "answer_mode": "pdf_compare_unavailable",
                    "failure_reason": str(exc),
                }

        return {
            "ok": True,
            "prepared_pdf_text": prepared_pdf_text,
            "answer_text": "",
            "answer_mode": "pdf_text_compare" if compare_mode else "pdf_text_summary",
            "failure_reason": "",
        }

    def _render_answer(
        self,
        *,
        question: str,
        prepared_pdf_text: str,
        file_name: str,
        selected_file_labels: list[str],
        available_file_labels: list[str],
        include_kb: bool,
        compare_mode: bool,
        route_hint: str,
        source_scope: str,
        content_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        answer_parts: list[str] = []
        missing_labels = [label for label in selected_file_labels if label not in set(available_file_labels)]
        live_streamed = False
        stream_mode = "unknown"
        streamed_text = ""
        pending_stream_whitespace = ""
        summary_mode = is_summary_question(question) and not compare_mode
        aligned_summary_mode = summary_mode and _is_aligned_pdf_summary_request(route_hint=route_hint, source_scope=source_scope)
        prompt = build_patent_pdf_answer_prompt(
            question=question,
            pdf_content=prepared_pdf_text,
            kb_section=build_kb_section({"kb_answer": _KB_BOUNDARY_PLACEHOLDER}) if include_kb else "",
            is_summary=summary_mode,
            is_compare=compare_mode,
            selected_file_labels=selected_file_labels or [str(file_name or "").strip() or "unknown.pdf"],
            route_hint=route_hint,
            source_scope=source_scope,
        )

        def _emit_stream_piece(piece: str) -> None:
            nonlocal live_streamed, stream_mode, streamed_text, pending_stream_whitespace
            text = str(piece or "")
            if not text:
                return
            answer_parts.append(text)
            if compare_mode or not callable(content_callback):
                return

            def _emit_live_text(raw_text: str) -> None:
                nonlocal live_streamed, streamed_text, pending_stream_whitespace
                candidate = f"{pending_stream_whitespace}{raw_text}"
                pending_stream_whitespace = ""
                normalized_emit = candidate.rstrip()
                pending_stream_whitespace = candidate[len(normalized_emit) :]
                if not normalized_emit:
                    return
                content_callback(normalized_emit)
                streamed_text += normalized_emit
                live_streamed = True

            if stream_mode == "unknown":
                buffered_text = "".join(answer_parts)
                normalized_stream_text = buffered_text.lstrip()
                normalized_buffer = "".join(answer_parts).lstrip()
                if not normalized_buffer:
                    pending_stream_whitespace = ""
                    return
                if summary_mode:
                    if not aligned_summary_mode and _has_literature_summary_sections(normalized_buffer):
                        stream_mode = "raw_structured"
                        _emit_live_text(normalized_stream_text)
                    return
                looks_like_heading_prefix = bool(
                    normalized_buffer.startswith("##")
                    or re.match(r"^(?:#{1,6}\s*)?(?:结论|证据|对比|限制)\b", normalized_buffer)
                )
                if _has_four_block_sections(normalized_buffer):
                    stream_mode = "raw_structured"
                    _emit_live_text(normalized_stream_text)
                    return
                elif not looks_like_heading_prefix or len(answer_parts) >= 2 or len(normalized_buffer) >= 120:
                    stream_mode = "wrapped_summary"
                    prefix = "## 结论\n"
                    content_callback(prefix)
                    streamed_text += prefix
                    if normalized_stream_text:
                        _emit_live_text(normalized_stream_text)
                    return
                else:
                    return
            _emit_live_text(text)

        def _buffer_text(text: str) -> str:
            return str(text or "").strip()

        def _compare_failure_response(reason: str) -> dict[str, Any]:
            failure_answer = build_compare_failure_message(
                question=question,
                available_docs=available_file_labels,
                missing_docs=missing_labels,
                reason=reason,
            )
            return {
                "ok": False,
                "answer_text": failure_answer,
                "answer_mode": "pdf_compare_unavailable",
                "failure_reason": reason,
                "stream_after_steps": True,
            }

        if callable(self._answer_question_fn):
            output = self._answer_question_fn(
                question=question,
                pdf_text=prepared_pdf_text,
                file_name=file_name,
                include_kb=include_kb,
                prompt=prompt,
                route_hint=route_hint,
                source_scope=source_scope,
            )
            if isinstance(output, (str, bytes)):
                answer = _buffer_text(str(output or ""))
                if answer:
                    try:
                        if compare_mode:
                            answer = _ensure_compare_answer_structure(answer=answer, prepared_pdf_text=prepared_pdf_text)
                        elif summary_mode:
                            answer = _ensure_literature_summary_structure(
                                answer=answer,
                                prepared_pdf_text=prepared_pdf_text,
                                route_hint=route_hint,
                                source_scope=source_scope,
                            )
                        else:
                            answer = _ensure_four_block_pdf_answer_structure(
                                answer=answer,
                                prepared_pdf_text=prepared_pdf_text,
                                include_kb=include_kb,
                                route_hint=route_hint,
                                source_scope=source_scope,
                            )
                    except CompareAnswerNormalizationError as exc:
                        if compare_mode:
                            return _compare_failure_response(str(exc))
                        raise
                    return {
                        "ok": True,
                        "answer_text": answer,
                        "answer_mode": "pdf_text_compare" if compare_mode else "pdf_text_summary",
                        "failure_reason": "",
                        "emit_after_steps": not live_streamed,
                        "stream_after_steps": False,
                    }
            else:
                try:
                    for piece in iter_text_output(output):
                        _emit_stream_piece(piece)
                except Exception:
                    answer_parts = []
        elif self._client is not None:
            try:
                stream_builder = getattr(self._client, "stream_answer", None)
                if callable(stream_builder):
                    for piece in iter_text_output(
                        stream_builder(
                            question=question,
                            pdf_text=prepared_pdf_text,
                            file_name=file_name,
                            include_kb=include_kb,
                            selected_file_labels=selected_file_labels,
                            route_hint=route_hint,
                            source_scope=source_scope,
                        )
                    ):
                        _emit_stream_piece(piece)
            except Exception:
                answer_parts = []
            if not "".join(answer_parts).strip():
                try:
                    answer = _buffer_text(
                        self._client.answer(
                            question=question,
                            pdf_text=prepared_pdf_text,
                            file_name=file_name,
                            include_kb=include_kb,
                            selected_file_labels=selected_file_labels,
                            route_hint=route_hint,
                            source_scope=source_scope,
                        )
                    )
                    if answer:
                        try:
                            if compare_mode:
                                answer = _ensure_compare_answer_structure(answer=answer, prepared_pdf_text=prepared_pdf_text)
                            elif summary_mode:
                                answer = _ensure_literature_summary_structure(
                                    answer=answer,
                                    prepared_pdf_text=prepared_pdf_text,
                                    route_hint=route_hint,
                                    source_scope=source_scope,
                                )
                            else:
                                answer = _ensure_four_block_pdf_answer_structure(
                                    answer=answer,
                                    prepared_pdf_text=prepared_pdf_text,
                                    include_kb=include_kb,
                                    route_hint=route_hint,
                                    source_scope=source_scope,
                                )
                        except CompareAnswerNormalizationError as exc:
                            if compare_mode:
                                return _compare_failure_response(str(exc))
                            raise
                        return {
                            "ok": True,
                            "answer_text": answer,
                            "answer_mode": "pdf_text_compare" if compare_mode else "pdf_text_summary",
                            "failure_reason": "",
                            "emit_after_steps": not live_streamed,
                            "stream_after_steps": False,
                        }
                except Exception:
                    answer_parts = []

        answer = "".join(answer_parts).strip()
        if answer:
            try:
                if compare_mode:
                    answer = _ensure_compare_answer_structure(answer=answer, prepared_pdf_text=prepared_pdf_text)
                elif summary_mode:
                    answer = _ensure_literature_summary_structure(
                        answer=answer,
                        prepared_pdf_text=prepared_pdf_text,
                        route_hint=route_hint,
                        source_scope=source_scope,
                    )
                else:
                    answer = _ensure_four_block_pdf_answer_structure(
                        answer=answer,
                        prepared_pdf_text=prepared_pdf_text,
                        include_kb=include_kb,
                        route_hint=route_hint,
                        source_scope=source_scope,
                    )
            except CompareAnswerNormalizationError as exc:
                if compare_mode:
                    return _compare_failure_response(str(exc))
                raise
            if callable(content_callback) and not compare_mode and answer_parts:
                if not live_streamed:
                    emit_text_chunks(answer, content_callback=content_callback)
                    live_streamed = True
                    streamed_text = answer
                elif answer.startswith(streamed_text):
                    suffix = answer[len(streamed_text) :]
                    if suffix:
                        emit_text_chunks(suffix, content_callback=content_callback)
                    streamed_text = answer
            return {
                "ok": True,
                "answer_text": answer,
                "answer_mode": "pdf_text_compare" if compare_mode else "pdf_text_summary",
                "failure_reason": "",
                "emit_after_steps": not live_streamed,
                "stream_after_steps": False,
            }

        fallback = (
            build_compare_failure_message(
                question=question,
                available_docs=available_file_labels,
                missing_docs=missing_labels,
                reason="模型未返回可用的比较结果",
            )
            if compare_mode
            else (
                _ensure_literature_summary_structure(
                    answer=build_extractive_fallback_summary(question=question, pdf_text=prepared_pdf_text),
                    prepared_pdf_text=prepared_pdf_text,
                    route_hint=route_hint,
                    source_scope=source_scope,
                )
                if summary_mode
                else _ensure_four_block_pdf_answer_structure(
                    answer=build_extractive_fallback_summary(question=question, pdf_text=prepared_pdf_text),
                    prepared_pdf_text=prepared_pdf_text,
                    include_kb=include_kb,
                    route_hint=route_hint,
                    source_scope=source_scope,
                )
            )
        )
        if compare_mode:
            return {
                "ok": False,
                "answer_text": fallback,
                "answer_mode": "pdf_compare_unavailable",
                "failure_reason": "模型未返回可用的比较结果",
                "stream_after_steps": True,
            }
        emit_text_chunks(fallback, content_callback=content_callback)
        return {
            "ok": True,
            "answer_text": fallback,
            "answer_mode": "pdf_text_summary",
            "failure_reason": "",
            "emit_after_steps": True,
            "stream_after_steps": False,
        }

    @staticmethod
    def _extract_pdf_text(pdf_path: str, *, max_pages: int = 10) -> str:
        if fitz is None:
            return ""
        doc = fitz.open(pdf_path)
        try:
            page_count = min(int(doc.page_count), max(1, int(max_pages)))
            chunks: list[str] = []
            metadata = doc.metadata or {}
            title = _collapse_whitespace(str(metadata.get("title") or ""))
            if title:
                chunks.append(f"标题: {title}")
            for page_index in range(page_count):
                text = _collapse_whitespace(doc[page_index].get_text())
                if not text:
                    continue
                chunks.append(text)
            return "\n".join(chunks).strip()
        finally:
            doc.close()

    @staticmethod
    def _select_targeted_execution_files(*, pdf_execution_files: list[Any], target_index: int | None) -> list[Any]:
        if target_index is None:
            return list(pdf_execution_files)
        if target_index < 0 or target_index >= len(pdf_execution_files):
            return list(pdf_execution_files[:1])
        return [pdf_execution_files[target_index]]

    @staticmethod
    def _select_targeted_labels(*, selected_labels: list[str], target_index: int) -> list[str]:
        if target_index < 0 or target_index >= len(selected_labels):
            return list(selected_labels[:1])
        return [str(selected_labels[target_index]).strip()]


class CompareAnswerNormalizationError(RuntimeError):
    """Raised when compare output cannot be normalized into the approved structure."""


def _ensure_compare_answer_structure(*, answer: str, prepared_pdf_text: str) -> str:
    normalized_answer = str(answer or "").strip()
    compare_documents = _extract_compare_documents(prepared_pdf_text=prepared_pdf_text)
    if not normalized_answer:
        raise CompareAnswerNormalizationError("模型未返回可用的比较结果")
    if not compare_documents:
        raise CompareAnswerNormalizationError("缺少可用于逐篇比较的文献证据")
    if _contains_heavy_english_compare_content(normalized_answer):
        raise CompareAnswerNormalizationError("模型返回的比较结果包含无法修复的英文碎片")
    summary_line = _extract_compare_summary_line(normalized_answer)
    if not summary_line:
        raise CompareAnswerNormalizationError("模型返回的比较结果无法修复为合格的中文结构化答案")

    normalized_compare_answer = _build_normalized_compare_answer(
        answer=normalized_answer,
        compare_documents=compare_documents,
        summary_line=summary_line,
    )
    if _has_ordered_compare_sections(
        text=normalized_compare_answer,
        compare_documents=compare_documents,
    ):
        return normalized_compare_answer
    raise CompareAnswerNormalizationError("模型返回的比较结果不满足逐篇结构化比较要求")


def _extract_compare_documents(*, prepared_pdf_text: str) -> list[dict[str, str]]:
    matches = list(MULTI_DOC_HEADER_PATTERN.finditer(str(prepared_pdf_text or "")))
    if not matches:
        return []
    documents: list[dict[str, str]] = []
    source_text = str(prepared_pdf_text or "")
    for index, matched in enumerate(matches):
        start = matched.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source_text)
        header = str(matched.group(0) or "").strip().strip("=")
        label = header.split(":", 1)[1].strip() if ":" in header else f"文献 {index + 1}"
        body = str(source_text[start:end] or "").strip()
        if label and body:
            documents.append({"label": label, "body": body})
    return documents


def _build_normalized_compare_answer(
    *,
    answer: str,
    compare_documents: list[dict[str, str]],
    summary_line: str,
) -> str:
    section_map = _require_compare_section_map(answer)
    compact_mode = len(compare_documents) > 2
    lines: list[str] = ["## 具体内容对比"]
    for index, document in enumerate(compare_documents, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                *[
                    f"- {item}"
                    for item in _require_compare_doc_items(
                        section_body=section_map.get("具体内容对比", ""),
                        index=index,
                        label=str(document.get("label") or ""),
                        reason="模型返回的比较结果未提供足够的逐篇中文核心内容",
                        max_items=1 if compact_mode else 2,
                    )
                ],
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, document in enumerate(compare_documents, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                *[
                    f"- {item}"
                    for item in _require_compare_doc_items(
                        section_body=section_map.get("研究方法差异", ""),
                        index=index,
                        label=str(document.get("label") or ""),
                        reason="模型返回的比较结果未给出逐篇研究方法描述",
                        max_items=1 if compact_mode else 2,
                    )
                ],
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, document in enumerate(compare_documents, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                *[
                    f"- {item}"
                    for item in _require_compare_doc_items(
                        section_body=section_map.get("应用领域差异", ""),
                        index=index,
                        label=str(document.get("label") or ""),
                        reason="模型返回的比较结果未给出逐篇应用领域描述",
                        max_items=1 if compact_mode else 2,
                    )
                ],
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            *[f"- {item}" for item in _require_compare_shared_items(section_map.get("相同点", ""))],
            "",
            "## 总结",
            f"- {summary_line}",
        ]
    )
    return "\n".join(lines).strip()


def _require_compare_section_map(answer: str) -> dict[str, str]:
    sections = _extract_compare_sections(answer)
    expected = ["具体内容对比", "研究方法差异", "应用领域差异", "相同点", "总结"]
    ordered_names = [name for name, _body in sections]
    if sorted(ordered_names) != sorted(expected):
        raise CompareAnswerNormalizationError("模型返回的比较结果缺少必要章节")
    if len(sections) != len(expected):
        raise CompareAnswerNormalizationError("模型返回的比较结果章节数量不完整")
    return {name: body for name, body in sections}


def _extract_compare_sections(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"(^|\n)\s*(?:#{1,6}\s*)?(具体内容对比|研究方法差异|应用领域差异|相同点|总结)\s*[：:]?\s*",
        flags=re.MULTILINE | re.IGNORECASE,
    )
    matches = list(pattern.finditer(str(text or "")))
    sections: list[tuple[str, str]] = []
    for index, matched in enumerate(matches):
        name = str(matched.group(2) or "").strip()
        start = matched.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(str(text or ""))
        body = str(text or "")[start:end].strip()
        sections.append((name, body))
    return sections


def _has_ordered_compare_sections(*, text: str, compare_documents: list[dict[str, str]]) -> bool:
    ordered_names = [name for name, _body in _extract_compare_sections(text)]
    expected = ["具体内容对比", "研究方法差异", "应用领域差异", "相同点", "总结"]
    if ordered_names != expected:
        return False
    section_map = {name: body for name, body in _extract_compare_sections(text)}
    for index, document in enumerate(compare_documents, start=1):
        label = str(document.get("label") or "")
        try:
            _require_compare_doc_items(
                section_body=section_map.get("具体内容对比", ""),
                index=index,
                label=label,
                reason="模型返回的比较结果未提供足够的逐篇中文核心内容",
                max_items=1,
            )
            _require_compare_doc_items(
                section_body=section_map.get("研究方法差异", ""),
                index=index,
                label=label,
                reason="模型返回的比较结果未给出逐篇研究方法描述",
                max_items=1,
            )
            _require_compare_doc_items(
                section_body=section_map.get("应用领域差异", ""),
                index=index,
                label=label,
                reason="模型返回的比较结果未给出逐篇应用领域描述",
                max_items=1,
            )
        except CompareAnswerNormalizationError:
            return False
    return True


def _require_compare_doc_items(
    *,
    section_body: str,
    index: int,
    label: str,
    reason: str,
    max_items: int,
) -> list[str]:
    doc_body = _extract_compare_doc_body(section_body=section_body, index=index)
    if not doc_body:
        raise CompareAnswerNormalizationError(reason)
    content_points = [
        item
        for item in _extract_compare_chinese_points(doc_body, max_items=max_items + 2)
        if not _is_placeholder_compare_point(item=item, label=label)
    ]
    if not content_points:
        raise CompareAnswerNormalizationError(reason)
    items = [f"对应文件：{label}"] if label else []
    items.extend(content_points[:max_items])
    return items


def _is_placeholder_compare_point(*, item: str, label: str) -> bool:
    normalized = _collapse_whitespace(str(item or ""))
    normalized_label = _collapse_whitespace(str(label or ""))
    if not normalized:
        return True
    if normalized in {"略", "略。"}:
        return True
    if normalized_label and normalized.startswith(normalized_label):
        suffix = normalized[len(normalized_label):].lstrip("：:，,;； ")
        if suffix in {"略", "略。"}:
            return True
    return False


def _extract_compare_doc_body(*, section_body: str, index: int) -> str:
    pattern = re.compile(r"(^|\n)\s*#{1,6}\s*文献\s*#?([0-9]+)[^\n]*", flags=re.MULTILINE)
    matches = list(pattern.finditer(str(section_body or "")))
    if not matches:
        return ""
    for position, matched in enumerate(matches):
        if int(str(matched.group(2) or "0") or 0) != index:
            continue
        start = matched.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(str(section_body or ""))
        return str(section_body or "")[start:end].strip()
    return ""


def _extract_compare_chinese_points(text: str, *, max_items: int) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    points: list[str] = []
    raw_parts = re.split(r"\n+|(?<=[。！？!?])\s*", normalized)
    for raw_part in raw_parts:
        item = _collapse_whitespace(re.sub(r"^[#>\-\*\d\.\)\s]+", "", raw_part))
        if not item:
            continue
        if "文献 #" in item:
            continue
        if re.search(r"[\u4e00-\u9fff]", item) is None:
            continue
        if len(item) < 4:
            continue
        if item in points:
            continue
        points.append(_truncate(item, 180))
        if len(points) >= max_items:
            break
    return points


def _require_compare_shared_items(section_body: str) -> list[str]:
    points = _extract_compare_chinese_points(section_body, max_items=3)
    if points:
        return points
    raise CompareAnswerNormalizationError("模型返回的比较结果未给出可用的中文共同点总结")


def _contains_heavy_english_compare_content(text: str) -> bool:
    normalized = str(text or "")
    ascii_words = re.findall(r"[A-Za-z]{4,}(?:\s+[A-Za-z0-9%+\-]{2,})*", normalized)
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    if cjk_count == 0 and ascii_words:
        return True
    return len(ascii_words) >= 3 and len("".join(ascii_words)) > max(30, cjk_count * 2)


def _extract_compare_summary_line(answer: str) -> str:
    summary_body = _extract_markdown_section_body(answer, heading="总结")
    candidate = _first_sentence(summary_body) if summary_body else ""
    if not candidate:
        matched = re.search(r"(^|\n)\s*总结\s*[：:]\s*(.+)", str(answer or ""), flags=re.IGNORECASE)
        candidate = _collapse_whitespace(str(matched.group(2) or "")) if matched else ""
    candidate = _collapse_whitespace(re.sub(r"^[#>\-\*\d\.\)\s]+", "", str(candidate or "")))
    if not candidate:
        return ""
    if re.search(r"[A-Za-z]+-\s+[A-Za-z]+", candidate):
        return ""
    if re.search(r"[\u4e00-\u9fff]", candidate) is None:
        return ""
    return _truncate(candidate, 220)


def _extract_markdown_section_body(text: str, *, heading: str) -> str:
    normalized = str(text or "")
    marker = f"## {heading}"
    start = normalized.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    next_heading = normalized.find("\n## ", start)
    if next_heading < 0:
        return normalized[start:].strip()
    return normalized[start:next_heading].strip()


def _first_sentence(text: str) -> str:
    for raw_line in str(text or "").splitlines():
        line = _collapse_whitespace(raw_line)
        if not line:
            continue
        if re.match(r"^#{1,6}\s*", line):
            continue
        if re.match(r"^\d+[\.\)]\s+", line):
            continue
        if re.match(r"^[\-\*]\s+", line):
            line = re.sub(r"^[\-\*]\s+", "", line).strip()
        parts = re.split(r"(?<=[。！？.!?])\s+", line, maxsplit=1)
        first = str(parts[0] or "").strip()
        if first:
            return first
    return ""
