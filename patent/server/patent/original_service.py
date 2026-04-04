from __future__ import annotations

from html import escape
from typing import Any, Callable
from urllib.parse import urlencode

from server.patent.original_models import OriginalRequest, OriginalViewResult


ProviderFn = Callable[[OriginalRequest, str, bool], OriginalViewResult]

_VALID_SECTIONS = {"abstract", "claim", "description", "figure", "fulltext"}
_VALID_FORMATS = {"html", "json", "text", "redirect"}


def build_original_anchor(*, section: str, claim_number: int | None, paragraph_id: str | None) -> str:
    normalized_section = str(section or "").strip().lower()
    normalized_paragraph_id = str(paragraph_id or "").strip()
    if normalized_section == "claim":
        if claim_number is None:
            raise ValueError("claim_number is required when section=claim")
        return f"claim:{int(claim_number)}"
    if normalized_section == "description" and normalized_paragraph_id:
        return f"paragraph:{normalized_paragraph_id}"
    if normalized_section == "abstract":
        return "section:abstract"
    if normalized_section == "description":
        return "section:description"
    if normalized_section == "figure":
        return "section:figure"
    if normalized_section == "fulltext":
        return "fulltext"
    raise ValueError("section must be one of abstract|claim|description|figure|fulltext")


def parse_original_request(
    *,
    canonical_patent_id: str,
    section: str | None,
    claim_number: str | int | None,
    paragraph_id: str | None,
    response_format: str | None,
) -> OriginalRequest:
    normalized_patent_id = str(canonical_patent_id or "").strip().upper()
    if not normalized_patent_id:
        raise ValueError("canonical_patent_id is required")

    normalized_section = str(section or "fulltext").strip().lower()
    if normalized_section not in _VALID_SECTIONS:
        raise ValueError("section must be one of abstract|claim|description|figure|fulltext")

    normalized_format = str(response_format or "html").strip().lower()
    if normalized_format not in _VALID_FORMATS:
        raise ValueError("format must be one of html|json|text|redirect")

    parsed_claim_number: int | None = None
    if claim_number not in (None, ""):
        try:
            parsed_claim_number = int(claim_number)
        except (TypeError, ValueError) as exc:
            raise ValueError("claim_number must be an integer") from exc
        if parsed_claim_number <= 0:
            raise ValueError("claim_number must be greater than 0")

    normalized_paragraph_id = str(paragraph_id or "").strip() or None
    if normalized_section != "claim" and parsed_claim_number is not None:
        raise ValueError("claim_number is only allowed when section=claim")
    if normalized_section != "description" and normalized_paragraph_id is not None:
        raise ValueError("paragraph_id is only allowed when section=description")
    if normalized_section == "claim" and normalized_paragraph_id is not None:
        raise ValueError("paragraph_id is only allowed when section=description")
    anchor = build_original_anchor(
        section=normalized_section,
        claim_number=parsed_claim_number,
        paragraph_id=normalized_paragraph_id,
    )
    return OriginalRequest(
        canonical_patent_id=normalized_patent_id,
        section=normalized_section,  # type: ignore[arg-type]
        claim_number=parsed_claim_number,
        paragraph_id=normalized_paragraph_id,
        response_format=normalized_format,  # type: ignore[arg-type]
        anchor=anchor,
    )


def build_original_viewer_uri(request: OriginalRequest) -> str:
    params: dict[str, str] = {"section": request.section}
    if request.claim_number is not None:
        params["claim_number"] = str(request.claim_number)
    if request.paragraph_id:
        params["paragraph_id"] = request.paragraph_id
    if request.response_format:
        params["format"] = request.response_format
    return f"/api/patent/original/{request.canonical_patent_id}?{urlencode(params)}"


