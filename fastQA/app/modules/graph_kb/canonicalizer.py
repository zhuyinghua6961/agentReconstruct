from __future__ import annotations

import re
from typing import Any

from app.modules.graph_kb.models import GraphEvidenceBundle, GraphQueryPlanV2


_DOI_RE = re.compile(r"^10\.\d{1,9}/", re.IGNORECASE)


def _normalize_doi(value: Any) -> str:
    text = str(value or "").strip().rstrip(".,;:")
    return text if _DOI_RE.match(text) else ""


def canonicalize_graph_rows(*, plan: GraphQueryPlanV2, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> GraphEvidenceBundle:
    normalized_rows = tuple(dict(item or {}) for item in list(rows or []) if isinstance(item, dict))
    doi_candidates: list[str] = []
    facts: list[str] = []
    for row in normalized_rows:
        doi = _normalize_doi(row.get("doi"))
        if doi and doi not in doi_candidates:
            doi_candidates.append(doi)
        fact_parts = [f"{key}={value}" for key, value in row.items() if value not in (None, "", [], ())]
        if fact_parts:
            facts.append("; ".join(fact_parts))
    return GraphEvidenceBundle(
        doi_candidates=tuple(doi_candidates),
        facts=tuple(facts),
        render_slots={
            "rows": normalized_rows,
            "template_id": plan.legacy_template_id,
            "intent": plan.intent,
        },
        direct_answerable=bool(normalized_rows) and plan.strategy == "template",
        constraints_for_rag=(),
    )
