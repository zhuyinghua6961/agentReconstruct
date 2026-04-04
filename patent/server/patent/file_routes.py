from __future__ import annotations

from typing import Any, Callable

from server.patent.file_models import PatentFileContract, PatentFileRoutePlan
from server.patent.pdf_service import PatentPdfService
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
) -> dict[str, Any]:
    plan = plan_patent_file_route(contract)
    pdf_handler = pdf_service or PatentPdfService()
    tabular_handler = tabular_service or PatentTabularService()
    dispatch_step = _route_dispatch_step(plan.handler)
    if callable(progress_callback):
        progress_callback(dict(dispatch_step))
    if plan.handler == "pdf":
        service = pdf_handler
        result = service.execute(contract=contract, include_kb=plan.include_kb, progress_callback=progress_callback)
        return _with_leading_steps(result=result, steps=[dispatch_step])
    if plan.handler == "tabular":
        service = tabular_handler
        result = service.execute(contract=contract, include_kb=plan.include_kb, progress_callback=progress_callback)
        return _with_leading_steps(result=result, steps=[dispatch_step])
    return _build_hybrid_result(
        contract=contract,
        include_kb=plan.include_kb,
        pdf_service=pdf_handler,
        tabular_service=tabular_handler,
        progress_callback=progress_callback,
        dispatch_step=dispatch_step,
    )


def _build_hybrid_result(
    *,
    contract: PatentFileContract,
    include_kb: bool,
    pdf_service: PatentPdfService,
    tabular_service: PatentTabularService,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    dispatch_step: dict[str, Any],
) -> dict[str, Any]:
    used_files = [item.as_payload() for item in contract.selected_execution_files]
    profile = get_patent_mode_profile(contract.route)
    pdf_result = pdf_service.execute(contract=contract, include_kb=False, progress_callback=progress_callback)
    tabular_result = tabular_service.execute(contract=contract, include_kb=False, progress_callback=progress_callback)
    if callable(progress_callback):
        progress_callback(
            {
                "step": "hybrid_answer",
                "title": "整合文件答案",
                "message": "🧩 正在整合 PDF 与表格原始内容...",
                "status": "running",
            }
        )
    hybrid_step = {
        "step": "hybrid_answer",
        "title": "整合文件答案",
        "message": f"🧩 已整合 PDF 与表格原始内容，共 {len(used_files)} 个文件",
        "status": "success",
        "data": {"count": len(used_files)},
    }
    if callable(progress_callback):
        progress_callback(dict(hybrid_step))
    return {
        "handler": "hybrid",
        "answer_text": _compose_hybrid_answer(
            pdf_answer=str(pdf_result.get("answer_text") or "").strip(),
            tabular_answer=str(tabular_result.get("answer_text") or "").strip(),
            include_kb=include_kb,
        ),
        "route": contract.route,
        "query_mode": profile.query_mode,
        "source_scope": contract.source_scope,
        "steps": [
            dict(dispatch_step),
            *[dict(item) for item in list(pdf_result.get("steps") or []) if isinstance(item, dict)],
            *[dict(item) for item in list(tabular_result.get("steps") or []) if isinstance(item, dict)],
            hybrid_step,
        ],
        "metadata": {
            "handler": "hybrid",
            "source_scope": contract.source_scope,
            "selected_file_count": len(used_files),
            "kb_enabled": bool(include_kb),
            "answer_mode": "hybrid_file_synthesis",
            "pdf_answer_mode": str(dict(pdf_result.get("metadata") or {}).get("answer_mode") or ""),
            "tabular_answer_mode": str(dict(tabular_result.get("metadata") or {}).get("answer_mode") or ""),
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
    return payload


def _compose_hybrid_answer(*, pdf_answer: str, tabular_answer: str, include_kb: bool) -> str:
    sections: list[str] = ["基于当前选中的 PDF 与表格原始内容，整理结果如下："]
    if pdf_answer:
        sections.append(f"PDF 部分：\n{pdf_answer}")
    if tabular_answer:
        sections.append(f"表格部分：\n{tabular_answer}")
    if len(sections) == 1:
        sections.append("当前未拿到可读的 PDF 或表格原始内容，暂时无法生成联合回答。")
    elif include_kb:
        sections.append("文件部分结论已生成，patent 知识库结果会在下游继续合并。")
    return "\n\n".join(section for section in sections if section).strip()
