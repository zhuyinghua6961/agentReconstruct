from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

_ALLOWED_MODE = "fast"
_ALLOWED_ROUTES = {"kb_qa", "pdf_qa", "tabular_qa", "hybrid_qa"}


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
    requested_mode: str = _ALLOWED_MODE
    actual_mode: str = _ALLOWED_MODE
    route: str = "kb_qa"
    route_was_explicit: bool = False
    turn_mode: str = "kb_only"
    allow_kb_verification: bool = False
    trace_id: str = ""
    request_use_generation_driven: bool = False
    n_results_per_claim: int = 10
    active_stream_count: int | None = None
    options: dict[str, Any] = field(default_factory=dict)
    used_files: list[dict[str, Any]] = field(default_factory=list)
    execution_files: list[dict[str, Any]] = field(default_factory=list)
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
            "use_generation_driven": self.request_use_generation_driven,
            "request_use_generation_driven": self.request_use_generation_driven,
            "route_hint": self.route,
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
    has_table = bool(file_types & {"excel", "csv"})
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
    all_files = execution_files or used_files
    present_types = _file_types(all_files)
    has_pdf = bool(pdf_path) or _coerce_bool(source.get("use_pdf")) or ("pdf" in present_types)
    has_table = bool(present_types & {"excel", "csv"})
    has_file_resolution_context = bool(_coerce_positive_int(source.get("conversation_id"))) or bool(pdf_context)
    if route == "pdf_qa" and not has_pdf:
        if has_file_resolution_context:
            has_pdf = True
        else:
            raise RequestAdapterError(
                code="execution_files_required",
                message="pdf_qa requires a PDF file or pdf_path",
                detail={"route": route},
            )
    if route == "tabular_qa" and not has_table:
        if has_file_resolution_context:
            has_table = True
        else:
            raise RequestAdapterError(
                code="execution_files_required",
                message="tabular_qa requires at least one table file",
                detail={"route": route},
            )
    if route == "hybrid_qa" and not (has_pdf and has_table):
        if has_file_resolution_context:
            has_pdf = True
            has_table = True
        else:
            raise RequestAdapterError(
                code="execution_files_required",
                message="hybrid_qa requires both PDF and table files",
                detail={"route": route},
            )

    n_results_per_claim = _coerce_positive_int(source.get("n_results_per_claim")) or _coerce_positive_int(options.get("n_results_per_claim")) or 10
    active_stream_count = _coerce_positive_int(source.get("active_stream_count")) or _coerce_positive_int(options.get("active_stream_count"))
    allow_kb_verification = _coerce_bool(source.get("allow_kb_verification", False))
    request_use_generation_driven = _coerce_bool(
        source.get("use_generation_driven", options.get("use_generation_driven", False))
    )

    return GatewayAskRequest(
        question=question,
        conversation_id=_coerce_positive_int(source.get("conversation_id")),
        user_id=_coerce_positive_int(source.get("user_id")) or _coerce_positive_int(options.get("user_id")),
        chat_history=_as_history(source.get("chat_history")),
        requested_mode=requested_mode,
        actual_mode=actual_mode,
        route=route,
        route_was_explicit=bool(str(source.get("route") or source.get("route_hint") or "").strip()),
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
