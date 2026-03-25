from __future__ import annotations

import re
from typing import Any, Iterator

from app.modules.qa_tabular.executor import execute_compare_plan, execute_tabular_plan
from app.modules.qa_tabular.planner import plan_tabular_query
from app.modules.qa_pdf.common import IncrementalCleanState, incremental_clean_events_for_piece
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


def _has_file_source(file_item: dict[str, Any], *, allow_preview: bool = False) -> bool:
    local_path = str(file_item.get("local_path") or "").strip()
    storage_ref = str(file_item.get("storage_ref") or "").strip()
    if local_path or storage_ref:
        return True
    if allow_preview:
        file_meta = file_item.get("file_meta") if isinstance(file_item.get("file_meta"), dict) else {}
        parsed_preview = str(file_meta.get("parsed_preview") or "").strip()
        return bool(parsed_preview)
    return False


def _is_table_file_usable(file_item: dict[str, Any]) -> bool:
    return (not _is_file_failed(file_item)) and _has_file_source(file_item, allow_preview=False)


def _is_pdf_file_usable(file_item: dict[str, Any]) -> bool:
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


def _extract_doi_from_filename(file_name: str) -> str:
    text = str(file_name or "").strip()
    if not text:
        return ""
    if "." in text.rsplit("/", 1)[-1]:
        stem, suffix = text.rsplit(".", 1)
        if suffix.lower() in {"pdf", "csv", "xlsx", "xls"}:
            text = stem
    match = re.search(r"(10\.\d+[/_][-._;()/:A-Za-z0-9]+)", text)
    if not match:
        return ""
    return match.group(1).replace("_", "/", 1).rstrip(").,;")


