from __future__ import annotations

import inspect
import re
from typing import Any, Callable

from server.patent.file_models import PatentFileContract, PatentFileRoutePlan
from server.patent.pdf_service import PatentPdfService
from server.patent.streaming import emit_text_chunks
from server.patent.tabular_service import PatentTabularService
from server.services.mode_profiles import get_patent_mode_profile


_HYBRID_SCOPE_TO_PLAN = {
    "pdf+kb": ("pdf", ("pdf",), True),
    "table+kb": ("tabular", ("table",), True),
    "pdf+table": ("hybrid", ("pdf", "table"), False),
    "pdf+table+kb": ("hybrid", ("pdf", "table"), True),
}


def plan_patent_file_route(contract: PatentFileContract) -> PatentFileRoutePlan:
    if contract.route == "pdf_qa":
        return PatentFileRoutePlan(
            route=contract.route,
            source_scope=contract.source_scope,
            handler="pdf",
            file_families=("pdf",),
            include_kb=False,
        )
    if contract.route == "tabular_qa":
        return PatentFileRoutePlan(
            route=contract.route,
            source_scope=contract.source_scope,
            handler="tabular",
            file_families=("table",),
            include_kb=False,
        )
    handler, file_families, include_kb = _HYBRID_SCOPE_TO_PLAN[contract.source_scope]
    return PatentFileRoutePlan(
        route=contract.route,
        source_scope=contract.source_scope,
        handler=handler,
        file_families=file_families,
        include_kb=include_kb,
    )


