from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_IDENTIFIER_RE = re.compile(r"\b(?=[A-Z0-9/.,-]*\d)[A-Z]{2}[A-Z0-9][A-Z0-9/.,-]{4,}[A-Z0-9]\b")
_METRIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mAh/g|mah/g|g/cm3|g/cm³|C-rate|C|℃|°C|%)\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class PatentRetrievalIntent:
    explicit_patent_ids: list[str]
    metric_tokens: list[str]
    metric_units: list[str]
    entity_tokens: list[str]
    graph_candidate_ids: list[str]


@dataclass(frozen=True)
class PatentCandidateScore:
    patent_id: str
    score: float
    reasons: list[str]
    hits: list[dict[str, Any]]


def _normalize_id(value: Any) -> str:
    return str(value or "").strip().upper()


def _tokens(value: str) -> set[str]:
    return {item.lower() for item in _TOKEN_RE.findall(str(value or "")) if item.strip()}


def derive_patent_retrieval_intent(
    *,
    user_question: str,
    retrieval_claims: list[Any],
    graph_context: dict[str, Any] | None,
) -> PatentRetrievalIntent:
    text = " ".join(
        [
            str(user_question or ""),
            *[
                str(getattr(item, "claim", "") if not isinstance(item, dict) else item.get("claim", ""))
                for item in list(retrieval_claims or [])
            ],
        ]
    )
    metrics = [item for item in _METRIC_RE.findall(text)]
    metric_units = []
    for metric in metrics:
        normalized = metric.lower()
        if "mah/g" in normalized:
            metric_units.append("mah/g")
        elif "g/cm3" in normalized or "g/cm³" in normalized:
            metric_units.append("g/cm3")
        elif "%" in normalized:
            metric_units.append("%")
        elif "c-rate" in normalized or normalized.endswith("c"):
            metric_units.append("c")
    explicit_ids = [_normalize_id(item) for item in _IDENTIFIER_RE.findall(text.upper())]
    graph_ids = [_normalize_id(item) for item in list((graph_context or {}).get("stage2_patent_candidates") or (graph_context or {}).get("candidate_patent_ids") or [])]
    entities = [token for token in _tokens(text) if token not in {metric.lower() for metric in metrics}]
    return PatentRetrievalIntent(
        explicit_patent_ids=list(dict.fromkeys(explicit_ids)),
        metric_tokens=list(dict.fromkeys(metrics)),
        metric_units=list(dict.fromkeys(metric_units)),
        entity_tokens=list(dict.fromkeys(entities)),
        graph_candidate_ids=list(dict.fromkeys(graph_ids)),
    )


def _table_text(hit: dict[str, Any]) -> str:
    metadata = dict(hit.get("metadata") or {})
    parts: list[str] = []
    for table in list(metadata.get("table_supplements") or []):
        if not isinstance(table, dict):
            continue
        parts.append(str(table.get("table_title") or ""))
        for row in list(table.get("rows") or []):
            if isinstance(row, dict):
                parts.extend(str(value) for value in row.values())
    return " ".join(parts)


def aggregate_patent_candidates(
    *,
    hits: list[dict[str, Any]],
    intent: PatentRetrievalIntent,
    table_metric_boost_enabled: bool = False,
) -> list[PatentCandidateScore]:
    explicit_ids = {_normalize_id(item) for item in intent.explicit_patent_ids}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for hit in list(hits or []):
        patent_id = _normalize_id(hit.get("patent_id") or hit.get("canonical_patent_id"))
        if not patent_id:
            continue
        if explicit_ids and patent_id not in explicit_ids:
            continue
        grouped.setdefault(patent_id, []).append(hit)

    metric_tokens = [metric.lower().replace(" ", "") for metric in intent.metric_tokens]
    metric_units = [unit.lower() for unit in intent.metric_units]
    graph_ids = {_normalize_id(item) for item in intent.graph_candidate_ids}
    ranked: list[PatentCandidateScore] = []
    for patent_id, patent_hits in grouped.items():
        score = 0.0
        reasons: list[str] = []
        for hit in patent_hits:
            base_score = hit.get("score")
            try:
                score = max(score, float(base_score if base_score is not None else 0.0))
            except Exception:
                pass
            channel = str(hit.get("channel") or hit.get("stage2_source") or "")
            section = str(hit.get("section_type") or "").lower()
            document = str(hit.get("document") or "")
            normalized_document = document.lower().replace(" ", "").replace("cm³", "cm3")
            if channel == "graph_candidate" or patent_id in graph_ids:
                score += 0.05
                reasons.append("graph_candidate_boost")
            if channel == "chunk_vector_global":
                score += 0.08
                reasons.append("global_chunk_match")
            if section in {"description", "claim"}:
                score += 0.08
                reasons.append(f"{section}_section_match")
            if metric_tokens and any(metric in normalized_document for metric in metric_tokens):
                score += 0.35
                reasons.append("metric_threshold_match")
            elif metric_units and any(unit in normalized_document for unit in metric_units):
                score += 0.35
                reasons.append("metric_threshold_match")
            if table_metric_boost_enabled:
                normalized_table = _table_text(hit).lower().replace(" ", "").replace("cm³", "cm3")
                if metric_tokens and any(metric in normalized_table for metric in metric_tokens):
                    score += 0.40
                    reasons.append("table_metric_match")
                elif metric_units and any(unit in normalized_table for unit in metric_units):
                    score += 0.40
                    reasons.append("table_metric_match")
        ranked.append(
            PatentCandidateScore(
                patent_id=patent_id,
                score=score,
                reasons=list(dict.fromkeys(reasons)),
                hits=patent_hits,
            )
        )
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked
