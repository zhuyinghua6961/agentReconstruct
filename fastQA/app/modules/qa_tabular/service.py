from __future__ import annotations

import os
from typing import Any, Iterator

from app.modules.qa_tabular.executor import execute_compare_plan, execute_tabular_plan
from app.modules.qa_tabular.planner import plan_tabular_query
from app.modules.qa_pdf.common import IncrementalCleanState, incremental_clean_events_for_piece
from app.modules.qa_pdf.pdf_context import build_merged_pdf_context, extract_doi_from_filename
from app.modules.qa_tabular.renderer import (
    build_tabular_answer,
    build_tabular_result_context,
    infer_tabular_summary_focus_columns,
    iter_tabular_answer,
)
from app.modules.qa_tabular.schema_profiler import profile_workbook
from app.modules.qa_tabular.workbook_loader import load_workbook_cached


def _emit(payload: dict[str, Any], sse_event: Any) -> Any:
    if callable(sse_event):
        return sse_event(payload)
    return payload


def _iter_text_chunks(text: str, *, chunk_size: int = 12) -> Iterator[str]:
    value = str(text or "")
    size = max(1, int(chunk_size))
    for index in range(0, len(value), size):
        yield value[index : index + size]


def _is_file_failed(file_item: dict[str, Any]) -> bool:
    parse_status = str(file_item.get("parse_status") or "").strip().lower()
    index_status = str(file_item.get("index_status") or "").strip().lower()
    processing_stage = str(file_item.get("processing_stage") or "").strip().lower()
    return parse_status in {"failed"} or index_status in {"failed"} or processing_stage in {"failed"}


def _is_file_ready(file_item: dict[str, Any]) -> bool:
    parse_status = str(file_item.get("parse_status") or "").strip().lower()
    index_status = str(file_item.get("index_status") or "").strip().lower()
    processing_stage = str(file_item.get("processing_stage") or "").strip().lower()
    if _is_file_failed(file_item):
        return False
    if index_status == "ready" or processing_stage == "ready":
        return True
    if not parse_status and not index_status and not processing_stage:
        return True
    return parse_status == "ready"