def dispatch_patent_file_route(
    *,
    contract: PatentFileContract,
    pdf_service: PatentPdfService | None = None,
    tabular_service: PatentTabularService | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    content_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    plan = plan_patent_file_route(contract)
    pdf_handler = pdf_service or PatentPdfService()
    tabular_handler = tabular_service or PatentTabularService()
    dispatch_step = _route_dispatch_step(plan.handler)
    if callable(progress_callback):
        progress_callback(dict(dispatch_step))
    if plan.handler == "pdf":
        service = pdf_handler
        result = _call_with_supported_kwargs(
            service.execute,
            contract=contract,
            include_kb=plan.include_kb,
            progress_callback=progress_callback,
            content_callback=content_callback,
        )
        return _with_leading_steps(result=result, steps=[dispatch_step])
    if plan.handler == "tabular":
        service = tabular_handler
        result = _call_with_supported_kwargs(
            service.execute,
            contract=contract,
            include_kb=plan.include_kb,
            progress_callback=progress_callback,
            content_callback=content_callback,
        )
        return _with_leading_steps(result=result, steps=[dispatch_step])
    return _build_hybrid_result(
        contract=contract,
        include_kb=plan.include_kb,
        pdf_service=pdf_handler,
        tabular_service=tabular_handler,
        progress_callback=progress_callback,
        content_callback=content_callback,
        dispatch_step=dispatch_step,
    )


def _build_hybrid_result(
    *,
    contract: PatentFileContract,
    include_kb: bool,
    pdf_service: PatentPdfService,
    tabular_service: PatentTabularService,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    content_callback: Callable[[str], None] | None,
    dispatch_step: dict[str, Any],
) -> dict[str, Any]:
    used_files = [item.as_payload() for item in contract.selected_execution_files]
    profile = get_patent_mode_profile(contract.route)

    pdf_result = _call_with_supported_kwargs(
        pdf_service.execute,
        contract=contract,
        include_kb=False,
        progress_callback=progress_callback,
        content_callback=None,
    )
    tabular_result = _call_with_supported_kwargs(
        tabular_service.execute,
        contract=contract,
        include_kb=False,
        progress_callback=progress_callback,
        content_callback=None,
    )
    if callable(progress_callback) and not include_kb:
        progress_callback(
            {
                "step": "hybrid_answer",
                "title": "整合文件答案",
                "message": "🧩 正在整合 PDF 与表格原始内容...",
                "status": "running",
            }
        )
    pdf_answer = str(pdf_result.get("answer_text") or "").strip()
    tabular_answer = str(tabular_result.get("answer_text") or "").strip()
    synthesis_contract = build_patent_hybrid_synthesis_contract(
        question=contract.question,
        source_scope=contract.source_scope,
        pdf_answer=pdf_answer,
        tabular_answer=tabular_answer,
        pdf_evidence_context=str(dict(pdf_result.get("metadata") or {}).get("pdf_evidence_context") or ""),
        table_execution_context=str(dict(tabular_result.get("metadata") or {}).get("table_evidence_context") or ""),
        include_kb=include_kb,
    )
    answer_text = synthesize_patent_hybrid_answer(synthesis_contract=synthesis_contract)
    hybrid_success = _has_usable_hybrid_evidence(synthesis_contract=synthesis_contract)
    hybrid_step = {
        "step": "hybrid_answer",
        "title": "整合文件答案",
        "message": (
            f"🧩 已整合 PDF 与表格原始内容，共 {len(used_files)} 个文件"
            if hybrid_success
            else "🧩 文件统一合成失败：当前没有可用于联合回答的文件证据"
        ),
        "status": "success" if hybrid_success else "error",
        "data": {"count": len(used_files)},
    }
    include_file_hybrid_step = not include_kb
    if callable(content_callback) and include_file_hybrid_step:
        emit_text_chunks(answer_text, content_callback=content_callback)
    if callable(progress_callback) and include_file_hybrid_step:
        progress_callback(dict(hybrid_step))
    hybrid_steps = [dict(hybrid_step)] if include_file_hybrid_step else []
    return {
        "handler": "hybrid",
        "answer_text": answer_text,
        "route": contract.route,
        "query_mode": profile.query_mode,
        "source_scope": contract.source_scope,
        "steps": [
            dict(dispatch_step),
            *[dict(item) for item in list(pdf_result.get("steps") or []) if isinstance(item, dict)],
            *[dict(item) for item in list(tabular_result.get("steps") or []) if isinstance(item, dict)],
            *hybrid_steps,
        ],
        "metadata": {
            "handler": "hybrid",
            "source_scope": contract.source_scope,
            "selected_file_count": len(used_files),
            "kb_enabled": bool(include_kb),
            "answer_mode": "hybrid_unified_synthesis",
            "pdf_answer_mode": str(dict(pdf_result.get("metadata") or {}).get("answer_mode") or ""),
            "tabular_answer_mode": str(dict(tabular_result.get("metadata") or {}).get("answer_mode") or ""),
            "synthesis_contract": dict(synthesis_contract),
            "steps": [
                dict(dispatch_step),
                *[dict(item) for item in list(pdf_result.get("steps") or []) if isinstance(item, dict)],
                *[dict(item) for item in list(tabular_result.get("steps") or []) if isinstance(item, dict)],
                *hybrid_steps,
            ],
        },
        "timings": {
            **dict(pdf_result.get("timings") or {}),
            **dict(tabular_result.get("timings") or {}),
            "patent_hybrid_route_ms": 1,
        },
        "used_files": used_files,
        "selected_file_ids": list(contract.selected_file_ids),
        "file_selection": dict(contract.file_selection),
        "kb_enabled": bool(include_kb),
    }


def _route_dispatch_step(handler: str) -> dict[str, Any]:
    normalized = str(handler or "").strip().lower()
    if normalized == "pdf":
        return {
            "step": "dispatch",
            "title": "进入 PDF 分支",
            "message": "进入 PDF 问答分支",
            "status": "success",
        }
    return {
        "step": "dispatch",
        "title": "进入文件分支",
        "message": "进入表格/混合问答分支",
        "status": "success",
    }


def _with_leading_steps(*, result: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    payload = dict(result or {})
    payload["steps"] = [
        *[dict(item) for item in steps if isinstance(item, dict)],
        *[dict(item) for item in list(payload.get("steps") or []) if isinstance(item, dict)],
    ]
    metadata = dict(payload.get("metadata") or {})
    metadata["steps"] = [dict(item) for item in payload["steps"]]
    payload["metadata"] = metadata
    return payload


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
) -> dict[str, Any]:
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
        "include_kb": bool(include_kb),
        "file_precedence": "file_over_kb",
    }


