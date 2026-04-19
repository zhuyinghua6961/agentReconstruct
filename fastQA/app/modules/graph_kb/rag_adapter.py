from __future__ import annotations

import hashlib
import json
from typing import Any

from app.modules.graph_kb.models import GraphEvidenceBundle, GraphQueryPlanV2, GraphRagPayload, SemanticDecision


def _dedupe_preserve_order(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in list(values or []):
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return tuple(ordered)


def _collect_entity_hints(bundle: GraphEvidenceBundle) -> dict[str, tuple[str, ...]]:
    rows = list(bundle.render_slots.get("rows") or [])
    hints: dict[str, list[str]] = {
        "materials": [],
        "titles": [],
    }

    def _append(bucket: str, value: Any) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                _append(bucket, item)
            return
        text = str(value or "").strip()
        if not text or text in hints[bucket]:
            return
        hints[bucket].append(text)

    for row in rows:
        if not isinstance(row, dict):
            continue
        _append("materials", row.get("raw_materials"))
        _append("materials", row.get("matched_raw_materials"))
        _append("titles", row.get("title"))

    return {key: tuple(values) for key, values in hints.items() if values}


def _render_stage1_context(*, decision: SemanticDecision, plan: GraphQueryPlanV2, bundle: GraphEvidenceBundle) -> str:
    lines: list[str] = []
    if decision.legacy_route:
        lines.append(f"graph_route: {decision.legacy_route}")
    if plan.intent:
        lines.append(f"graph_intent: {plan.intent}")
    if bundle.doi_candidates:
        lines.append("graph_dois: " + ", ".join(bundle.doi_candidates[:10]))
    if bundle.facts:
        lines.append("graph_facts:")
        lines.extend(f"- {fact}" for fact in bundle.facts[:5])
    return "\n".join(lines).strip()


def _render_stage4_fact_block(bundle: GraphEvidenceBundle) -> str:
    facts = [str(item or "").strip() for item in list(bundle.facts or []) if str(item or "").strip()]
    if not facts:
        return ""
    return "\n".join(f"- {fact}" for fact in facts[:20])


def _fingerprint_payload(*, decision: SemanticDecision, plan: GraphQueryPlanV2, payload: GraphRagPayload) -> str:
    serialized = {
        "mode": decision.mode,
        "legacy_route": decision.legacy_route,
        "strategy": plan.strategy,
        "intent": plan.intent,
        "stage1_context_block": payload.stage1_context_block,
        "stage2_doi_candidates": list(payload.stage2_doi_candidates),
        "stage2_constraints": [
            {"field": item.field, "operator": item.operator, "value": item.value}
            for item in payload.stage2_constraints
        ],
        "stage2_entity_hints": {key: list(values) for key, values in sorted(payload.stage2_entity_hints.items())},
        "stage4_fact_block": payload.stage4_fact_block,
    }
    digest = hashlib.sha256(json.dumps(serialized, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"graph:{digest[:16]}"


def build_graph_rag_payload(
    *,
    decision: SemanticDecision,
    plan: GraphQueryPlanV2,
    bundle: GraphEvidenceBundle,
) -> GraphRagPayload:
    payload = GraphRagPayload(
        stage1_context_block=_render_stage1_context(decision=decision, plan=plan, bundle=bundle),
        stage2_doi_candidates=_dedupe_preserve_order(bundle.doi_candidates),
        stage2_constraints=tuple(bundle.constraints_for_rag or ()),
        stage2_entity_hints=_collect_entity_hints(bundle),
        stage4_fact_block=_render_stage4_fact_block(bundle),
        cache_fingerprint="pending",
    )
    return GraphRagPayload(
        stage1_context_block=payload.stage1_context_block,
        stage2_doi_candidates=payload.stage2_doi_candidates,
        stage2_constraints=payload.stage2_constraints,
        stage2_entity_hints=payload.stage2_entity_hints,
        stage4_fact_block=payload.stage4_fact_block,
        cache_fingerprint=_fingerprint_payload(decision=decision, plan=plan, payload=payload),
    )
