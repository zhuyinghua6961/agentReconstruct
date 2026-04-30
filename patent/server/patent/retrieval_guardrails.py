from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from server.patent.models import PatentRetrievalClaim
from server.patent.stage2_controls import PatentStage2RuntimeToggles


_IDENTIFIER_RE = re.compile(r"\b(?=[A-Z0-9/.,-]*\d)[A-Z]{2}[A-Z0-9][A-Z0-9/.,-]{4,}[A-Z0-9]\b")
_METRIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mAh/g|mah/g|g/cm3|g/cm³|C-rate|C|℃|°C|%)\b",
    re.IGNORECASE,
)
_MATERIAL_RE = re.compile(r"\b(?:LFP|LMFP|LiFePO4|NCM|NCA|LCO|LTO|SiC|SOC|SOH|IPC|CPC)\b", re.IGNORECASE)
_METRIC_NAME_RE = re.compile(r"(?:放电容量|压实密度|tap density|capacity|porosity|倍率|循环|SOC|SOH)", re.IGNORECASE)


@dataclass(frozen=True)
class GuardedPatentStage2Queries:
    queries: list[str]
    diagnostics: dict[str, Any]


def _unique_terms(values: list[str]) -> list[str]:
    terms: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if text and text not in terms:
            terms.append(text)
    return terms


def apply_patent_stage2_query_guardrails(
    *,
    user_question: str,
    retrieval_claim: PatentRetrievalClaim,
    queries: list[str],
    toggles: PatentStage2RuntimeToggles,
    graph_context: dict[str, Any] | None,
) -> GuardedPatentStage2Queries:
    normalized_queries = _unique_terms(list(queries or []))
    if not toggles.convergence_enabled:
        return GuardedPatentStage2Queries(
            queries=normalized_queries,
            diagnostics={"enabled": False, "injected_keywords": [], "injected_thresholds": []},
        )

    source_text = " ".join(
        [
            str(user_question or ""),
            str(retrieval_claim.claim or ""),
            " ".join(str(item) for item in list(retrieval_claim.keywords or [])),
        ]
    )
    thresholds = _unique_terms(_METRIC_RE.findall(source_text))
    identifiers = _unique_terms(_IDENTIFIER_RE.findall(source_text.upper()))
    materials = _unique_terms(_MATERIAL_RE.findall(source_text))
    metric_names = _unique_terms(_METRIC_NAME_RE.findall(source_text))
    keywords = _unique_terms([str(item) for item in list(retrieval_claim.keywords or [])])
    graph_hints = []
    if isinstance(graph_context, dict):
        for values in dict(graph_context.get("stage2_entity_hints") or {}).values():
            graph_hints.extend([str(item) for item in list(values or [])])
    injected_terms = _unique_terms([*identifiers, *keywords, *materials, *metric_names, *thresholds, *graph_hints])
    guarded: list[str] = []
    rewrites: list[dict[str, str]] = []
    for query in normalized_queries or [" ".join(str(retrieval_claim.claim or "").split()).strip()]:
        missing = [term for term in injected_terms if term and term.lower() not in query.lower()]
        guarded_query = " ".join([*missing, query]).strip() if missing else query
        if guarded_query and guarded_query not in guarded:
            guarded.append(guarded_query)
            rewrites.append({"original_query": query, "final_query": guarded_query})
    return GuardedPatentStage2Queries(
        queries=guarded,
        diagnostics={
            "enabled": True,
            "injected_keywords": keywords,
            "injected_thresholds": thresholds,
            "injected_entities": _unique_terms([*identifiers, *materials, *graph_hints]),
            "injected_metrics": _unique_terms([*metric_names, *thresholds]),
            "query_rewrites": rewrites,
            "graph_hint_count": len(graph_hints),
        },
    )