def synthesize_patent_hybrid_answer(*, synthesis_contract: dict[str, Any]) -> str:
    contract = dict(synthesis_contract or {})
    pdf_answer = str(contract.get("pdf_answer") or "").strip()
    tabular_answer = str(contract.get("tabular_answer") or "").strip()
    kb_answer = str(contract.get("kb_answer") or "").strip()
    pdf_evidence_context = str(contract.get("pdf_evidence_context") or "").strip()
    table_execution_context = str(contract.get("table_execution_context") or "").strip()
    kb_evidence_context = str(contract.get("kb_evidence_context") or "").strip()
    kb_reference_instruction = str(contract.get("kb_reference_instruction") or "").strip()
    usable_kb_answer = "" if _is_degraded_answer(kb_answer) else kb_answer

    lead = _select_hybrid_direct_conclusion(
        table_execution_context=table_execution_context,
        pdf_evidence_context=pdf_evidence_context,
        tabular_answer=tabular_answer,
        pdf_answer=pdf_answer,
        kb_answer=usable_kb_answer,
    )
    if not lead:
        return "当前未拿到可读的 PDF、表格或知识库证据，暂时无法生成联合回答。"

    sections: list[str] = [f"直接结论：{lead}"]
    if table_execution_context or pdf_evidence_context or tabular_answer or pdf_answer:
        file_lines: list[str] = []
        if table_execution_context or (tabular_answer and not _is_degraded_answer(tabular_answer)):
            file_lines.append(f"表格执行结果：{table_execution_context or tabular_answer}")
        if pdf_evidence_context or (pdf_answer and not _is_degraded_answer(pdf_answer)):
            file_lines.append(f"PDF 原文证据：{pdf_evidence_context or pdf_answer}")
        if file_lines:
            sections.append("文件依据：\n" + "\n".join(file_lines))
    if usable_kb_answer:
        kb_lines = [f"知识库补充：{usable_kb_answer}"]
        if kb_reference_instruction:
            kb_lines.append(kb_reference_instruction)
        conflict_message = _detect_conflict_message(
            file_context="\n".join(part for part in (table_execution_context, pdf_evidence_context) if part),
            kb_context=kb_evidence_context or usable_kb_answer,
        )
        if conflict_message:
            kb_lines.append(conflict_message)
        elif table_execution_context or pdf_evidence_context or tabular_answer or pdf_answer:
            kb_lines.append("冲突处理：当前未检测到明确冲突；若知识库与文件证据后续出现不一致，以文件原文和表格执行结果为准。")
        else:
            kb_lines.append("说明：当前文件证据不足，本结论主要来自知识库补充。")
        sections.append("\n".join(kb_lines))
    return "\n\n".join(part for part in sections if part).strip()


def _detect_conflict_message(*, file_context: str, kb_context: str) -> str:
    file_text = str(file_context or "").strip()
    kb_text = str(kb_context or "").strip()
    if not file_text or not kb_text:
        return ""
    file_metrics = _extract_metric_values(file_text)
    kb_metrics = _extract_metric_values(kb_text)
    shared_metrics = sorted(set(file_metrics) & set(kb_metrics))
    conflicting_metrics = [
        metric
        for metric in shared_metrics
        if file_metrics.get(metric) and kb_metrics.get(metric) and file_metrics.get(metric) != kb_metrics.get(metric)
    ]
    if conflicting_metrics:
        return (
            "冲突说明：文件证据与知识库证据存在冲突。"
            f" 冲突指标：{', '.join(conflicting_metrics)}。"
            " 当前按文件原文和表格执行结果为准。"
        )
    polarity_conflict = _detect_polarity_conflict(file_text=file_text, kb_text=kb_text)
    if polarity_conflict:
        return (
            "冲突说明：文件证据与知识库证据存在冲突。"
            f" 冲突点：{polarity_conflict}。"
            " 当前按文件原文和表格执行结果为准。"
        )
    return ""


def _extract_numbers(text: str) -> list[str]:
    return [value for value in re.findall(r"\d+(?:\.\d+)?", str(text or ""))]


