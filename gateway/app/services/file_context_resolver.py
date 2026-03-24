"""Gateway-owned file context resolver focused on intent judgment."""

from __future__ import annotations

import re
from typing import Any

from app.models.files import ConversationFileRow
from app.models.routing import FileContextDecision

_GENERIC_FILE_WORDS = (
    "文献",
    "论文",
    "文件",
    "paper",
    "papers",
    "file",
    "files",
)
_SINGULAR_FILE_REFS = (
    "这篇文献",
    "这篇论文",
    "这个文件",
    "这份文件",
    "该文献",
    "该文件",
    "this paper",
    "this file",
)
_PLURAL_FILE_REFS = (
    "这些文献",
    "这些文件",
    "所有文献",
    "所有文件",
    "全部文献",
    "all files",
    "all papers",
)
_LATEST_FILE_REFS = (
    "最新上传",
    "刚上传",
    "刚才上传",
    "latest uploaded",
    "latest file",
)
_UPLOAD_CONTEXT_WORDS = (
    "上传",
    "uploaded",
)
_TABLE_FILE_WORDS = (
    "表格",
    "sheet",
    "excel",
    "csv",
    "工作表",
)
_TABLE_OPERATION_WORDS = (
    "列",
    "字段",
    "行",
    "筛选",
    "过滤",
    "统计",
    "分组",
    "排序",
)
_MIXED_HINTS = (
    "结合知识库",
    "结合外部知识",
    "并用知识库",
    "知识库补充",
    "结合数据库",
    "knowledge base",
)
_MIXED_ACTION_HINTS = (
    "结合",
    "并",
    "同时",
    "一起",
    "参考",
    "补充",
    "验证",
    "讲解",
    "解释",
    "分析",
)
_FILE_ROUTE_HINTS = {"pdf_qa", "tabular_qa", "hybrid_qa", "file_only", "mixed"}