def _format_pdf_evidence_context(pdf_files: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for item in pdf_files[:3]:
        file_name = str(item.get("file_name") or "").strip()
        file_meta = item.get("file_meta") if isinstance(item.get("file_meta"), dict) else {}
        preview = str(file_meta.get("parsed_preview") or "").strip()
        doi = _extract_doi_from_filename(file_name)
        title = file_name or "uploaded.pdf"
        if doi:
            title = f"{title} (DOI: {doi})"
        rows.append(title)
        if preview:
            rows.append(preview[:600])
    return "\n".join(rows).strip()


def _split_text_chunks(text: str, *, max_chars: int = 720, overlap_chars: int = 120, max_chunks: int = 18) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    parts = [part.strip() for part in normalized.split("\n\n") if part.strip()] or [normalized]
    chunks: list[str] = []
    current = ""
    for part in parts:
        if len(current) + len(part) + 2 <= max_chars:
            current = (current + "\n\n" + part).strip() if current else part
            continue
        if current:
            chunks.append(current)
        current = part
        if len(chunks) >= max_chunks:
            break
    if current and len(chunks) < max_chunks:
        chunks.append(current)
    if overlap_chars > 0 and len(chunks) > 1:
        merged = [chunks[0]]
        for chunk in chunks[1:]:
            merged.append((merged[-1][-overlap_chars:] + "\n\n" + chunk).strip())
        chunks = merged
    return chunks[:max_chunks]


def _split_sentences(text: str, *, max_sentences: int = 240) -> list[str]:
    parts = re.split(r"(?<=[。！？?!\.])\s+|\n+", str(text or ""))
    sentences: list[str] = []
    for part in parts:
        value = str(part or "").strip()
        if len(value) < 24:
            continue
        sentences.append(value)
        if len(sentences) >= max_sentences:
            break
    return sentences


def _build_sentence_windows(sentences: list[str], *, window_sizes: tuple[int, ...] = (2, 3), max_windows: int = 120) -> list[str]:
    windows: list[str] = []
    for window_size in window_sizes:
        if window_size <= 1:
            continue
        for index in range(0, max(0, len(sentences) - window_size + 1)):
            window = " ".join(sentences[index : index + window_size]).strip()
            if len(window) < 40:
                continue
            windows.append(window)
            if len(windows) >= max_windows:
                return windows
    return windows


def _dedupe_text_candidates(candidates: list[str], *, limit: int = 80) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = re.sub(r"\s+", " ", str(candidate or "").strip().lower())
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(str(candidate).strip())
        if len(deduped) >= limit:
            break
    return deduped


def _tokenize_text(text: str) -> set[str]:
    tokens: set[str] = set()
    for matched in re.findall(r"[A-Za-z0-9_./+-]+|[\u4e00-\u9fff]{2,8}", str(text or "").lower()):
        token = matched.strip()
        if len(token) > 1:
            tokens.add(token)
    return tokens


def _score_evidence_chunk(*, question: str, text: str) -> float:
    q_tokens = _tokenize_text(question)
    t_tokens = _tokenize_text(text)
    if not q_tokens or not t_tokens:
        return 0.0
    overlap = len(q_tokens & t_tokens)
    numeric_overlap = len(set(re.findall(r"\d+(?:\.\d+)?", str(question or ""))) & set(re.findall(r"\d+(?:\.\d+)?", str(text or ""))))
    exact_hits = sum(1 for token in q_tokens if len(token) >= 3 and token in str(text or "").lower())
    coverage = overlap / max(1, len(q_tokens))
    length_bonus = min(len(str(text or "")), 900) / 900.0
    return coverage * 2.4 + exact_hits * 0.18 + numeric_overlap * 0.8 + length_bonus * 0.2


def _build_evidence_candidates(*, extracted_text: str, preview_text: str) -> list[str]:
    candidates: list[str] = []
    extracted = str(extracted_text or "").strip()
    preview = str(preview_text or "").strip()
    if extracted:
        candidates.extend(_split_text_chunks(extracted, max_chars=680, overlap_chars=100, max_chunks=24))
        sentences = _split_sentences(extracted, max_sentences=220)
        candidates.extend(_build_sentence_windows(sentences, window_sizes=(2, 3), max_windows=80))
    if preview:
        candidates.append(preview[:900])
    return _dedupe_text_candidates(candidates, limit=90)


def _retrieve_hybrid_evidence(
    *,
    question: str,
    pdf_files: list[dict[str, Any]],
    extract_pdf_text_fn: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in pdf_files:
        local_path = str(item.get("local_path") or "").strip()
        file_meta = item.get("file_meta") if isinstance(item.get("file_meta"), dict) else {}
        preview_text = str(file_meta.get("parsed_preview") or "").strip()
        extracted_text = ""
        if local_path and callable(extract_pdf_text_fn):
            try:
                extracted_text = str(extract_pdf_text_fn(local_path) or "").strip()
            except Exception:
                extracted_text = ""
        for idx, chunk in enumerate(_build_evidence_candidates(extracted_text=extracted_text, preview_text=preview_text), start=1):
            score = _score_evidence_chunk(question=question, text=chunk)
            if score <= 0:
                continue
            rows.append(
                {
                    "file_id": int(item.get("file_id") or 0),
                    "file_name": str(item.get("file_name") or ""),
                    "doi": _extract_doi_from_filename(str(item.get("file_name") or "")),
                    "chunk_id": idx,
                    "text": chunk,
                    "score": score,
                    "source_type": "pdf_text" if extracted_text else "parsed_preview",
                }
            )
    rows.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return rows[:8]


def _format_hybrid_evidence_context(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    parts: list[str] = []
    for idx, item in enumerate(rows, start=1):
        header = f"[E{idx}] 文件#{item.get('file_id') or 0} chunk#{item.get('chunk_id') or 0}: {item.get('file_name') or ''}"
        doi = str(item.get("doi") or "")
        if doi:
            header += f" | DOI={doi}"
        parts.append(header)
        parts.append(str(item.get("text") or ""))
    return "\n".join(parts).strip()


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
        extract_pdf_text_fn = kwargs.get("extract_pdf_text_fn")

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
                continue

        if not loaded_tables:
            yield _emit({"type": "error", "error": "表格文件读取失败，请检查文件格式"}, sse_event)
            return

        yield _emit({"type": "thinking", "content": "🧭 正在识别工作表、字段和执行意图..."}, sse_event)
        primary_table = loaded_tables[0]
        plan = self.plan(
            question=question,
            profile=primary_table["profile"],
            profiles=[item["profile"] for item in loaded_tables],
            workbook_count=len(loaded_tables),
        )
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

        hybrid_evidence_rows: list[dict[str, Any]] = []
        pdf_evidence_context = ""
        if hybrid_mode:
            hybrid_evidence_rows = _retrieve_hybrid_evidence(
                question=question,
                pdf_files=pdf_files,
                extract_pdf_text_fn=extract_pdf_text_fn,
            )
            if hybrid_evidence_rows:
                pdf_evidence_context = _format_hybrid_evidence_context(hybrid_evidence_rows)
            else:
                pdf_evidence_context = _format_pdf_evidence_context(pdf_files)
        if hybrid_mode and pdf_files:
            yield _emit(
                {
                    "type": "step",
                    "step": "hybrid_evidence",
                    "status": "success",
                    "message": (
                        f"🧩 已检索到 {len(hybrid_evidence_rows)} 条文献证据片段"
                        if hybrid_evidence_rows
                        else f"🧩 已加载 {len(pdf_files)} 篇文献预览用于交叉验证"
                    ),
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
        yield _emit({"type": "thinking", "content": "✍️ 正在基于真实执行结果生成答案..."}, sse_event)
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
        references = [
            _extract_doi_from_filename(str(item.get("doi") or item.get("file_name") or ""))
            for item in (hybrid_evidence_rows or pdf_files)
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
                    "table_file_count": len(table_files),
                    "pdf_file_count": len(pdf_files),
                    "hybrid_evidence_count": len(hybrid_evidence_rows),
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