def _extract_metric_values(text: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    normalized = str(text or "")
    patterns = {
        "capacity": r"(?:容量|capacity)[^\d]{0,12}(\d+(?:\.\d+)?)\s*(mAh|mah)",
        "efficiency": r"(?:效率|efficiency)[^\d]{0,12}(\d+(?:\.\d+)?)\s*(%)",
        "voltage": r"(?:电压|voltage)[^\d]{0,12}(\d+(?:\.\d+)?)\s*(V|v)",
        "cycle_life": r"(?:循环寿命|cycle\s*life)[^\d]{0,12}(\d+(?:\.\d+)?)",
    }
    for metric, pattern in patterns.items():
        matched = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not matched:
            continue
        value = str(matched.group(1) or "").strip()
        unit = str(matched.group(2) or "").strip() if matched.lastindex and matched.lastindex >= 2 else ""
        rows[metric] = f"{value}{unit}"
    return rows


def _detect_polarity_conflict(*, file_text: str, kb_text: str) -> str:
    pairs = [
        ("提高", "下降"),
        ("改善", "恶化"),
        ("稳定", "不稳定"),
        ("有效", "无效"),
        ("支持", "不支持"),
        ("increase", "decrease"),
        ("improved", "degraded"),
        ("stable", "unstable"),
        ("effective", "ineffective"),
        ("support", "not support"),
    ]
    file_lower = file_text.lower()
    kb_lower = kb_text.lower()
    for left, right in pairs:
        if (left in file_text and right in kb_text) or (right in file_text and left in kb_text):
            return f"{left}/{right}"
        if (left in file_lower and right in kb_lower) or (right in file_lower and left in kb_lower):
            return f"{left}/{right}"
    return ""


def _is_degraded_answer(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    markers = (
        "found no matching results",
        "未拿到可读",
        "未找到可用的知识库",
        "未找到匹配",
        "无法生成",
        "请稍后重试",
        "文件不可读",
        "暂时无法",
    )
    return any(marker in normalized for marker in markers)


def _has_usable_hybrid_evidence(*, synthesis_contract: dict[str, Any]) -> bool:
    contract = dict(synthesis_contract or {})
    candidates = [
        str(contract.get("pdf_evidence_context") or "").strip(),
        str(contract.get("table_execution_context") or "").strip(),
        str(contract.get("pdf_answer") or "").strip(),
        str(contract.get("tabular_answer") or "").strip(),
        str(contract.get("kb_evidence_context") or "").strip(),
        str(contract.get("kb_answer") or "").strip(),
    ]
    return any(candidate and not _is_degraded_answer(candidate) for candidate in candidates)


def _select_hybrid_direct_conclusion(
    *,
    table_execution_context: str,
    pdf_evidence_context: str,
    tabular_answer: str,
    pdf_answer: str,
    kb_answer: str,
) -> str:
    for candidate in (
        _lead_from_table_context(table_execution_context),
        _lead_from_pdf_context(pdf_evidence_context),
        tabular_answer if tabular_answer and not _is_degraded_answer(tabular_answer) else "",
        pdf_answer if pdf_answer and not _is_degraded_answer(pdf_answer) else "",
        kb_answer if kb_answer and not _is_degraded_answer(kb_answer) else "",
    ):
        if candidate:
            return candidate
    return ""


def _lead_from_table_context(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    material_hits = re.findall(r"material=([^;]+);\s*capacity_mAh=([^;]+)", normalized, flags=re.IGNORECASE)
    if material_hits:
        parts = [f"{material.strip()} {capacity.strip()}mAh" for material, capacity in material_hits[:3]]
        return "表格结果显示：" + "，".join(parts) + "。"
    return _clip_lead_text(normalized)


def _lead_from_pdf_context(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    body_lines = [line for line in lines if not line.startswith("==== 文献 ")]
    candidate = " ".join(body_lines).strip() if body_lines else normalized
    return _clip_lead_text(candidate)


def _clip_lead_text(text: str, *, limit: int = 120) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(1, limit - 1)].rstrip() + "…"


def _call_with_supported_kwargs(fn, /, **kwargs):
    if not callable(fn):
        raise TypeError("target is not callable")
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(**kwargs)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return fn(**kwargs)
    filtered = {
        key: value
        for key, value in kwargs.items()
        if key in signature.parameters
    }
    return fn(**filtered)