def _strict_upload_minio_only() -> bool:
    raw = str(os.getenv("FASTQA_UPLOAD_MINIO_ONLY", "") or os.getenv("QA_ORIGINAL_MINIO_ONLY", "true") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _has_materialization_error(file_item: dict[str, Any]) -> bool:
    return bool(str(file_item.get("storage_error") or "").strip())


def _has_file_source(file_item: dict[str, Any], *, allow_preview: bool = False) -> bool:
    if _has_materialization_error(file_item):
        return False
    local_path = str(file_item.get("local_path") or "").strip()
    storage_ref = str(file_item.get("storage_ref") or "").strip()
    if local_path or storage_ref:
        return True
    if allow_preview and not _strict_upload_minio_only():
        file_meta = file_item.get("file_meta") if isinstance(file_item.get("file_meta"), dict) else {}
        parsed_preview = str(file_meta.get("parsed_preview") or "").strip()
        return bool(parsed_preview)
    return False


def _is_table_file_usable(file_item: dict[str, Any]) -> bool:
    return (not _is_file_failed(file_item)) and _has_file_source(file_item, allow_preview=False)


def _is_pdf_file_usable(file_item: dict[str, Any]) -> bool:
    if _is_file_failed(file_item) or _has_materialization_error(file_item):
        return False
    storage_ref = str(file_item.get("storage_ref") or "").strip()
    if _strict_upload_minio_only() and storage_ref.startswith("minio://"):
        return bool(str(file_item.get("local_path") or "").strip())
    return (not _is_file_failed(file_item)) and _has_file_source(file_item, allow_preview=True)


def _summarize_files(files: list[dict[str, Any]], *, limit: int = 3) -> str:
    names: list[str] = []
    for item in files[:limit]:
        name = str(item.get("file_name") or item.get("title") or item.get("file_id") or "").strip()
        if name:
            names.append(name)
    if not names:
        return ""
    suffix = " 等" if len(files) > limit else ""
    return ", ".join(names) + suffix


def _fallback_profile_for_workbook(workbook: dict[str, Any]) -> dict[str, Any]:
    sheets = workbook.get("sheets") if isinstance(workbook.get("sheets"), list) else []
    normalized_sheets: list[dict[str, Any]] = []
    for idx, item in enumerate(sheets):
        if not isinstance(item, dict):
            continue
        normalized_sheets.append(
            {
                "sheet_name": str(item.get("sheet_name") or f"Sheet{idx + 1}"),
                "sheet_index": int(item.get("sheet_index") or idx),
                "column_names": list(item.get("column_names") or []),
                "row_count": int(item.get("row_count") or 0),
            }
        )
    return {
        "file_name": str(workbook.get("file_name") or "uploaded-table"),
        "sheet_count": len(normalized_sheets),
        "sheets": normalized_sheets,
    }


class QaTabularService:
    def load_workbook(self, file_item: dict[str, Any]) -> dict[str, Any]:
        return load_workbook_cached(file_item)

    def plan(self, **kwargs: Any) -> dict[str, Any]:
        return plan_tabular_query(**kwargs)

    def execute(self, *, workbook: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        return execute_tabular_plan(workbook=workbook, plan=plan)

    def execute_compare(self, *, workbooks: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
        return execute_compare_plan(workbooks=workbooks, plan=plan)

    def render_context(self, *, file_name: str, plan: dict[str, Any], result: dict[str, Any]) -> str:
        return build_tabular_result_context(file_name=file_name, plan=plan, result=result)

    def synthesize_answer(self, **kwargs: Any) -> str:
        return build_tabular_answer(**kwargs)

    def iter_synthesize_answer(self, **kwargs: Any) -> Iterator[str]:
        yield from iter_tabular_answer(**kwargs)

    def iter_answer_events(self, **kwargs: Any) -> Iterator[Any]:
        question = str(kwargs.get("question") or "").strip()
        used_files = kwargs.get("used_files") if isinstance(kwargs.get("used_files"), list) else []
        route_hint = str(kwargs.get("route_hint") or "tabular_qa").strip().lower() or "tabular_qa"
        agent = kwargs.get("agent")
        sse_event = kwargs.get("sse_event")
        clean_answer_for_frontend = kwargs.get("clean_answer_for_frontend") or (lambda text: text)
        log_qa_interaction = kwargs.get("log_qa_interaction") or (lambda **_kwargs: None)
        load_pdf_content_fn = kwargs.get("load_pdf_content_fn")
        extract_pdf_text_fn = kwargs.get("extract_pdf_text_fn")
        logger = kwargs.get("logger")
        file_selection = kwargs.get("file_selection") if isinstance(kwargs.get("file_selection"), dict) else {}
        selection_strategy = str(file_selection.get("strategy") or kwargs.get("selection_strategy") or "").strip()
        try:
            max_pdf_chars = max(4000, int(kwargs.get("max_pdf_chars") or 12000))
        except (TypeError, ValueError):
            max_pdf_chars = 12000

        source_scope = str(kwargs.get("source_scope") or "").strip()
        kb_enabled = bool(kwargs.get("kb_enabled") or False)
        kb_evidence_context = str(kwargs.get("kb_evidence_context") or "").strip()
        kb_reference_instruction = str(kwargs.get("kb_reference_instruction") or "").strip()
        kb_references = [
            str(item).strip()
            for item in (kwargs.get("kb_references") or [])
            if str(item or "").strip()
        ]

        table_candidates = [
            item
            for item in used_files
            if isinstance(item, dict) and str(item.get("file_type") or "").strip().lower() in {"excel", "csv"}
        ]
        table_files = [item for item in table_candidates if _is_table_file_usable(item)]
        if _strict_upload_minio_only() and table_candidates and len(table_files) != len(table_candidates):
            pending_hint = _summarize_files(table_candidates)
            message = "表格文件仍在处理中或源文件不可用，请稍后重试"
            if pending_hint:
                message += f"：{pending_hint}"
            yield _emit({"type": "error", "error": message}, sse_event)
            return
        pending_table_files = [item for item in table_files if not _is_file_ready(item)]
        if not table_files:
            if table_candidates:
                pending_hint = _summarize_files(table_candidates)
                message = "表格文件仍在处理中或源文件不可用，请稍后重试"
                if pending_hint:
                    message += f"：{pending_hint}"
                yield _emit({"type": "error", "error": message}, sse_event)
                return
            yield _emit({"type": "error", "error": "未找到可用的表格文件"}, sse_event)
            return

        hybrid_mode = route_hint == "hybrid_qa"
        pdf_candidates = [
            item
            for item in used_files
            if isinstance(item, dict) and str(item.get("file_type") or "").strip().lower() == "pdf"
        ]
        pdf_files = [item for item in pdf_candidates if _is_pdf_file_usable(item)]
        if hybrid_mode and _strict_upload_minio_only() and pdf_candidates and len(pdf_files) != len(pdf_candidates):
            pending_hint = _summarize_files(pdf_candidates)
            message = "PDF 文件仍在处理中或源文件不可用，请稍后重试"
            if pending_hint:
                message += f"：{pending_hint}"
            yield _emit({"type": "error", "error": message}, sse_event)
            return
        pending_pdf_files = [item for item in pdf_files if not _is_file_ready(item)]
        query_mode = "混合文件问答" if hybrid_mode else "表格问答"

        yield _emit({"type": "metadata", "query_mode": query_mode, "expert": "tabular"}, sse_event)
        if pending_table_files:
            yield _emit(
                {
                    "type": "step",
                    "step": "file_readiness",
                    "status": "warning",
                    "message": f"检测到 {len(pending_table_files)} 个表格文件仍在处理中，已尝试直接读取源文件继续回答",
                },
                sse_event,
            )
        if hybrid_mode and pending_pdf_files:
            yield _emit(
                {
                    "type": "step",
                    "step": "file_readiness",
                    "status": "warning",
                    "message": f"检测到 {len(pending_pdf_files)} 个 PDF 文件仍在处理中，已尝试直接提取原始文档证据",
                },
                sse_event,
            )
        yield _emit(
            {
                "type": "thinking",
                "content": f"📊 已匹配 {len(table_files)} 个表格文件，正在加载全表数据...",
            },
            sse_event,
        )

        loaded_tables: list[dict[str, Any]] = []
        for item in table_files[:3]:
            try:
                workbook = self.load_workbook(item)
                try:
                    profile = profile_workbook(workbook)
                except Exception:
                    profile = _fallback_profile_for_workbook(workbook)
                loaded_tables.append(
                    {
                        "file_item": item,
                        "workbook": workbook,
                        "profile": profile,
                    }
                )
            except Exception:
                if _strict_upload_minio_only():
                    yield _emit({"type": "error", "error": "表格文件读取失败，请检查文件格式"}, sse_event)
                    return
                continue

        if not loaded_tables:
            yield _emit({"type": "error", "error": "表格文件读取失败，请检查文件格式"}, sse_event)
            return

        yield _emit({"type": "thinking", "content": "🧭 正在识别工作表、字段和执行意图..."}, sse_event)
        primary_table = loaded_tables[0]
        planned_operation = ""
        operation_guard_applied = False
        plan = self.plan(
            question=question,
            profile=primary_table["profile"],
            profiles=[item["profile"] for item in loaded_tables],
            workbook_count=len(loaded_tables),
            route_hint=route_hint,
            table_file_count=len(table_files),
            selection_strategy=selection_strategy,
        )
        planned_operation = str(plan.get("operation") or "")
        if hybrid_mode and planned_operation == "compare_tables":
            plan = {**plan, "operation": "summary"}
            operation_guard_applied = True
        if plan.get("needs_clarification"):
            message = str(plan.get("clarification_message") or "表格问答需要澄清")
            yield _emit(
                {
                    "type": "step",
                    "step": "tabular_plan",
                    "status": "error",
                    "message": message,
                },
                sse_event,
            )
            yield _emit({"type": "error", "error": message}, sse_event)
            return

        yield _emit(
            {
                "type": "step",
                "step": "tabular_plan",
                "status": "success",
                "message": f"📋 已识别工作表 {plan.get('sheet_name')}，执行操作 {plan.get('operation')}",
            },
            sse_event,
        )

        try:
            if str(plan.get("operation") or "") == "compare_tables":
                execution_result = self.execute_compare(
                    workbooks=[item["workbook"] for item in loaded_tables],
                    plan=plan,
                )
            else:
                execution_result = self.execute(
                    workbook=primary_table["workbook"],
                    plan=plan,
                )
        except Exception as exc:
            yield _emit({"type": "error", "error": f"表格执行失败: {exc}"}, sse_event)
            return

        yield _emit(
            {
                "type": "step",
                "step": "tabular_execute",
                "status": "success",
                "message": f"🧮 已完成全表执行，得到 {int(execution_result.get('row_count_after') or 0)} 条结果记录",
            },
            sse_event,
        )

        summary_stats = execution_result.get("summary_stats") if isinstance(execution_result.get("summary_stats"), dict) else {}
        summary_focus_columns = infer_tabular_summary_focus_columns(question=question, plan=plan, result=execution_result)
        if str(execution_result.get("operation") or "") == "summary":
            yield _emit(
                {
                    "type": "step",
                    "step": "tabular_summary_context",
                    "status": "success",
                    "message": (
                        f"📌 概览聚焦列: {', '.join(summary_focus_columns) or '未识别到明确焦点列'}；"
                        f"代表性样例 {len(execution_result.get('result_rows') or [])} 条"
                    ),
                },
                sse_event,
            )

        pdf_evidence_context = ""
        pdf_references: list[str] = []
        loaded_pdf_count = 0
        pdf_context_source = ""
        if hybrid_mode:
            if callable(load_pdf_content_fn):
                pdf_evidence_context, pdf_references, loaded_pdf_count = build_merged_pdf_context(
                    pdf_files=pdf_files,
                    load_pdf_content_fn=load_pdf_content_fn,
                    question=question,
                    max_pdf_chars=max_pdf_chars,
                    logger=logger,
                )
                pdf_context_source = "pdf_qa_merge" if loaded_pdf_count > 0 else ("parsed_preview" if pdf_evidence_context else "empty")
            elif callable(extract_pdf_text_fn):
                pdf_evidence_context, pdf_references, loaded_pdf_count = build_merged_pdf_context(
                    pdf_files=pdf_files,
                    load_pdf_content_fn=lambda **load_kwargs: (
                        str(extract_pdf_text_fn(str(load_kwargs.get("pdf_path") or "")) or ""),
                        None,
                    ),
                    question=question,
                    max_pdf_chars=max_pdf_chars,
                    logger=logger,
                )
                pdf_context_source = "pdf_text_extract" if loaded_pdf_count > 0 else ("parsed_preview" if pdf_evidence_context else "empty")
        if hybrid_mode and pdf_files:
            if loaded_pdf_count > 0:
                hybrid_message = (
                    f"🧩 已按 PDF 问答方式加载 {loaded_pdf_count} 篇文献原文（chars={len(pdf_evidence_context)}）"
                )
            elif pdf_evidence_context:
                hybrid_message = f"🧩 已加载 {len(pdf_files)} 篇文献预览用于交叉验证（chars={len(pdf_evidence_context)}）"
            else:
                hybrid_message = f"🧩 未能加载 {len(pdf_files)} 篇文献原文，将仅依据表格结果作答"
            yield _emit(
                {
                    "type": "step",
                    "step": "hybrid_evidence",
                    "status": "success" if pdf_evidence_context else "warning",
                    "message": hybrid_message,
                },
                sse_event,
            )


        if kb_enabled:
            yield _emit(
                {
                    "type": "step",
                    "step": "kb_evidence",
                    "status": "success" if kb_evidence_context else "warning",
                    "message": (
                        f"🧠 已加载知识库证据（chars={len(kb_evidence_context)}）"
                        if kb_evidence_context
                        else "🧠 未检索到可用的知识库证据"
                    ),
                },
                sse_event,
            )
        yield _emit({"type": "thinking", "content": "✍️ 正在综合文献与表格材料生成答案..."}, sse_event)
        file_name = str(
            primary_table["workbook"].get("file_name")
            or primary_table["file_item"].get("file_name")
            or "uploaded-table"
        )
        llm = getattr(agent, "llm", None) if agent is not None else None
        clean_state = IncrementalCleanState()
        raw_parts: list[str] = []
        try:
            for piece in self.iter_synthesize_answer(
                question=question,
                file_name=file_name,
                plan=plan,
                result=execution_result,
                route_hint=route_hint,
                llm=llm,
                pdf_evidence_context=pdf_evidence_context,
                kb_evidence_context=kb_evidence_context,
                kb_reference_instruction=kb_reference_instruction,
                source_scope=source_scope,
            ):
                text = str(piece or "")
                if not text:
                    continue
                raw_parts.append(text)
                for event in incremental_clean_events_for_piece(
                    text,
                    state=clean_state,
                    clean_answer_for_frontend=clean_answer_for_frontend,
                    filter_literature_markers_for_streaming=lambda content: content,
                    sse_event=lambda payload: payload,
                ):
                    yield _emit(event, sse_event)
        except Exception:
            fallback = "表格已读取，但模型合成答案失败。请稍后重试，或缩小问题范围后再次提问。"
            raw_parts = [fallback]
            clean_state = IncrementalCleanState()
            for event in incremental_clean_events_for_piece(
                fallback,
                state=clean_state,
                clean_answer_for_frontend=clean_answer_for_frontend,
                filter_literature_markers_for_streaming=lambda content: content,
                sse_event=lambda payload: payload,
            ):
                yield _emit(event, sse_event)

        answer = str(clean_answer_for_frontend("".join(raw_parts)) or "").strip()
        references = list(pdf_references)
        if not references:
            references = [
                extract_doi_from_filename(str(item.get("file_name") or ""))
                for item in pdf_files
            ]
        references = [item for item in references if item]
        for doi in kb_references:
            if doi and doi not in references:
                references.append(doi)

        try:
            log_qa_interaction(
                question=question,
                answer=answer,
                query_mode=query_mode,
                references=references[:15],
                extra={
                    "tabular_branch": True,
                    "hybrid_mode": hybrid_mode,
                    "route_hint": route_hint,
                    "selection_strategy": selection_strategy,
                    "table_file_count": len(table_files),
                    "pdf_file_count": len(pdf_files),
                    "planned_operation": planned_operation or str(plan.get("operation") or ""),
                    "operation_guard_applied": operation_guard_applied,
                    "pdf_context_chars": len(pdf_evidence_context),
                    "pdf_context_source": pdf_context_source,
                    "loaded_pdf_count": loaded_pdf_count,
                    "kb_enabled": kb_enabled,
                    "kb_reference_count": len(kb_references),
                    "kb_evidence_chars": len(kb_evidence_context),
                    "summary_column_count": int(summary_stats.get("column_count") or 0) if str(execution_result.get("operation") or "") == "summary" else 0,
                    "summary_focus_columns": summary_focus_columns,
                    "summary_sample_count": len(execution_result.get("result_rows") or []) if str(execution_result.get("operation") or "") == "summary" else 0,
                    "summary_sample_strategy": str(summary_stats.get("sample_strategy") or "") if str(execution_result.get("operation") or "") == "summary" else "",
                    "streaming": True,
                },
            )
        except Exception:
            pass

        yield _emit(
            {
                "type": "done",
                "references": references,
                "route": route_hint,
            },
            sse_event,
        )


qa_tabular_service = QaTabularService()