def build_stub_original_result(request: OriginalRequest, trace_id: str, head_only: bool) -> OriginalViewResult:
    _ = head_only
    if request.response_format == "redirect":
        return OriginalViewResult(
            kind="redirect",
            status_code=302,
            headers={"Cache-Control": "public, max-age=300", "ETag": '"patent-original-stub-redirect-v1"'},
            redirect_url=f"https://patent.example.invalid/original/{request.canonical_patent_id}",
        )

    section_label = {
        "claim": f"Claim {request.claim_number}" if request.claim_number is not None else "Claim",
        "description": "Description",
        "abstract": "Abstract",
        "figure": "Figure",
        "fulltext": "Full Text",
    }.get(request.section, "Original")
    if request.response_format == "html":
        safe_patent_id = escape(request.canonical_patent_id)
        safe_section_label = escape(section_label)
        safe_anchor = escape(request.anchor)
        return OriginalViewResult(
            kind="content",
            status_code=200,
            headers={"Cache-Control": "public, max-age=300", "ETag": '"patent-original-stub-v1"', "Content-Type": "text/html; charset=utf-8"},
            payload=(
                f"<article data-patent-id=\"{safe_patent_id}\">"
                f"<h1>{safe_section_label}</h1>"
                f"<div>Patent original stub content for {safe_patent_id} [{safe_anchor}]</div>"
                "</article>"
            ),
        )
    if request.response_format == "text":
        return OriginalViewResult(
            kind="content",
            status_code=200,
            headers={"Cache-Control": "public, max-age=300", "ETag": '"patent-original-stub-v1"', "Content-Type": "text/plain; charset=utf-8"},
            payload=f"Patent original stub content for {request.canonical_patent_id} [{request.anchor}]",
        )
    return OriginalViewResult(
        kind="content",
        status_code=200,
        headers={"Cache-Control": "public, max-age=300", "ETag": '"patent-original-stub-v1"'},
        payload={
            "success": True,
            "canonical_patent_id": request.canonical_patent_id,
            "title": f"Patent original stub for {request.canonical_patent_id}",
            "provider": "patent_stub",
            "section": request.section,
            "section_label": section_label,
            "content_format": request.response_format,
            "content": f"Patent original stub content for {request.canonical_patent_id} [{request.anchor}]",
            "trace_id": trace_id,
        },
    )


class OriginalViewService:
    def __init__(
        self,
        *,
        execution_cache: Any | None = None,
        provider: ProviderFn | None = None,
        cache_ttl_seconds: int = 300,
        original_version: str = "v1",
    ) -> None:
        self._execution_cache = execution_cache
        self._provider = provider or build_stub_original_result
        self._cache_ttl_seconds = max(1, int(cache_ttl_seconds))
        self._original_version = str(original_version or "v1").strip() or "v1"

    def get_original_view(
        self,
        *,
        canonical_patent_id: str,
        section: str | None,
        claim_number: str | int | None,
        paragraph_id: str | None,
        response_format: str | None,
        head_only: bool,
        trace_id: str,
    ) -> dict[str, object]:
        request = parse_original_request(
            canonical_patent_id=canonical_patent_id,
            section=section,
            claim_number=claim_number,
            paragraph_id=paragraph_id,
            response_format=response_format,
        )
        cached = None if head_only else self._get_cached_result(request)
        if cached is not None:
            return self._apply_runtime_fields(cached, trace_id=str(trace_id or "").strip())
        result = self._provider(request, str(trace_id or "").strip(), bool(head_only))
        if isinstance(result, OriginalViewResult):
            payload = {
                "kind": result.kind,
                "status_code": int(result.status_code),
                "headers": dict(result.headers or {}),
                "payload": result.payload,
                "redirect_url": result.redirect_url,
            }
        elif isinstance(result, dict):
            payload = dict(result)
        else:
            raise ValueError("provider must return OriginalViewResult or dict")
        cache_payload = self._build_cache_payload(payload)
        if not head_only:
            self._set_cached_result(request, cache_payload)
        return self._apply_runtime_fields(payload, trace_id=str(trace_id or "").strip())

    def _get_cached_result(self, request: OriginalRequest) -> dict[str, object] | None:
        if self._execution_cache is None:
            return None
        getter = getattr(self._execution_cache, "get_original_cache", None)
        if not callable(getter):
            return None
        cached = getter(
            canonical_patent_id=request.canonical_patent_id,
            section=request.section,
            anchor=request.anchor,
            response_format=request.response_format,
            original_version=self._original_version,
        )
        return dict(cached or {}) if isinstance(cached, dict) else None

    def _set_cached_result(self, request: OriginalRequest, payload: dict[str, object]) -> None:
        if self._execution_cache is None:
            return
        setter = getattr(self._execution_cache, "set_original_cache", None)
        if not callable(setter):
            return
        setter(
            canonical_patent_id=request.canonical_patent_id,
            section=request.section,
            anchor=request.anchor,
            response_format=request.response_format,
            original_version=self._original_version,
            payload=payload,
            ttl_seconds=self._cache_ttl_seconds,
        )

    @staticmethod
    def _build_cache_payload(payload: dict[str, object]) -> dict[str, object]:
        cached = dict(payload or {})
        body = cached.get("payload")
        if isinstance(body, dict) and "trace_id" in body:
            cached["payload"] = {key: value for key, value in body.items() if key != "trace_id"}
        return cached

    @staticmethod
    def _apply_runtime_fields(payload: dict[str, object], *, trace_id: str) -> dict[str, object]:
        response_payload = dict(payload or {})
        body = response_payload.get("payload")
        if isinstance(body, dict):
            normalized_body = dict(body)
            if trace_id:
                normalized_body["trace_id"] = trace_id
            response_payload["payload"] = normalized_body
        return response_payload
