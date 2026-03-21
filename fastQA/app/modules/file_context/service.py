from __future__ import annotations

from typing import Any, Callable

from app.modules.file_context.models import FileContextResult, NormalizedFileRow
from app.modules.file_context.parser import (
    build_clarification_message,
    build_clarify_candidates,
    build_execution_file_payload,
    build_used_file_payload,
    detect_file_intent,
    detect_latest_reference,
    detect_mixed_intent,
    detect_plural_file_reference,
    detect_singular_file_reference,
    extract_explicit_file_refs,
    extract_ordinal_refs,
    normalize_file_row,
    normalize_int_list,
    resolve_ordinal_refs_to_file_ids,
    resolve_refs_to_file_ids,
    sort_files,
)


class FileContextService:
    _TABULAR_SELECTION_HINTS = (
        "表格",
        "工作表",
        "sheet",
        "excel",
        "csv",
        "列",
        "字段",
        "行",
        "筛选",
        "过滤",
        "统计",
        "分组",
        "排序",
    )
    _PDF_SELECTION_HINTS = (
        "文献",
        "论文",
        "pdf",
        "页",
        "章节",
        "图",
        "表",
        "附录",
        "摘要",
        "结论",
        "doi",
    )

    def _normalize_match_text(self, value: Any) -> str:
        return "".join(str(value or "").strip().lower().split())

    def _looks_like_numeric_token(self, token: str) -> bool:
        compact = str(token or "").strip().replace(".", "", 1).replace("-", "", 1)
        return bool(compact) and compact.isdigit()

    def _question_has_tabular_focus(self, *, question: str, selected_files: list[NormalizedFileRow]) -> bool:
        normalized_question = self._normalize_match_text(question)
        if not normalized_question:
            return False

        for token in self._TABULAR_SELECTION_HINTS:
            if self._normalize_match_text(token) in normalized_question:
                return True

        seen_tokens: set[str] = set()
        for item in selected_files:
            file_meta = item.get("file_meta") if isinstance(item.get("file_meta"), dict) else {}
            for column in file_meta.get("columns") or []:
                token = self._normalize_match_text(column)
                if len(token) < 2 or token in seen_tokens:
                    continue
                seen_tokens.add(token)
                if token in normalized_question:
                    return True

            for row in file_meta.get("sample_rows") or []:
                if not isinstance(row, list):
                    continue
                for cell in row:
                    token = self._normalize_match_text(cell)
                    if (
                        len(token) < 3
                        or len(token) > 32
                        or token in seen_tokens
                        or token in {"nan", "none", "null"}
                        or self._looks_like_numeric_token(token)
                    ):
                        continue
                    seen_tokens.add(token)
                    if token in normalized_question:
                        return True

        return False

    def _question_has_pdf_focus(self, *, question: str, selected_files: list[NormalizedFileRow]) -> bool:
        normalized_question = self._normalize_match_text(question)
        if not normalized_question:
            return False

        for token in self._PDF_SELECTION_HINTS:
            if self._normalize_match_text(token) in normalized_question:
                return True

        for item in selected_files:
            file_name = str(item.get("file_name") or "").strip()
            stem = file_name.rsplit(".", 1)[0].strip() if "." in file_name else file_name
            for raw_token in (file_name, stem):
                token = self._normalize_match_text(raw_token)
                if len(token) >= 4 and token in normalized_question:
                    return True
        return False

    def _question_has_selected_file_focus(self, *, question: str, selected_files: list[NormalizedFileRow]) -> bool:
        has_pdf = any(str(item.get("file_type") or "") == "pdf" for item in selected_files)
        has_table = any(str(item.get("file_type") or "") in {"excel", "csv"} for item in selected_files)
        if has_table and self._question_has_tabular_focus(question=question, selected_files=selected_files):
            return True
        if has_pdf and self._question_has_pdf_focus(question=question, selected_files=selected_files):
            return True
        return False

    def resolve_request_file_context(
        self,
        *,
        question: str,
        conversation_id: int | None,
        pdf_context: dict[str, Any] | None,
        current_pdf_path: str | None,
        list_uploaded_files_fn: Callable[[int], list[dict[str, Any]]] | None,
        logger: Any,
        max_selected_files: int = 10,
    ) -> FileContextResult:
        ctx = pdf_context if isinstance(pdf_context, dict) else {}
        explicit_refs = extract_explicit_file_refs(question)
        ordinal_refs = extract_ordinal_refs(question)
        selected_ids = normalize_int_list(ctx.get("selected_ids") or ctx.get("selected_file_ids"))
        newly_uploaded_ids = normalize_int_list(ctx.get("newly_uploaded_ids") or ctx.get("newly_uploaded_file_ids"))
        all_available_ids = normalize_int_list(ctx.get("all_available_ids") or ctx.get("all_available_file_ids"))
        last_focus_ids = normalize_int_list(ctx.get("last_focus_ids") or ctx.get("last_focus_file_ids"))
        last_turn_route = str(ctx.get("last_turn_route") or "").strip().lower()
        file_intent = detect_file_intent(question, explicit_refs=explicit_refs)
        mixed_intent = detect_mixed_intent(question)
        plural_ref = detect_plural_file_reference(question)
        singular_ref = detect_singular_file_reference(question)
        latest_ref = detect_latest_reference(question)
        can_reuse_last_focus = (
            not last_turn_route or last_turn_route in {"pdf_qa", "tabular_qa", "hybrid_qa", "file_only", "mixed"}
        )

        files: list[dict[str, Any]] = []
        if conversation_id and callable(list_uploaded_files_fn):
            try:
                files = list_uploaded_files_fn(int(conversation_id)) or []
            except Exception as exc:
                logger.warning(f"resolve file context failed to list conversation files: {exc}")
                files = []

        file_map: dict[int, NormalizedFileRow] = {}
        for raw in files:
            if not isinstance(raw, dict):
                continue
            normalized = normalize_file_row(raw)
            file_id = int(normalized.get("file_id") or 0)
            if file_id <= 0:
                continue
            file_map[file_id] = normalized

        sorted_files = sort_files(list(file_map.values()))
        active_sorted_files = [
            row for row in sorted_files if str(row.get("file_status") or "active").strip().lower() != "deleted"
        ]
        for idx, row in enumerate(active_sorted_files, start=1):
            row["display_no"] = idx
        file_no_map: dict[int, NormalizedFileRow] = {}
        display_no_map: dict[int, NormalizedFileRow] = {}
        active_file_map: dict[int, NormalizedFileRow] = {}
        active_file_no_map: dict[int, NormalizedFileRow] = {}
        deleted_file_map: dict[int, NormalizedFileRow] = {}
        deleted_file_no_map: dict[int, NormalizedFileRow] = {}
        for row in sorted_files:
            file_no = int(row.get("file_no") or 0)
            file_id = int(row.get("file_id") or 0)
            status = str(row.get("file_status") or "active").strip().lower()
            if file_no > 0 and file_no not in file_no_map:
                file_no_map[file_no] = row
            if status == "deleted":
                if file_id > 0:
                    deleted_file_map[file_id] = row
                if file_no > 0 and file_no not in deleted_file_no_map:
                    deleted_file_no_map[file_no] = row
                continue
            display_no = int(row.get("display_no") or 0)
            if file_id > 0:
                active_file_map[file_id] = row
            if display_no > 0 and display_no not in display_no_map:
                display_no_map[display_no] = row
            if file_no > 0 and file_no not in active_file_no_map:
                active_file_no_map[file_no] = row

        explicit_ref_map = dict(display_no_map)
        for key, value in active_file_no_map.items():
            explicit_ref_map.setdefault(key, value)

        matched_newly_uploaded = resolve_refs_to_file_ids(
            newly_uploaded_ids,
            file_no_map=explicit_ref_map,
            file_id_map=active_file_map,
        )

        selected_file_ids: list[int] = []
        strategy = "none"
        needs_clarification = False
        clarification_message = ""
        clarify_candidates = []
        selection_semantic = "none"

        if explicit_refs:
            matched = resolve_refs_to_file_ids(
                explicit_refs,
                file_no_map=explicit_ref_map,
                file_id_map=active_file_map,
            )
            if matched and len(matched) == len(explicit_refs):
                selected_file_ids = matched
                strategy = "explicit_ref"
                selection_semantic = "absolute_no"
            else:
                unresolved: list[int] = []
                for value in explicit_refs:
                    active_target = explicit_ref_map.get(value) or active_file_map.get(value)
                    if not active_target:
                        unresolved.append(value)
                deleted_refs = [value for value in unresolved if deleted_file_no_map.get(value) or deleted_file_map.get(value)]
                missing_refs = [value for value in unresolved if value not in deleted_refs]
                needs_clarification = True
                strategy = "explicit_ref_deleted" if deleted_refs else "explicit_ref_missing"
                selection_semantic = "clarify"
                clarify_candidates = build_clarify_candidates(active_sorted_files)
                if deleted_refs:
                    deleted_text = "、".join(f"#{value}" for value in deleted_refs)
                    if clarify_candidates:
                        clarification_message = (
                            f"你指定的文件 {deleted_text} 已从当前对话移除。\n"
                            + build_clarification_message(clarify_candidates)
                        )
                    else:
                        clarification_message = f"你指定的文件 {deleted_text} 已从当前对话移除，当前会话没有可用上传文件。"
                elif missing_refs:
                    missing_text = "、".join(f"#{value}" for value in missing_refs)
                    if clarify_candidates:
                        clarification_message = (
                            f"未找到你指定的文件编号 {missing_text}。\n" + build_clarification_message(clarify_candidates)
                        )
                    else:
                        clarification_message = f"未找到你指定的文件编号 {missing_text}，请先上传文件后再提问。"

        prefer_recent_upload_for_singular = singular_ref and bool(matched_newly_uploaded)

        if (
            not selected_file_ids
            and not needs_clarification
            and selected_ids
            and not prefer_recent_upload_for_singular
        ):
            matched = resolve_refs_to_file_ids(
                selected_ids,
                file_no_map=explicit_ref_map,
                file_id_map=active_file_map,
            )
            if matched:
                selected_file_ids = matched
                strategy = "selected_ids"
                selection_semantic = "client_selected"

        if (
            not selected_file_ids
            and not needs_clarification
            and file_intent
            and bool(ordinal_refs.get("has_ordinal"))
        ):
            matched = resolve_ordinal_refs_to_file_ids(ordinal_refs=ordinal_refs, sorted_files=active_sorted_files)
            if matched:
                selected_file_ids = matched
                strategy = "ordinal_ref"
                selection_semantic = "ordinal_index"

        if (
            not selected_file_ids
            and not needs_clarification
            and file_intent
            and bool(ordinal_refs.get("has_ambiguous"))
            and active_sorted_files
        ):
            needs_clarification = True
            clarify_candidates = build_clarify_candidates(active_sorted_files)
            clarification_message = build_clarification_message(clarify_candidates, include_order_hint=True)
            strategy = "ordinal_ambiguous"
            selection_semantic = "clarify"

        if not selected_file_ids and file_intent and not needs_clarification:
            if plural_ref and active_sorted_files:
                selected_file_ids = [int(item.get("file_id") or 0) for item in active_sorted_files]
                strategy = "plural_all"
                selection_semantic = "plural_scope"
            elif latest_ref and active_sorted_files:
                selected_file_ids = [int(active_sorted_files[-1].get("file_id") or 0)]
                strategy = "latest_file"
                selection_semantic = "latest_fallback"
            elif singular_ref:
                if matched_newly_uploaded:
                    selected_file_ids = [int(matched_newly_uploaded[-1])]
                    strategy = "latest_newly_uploaded"
                    selection_semantic = "latest_recent_upload"
                else:
                    matched_last_focus: list[int] = []
                    if can_reuse_last_focus:
                        matched_last_focus = resolve_refs_to_file_ids(
                            last_focus_ids,
                            file_no_map=explicit_ref_map,
                            file_id_map=active_file_map,
                        )
                    if len(matched_last_focus) == 1:
                        selected_file_ids = matched_last_focus
                        strategy = "last_focus"
                        selection_semantic = "last_focus"
                    elif len(active_sorted_files) == 1:
                        selected_file_ids = [int(active_sorted_files[0].get("file_id") or 0)]
                        strategy = "single_file_auto"
                        selection_semantic = "single_file_auto"
                    elif len(active_sorted_files) > 1:
                        needs_clarification = True
                        clarify_candidates = build_clarify_candidates(active_sorted_files)
                        clarification_message = build_clarification_message(clarify_candidates)
                        strategy = "clarify_required"
                        selection_semantic = "clarify"
            else:
                candidate_sources: list[tuple[list[int], str]] = []
                if can_reuse_last_focus:
                    candidate_sources.append((last_focus_ids, "last_focus"))
                candidate_sources.extend(
                    [
                        (newly_uploaded_ids, "newly_uploaded"),
                        (all_available_ids, "all_available"),
                    ]
                )
                for ids, name in candidate_sources:
                    if not ids:
                        continue
                    matched = resolve_refs_to_file_ids(
                        ids,
                        file_no_map=explicit_ref_map,
                        file_id_map=active_file_map,
                    )
                    if matched:
                        selected_file_ids = matched
                        strategy = name
                        if name == "last_focus":
                            selection_semantic = "last_focus"
                        elif name == "newly_uploaded":
                            selection_semantic = "recent_upload_scope"
                        else:
                            selection_semantic = "available_scope"
                        break

        if (
            not selected_file_ids
            and file_intent
            and str(ctx.get("strategy") or "").strip().lower() == "all"
            and active_sorted_files
        ):
            selected_file_ids = [int(item.get("file_id") or 0) for item in active_sorted_files]
            strategy = "all_files_fallback"
            selection_semantic = "all_scope"

        if not selected_file_ids and file_intent and not needs_clarification:
            if len(active_sorted_files) == 1:
                selected_file_ids = [int(active_sorted_files[0].get("file_id") or 0)]
                strategy = "single_file_auto"
                selection_semantic = "single_file_auto"
            elif singular_ref and len(active_sorted_files) > 1:
                needs_clarification = True
                clarify_candidates = build_clarify_candidates(active_sorted_files)
                clarification_message = build_clarification_message(clarify_candidates)
                strategy = "clarify_required"
                selection_semantic = "clarify"

        if max_selected_files > 0:
            selected_file_ids = selected_file_ids[:max_selected_files]

        selected_files = [active_file_map[file_id] for file_id in selected_file_ids if file_id in active_file_map]
        used_files = [build_used_file_payload(file_row=item, selected_reason=strategy) for item in selected_files]
        execution_files = [build_execution_file_payload(file_row=item, selected_reason=strategy) for item in selected_files]

        ready_file_ids = [
            int(item.get("file_id") or 0)
            for item in selected_files
            if str(item.get("index_status") or "").strip().lower() == "ready"
        ]
        failed_file_ids = [
            int(item.get("file_id") or 0)
            for item in selected_files
            if str(item.get("parse_status") or "").strip().lower() == "failed"
            or str(item.get("index_status") or "").strip().lower() == "failed"
            or str(item.get("processing_stage") or "").strip().lower() == "failed"
        ]
        ready_set = set(ready_file_ids)
        failed_set = set(failed_file_ids)
        pending_file_ids = [
            int(item.get("file_id") or 0)
            for item in selected_files
            if int(item.get("file_id") or 0) not in ready_set and int(item.get("file_id") or 0) not in failed_set
        ]

        primary_pdf_path = ""
        primary_table_path = ""
        selected_has_pdf = False
        selected_has_table = False
        for item in selected_files:
            file_type = str(item.get("file_type") or "")
            if file_type == "pdf":
                selected_has_pdf = True
            if file_type in {"excel", "csv"}:
                selected_has_table = True
            if file_type == "pdf" and str(item.get("local_path")):
                primary_pdf_path = str(item.get("local_path"))
            if file_type in {"excel", "csv"} and str(item.get("local_path")) and not primary_table_path:
                primary_table_path = str(item.get("local_path"))

        if not primary_pdf_path and not conversation_id and file_intent:
            fallback_pdf = str(current_pdf_path or "").strip()
            if fallback_pdf:
                primary_pdf_path = fallback_pdf
                if strategy == "none":
                    strategy = "current_pdf_fallback"
                if not selected_files:
                    selected_has_pdf = True

        if selected_has_pdf and selected_has_table:
            route_hint = "hybrid_qa"
        elif selected_has_pdf:
            route_hint = "pdf_qa"
        elif selected_has_table:
            route_hint = "tabular_qa"
        else:
            route_hint = "kb_qa"

        if (
            strategy == "selected_ids"
            and not file_intent
            and not self._question_has_selected_file_focus(question=question, selected_files=selected_files)
        ):
            route_hint = "kb_qa"
            used_files = []
            execution_files = []
            ready_file_ids = []
            pending_file_ids = []
            failed_file_ids = []
            primary_table_path = ""

        has_selected_files = bool(selected_file_ids)
        if (file_intent or has_selected_files) and route_hint in {"pdf_qa", "tabular_qa", "hybrid_qa"}:
            question_mode = "mixed_task" if mixed_intent else "file_task"
        else:
            question_mode = "kb_task"

        if route_hint == "file_clarify":
            turn_mode = "file_only"
        elif question_mode == "mixed_task":
            turn_mode = "mixed"
        elif question_mode == "file_task":
            turn_mode = "file_only"
        else:
            turn_mode = "kb_only"

        allow_kb_verification = question_mode == "mixed_task" and route_hint in {"pdf_qa", "tabular_qa", "hybrid_qa"}

        if selected_file_ids or needs_clarification:
            logger.info(
                "📎 文件上下文解析: "
                f"conversation_id={conversation_id}, strategy={strategy}, selected={selected_file_ids}, "
                f"file_intent={int(file_intent)}, clarify={int(needs_clarification)}"
            )

        return {
            "strategy": strategy,
            "file_intent": bool(file_intent),
            "needs_clarification": bool(needs_clarification),
            "clarification_message": clarification_message,
            "clarify_candidates": clarify_candidates,
            "explicit_file_ids": explicit_refs,
            "selected_file_ids": selected_file_ids,
            "used_files": used_files,
            "execution_files": execution_files,
            "ready_file_ids": ready_file_ids,
            "pending_file_ids": pending_file_ids,
            "failed_file_ids": failed_file_ids,
            "selected_has_pdf": selected_has_pdf,
            "selected_has_table": selected_has_table,
            "primary_pdf_path": primary_pdf_path or None,
            "primary_table_path": primary_table_path or None,
            "route_hint": route_hint,
            "question_mode": question_mode,
            "turn_mode": turn_mode,
            "allow_kb_verification": allow_kb_verification,
            "selection_semantic": selection_semantic,
        }


file_context_service = FileContextService()


def resolve_request_file_context(
    *,
    question: str,
    conversation_id: int | None,
    pdf_context: dict[str, Any] | None,
    current_pdf_path: str | None,
    list_uploaded_files_fn: Callable[[int], list[dict[str, Any]]] | None,
    logger: Any,
    max_selected_files: int = 10,
) -> FileContextResult:
    return file_context_service.resolve_request_file_context(
        question=question,
        conversation_id=conversation_id,
        pdf_context=pdf_context,
        current_pdf_path=current_pdf_path,
        list_uploaded_files_fn=list_uploaded_files_fn,
        logger=logger,
        max_selected_files=max_selected_files,
    )
