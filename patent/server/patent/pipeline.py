from __future__ import annotations

from typing import Any

from server.schemas.request_models import PatentAskRequest
from server.services.mode_profiles import PatentModeProfile, get_patent_mode_profile



def build_stub_patent_result(
    *,
    request: PatentAskRequest,
    context: dict[str, Any] | None = None,
    profile: PatentModeProfile | None = None,
) -> dict[str, Any]:
    resolved_profile = profile or get_patent_mode_profile()
    _ = context or {}
    answer_text = f"Patent Phase 1 stub answer: {request.question}"
    return {
        "answer_text": answer_text,
        "requested_mode": resolved_profile.requested_mode,
        "actual_mode": resolved_profile.actual_mode,
        "route": resolved_profile.route,
        "query_mode": resolved_profile.query_mode,
        "steps": [
            {
                "step": "patent_stub",
                "title": "Patent Stub",
                "message": "Patent Phase 1 stub execution completed.",
                "status": "success",
            }
        ],
        "references": [],
        "reference_links": [],
        "pdf_links": [],
        "used_files": [],
        "file_selection": dict(request.file_selection or {}),
        "source_scope": request.source_scope,
        "timings": {
            "stub_total_ms": 1,
        },
    }