_DIRECT_ORDINAL_PATTERN = re.compile(r"第\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
_FRONT_ORDINAL_PATTERN = re.compile(r"前\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
_BACK_ORDINAL_PATTERN = re.compile(r"后\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
_REVERSE_ORDINAL_PATTERN = re.compile(r"倒数第\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
_DEICTIC_COUNT_PATTERN = re.compile(r"这\s*([0-9零〇一二两三四五六七八九十]+)\s*(?:篇(?:文献|论文)|个(?:文件|表格|pdf|excel|csv))")
_EXPLICIT_REF_PATTERN = re.compile(r"#\s*(\d+)")


class FileContextResolver:
    def resolve(
        self,
        *,
        question: str,
        pdf_context: dict[str, Any] | None = None,
        available_files: list[ConversationFileRow] | None = None,
    ) -> FileContextDecision:
        text = str(question or "").strip()
        ctx = pdf_context if isinstance(pdf_context, dict) else {}
        file_rows = [row for row in (available_files or []) if isinstance(row, ConversationFileRow)]
        active_rows = [row for row in file_rows if not row.is_deleted]
        file_map = {row.file_id: row for row in active_rows}

        raw_selected_ids = self._normalize_int_list(ctx.get("selected_ids"))
        raw_newly_uploaded_ids = self._normalize_int_list(ctx.get("newly_uploaded_ids"))
        raw_all_available_ids = self._normalize_int_list(ctx.get("all_available_ids"))
        raw_last_focus_source = ctx.get("last_focus_ids") if "last_focus_ids" in ctx else ctx.get("last_focus_file_ids")
        raw_last_focus_ids = self._normalize_int_list(raw_last_focus_source)

        selected_ids = self._filter_known_ids(raw_selected_ids, file_map) or raw_selected_ids
        newly_uploaded_ids = self._filter_known_ids(raw_newly_uploaded_ids, file_map) or raw_newly_uploaded_ids
        all_available_ids = self._filter_known_ids(raw_all_available_ids, file_map) or raw_all_available_ids or [row.file_id for row in active_rows]
        last_focus_ids = self._filter_known_ids(raw_last_focus_ids, file_map)
        last_turn_route = str(ctx.get("last_turn_route") or "").strip().lower()
        candidate_ids = selected_ids or all_available_ids

        if not text:
            return self._kb_only(selected_ids=selected_ids)

        lower = text.lower()
        explicit_refs = self._extract_explicit_refs(text)
        ordinal_selection = self._extract_ordinal_selection(text=text, candidates=all_available_ids)
        deictic_count_selection = self._extract_deictic_count_selection(text=text, candidates=candidate_ids)
        singular_ref = self._contains_any(lower, _SINGULAR_FILE_REFS)
        plural_ref = self._contains_any(lower, _PLURAL_FILE_REFS)
        latest_ref = self._contains_any(lower, _LATEST_FILE_REFS)
        mixed_intent = self._detect_mixed_intent(lower)
        table_focus = self._question_has_table_focus(lower=lower, active_rows=active_rows, candidate_ids=candidate_ids)
        file_name_focus = self._question_has_file_name_focus(lower=lower, file_map=file_map, candidate_ids=candidate_ids)

        strong_file_intent = bool(explicit_refs or ordinal_selection or deictic_count_selection or singular_ref or plural_ref or latest_ref)
        upload_file_intent = self._contains_any(lower, _UPLOAD_CONTEXT_WORDS) and bool(candidate_ids or newly_uploaded_ids)
        generic_file_topic = self._contains_any(lower, _GENERIC_FILE_WORDS)
        file_intent = strong_file_intent or upload_file_intent or table_focus or file_name_focus

        if explicit_refs:
            resolved = self._resolve_explicit_refs(explicit_refs, all_available_ids or selected_ids or newly_uploaded_ids)
            if resolved:
                return self._file_turn(
                    route=self._route_for_selection(selected_ids=resolved, file_map=file_map, table_focus=table_focus),
                    selected_file_ids=resolved,
                    strategy="explicit_ref",
                    allow_kb_verification=mixed_intent,
                    file_map=file_map,
                )
            return self._clarify(selected_ids=selected_ids or all_available_ids, message="文件编号无法唯一解析，请明确指定文件")

        if ordinal_selection:
            return self._file_turn(
                route=self._route_for_selection(selected_ids=ordinal_selection, file_map=file_map, table_focus=table_focus),
                selected_file_ids=ordinal_selection,
                strategy="ordinal_ref",
                allow_kb_verification=mixed_intent,
                file_map=file_map,
            )

        if deictic_count_selection:
            return self._file_turn(
                route=self._route_for_selection(selected_ids=deictic_count_selection, file_map=file_map, table_focus=table_focus),
                selected_file_ids=deictic_count_selection,
                strategy="deictic_count_scope",
                allow_kb_verification=mixed_intent,
                file_map=file_map,
            )

        if not file_intent:
            return self._kb_only(selected_ids=selected_ids)

        if generic_file_topic and not strong_file_intent and not upload_file_intent and not table_focus and not file_name_focus:
            return self._kb_only(selected_ids=selected_ids)

        if plural_ref and candidate_ids:
            return self._file_turn(
                route=self._route_for_selection(selected_ids=candidate_ids, file_map=file_map, table_focus=table_focus),
                selected_file_ids=candidate_ids,
                strategy="plural_scope",
                allow_kb_verification=mixed_intent,
                file_map=file_map,
            )

        if latest_ref and newly_uploaded_ids:
            selected = [newly_uploaded_ids[-1]]
            return self._file_turn(
                route=self._route_for_selection(selected_ids=selected, file_map=file_map, table_focus=table_focus),
                selected_file_ids=selected,
                strategy="latest_new_upload",
                allow_kb_verification=mixed_intent,
                file_map=file_map,
            )

        if singular_ref:
            if len(selected_ids) == 1:
                return self._file_turn(
                    route=self._route_for_selection(selected_ids=selected_ids, file_map=file_map, table_focus=table_focus),
                    selected_file_ids=selected_ids,
                    strategy="selected_single",
                    allow_kb_verification=mixed_intent,
                    file_map=file_map,
                )
            if len(last_focus_ids) == 1 and last_turn_route in _FILE_ROUTE_HINTS:
                return self._file_turn(
                    route=self._route_from_last_focus(last_turn_route=last_turn_route, file_map=file_map, selected_ids=last_focus_ids, table_focus=table_focus),
                    selected_file_ids=last_focus_ids,
                    strategy="last_focus",
                    allow_kb_verification=mixed_intent,
                    file_map=file_map,
                )
            if newly_uploaded_ids:
                selected = [newly_uploaded_ids[-1]]
                return self._file_turn(
                    route=self._route_for_selection(selected_ids=selected, file_map=file_map, table_focus=table_focus),
                    selected_file_ids=selected,
                    strategy="latest_new_upload",
                    allow_kb_verification=mixed_intent,
                    file_map=file_map,
                )
            if len(candidate_ids) == 1:
                selected = [candidate_ids[0]]
                return self._file_turn(
                    route=self._route_for_selection(selected_ids=selected, file_map=file_map, table_focus=table_focus),
                    selected_file_ids=selected,
                    strategy="single_candidate",
                    allow_kb_verification=mixed_intent,
                    file_map=file_map,
                )
            if len(candidate_ids) > 1:
                return self._clarify(selected_ids=candidate_ids, message="当前对话中有多个候选文件，请明确指定文件")

        if (table_focus or file_name_focus) and candidate_ids:
            return self._file_turn(
                route=self._route_for_selection(selected_ids=candidate_ids, file_map=file_map, table_focus=table_focus),
                selected_file_ids=candidate_ids,
                strategy="metadata_focus_scope",
                allow_kb_verification=mixed_intent,
                file_map=file_map,
            )

        return self._kb_only(selected_ids=selected_ids)

    def _kb_only(self, *, selected_ids: list[int]) -> FileContextDecision:
        return FileContextDecision(
            route="kb_qa",
            turn_mode="kb_only",
            allow_kb_verification=False,
            selected_file_ids=selected_ids,
            strategy="none" if not selected_ids else "selected_ids_no_file_intent",
        )

    def _clarify(self, *, selected_ids: list[int], message: str) -> FileContextDecision:
        return FileContextDecision(
            route="kb_qa",
            turn_mode="kb_only",
            allow_kb_verification=False,
            needs_clarification=True,
            clarification_message=message,
            selected_file_ids=selected_ids,
            strategy="clarify_required",
        )

    def _file_turn(
        self,
        *,
        route: str,
        selected_file_ids: list[int],
        strategy: str,
        allow_kb_verification: bool,
        file_map: dict[int, ConversationFileRow],
    ) -> FileContextDecision:
        turn_mode = "mixed" if allow_kb_verification else "file_only"
        used_files = [self._build_file_payload(file_id, strategy, file_map) for file_id in selected_file_ids]
        return FileContextDecision(
            route=route,
            turn_mode=turn_mode,
            allow_kb_verification=allow_kb_verification,
            selected_file_ids=selected_file_ids,
            used_files=used_files,
            execution_files=list(used_files),
            strategy=strategy,
        )

    def _build_file_payload(self, file_id: int, strategy: str, file_map: dict[int, ConversationFileRow]) -> dict[str, Any]:
        row = file_map.get(file_id)
        payload = {
            "file_id": int(file_id),
            "selected_reason": strategy,
            "source": "gateway_file_context",
        }
        if row is None:
            return payload
        payload.update(
            {
                "file_type": row.file_type,
                "file_name": row.file_name,
                "local_path": row.local_path,
                "storage_ref": row.storage_ref,
                "file_status": row.file_status,
                "parse_status": row.parse_status,
                "index_status": row.index_status,
                "processing_stage": row.processing_stage,
                "file_meta": row.file_meta,
            }
        )
        return payload

    def _route_for_selection(self, *, selected_ids: list[int], file_map: dict[int, ConversationFileRow], table_focus: bool) -> str:
        has_pdf = any((file_map.get(file_id).is_pdf if file_map.get(file_id) else False) for file_id in selected_ids)
        has_table = any((file_map.get(file_id).is_table if file_map.get(file_id) else False) for file_id in selected_ids)
        if has_pdf and has_table:
            return "hybrid_qa"
        if has_table:
            return "tabular_qa"
        if table_focus:
            return "tabular_qa"
        return "pdf_qa"

    def _route_from_last_focus(
        self,
        *,
        last_turn_route: str,
        file_map: dict[int, ConversationFileRow],
        selected_ids: list[int],
        table_focus: bool,
    ) -> str:
        if table_focus:
            return self._route_for_selection(selected_ids=selected_ids, file_map=file_map, table_focus=True)
        if last_turn_route in {"pdf_qa", "tabular_qa", "hybrid_qa"}:
            return last_turn_route
        return self._route_for_selection(selected_ids=selected_ids, file_map=file_map, table_focus=False)

    def _question_has_table_focus(self, *, lower: str, active_rows: list[ConversationFileRow], candidate_ids: list[int]) -> bool:
        if self._contains_any(lower, _TABLE_FILE_WORDS) or self._contains_any(lower, _TABLE_OPERATION_WORDS):
            return True
        candidate_set = set(candidate_ids)
        for row in active_rows:
            if candidate_set and row.file_id not in candidate_set:
                continue
            if not row.is_table:
                continue
            file_meta = row.file_meta if isinstance(row.file_meta, dict) else {}
            for column in file_meta.get("columns") or []:
                token = str(column or "").strip().lower()
                if len(token) >= 2 and token in lower:
                    return True
        return False

    def _question_has_file_name_focus(self, *, lower: str, file_map: dict[int, ConversationFileRow], candidate_ids: list[int]) -> bool:
        for file_id in candidate_ids:
            row = file_map.get(file_id)
            if row is None:
                continue
            for raw_name in (row.file_name, row.file_name.rsplit('.', 1)[0] if '.' in row.file_name else row.file_name):
                token = ''.join(str(raw_name or '').strip().lower().split())
                if len(token) >= 4 and token in ''.join(lower.split()):
                    return True
        return False

    def _filter_known_ids(self, ids: list[int], file_map: dict[int, ConversationFileRow]) -> list[int]:
        if not ids or not file_map:
            return []
        return [file_id for file_id in ids if file_id in file_map]

    def _extract_explicit_refs(self, text: str) -> list[int]:
        seen: set[int] = set()
        result: list[int] = []
        for matched in _EXPLICIT_REF_PATTERN.findall(text):
            parsed = int(matched)
            if parsed <= 0 or parsed in seen:
                continue
            seen.add(parsed)
            result.append(parsed)
        return result

    def _resolve_explicit_refs(self, refs: list[int], candidates: list[int]) -> list[int]:
        if not refs or not candidates:
            return []
        resolved: list[int] = []
        for ref in refs:
            index = ref - 1
            if index < 0 or index >= len(candidates):
                return []
            resolved.append(candidates[index])
        return resolved

    def _extract_ordinal_selection(self, *, text: str, candidates: list[int]) -> list[int]:
        if not candidates:
            return []

        direct = self._extract_numbers(_DIRECT_ORDINAL_PATTERN, text)
        if direct:
            return self._resolve_explicit_refs(direct, candidates)

        front = self._extract_numbers(_FRONT_ORDINAL_PATTERN, text)
        if front:
            count = min(front[0], len(candidates))
            return candidates[:count]

        back = self._extract_numbers(_BACK_ORDINAL_PATTERN, text)
        if back:
            count = min(back[0], len(candidates))
            return candidates[-count:]

        reverse = self._extract_numbers(_REVERSE_ORDINAL_PATTERN, text)
        if reverse:
            index = reverse[0]
            if 0 < index <= len(candidates):
                return [candidates[-index]]
        return []

    def _extract_deictic_count_selection(self, *, text: str, candidates: list[int]) -> list[int]:
        if not candidates:
            return []
        counts = self._extract_numbers(_DEICTIC_COUNT_PATTERN, text)
        if not counts:
            return []
        count = counts[0]
        if count <= 0:
            return []
        if count == len(candidates):
            return candidates[:count]
        return []

    def _extract_numbers(self, pattern: re.Pattern[str], text: str) -> list[int]:
        result: list[int] = []
        seen: set[int] = set()
        for token in pattern.findall(text):
            value = self._parse_numeric_token(token)
            if value is None or value <= 0 or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _parse_numeric_token(self, token: str) -> int | None:
        text = str(token or "").strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            pass
        digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if text in digits:
            return digits[text]
        if text == "十":
            return 10
        if "十" in text:
            left, right = text.split("十", 1)
            if left and left not in digits:
                return None
            if right and right not in digits:
                return None
            tens = digits[left] if left else 1
            ones = digits[right] if right else 0
            return tens * 10 + ones
        return None

    def _contains_any(self, text: str, tokens: tuple[str, ...]) -> bool:
        return any(token in text for token in tokens)

    def _detect_mixed_intent(self, text: str) -> bool:
        if self._contains_any(text, _MIXED_HINTS):
            return True
        has_kb_token = ("知识库" in text) or ("knowledge base" in text) or (" kb " in f" {text} ")
        if not has_kb_token:
            return False
        return self._contains_any(text, _MIXED_ACTION_HINTS)

    def _normalize_int_list(self, value: Any) -> list[int]:
        if not isinstance(value, list):
            return []
        result: list[int] = []
        seen: set[int] = set()
        for item in value:
            try:
                parsed = int(item)
            except (TypeError, ValueError):
                continue
            if parsed <= 0 or parsed in seen:
                continue
            seen.add(parsed)
            result.append(parsed)
        return result
