"""Structured upstream call failures for SSE error frames."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from server.utils.user_errors import build_upstream_error_message


def status_code_from_exception(exc: Exception | None) -> int | None:
    if exc is None:
        return None
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


@dataclass(slots=True)
class UpstreamCallError(Exception):
    code: str
    component: str
    stage: str
    message: str
    status_code: int | None = None
    error: str = ""
    retriable: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.error:
            self.error = str(self.code or "").strip().lower()

    def __str__(self) -> str:
        return str(self.message or self.code)

    @classmethod
    def llm_unavailable(
        cls,
        *,
        stage: str,
        status_code: int | None = None,
        detail: str = "",
        retriable: bool = True,
    ) -> UpstreamCallError:
        return cls(
            code="LLM_UNAVAILABLE",
            error="llm_unavailable",
            component="llm",
            stage=stage,
            status_code=status_code,
            message=build_upstream_error_message("llm", status_code=status_code, detail=detail),
            retriable=retriable,
        )

    @classmethod
    def embedding_unavailable(
        cls,
        *,
        stage: str = "stage2",
        status_code: int | None = None,
        detail: str = "",
    ) -> UpstreamCallError:
        return cls(
            code="EMBEDDING_UNAVAILABLE",
            error="embedding_unavailable",
            component="embedding",
            stage=stage,
            status_code=status_code,
            message=build_upstream_error_message("embedding", status_code=status_code, detail=detail),
            retriable=True,
        )

    @classmethod
    def retrieval_failed(
        cls,
        *,
        stage: str = "stage3",
        status_code: int | None = None,
        detail: str = "",
    ) -> UpstreamCallError:
        return cls(
            code="RETRIEVAL_FAILED",
            error="retrieval_failed",
            component="retrieval",
            stage=stage,
            status_code=status_code,
            message=build_upstream_error_message("retrieval", status_code=status_code, detail=detail),
            retriable=True,
        )

    @classmethod
    def stream_interrupted(
        cls,
        *,
        stage: str,
        status_code: int | None = None,
        detail: str = "",
    ) -> UpstreamCallError:
        return cls(
            code="UPSTREAM_STREAM_INTERRUPTED",
            error="upstream_stream_interrupted",
            component="llm",
            stage=stage,
            status_code=status_code,
            message=build_upstream_error_message("stream", status_code=status_code, detail=detail),
            retriable=True,
        )

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        *,
        code: str,
        component: str,
        stage: str,
        error: str = "",
        retriable: bool = True,
    ) -> UpstreamCallError:
        if isinstance(exc, UpstreamCallError):
            return exc
        status_code = status_code_from_exception(exc)
        message = build_upstream_error_message(component, status_code=status_code, detail=str(exc))
        return cls(
            code=code,
            error=error or str(code).lower(),
            component=component,
            stage=stage,
            status_code=status_code,
            message=message,
            retriable=retriable,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("extra", None)
        if self.extra:
            payload.update(self.extra)
        return payload


def build_sse_error_event(
    upstream: UpstreamCallError | dict[str, Any],
    *,
    trace_id: str = "",
) -> dict[str, Any]:
    data = upstream.to_dict() if isinstance(upstream, UpstreamCallError) else dict(upstream)
    event = {
        "type": "error",
        "code": str(data.get("code") or "UPSTREAM_ERROR"),
        "error": str(data.get("error") or "upstream_error"),
        "message": str(data.get("message") or ""),
        "retriable": bool(data.get("retriable", True)),
        "trace_id": str(trace_id or ""),
    }
    if data.get("status_code") is not None:
        event["status_code"] = int(data["status_code"])
    if data.get("stage"):
        event["failure_stage"] = str(data["stage"])
    if data.get("component"):
        event["component"] = str(data["component"])
    return event


def coerce_upstream_error(value: Any) -> UpstreamCallError | None:
    if isinstance(value, UpstreamCallError):
        return value
    if isinstance(value, dict) and value.get("code"):
        return UpstreamCallError(
            code=str(value.get("code") or ""),
            error=str(value.get("error") or ""),
            component=str(value.get("component") or ""),
            stage=str(value.get("stage") or value.get("failure_stage") or ""),
            status_code=value.get("status_code"),
            message=str(value.get("message") or ""),
            retriable=bool(value.get("retriable", True)),
        )
    return None
