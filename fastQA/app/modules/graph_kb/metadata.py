from __future__ import annotations

from typing import Any


def build_graph_route_metadata(
    *,
    route_family: str = "",
    tri_state_mode: str = "",
    strategy: str = "",
    intent: str = "",
    result_count: int | None = None,
    confidence: float | None = None,
    doi_candidates_count: int | None = None,
    filtered_doi_count: int | None = None,
    suspicious_doi_count: int | None = None,
    fallback_reason: str = "",
    direct_answer_eligible: bool | None = None,
    rag_injection_enabled: bool | None = None,
    doi_source: str = "none",
    graph_pipeline_version: str = "v2",
) -> dict[str, Any]:
    route = str(route_family or "").strip()
    payload: dict[str, Any] = {
        "graph_pipeline_version": str(graph_pipeline_version or "v2"),
        "knowledge_route_family": route,
        "legacy_route_family": route,
        "tri_state_mode": str(tri_state_mode or ""),
        "graph_strategy": str(strategy or ""),
        "graph_intent": str(intent or ""),
        "graph_fallback_reason": str(fallback_reason or ""),
        "doi_source": str(doi_source or "none"),
    }
    if result_count is not None:
        payload["graph_result_count"] = int(result_count)
    if confidence is not None:
        payload["graph_confidence"] = float(confidence)
    if doi_candidates_count is not None:
        payload["graph_doi_candidates_count"] = int(doi_candidates_count)
    if filtered_doi_count is not None:
        payload["graph_filtered_doi_count"] = int(filtered_doi_count)
    if suspicious_doi_count is not None:
        payload["graph_suspicious_doi_count"] = int(suspicious_doi_count)
    if direct_answer_eligible is not None:
        payload["graph_direct_answer_eligible"] = bool(direct_answer_eligible)
    if rag_injection_enabled is not None:
        payload["graph_rag_injection_enabled"] = bool(rag_injection_enabled)
    return payload
