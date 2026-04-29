from __future__ import annotations

from typing import Any

from server.patent.graph_kb.slots import PatentGraphQuestionSlots


_VOLATILE_EVIDENCE_KEYS = {"raw_rows", "rows", "diagnostics"}


def _clean_evidence_quality(value: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, item in dict(value or {}).items():
        if str(key) in _VOLATILE_EVIDENCE_KEYS:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            cleaned[str(key)] = item
    return cleaned


def build_patent_graph_route_metadata(
    *,
    attempted: bool,
    mode: str = "",
    route_family: str = "",
    strategy: str = "",
    template_id: str = "",
    path_id: str = "",
    fingerprint: str = "",
    row_count: int | None = None,
    evidence_quality: dict[str, Any] | None = None,
    downgrade_reason: str = "",
    stage2_behavior: str = "",
    graph_pipeline_version: str = "v2",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "graph_pipeline_version": str(graph_pipeline_version or "v2"),
        "graph_kb_attempted": bool(attempted),
        "graph_kb_mode": str(mode or ""),
        "graph_kb_route_family": str(route_family or ""),
        "graph_kb_strategy": str(strategy or ""),
        "graph_kb_template_id": str(template_id or ""),
        "graph_kb_path_id": str(path_id or ""),
        "graph_kb_fingerprint": str(fingerprint or ""),
    }
    if row_count is not None:
        metadata["graph_kb_row_count"] = int(row_count)
    cleaned_quality = _clean_evidence_quality(evidence_quality)
    if cleaned_quality:
        metadata["graph_kb_evidence_quality"] = cleaned_quality
    if downgrade_reason:
        metadata["graph_kb_downgrade_reason"] = str(downgrade_reason)
    if stage2_behavior:
        metadata["graph_kb_stage2_behavior"] = str(stage2_behavior)
    return metadata


def summarize_patent_graph_slots(slots: PatentGraphQuestionSlots) -> dict[str, Any]:
    lists = {
        "patent_ids": list(slots.patent_ids[:10]),
        "ipc_prefixes": list(slots.ipc_prefixes[:10]),
        "ipc_code_prefixes": list(slots.ipc_code_prefixes[:10]),
        "ipc_full_codes": list(slots.ipc_full_codes[:10]),
        "applicant_names": list(slots.applicant_names[:5]),
        "inventor_names": list(slots.inventor_names[:5]),
        "agency_names": list(slots.agency_names[:5]),
        "material_terms": list(slots.material_terms[:10]),
        "process_terms": list(slots.process_terms[:10]),
        "metric_terms": list(slots.metric_terms[:10]),
    }
    return {
        **{key: value for key, value in lists.items() if value},
        "counts": {
            "patent_ids": len(slots.patent_ids),
            "ipc_prefixes": len(slots.ipc_prefixes),
            "ipc_code_prefixes": len(slots.ipc_code_prefixes),
            "ipc_full_codes": len(slots.ipc_full_codes),
            "applicant_names": len(slots.applicant_names),
            "inventor_names": len(slots.inventor_names),
            "agency_names": len(slots.agency_names),
            "material_terms": len(slots.material_terms),
            "process_terms": len(slots.process_terms),
            "metric_terms": len(slots.metric_terms),
        },
        "asks_lookup": slots.asks_lookup,
        "asks_list": slots.asks_list,
        "asks_count": slots.asks_count,
        "asks_compare": slots.asks_compare,
        "asks_rank": slots.asks_rank,
        "asks_why_how": slots.asks_why_how,
        "asks_trend_landscape": slots.asks_trend_landscape,
        "has_doi": slots.has_doi,
    }

