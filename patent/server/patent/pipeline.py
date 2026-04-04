from __future__ import annotations

from typing import Any

from server.patent.retrieval_models import PatentRetrievalOutcome
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


def build_retrieval_patent_result(
    *,
    request: PatentAskRequest,
    retrieval_outcome: PatentRetrievalOutcome,
    profile: PatentModeProfile | None = None,
) -> dict[str, Any]:
    resolved_profile = profile or get_patent_mode_profile()
    if retrieval_outcome.not_found:
        return {
            "answer_text": "Patent retrieval found no matching results.",
            "requested_mode": resolved_profile.requested_mode,
            "actual_mode": resolved_profile.actual_mode,
            "route": resolved_profile.route,
            "query_mode": resolved_profile.query_mode,
            "steps": [
                {
                    "step": "retrieval_not_found",
                    "title": "Patent Retrieval",
                    "message": "No matching patent was found by the no-vector retrieval pipeline.",
                    "status": "success",
                }
            ],
            "references": [],
            "reference_objects": [],
            "reference_links": [],
            "original_links": [],
            "used_files": [],
            "file_selection": dict(request.file_selection or {}),
            "source_scope": request.source_scope,
            "metadata": {
                "retrieval_backend": retrieval_outcome.retrieval_backend,
                "retrieval_version": retrieval_outcome.retrieval_version,
                "catalog_index_version": retrieval_outcome.catalog_index_version,
                "cache_hit": retrieval_outcome.cache_hit,
                "negative_cache_hit": retrieval_outcome.negative_cache_hit,
                "not_found": True,
            },
            "timings": dict(retrieval_outcome.timings),
        }

    return {
        "answer_text": str(retrieval_outcome.answer_text or (f"Patent retrieval answer: {retrieval_outcome.evidences[0].title}" if retrieval_outcome.evidences else "Patent retrieval answer")),
        "requested_mode": resolved_profile.requested_mode,
        "actual_mode": resolved_profile.actual_mode,
        "route": resolved_profile.route,
        "query_mode": resolved_profile.query_mode,
        "steps": [
            {
                "step": "retrieval_mvp",
                "title": "Patent Retrieval",
                "message": (
                    f"Resolved answer through {retrieval_outcome.retrieval_backend}; "
                    f"matched {len(retrieval_outcome.references)} patent(s) and "
                    f"loaded {sum(len(item.table_supplements) for item in retrieval_outcome.evidences)} table supplement(s)."
                ),
                "status": "success",
            }
        ],
        "references": list(retrieval_outcome.references),
        "reference_objects": list(retrieval_outcome.reference_objects),
        "reference_links": list(retrieval_outcome.reference_links),
        "original_links": list(retrieval_outcome.original_links),
        "used_files": [],
        "file_selection": dict(request.file_selection or {}),
        "source_scope": request.source_scope,
        "metadata": {
            "retrieval_backend": retrieval_outcome.retrieval_backend,
            "retrieval_version": retrieval_outcome.retrieval_version,
            "catalog_index_version": retrieval_outcome.catalog_index_version,
            "cache_hit": retrieval_outcome.cache_hit,
            "negative_cache_hit": retrieval_outcome.negative_cache_hit,
            "not_found": False,
        },
        "timings": dict(retrieval_outcome.timings),
    }
