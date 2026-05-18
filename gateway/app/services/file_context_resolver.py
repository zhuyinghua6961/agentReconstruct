"""Gateway-owned file context resolver focused on intent judgment."""

from __future__ import annotations

import logging
import re
import os
from typing import Any

from app.models.files import ConversationFileRow
from app.models.routing import FileContextDecision
from app.services.route_classifier import ClassifierDecision, ClassifierThresholdPolicy, NoopRouteClassifier, RouteClassifier

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
_SINGULAR_TABLE_REFS = (
    "这个表格",
    "这张表",
    "这份表格",
    "该表格",
    "该表",
    "这个excel",
    "这个 excel",
    "这个csv",
    "这个 csv",
    "这个工作表",
    "该工作表",
    "this table",
    "this sheet",
    "this excel",
    "this csv",
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
)
_SELECTED_SCOPE_REFS = (
    "所选文件",
    "所选文献",
    "所选论文",
    "选中文件",
    "选中的文件",
    "选中的文献",
    "选中的论文",
    "当前选中的文件",
    "当前选择的文件",
    "selected file",
    "selected files",
)
_FILE_CONTENT_REFS = (
    "文献内容",
    "论文内容",
    "文件内容",
    "paper content",
    "file content",
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
_FILE_ACTION_WORDS = (
    "总结",
    "概括",
    "对比",
    "比较",
    "分析",
    "解读",
    "说明",
    "解释",
    "评估",
    "梳理",
    "review",
    "summarize",
    "compare",
    "analyze",
)
_GENERIC_META_QUESTION_HINTS = (
    "怎么写",
    "怎么做",
    "怎么处理",
    "方法",
    "哪些方面",
    "哪些维度",
    "有哪些",
    "应该",
)
_FILE_ROUTE_HINTS = {"pdf_qa", "tabular_qa", "hybrid_qa"}
_LOGGER = logging.getLogger("gateway.file_context_resolver")

_DIRECT_ORDINAL_PATTERN = re.compile(r"第\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
_FRONT_ORDINAL_PATTERN = re.compile(r"前\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
_BACK_ORDINAL_PATTERN = re.compile(r"后\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
_REVERSE_ORDINAL_PATTERN = re.compile(r"倒数第\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
_DEICTIC_COUNT_PATTERN = re.compile(r"这\s*([0-9零〇一二两三四五六七八九十]+)\s*(?:篇(?:文献|论文)|个(?:文件|表格|pdf|excel|csv))")
_EXPLICIT_REF_PATTERN = re.compile(r"#\s*(\d+)")
_FILE_ACTION_TARGET_PATTERN = re.compile(
    r"(?:请|帮我|帮忙)?\s*(?:结合知识库|参考知识库并|参考知识库|结合)?\s*"
    r"(?:总结|概括|对比|比较|分析|解读|梳理|说明|解释|评估)\s*(?:一下|下)?\s*"
    r"(?:所选|选中(?:的)?|这些|这几篇)?\s*(?:文献|论文|文件|表格)"
)
_TABLE_OPERATION_PATTERNS = (
    re.compile(r"(?:按|根据)\s*(?:第?\d+\s*列|[^，。！？\s]{1,20}(?:列|字段)|字段)\s*(?:筛选|过滤|分组|排序)"),
    re.compile(r"(?:输出|查看|显示|保留|返回)?前\s*\d+\s*行"),
    re.compile(r"(?:输出|查看|显示|保留|返回)?后\s*\d+\s*行"),
    re.compile(r"第\s*\d+\s*行"),
)
_DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _record_metric(metrics: Any | None, name: str, **labels: Any) -> None:
    if metrics is None:
        _LOGGER.info("qa_original_metric name=%s labels=%s", name, labels)
        return
    for method_name in ("increment", "inc", "record"):
        method = getattr(metrics, method_name, None)
        if not callable(method):
            continue
        try:
            method(name, **labels)
            return
        except TypeError:
            try:
                method(name, labels)
                return
            except TypeError:
                continue
    counter = getattr(metrics, "counter", None)
    if callable(counter):
        try:
            metric = counter(name, **labels)
            inc = getattr(metric, "inc", None)
            if callable(inc):
                inc()
                return
        except TypeError:
            pass
    if callable(metrics):
        try:
            metrics(name, **labels)
        except TypeError:
            metrics(name, labels)


def _source_family_for_row(row: ConversationFileRow) -> str:
    if row.is_pdf:
        return "upload_pdf"
    if row.is_table:
        return "upload_table"
    return "upload_object"


class FileContextResolver:
    def __init__(
        self,
        *,
        route_classifier: RouteClassifier | None = None,
        classifier_enabled: bool = False,
        classifier_policy: ClassifierThresholdPolicy | None = None,
        metrics: Any | None = None,
    ) -> None:
        self._route_classifier = route_classifier or NoopRouteClassifier()
        self._classifier_enabled = bool(classifier_enabled)
        self._classifier_policy = classifier_policy or ClassifierThresholdPolicy()
        self._metrics = metrics

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

        selected_ids = list(raw_selected_ids)
        known_selected_ids = self._filter_known_ids(raw_selected_ids, file_map)
        newly_uploaded_ids = self._filter_known_ids(raw_newly_uploaded_ids, file_map)
        all_available_ids = self._filter_known_ids(raw_all_available_ids, file_map) or [row.file_id for row in active_rows] or raw_all_available_ids
        reference_ids = [row.file_id for row in self._reference_resolution_rows(active_rows)] or list(all_available_ids)
        last_focus_ids = self._filter_known_ids(raw_last_focus_ids, file_map)
        last_turn_route = str(ctx.get("last_turn_route") or "").strip().lower()
        candidate_ids = known_selected_ids or all_available_ids

        if not text:
            return self._kb_only(selected_ids=selected_ids)

        lower = text.lower()
        explicit_refs = self._extract_explicit_refs(text)
        has_ordinal_reference = self._has_ordinal_reference(text)
        ordinal_selection = self._extract_ordinal_selection(text=text, candidates=reference_ids)
        deictic_count_selection = self._extract_deictic_count_selection(text=text, candidates=candidate_ids)
        singular_ref = self._contains_any(lower, _SINGULAR_FILE_REFS)
        table_singular_ref = self._contains_any(lower, _SINGULAR_TABLE_REFS)
        plural_ref = self._contains_any(lower, _PLURAL_FILE_REFS)
        latest_ref = self._contains_any(lower, _LATEST_FILE_REFS)
        mixed_intent = self._detect_mixed_intent(lower)
        generic_file_topic = self._contains_any(lower, _GENERIC_FILE_WORDS)
        table_focus = self._question_has_table_focus(lower=lower, active_rows=active_rows, candidate_ids=candidate_ids)
        file_name_focus = self._question_has_file_name_focus(lower=lower, file_map=file_map, candidate_ids=candidate_ids)
        selected_scope_ref = bool(selected_ids) and self._contains_any(lower, _SELECTED_SCOPE_REFS)
        selected_scope_action = bool(selected_ids) and not (
            explicit_refs
            or ordinal_selection
            or deictic_count_selection
            or singular_ref
            or table_singular_ref
            or plural_ref
            or latest_ref
        ) and self._detect_selected_scope_action(
            lower=lower,
            mixed_intent=mixed_intent,
        )
        literature_identifier = self._has_literature_identifier(text)

        if literature_identifier and not (
            explicit_refs
            or has_ordinal_reference
            or deictic_count_selection
            or selected_scope_action
            or table_singular_ref
            or table_focus
            or file_name_focus
        ):
            return self._kb_only(selected_ids=selected_ids)

        strong_file_intent = bool(
            explicit_refs
            or has_ordinal_reference
            or deictic_count_selection
            or singular_ref
            or table_singular_ref
            or plural_ref
            or latest_ref
        )
        file_intent = strong_file_intent or table_focus or file_name_focus or selected_scope_action

        if explicit_refs:
            resolved = self._resolve_explicit_refs(explicit_refs, reference_ids)
            if resolved:
                return self._resolved_file_turn(
                    resolved_ids=resolved,
                    strategy="explicit_ref",
                    allow_kb_verification=mixed_intent,
                    file_map=file_map,
                    table_focus=table_focus,
                )
            return self._clarify(
                selected_ids=selected_ids or all_available_ids,
                message="文件编号无法唯一解析，请明确指定文件",
                file_map=file_map,
                candidate_ids=reference_ids,
            )

        if ordinal_selection:
            return self._resolved_file_turn(
                resolved_ids=ordinal_selection,
                strategy="ordinal_ref",
                allow_kb_verification=mixed_intent,
                file_map=file_map,
                table_focus=table_focus,
            )
        if has_ordinal_reference:
            return self._clarify(
                selected_ids=selected_ids or all_available_ids,
                message="文件编号无法唯一解析，请明确指定文件",
                file_map=file_map,
                candidate_ids=reference_ids,
            )

        if deictic_count_selection:
            return self._file_turn(
                route=self._route_for_selection(selected_ids=deictic_count_selection, file_map=file_map, table_focus=table_focus),
                selected_file_ids=deictic_count_selection,
                strategy="deictic_count_scope",
                allow_kb_verification=mixed_intent,
                file_map=file_map,
            )

        if selected_scope_action:
            if not known_selected_ids:
                return self._clarify(
                    selected_ids=selected_ids or all_available_ids,
                    message="所选文件已失效或不可用，请重新选择文件",
                    file_map=file_map,
                    candidate_ids=self._ordered_candidate_ids(all_available_ids, reference_ids),
                )
            return self._file_turn(
                route=self._route_for_selection(selected_ids=known_selected_ids, file_map=file_map, table_focus=table_focus),
                selected_file_ids=known_selected_ids,
                strategy="selected_scope",
                allow_kb_verification=mixed_intent,
                file_map=file_map,
            )

        if not file_intent:
            classifier_decision = self._maybe_apply_classifier(
                question=text,
                selected_ids=selected_ids,
                known_selected_ids=known_selected_ids,
                all_available_ids=all_available_ids,
                file_map=file_map,
            )
            if classifier_decision is not None:
                return classifier_decision
            return self._kb_only(selected_ids=selected_ids)

        if generic_file_topic and not strong_file_intent and not table_focus and not file_name_focus:
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
        if latest_ref:
            return self._clarify(
                selected_ids=selected_ids or all_available_ids,
                message="最新上传文件无法确定，请重新选择文件",
                file_map=file_map,
                candidate_ids=self._ordered_candidate_ids(all_available_ids, reference_ids),
            )

        if table_singular_ref:
            table_selected_ids = self._filter_ids_by_type(ids=known_selected_ids, file_map=file_map, table_only=True)
            table_last_focus_ids = self._filter_ids_by_type(ids=last_focus_ids, file_map=file_map, table_only=True)
            table_newly_uploaded_ids = self._filter_ids_by_type(ids=newly_uploaded_ids, file_map=file_map, table_only=True)
            table_candidate_ids = self._filter_ids_by_type(
                ids=(known_selected_ids or all_available_ids),
                file_map=file_map,
                table_only=True,
            )

            if len(table_selected_ids) == 1:
                return self._file_turn(
                    route=self._route_for_selection(selected_ids=table_selected_ids, file_map=file_map, table_focus=True),
                    selected_file_ids=table_selected_ids,
                    strategy="selected_single",
                    allow_kb_verification=mixed_intent,
                    file_map=file_map,
                )
            if len(table_last_focus_ids) == 1 and last_turn_route in _FILE_ROUTE_HINTS:
                return self._file_turn(
                    route=self._route_from_last_focus(
                        last_turn_route=last_turn_route,
                        file_map=file_map,
                        selected_ids=table_last_focus_ids,
                        table_focus=True,
                    ),
                    selected_file_ids=table_last_focus_ids,
                    strategy="last_focus",
                    allow_kb_verification=mixed_intent,
                    file_map=file_map,
                )
            if len(table_candidate_ids) == 1:
                selected = [table_candidate_ids[0]]
                return self._file_turn(
                    route=self._route_for_selection(selected_ids=selected, file_map=file_map, table_focus=True),
                    selected_file_ids=selected,
                    strategy="single_candidate",
                    allow_kb_verification=mixed_intent,
                    file_map=file_map,
                )
            if len(table_candidate_ids) > 1:
                return self._clarify(
                    selected_ids=table_candidate_ids,
                    message="当前对话中有多个候选表格，请明确指定文件",
                    file_map=file_map,
                    candidate_ids=self._ordered_candidate_ids(table_candidate_ids, reference_ids),
                )
            return self._clarify(
                selected_ids=selected_ids or all_available_ids,
                message="当前对话中未找到可用表格，请明确指定文件",
                file_map=file_map,
                candidate_ids=self._ordered_candidate_ids(table_candidate_ids or all_available_ids, reference_ids),
            )

        if singular_ref:
            if len(known_selected_ids) == 1:
                return self._file_turn(
                    route=self._route_for_selection(selected_ids=known_selected_ids, file_map=file_map, table_focus=table_focus),
                    selected_file_ids=known_selected_ids,
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
                return self._clarify(
                    selected_ids=candidate_ids,
                    message="当前对话中有多个候选文件，请明确指定文件",
                    file_map=file_map,
                    candidate_ids=self._ordered_candidate_ids(candidate_ids, reference_ids),
                )
            return self._clarify(
                selected_ids=selected_ids or all_available_ids,
                message="当前对话中未找到可用文件，请明确指定文件",
                file_map=file_map,
                candidate_ids=self._ordered_candidate_ids(all_available_ids, reference_ids),
            )

        if (table_focus or file_name_focus) and candidate_ids:
            scoped_candidate_ids = candidate_ids
            if table_focus:
                table_candidate_ids = self._filter_ids_by_type(ids=candidate_ids, file_map=file_map, table_only=True)
                if table_candidate_ids:
                    scoped_candidate_ids = table_candidate_ids
            return self._file_turn(
                route=self._route_for_selection(selected_ids=scoped_candidate_ids, file_map=file_map, table_focus=table_focus),
                selected_file_ids=scoped_candidate_ids,
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

    def _classifier_kb_decision(
        self,
        *,
        selected_ids: list[int],
        confidence: float,
        reason_codes: list[str],
    ) -> FileContextDecision:
        return FileContextDecision(
            route="kb_qa",
            turn_mode="kb_only",
            allow_kb_verification=False,
            selected_file_ids=selected_ids,
            strategy="classifier_resolved",
            classifier_used=True,
            classifier_confidence=float(confidence),
            classifier_reason_codes=list(reason_codes),
        )

    def _clarify(
        self,
        *,
        selected_ids: list[int],
        message: str,
        file_map: dict[int, ConversationFileRow] | None = None,
        candidate_ids: list[int] | None = None,
    ) -> FileContextDecision:
        return FileContextDecision(
            route="kb_qa",
            turn_mode="kb_only",
            allow_kb_verification=False,
            needs_clarification=True,
            clarification_message=message,
            clarify_candidates=self._build_clarify_candidates(candidate_ids=candidate_ids or selected_ids, file_map=file_map or {}),
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
        status = self._selection_status(resolved_ids=selected_file_ids, file_map=file_map)
        if status is not None:
            return self._status_turn(
                route=route,
                selected_file_ids=selected_file_ids,
                strategy=strategy,
                allow_kb_verification=allow_kb_verification,
                file_map=file_map,
                status_code=status["code"],
                status_error=status["error"],
                status_message=status["message"],
                status_retriable=bool(status["retriable"]),
                status_detail=dict(status["detail"]),
            )
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

    def _status_turn(
        self,
        *,
        route: str,
        selected_file_ids: list[int],
        strategy: str,
        allow_kb_verification: bool,
        file_map: dict[int, ConversationFileRow],
        status_code: str,
        status_error: str,
        status_message: str,
        status_retriable: bool,
        status_detail: dict[str, Any],
    ) -> FileContextDecision:
        turn_mode = "mixed" if allow_kb_verification else "file_only"
        used_files = [self._build_file_payload(file_id, strategy, file_map) for file_id in selected_file_ids]
        return FileContextDecision(
            route=route,
            turn_mode=turn_mode,
            allow_kb_verification=allow_kb_verification,
            selected_file_ids=selected_file_ids,
            used_files=used_files,
            execution_files=[],
            strategy=strategy,
            status_code=status_code,
            status_error=status_error,
            status_message=status_message,
            status_retriable=status_retriable,
            status_detail=status_detail,
        )

    def _resolved_file_turn(
        self,
        *,
        resolved_ids: list[int],
        strategy: str,
        allow_kb_verification: bool,
        file_map: dict[int, ConversationFileRow],
        table_focus: bool,
    ) -> FileContextDecision:
        if resolved_ids and not any(file_id in file_map for file_id in resolved_ids):
            return self._clarify(
                selected_ids=resolved_ids,
                message="文件信息不完整，请重新选择文件",
                file_map=file_map,
                candidate_ids=resolved_ids,
            )
        status = self._selection_status(resolved_ids=resolved_ids, file_map=file_map)
        route = self._route_for_selection(selected_ids=resolved_ids, file_map=file_map, table_focus=table_focus)
        if status is not None:
            return self._status_turn(
                route=route,
                selected_file_ids=resolved_ids,
                strategy=strategy,
                allow_kb_verification=allow_kb_verification,
                file_map=file_map,
                status_code=status["code"],
                status_error=status["error"],
                status_message=status["message"],
                status_retriable=bool(status["retriable"]),
                status_detail=dict(status["detail"]),
            )
        return self._file_turn(
            route=route,
            selected_file_ids=resolved_ids,
            strategy=strategy,
            allow_kb_verification=allow_kb_verification,
            file_map=file_map,
        )

    def _classifier_file_turn(
        self,
        *,
        decision: ClassifierDecision,
        selected_file_ids: list[int],
        file_map: dict[int, ConversationFileRow],
    ) -> FileContextDecision:
        allow_kb_verification = decision.turn_mode == "mixed" or "kb" in str(decision.source_scope or "")
        route = str(decision.route or "").strip().lower()
        if route == "hybrid_qa":
            scope_tokens = {part.strip().lower() for part in str(decision.source_scope or "").split("+") if part.strip()}
            if scope_tokens == {"pdf"}:
                route = "pdf_qa"
            elif scope_tokens == {"table"}:
                route = "tabular_qa"
        file_turn = self._file_turn(
            route=route,
            selected_file_ids=selected_file_ids,
            strategy="classifier_resolved",
            allow_kb_verification=allow_kb_verification,
            file_map=file_map,
        )
        return FileContextDecision(
            **{
                **file_turn.__dict__,
                "classifier_used": True,
                "classifier_confidence": float(decision.confidence),
                "classifier_reason_codes": list(decision.reason_codes),
            }
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

    def _build_clarify_candidates(self, *, candidate_ids: list[int], file_map: dict[int, ConversationFileRow]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[int] = set()
        for file_id in candidate_ids:
            if file_id in seen:
                continue
            seen.add(file_id)
            row = file_map.get(file_id)
            payload: dict[str, Any] = {"file_id": int(file_id)}
            if row is not None:
                payload.update(
                    {
                        "file_name": row.file_name,
                        "file_type": row.file_type,
                        "display_no": row.display_no,
                        "file_no": row.file_no,
                        "file_status": row.file_status,
                        "parse_status": row.parse_status,
                        "index_status": row.index_status,
                        "processing_stage": row.processing_stage,
                    }
                )
            candidates.append(payload)
        return candidates

    def _ordered_candidate_ids(self, candidate_ids: list[int], reference_ids: list[int]) -> list[int]:
        if not candidate_ids:
            return []
        order = {file_id: index for index, file_id in enumerate(reference_ids)}
        return sorted(
            {int(file_id) for file_id in candidate_ids},
            key=lambda file_id: (order.get(file_id, 10**9), file_id),
        )

    def _maybe_apply_classifier(
        self,
        *,
        question: str,
        selected_ids: list[int],
        known_selected_ids: list[int],
        all_available_ids: list[int],
        file_map: dict[int, ConversationFileRow],
    ) -> FileContextDecision | None:
        if not self._classifier_enabled:
            return None
        # Classifier is reserved for true ambiguity after the rule layer:
        # the user explicitly has a selected scope, but the question text
        # does not contain enough file intent for deterministic routing.
        candidate_ids = list(known_selected_ids)
        if not candidate_ids:
            return None
        classifier_decision = self._route_classifier.classify(
            question=question,
            selected_ids=list(selected_ids),
            all_available_ids=list(all_available_ids),
            candidate_files=[file_map[file_id] for file_id in candidate_ids if file_id in file_map],
        )
        if classifier_decision is None:
            return None
        conflicts_with_rule = classifier_decision.route != "kb_qa"
        if not self._classifier_policy.should_apply(decision=classifier_decision, conflicts_with_rule=conflicts_with_rule):
            return None
        if classifier_decision.route == "kb_qa":
            return self._classifier_kb_decision(
                selected_ids=selected_ids,
                confidence=classifier_decision.confidence,
                reason_codes=list(classifier_decision.reason_codes),
            )
        scoped_ids = self._classifier_selected_ids(
            source_scope=classifier_decision.source_scope,
            candidate_ids=candidate_ids,
            file_map=file_map,
        )
        if not scoped_ids:
            return None
        return self._classifier_file_turn(
            decision=classifier_decision,
            selected_file_ids=scoped_ids,
            file_map=file_map,
        )

    def _classifier_selected_ids(self, *, source_scope: str, candidate_ids: list[int], file_map: dict[int, ConversationFileRow]) -> list[int]:
        normalized_scope = str(source_scope or "").strip().lower()
        if normalized_scope == "kb":
            return []
        wants_pdf = "pdf" in normalized_scope
        wants_table = "table" in normalized_scope
        if not wants_pdf and not wants_table:
            return []
        selected: list[int] = []
        for file_id in candidate_ids:
            row = file_map.get(file_id)
            if row is None:
                continue
            if wants_pdf and row.is_pdf:
                selected.append(file_id)
            elif wants_table and row.is_table:
                selected.append(file_id)
        return selected

    def _selection_status(self, *, resolved_ids: list[int], file_map: dict[int, ConversationFileRow]) -> dict[str, Any] | None:
        for file_id in resolved_ids:
            row = file_map.get(file_id)
            if row is None or row.is_deleted:
                return {
                    "code": "FILE_NOT_FOUND",
                    "error": "file_not_found",
                    "message": "目标文件不存在或已删除，请重新选择文件",
                    "retriable": False,
                    "detail": {"file_id": file_id},
                }
            if self._is_file_failed(row):
                return {
                    "code": "FILE_PROCESSING_FAILED",
                    "error": "file_processing_failed",
                    "message": f"文件 {row.file_name or row.file_id} 处理失败，请重新上传或更换文件",
                    "retriable": False,
                    "detail": self._file_status_detail(row),
                }
            if not row.is_ready:
                return {
                    "code": "FILE_NOT_READY",
                    "error": "file_not_ready",
                    "message": f"文件 {row.file_name or row.file_id} 仍在处理中，请稍后再试",
                    "retriable": True,
                    "detail": self._file_status_detail(row),
                }
            if self._strict_minio_only() and not row.has_minio_storage_ref:
                storage_reason = "storage_ref_missing" if not str(row.storage_ref or "").strip() else "storage_ref_not_minio"
                status_code = "FILE_STORAGE_REF_MISSING" if storage_reason == "storage_ref_missing" else "FILE_STORAGE_REF_NOT_MINIO"
                _record_metric(
                    self._metrics,
                    "qa_original_storage_ref_missing_total",
                    service="gateway",
                    source_family=_source_family_for_row(row),
                    result="blocked",
                    reason=storage_reason,
                )
                return {
                    "code": status_code,
                    "error": storage_reason,
                    "message": f"文件 {row.file_name or row.file_id} 缺少可执行的 MinIO 存储引用，请重新上传或刷新文件元数据",
                    "retriable": False,
                    "detail": self._file_storage_detail(row=row, selected_ids=resolved_ids, file_map=file_map, reason=storage_reason),
                }
        return None

    def _strict_minio_only(self) -> bool:
        return _env_bool("QA_ORIGINAL_MINIO_ONLY", True)

    def _is_file_failed(self, row: ConversationFileRow) -> bool:
        parse_status = str(row.parse_status or "").strip().lower()
        index_status = str(row.index_status or "").strip().lower()
        stage = str(row.processing_stage or "").strip().lower()
        return parse_status == "failed" or index_status == "failed" or stage == "failed"

    def _file_status_detail(self, row: ConversationFileRow) -> dict[str, Any]:
        return {
            "file_id": row.file_id,
            "file_name": row.file_name,
            "file_status": row.file_status,
            "parse_status": row.parse_status,
            "index_status": row.index_status,
            "processing_stage": row.processing_stage,
        }

    def _file_storage_detail(
        self,
        *,
        row: ConversationFileRow,
        selected_ids: list[int],
        file_map: dict[int, ConversationFileRow],
        reason: str,
    ) -> dict[str, Any]:
        selected_rows = [file_map[file_id] for file_id in selected_ids if file_id in file_map]
        return {
            **self._file_status_detail(row),
            "storage_ref": row.storage_ref,
            "local_path": row.local_path,
            "reason_codes": [reason],
            "missing_storage_ref_count": sum(1 for item in selected_rows if not str(item.storage_ref or "").strip()),
            "minio_storage_ref_count": sum(1 for item in selected_rows if item.has_minio_storage_ref),
            "local_only_file_count": sum(1 for item in selected_rows if str(item.local_path or "").strip() and not item.has_minio_storage_ref),
        }

    def _reference_resolution_rows(self, active_rows: list[ConversationFileRow]) -> list[ConversationFileRow]:
        def _sort_key(row: ConversationFileRow) -> tuple[int, int, int]:
            display_no = row.display_no if int(row.display_no or 0) > 0 else 10**9
            file_no = row.file_no if int(row.file_no or 0) > 0 else 10**9
            return (display_no, file_no, row.file_id)

        return sorted(active_rows, key=_sort_key)

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
        candidate_set = set(candidate_ids)
        table_rows: list[ConversationFileRow] = []
        for row in active_rows:
            if candidate_set and row.file_id not in candidate_set:
                continue
            if row.is_table:
                table_rows.append(row)
        if not table_rows:
            return False
        if self._contains_any(lower, _TABLE_FILE_WORDS):
            return True
        if any(pattern.search(lower) for pattern in _TABLE_OPERATION_PATTERNS):
            return True
        for row in table_rows:
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

    def _detect_selected_scope_action(self, *, lower: str, mixed_intent: bool) -> bool:
        has_selection_ref = self._contains_any(lower, _SELECTED_SCOPE_REFS)
        has_file_content_ref = self._contains_any(lower, _FILE_CONTENT_REFS)
        has_file_action = self._contains_any(lower, _FILE_ACTION_WORDS)
        is_generic_meta_question = self._contains_any(lower, _GENERIC_META_QUESTION_HINTS)
        if has_selection_ref and has_file_action and not is_generic_meta_question:
            return True
        if mixed_intent and has_file_content_ref and has_file_action and not is_generic_meta_question:
            return True
        normalized = lower.strip().strip("，。！？,.!?")
        if _FILE_ACTION_TARGET_PATTERN.fullmatch(normalized):
            return True
        return False

    def _filter_known_ids(self, ids: list[int], file_map: dict[int, ConversationFileRow]) -> list[int]:
        if not ids or not file_map:
            return []
        return [file_id for file_id in ids if file_id in file_map]

    def _filter_ids_by_type(
        self,
        *,
        ids: list[int],
        file_map: dict[int, ConversationFileRow],
        table_only: bool,
    ) -> list[int]:
        if not ids or not file_map:
            return []
        filtered: list[int] = []
        for file_id in ids:
            row = file_map.get(file_id)
            if row is None:
                continue
            if table_only and not row.is_table:
                continue
            if not table_only and not row.is_pdf:
                continue
            filtered.append(file_id)
        return filtered

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

    def _has_ordinal_reference(self, text: str) -> bool:
        return bool(
            _DIRECT_ORDINAL_PATTERN.search(text)
            or _FRONT_ORDINAL_PATTERN.search(text)
            or _BACK_ORDINAL_PATTERN.search(text)
            or _REVERSE_ORDINAL_PATTERN.search(text)
        )

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

    def _has_literature_identifier(self, text: str) -> bool:
        return bool(_DOI_PATTERN.search(text or ""))

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
