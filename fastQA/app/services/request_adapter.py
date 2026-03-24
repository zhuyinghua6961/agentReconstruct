from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

_ALLOWED_MODE = "fast"
_ALLOWED_ROUTES = {"kb_qa", "pdf_qa", "tabular_qa", "hybrid_qa"}
_TABLE_FILE_TYPES = {"excel", "csv", "table", "xls", "xlsx"}
_SOURCE_SCOPE_ORDER = ("pdf", "table", "kb")
_SOURCE_SCOPE_TOKENS = set(_SOURCE_SCOPE_ORDER)
_HYBRID_SOURCE_SCOPES = {"pdf+kb", "table+kb", "pdf+table", "pdf+table+kb"}


class RequestAdapterError(ValueError):
    def __init__(self, *, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = dict(detail or {})

    def to_payload(self) -> dict[str, Any]:
        payload = {"code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class GatewayAskRequest:
    question: str
    conversation_id: int | None = None
    user_id: int | None = None
    chat_history: list[dict[str, Any]] = field(default_factory=list)
    request_chat_history: list[dict[str, Any]] = field(default_factory=list)
    authority_chat_history: list[dict[str, Any]] = field(default_factory=list)
    authority_summary: dict[str, Any] = field(default_factory=dict)
    authority_conversation_state: dict[str, Any] = field(default_factory=dict)
    requested_mode: str = _ALLOWED_MODE
    actual_mode: str = _ALLOWED_MODE
    route: str = "kb_qa"
    route_was_explicit: bool = False
    source_scope: str = ""
    kb_enabled: bool = False
    turn_mode: str = "kb_only"
    allow_kb_verification: bool = False
    trace_id: str = ""
    request_use_generation_driven: bool = False
    n_results_per_claim: int = 10
    active_stream_count: int | None = None
    options: dict[str, Any] = field(default_factory=dict)
    used_files: list[dict[str, Any]] = field(default_factory=list)
    execution_files: list[dict[str, Any]] = field(default_factory=list)
    selected_file_ids: list[int] = field(default_factory=list)
    primary_file_id: int | None = None
    file_selection: dict[str, Any] = field(default_factory=dict)
    pdf_context: dict[str, Any] = field(default_factory=dict)
    pdf_path: str = ""
    current_pdf_path: str = ""
    use_pdf: bool = False

    def to_qakb_payload(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "chat_history": list(self.chat_history),
            "request_chat_history": list(self.request_chat_history),
            "authority_chat_history": list(self.authority_chat_history),
            "authority_summary": dict(self.authority_summary),
            "authority_conversation_state": dict(self.authority_conversation_state),
            "use_generation_driven": self.request_use_generation_driven,
            "request_use_generation_driven": self.request_use_generation_driven,
            "route_hint": self.route,
            "source_scope": self.source_scope,
            "kb_enabled": self.kb_enabled,
            "selected_file_ids": list(self.selected_file_ids),
            "primary_file_id": self.primary_file_id,
            "file_selection": dict(self.file_selection),
            "n_results_per_claim": self.n_results_per_claim,
            "active_stream_count": self.active_stream_count,
            "trace_id": self.trace_id,
        }


FastAskRequest = GatewayAskRequest


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _as_history(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = [dict(item) for item in value if isinstance(item, dict)]
    return normalized[-10:]


def _as_options(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _as_file_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _as_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    normalized: list[int] = []
    seen: set[int] = set()
    for item in value:
        parsed = _coerce_positive_int(item)
        if parsed is None or parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
    return normalized


def _file_ids(files: list[dict[str, Any]]) -> set[int]:
    normalized: set[int] = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        parsed = _coerce_positive_int(item.get("file_id"))
        if parsed is not None:
            normalized.add(parsed)
    return normalized


def _file_types(files: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("file_type") or "").strip().lower()
        for item in files
        if isinstance(item, dict)
    }


def _infer_route(
    *,
    route: str,
    execution_files: list[dict[str, Any]],
    used_files: list[dict[str, Any]],
    use_pdf: bool,
    pdf_path: str,
) -> str:
    normalized = str(route or "").strip().lower()
    if normalized:
        return normalized
    file_items = execution_files or used_files
    file_types = _file_types(file_items)
    has_pdf = bool(pdf_path) or use_pdf or ("pdf" in file_types)
    has_table = bool(file_types & _TABLE_FILE_TYPES)
    if has_pdf and has_table:
        return "hybrid_qa"
    if has_pdf:
        return "pdf_qa"
    if has_table:
        return "tabular_qa"
    return "kb_qa"


def _infer_turn_mode(*, route: str, turn_mode: str, allow_kb_verification: bool) -> str:
    explicit = str(turn_mode or "").strip().lower()
    if explicit in {"kb_only", "file_only", "mixed"}:
        return explicit
    if route == "kb_qa":
        return "kb_only"
    if allow_kb_verification:
        return "mixed"
    return "file_only"


def _normalize_source_scope(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    tokens = [part.strip().lower() for part in raw.split("+") if part.strip()]
    if not tokens:
        return ""
    if any(token not in _SOURCE_SCOPE_TOKENS for token in tokens):
        raise RequestAdapterError(
            code="source_scope_invalid",
            message="unsupported source_scope for fastQA",
            detail={"source_scope": raw},
        )
    ordered_tokens = [token for token in _SOURCE_SCOPE_ORDER if token in set(tokens)]
    return "+".join(ordered_tokens)


def _infer_source_scope(*, route: str, has_pdf: bool, has_table: bool, kb_enabled: bool) -> str:
    if route == "pdf_qa":
        return "pdf"
    if route == "tabular_qa":
        return "table"
    if route == "hybrid_qa":
        if has_pdf and has_table:
            return "pdf+table+kb" if kb_enabled else "pdf+table"
        if has_pdf:
            return "pdf+kb" if kb_enabled else "pdf"
        if has_table:
            return "table+kb" if kb_enabled else "table"
        return "pdf+table+kb" if kb_enabled else "pdf+table"
    if kb_enabled:
        return "kb"
    return "kb"


def _route_requires_pdf(*, route: str, source_scope: str) -> bool:
    return route == "pdf_qa" or source_scope in {"pdf+kb", "pdf+table", "pdf+table+kb"}


def _route_requires_table(*, route: str, source_scope: str) -> bool:
    return route == "tabular_qa" or source_scope in {"table+kb", "pdf+table", "pdf+table+kb"}


def _validate_route_source_scope(*, route: str, source_scope: str) -> None:
    if route == "pdf_qa" and source_scope != "pdf":
        raise RequestAdapterError(
            code="source_scope_invalid",
            message="pdf_qa requires source_scope=pdf",
            detail={"route": route, "source_scope": source_scope},
        )
    if route == "tabular_qa" and source_scope != "table":
        raise RequestAdapterError(
            code="source_scope_invalid",
            message="tabular_qa requires source_scope=table",
            detail={"route": route, "source_scope": source_scope},
        )
    if route == "hybrid_qa" and source_scope not in _HYBRID_SOURCE_SCOPES:
        raise RequestAdapterError(
            code="source_scope_invalid",
            message="hybrid_qa requires source_scope=pdf+kb, table+kb, pdf+table, or pdf+table+kb",
            detail={"route": route, "source_scope": source_scope},
        )


def adapt_gateway_ask_payload(payload: Mapping[str, Any]) -> GatewayAskRequest:
    source = dict(payload or {})
    question = str(source.get("question") or "").strip()
    if not question:
        raise RequestAdapterError(code="question_required", message="question is required")

    requested_mode = str(source.get("requested_mode") or _ALLOWED_MODE).strip() or _ALLOWED_MODE
    actual_mode = str(source.get("actual_mode") or requested_mode or _ALLOWED_MODE).strip() or _ALLOWED_MODE
    if requested_mode != _ALLOWED_MODE or actual_mode != _ALLOWED_MODE:
        raise RequestAdapterError(
            code="mode_not_supported",
            message="fastQA only supports fast mode",
            detail={"requested_mode": requested_mode, "actual_mode": actual_mode},
        )

    pdf_context = dict(source.get("pdf_context") or {}) if isinstance(source.get("pdf_context"), dict) else {}
    used_files = _as_file_list(source.get("used_files"))
    execution_files = _as_file_list(source.get("execution_files"))
    pdf_path = str(
        source.get("pdf_path")
        or source.get("current_pdf_path")
        or pdf_context.get("current_pdf_path")
        or pdf_context.get("primary_pdf_path")
        or ""
    ).strip()
    route = _infer_route(
        route=str(source.get("route") or source.get("route_hint") or ""),
        execution_files=execution_files,
        used_files=used_files,
        use_pdf=_coerce_bool(source.get("use_pdf")),
        pdf_path=pdf_path,
    )
    if route not in _ALLOWED_ROUTES:
        raise RequestAdapterError(
            code="route_invalid",
            message="unsupported route for fastQA",
            detail={"route": route},
        )

    options = _as_options(source.get("options"))
    raw_file_selection = _as_options(source.get("file_selection"))
    selected_file_ids = _as_int_list(source.get("selected_file_ids")) or _as_int_list(raw_file_selection.get("selected_file_ids"))
    primary_file_id = _coerce_positive_int(source.get("primary_file_id")) or _coerce_positive_int(raw_file_selection.get("primary_file_id"))
    file_items = execution_files or used_files
    available_file_ids = _file_ids(file_items)
    if primary_file_id is not None:
        if selected_file_ids and primary_file_id not in selected_file_ids:
            raise RequestAdapterError(
                code="primary_file_invalid",
                message="primary_file_id must be included in selected_file_ids",
                detail={"primary_file_id": primary_file_id, "selected_file_ids": list(selected_file_ids)},
            )
        if available_file_ids and primary_file_id not in available_file_ids:
            raise RequestAdapterError(
                code="primary_file_invalid",
                message="primary_file_id must reference one of the execution files",
                detail={"primary_file_id": primary_file_id, "execution_file_ids": sorted(available_file_ids)},
            )
    present_types = _file_types(file_items)
    has_pdf = bool(pdf_path) or _coerce_bool(source.get("use_pdf")) or ("pdf" in present_types)
    has_table = bool(present_types & _TABLE_FILE_TYPES)
    has_file_resolution_context = bool(_coerce_positive_int(source.get("conversation_id"))) or bool(pdf_context)

    source_scope = _normalize_source_scope(source.get("source_scope") or raw_file_selection.get("source_scope"))
    kb_enabled = _coerce_bool(
        source.get(
            "kb_enabled",
            raw_file_selection.get("kb_enabled", "kb" in source_scope or route == "kb_qa"),
        ),
        default=("kb" in source_scope or route == "kb_qa"),
    )
    if not source_scope:
        source_scope = _infer_source_scope(route=route, has_pdf=has_pdf, has_table=has_table, kb_enabled=kb_enabled)

    if route in {"pdf_qa", "tabular_qa", "hybrid_qa"}:
        _validate_route_source_scope(route=route, source_scope=source_scope)

    if _route_requires_pdf(route=route, source_scope=source_scope) and not has_pdf:
        if has_file_resolution_context:
            has_pdf = True
        else:
            raise RequestAdapterError(
                code="execution_files_required",
                message="pdf_qa requires a PDF file or pdf_path" if route == "pdf_qa" else "selected source_scope requires at least one PDF file",
                detail={"route": route, "source_scope": source_scope},
            )
    if _route_requires_table(route=route, source_scope=source_scope) and not has_table:
        if has_file_resolution_context:
            has_table = True
        else:
            raise RequestAdapterError(
                code="execution_files_required",
                message="tabular_qa requires at least one table file" if route == "tabular_qa" else "selected source_scope requires at least one table file",
                detail={"route": route, "source_scope": source_scope},
            )

    n_results_per_claim = _coerce_positive_int(source.get("n_results_per_claim")) or _coerce_positive_int(options.get("n_results_per_claim")) or 10
    active_stream_count = _coerce_positive_int(source.get("active_stream_count")) or _coerce_positive_int(options.get("active_stream_count"))
    allow_kb_verification = _coerce_bool(source.get("allow_kb_verification", False))
    request_use_generation_driven = _coerce_bool(
        source.get("use_generation_driven", options.get("use_generation_driven", False))
    )

    file_selection = dict(raw_file_selection)
    file_selection["source_scope"] = source_scope
    file_selection["kb_enabled"] = kb_enabled
    if selected_file_ids:
        file_selection["selected_file_ids"] = list(selected_file_ids)
    if primary_file_id is not None:
        file_selection["primary_file_id"] = primary_file_id

    request_chat_history = _as_history(source.get("chat_history"))

    return GatewayAskRequest(
        question=question,
        conversation_id=_coerce_positive_int(source.get("conversation_id")),
        user_id=_coerce_positive_int(source.get("user_id")) or _coerce_positive_int(options.get("user_id")),
        chat_history=list(request_chat_history),
        request_chat_history=request_chat_history,
        requested_mode=requested_mode,
        actual_mode=actual_mode,
        route=route,
        route_was_explicit=bool(str(source.get("route") or source.get("route_hint") or "").strip()),
        source_scope=source_scope,
        kb_enabled=kb_enabled,
        turn_mode=_infer_turn_mode(
            route=route,
            turn_mode=str(source.get("turn_mode") or ""),
            allow_kb_verification=allow_kb_verification,
        ),
        allow_kb_verification=allow_kb_verification,
        trace_id=str(source.get("trace_id") or "").strip(),
        request_use_generation_driven=request_use_generation_driven,
        n_results_per_claim=n_results_per_claim,
        active_stream_count=active_stream_count,
        options=options,
        used_files=used_files,
        execution_files=execution_files,
        selected_file_ids=selected_file_ids,
        primary_file_id=primary_file_id,
        file_selection=file_selection,
        pdf_context=pdf_context,
        pdf_path=pdf_path,
        current_pdf_path=str(
            source.get("current_pdf_path")
            or pdf_context.get("current_pdf_path")
            or pdf_context.get("primary_pdf_path")
            or pdf_path
            or ""
        ).strip(),
        use_pdf=_coerce_bool(source.get("use_pdf", False)),
    )
