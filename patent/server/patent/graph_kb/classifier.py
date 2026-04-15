from __future__ import annotations

import re
from typing import Any

from server.patent.graph_kb.models import PatentGraphKbDecision


_FOLLOWUP_HINTS = ("它", "这个", "那件", "上面", "前者", "后者")
_BROAD_HINTS = ("为什么", "如何评价", "趋势", "综述", "对比分析", "替代窗口")
_DOI_PATTERN = re.compile(r"10\.\d+/[A-Za-z0-9._\-()/]+", re.IGNORECASE)
_PATENT_ID_PATTERN = re.compile(r"\b(?:CN|US|WO|JP|EP|KR)[A-Z0-9]{6,}\b", re.IGNORECASE)
_IPC_PATTERN = re.compile(r"\b[A-H][0-9]{2}[A-Z][0-9]+/[0-9A-Z]+\b", re.IGNORECASE)
_APPLICANT_LISTING_PATTERN = re.compile(r"^(?P<name>[\u4e00-\u9fffA-Za-z0-9()（）·\-.]+?)有哪些专利$")


def _normalize_question(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().rstrip("？?。.!！")


def _extract_patent_ids(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    patent_ids: list[str] = []
    for item in _PATENT_ID_PATTERN.findall(text):
        normalized = str(item or "").upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        patent_ids.append(normalized)
    return tuple(patent_ids)


def _contains_file_context(context: dict[str, Any]) -> bool:
    state = context.get("conversation_state") if isinstance(context.get("conversation_state"), dict) else {}
    source_selection = context.get("source_selection") if isinstance(context.get("source_selection"), dict) else {}
    last_turn_route = str(state.get("last_turn_route") or "").strip().lower()
    source_scope = str(source_selection.get("source_scope") or "").strip().lower()
    selected_file_ids = list(source_selection.get("selected_file_ids") or [])
    execution_files = list(source_selection.get("execution_files") or [])
    used_files = list(source_selection.get("used_files") or [])
    if last_turn_route in {"pdf_qa", "tabular_qa", "hybrid_qa"}:
        return True
    if any(token in source_scope for token in ("pdf", "table")):
        return True
    return bool(selected_file_ids or execution_files or used_files)


def classify_patent_graph_kb_question(
    question: str,
    *,
    conversation_context: dict[str, Any] | None = None,
) -> PatentGraphKbDecision:
    text = _normalize_question(question)
    if not text:
        return PatentGraphKbDecision(decision="skip", reason="no_graph_signal", standalone=True, signals=("empty",))

    context = conversation_context or {}
    patent_ids = _extract_patent_ids(text)
    has_patent_id = bool(patent_ids)

    if _contains_file_context(context):
        return PatentGraphKbDecision(
            decision="skip",
            reason="file_context_present",
            standalone=False,
            signals=("file_context",),
        )

    if len(patent_ids) > 1:
        return PatentGraphKbDecision(
            decision="skip",
            reason="multiple_patent_ids",
            standalone=True,
            signals=("patent_id", "multi_patent"),
        )

    if not has_patent_id and any(hint in text for hint in _FOLLOWUP_HINTS):
        return PatentGraphKbDecision(
            decision="skip",
            reason="ambiguous_followup",
            standalone=False,
            signals=("followup_hint",),
        )

    if _DOI_PATTERN.search(text):
        return PatentGraphKbDecision(
            decision="skip",
            reason="doi_not_supported",
            standalone=True,
            signals=("doi",),
        )

    if any(hint in text for hint in _BROAD_HINTS):
        return PatentGraphKbDecision(
            decision="skip",
            reason="broad_semantic_question",
            standalone=True,
            signals=("broad_hint",),
        )

    if has_patent_id:
        if any(hint in text for hint in ("工艺步骤", "步骤", "工艺")):
            return PatentGraphKbDecision("try_graph", "patent_process_steps", True, ("patent_id", "process"))
        if any(hint in text for hint in ("原料", "材料角色")):
            return PatentGraphKbDecision("try_graph", "patent_material_roles", True, ("patent_id", "materials"))
        if any(hint in text for hint in ("实验表格", "性能数据", "实验数据", "测量")):
            return PatentGraphKbDecision("try_graph", "patent_experiment_tables", True, ("patent_id", "experiments"))
        if any(hint in text for hint in ("技术问题", "方案", "应用场景")):
            return PatentGraphKbDecision("try_graph", "patent_problem_solution", True, ("patent_id", "problem_solution"))
        if any(hint in text for hint in ("发明点", "保护范围", "保护", "性能事实", "claim")):
            return PatentGraphKbDecision("try_graph", "patent_inventive_scope", True, ("patent_id", "inventive_scope"))
        if "引用" in text:
            return PatentGraphKbDecision("try_graph", "patent_citations", True, ("patent_id", "citations"))
        return PatentGraphKbDecision("try_graph", "patent_id_lookup", True, ("patent_id",))

    if _IPC_PATTERN.search(text) and "专利" in text:
        return PatentGraphKbDecision("try_graph", "ipc_listing", True, ("ipc", "listing"))

    if _APPLICANT_LISTING_PATTERN.fullmatch(text):
        return PatentGraphKbDecision("try_graph", "applicant_listing", True, ("applicant", "listing"))

    return PatentGraphKbDecision(
        decision="skip",
        reason="no_graph_signal",
        standalone=True,
        signals=("default_skip",),
    )
