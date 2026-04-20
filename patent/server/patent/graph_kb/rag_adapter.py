from __future__ import annotations

import hashlib
import json

from server.patent.graph_kb.models import PatentGraphEvidenceBundle, PatentGraphQueryPlanV2, PatentGraphRagPayload, PatentGraphSemanticDecision


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


def _collect_entity_hints(bundle: PatentGraphEvidenceBundle) -> dict[str, tuple[str, ...]]:
    hints = {
        "ipc_codes": _dedupe_preserve_order(bundle.ipc_candidates),
        "organizations": _dedupe_preserve_order(bundle.organization_candidates),
        "inventors": _dedupe_preserve_order(bundle.inventor_candidates),
    }
    return {key: value for key, value in hints.items() if value}


def _render_stage1_context(
    *,
    decision: PatentGraphSemanticDecision,
    plan: PatentGraphQueryPlanV2,
    bundle: PatentGraphEvidenceBundle,
) -> str:
    lines: list[str] = [
        f"graph_mode: {decision.mode}",
        f"graph_route_family: {decision.route_family}",
    ]
    if plan.strategy:
        lines.append(f"graph_strategy: {plan.strategy}")
    if plan.intent:
        lines.append(f"graph_intent: {plan.intent}")
    if bundle.patent_candidates:
        lines.append("graph_patent_candidates: " + ", ".join(bundle.patent_candidates[:10]))
    if bundle.ipc_candidates:
        lines.append("graph_ipc_candidates: " + ", ".join(bundle.ipc_candidates[:10]))
    if bundle.organization_candidates:
        lines.append("graph_organizations: " + ", ".join(bundle.organization_candidates[:10]))
    if bundle.inventor_candidates:
        lines.append("graph_inventors: " + ", ".join(bundle.inventor_candidates[:10]))
    if bundle.constraints_for_rag:
        lines.append("graph_constraints:")
        for item in bundle.constraints_for_rag[:10]:
            lines.append(f"- {item.field} {item.operator} {item.value}")
    if bundle.facts:
        lines.append("graph_facts:")
        lines.extend(f"- {fact}" for fact in bundle.facts[:5])
    return "\n".join(lines).strip()


def _render_stage4_fact_block(bundle: PatentGraphEvidenceBundle) -> str:
    facts = [str(item or "").strip() for item in list(bundle.facts or ()) if str(item or "").strip()]
    if not facts:
        return ""
    return "\n".join(f"- {fact}" for fact in facts[:20])


def _fingerprint_payload(
    *,
    decision: PatentGraphSemanticDecision,
    plan: PatentGraphQueryPlanV2,
    payload: PatentGraphRagPayload,
) -> str:
    serialized = {
        "mode": decision.mode,
        "route_family": decision.route_family,
        "strategy": plan.strategy,
        "intent": plan.intent,
        "stage1_context_block": payload.stage1_context_block,
        "stage2_patent_candidates": list(payload.stage2_patent_candidates),
        "stage2_constraints": [
            {"field": item.field, "operator": item.operator, "value": item.value}
            for item in payload.stage2_constraints
        ],
        "stage2_entity_hints": {key: list(values) for key, values in sorted(payload.stage2_entity_hints.items())},
        "stage4_fact_block": payload.stage4_fact_block,
        "stage4_graph_candidate_patent_ids": list(payload.stage4_graph_candidate_patent_ids),
    }
    digest = hashlib.sha256(json.dumps(serialized, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"patent-graph:{digest[:16]}"


def build_patent_graph_rag_payload(
    *,
    decision: PatentGraphSemanticDecision,
    plan: PatentGraphQueryPlanV2,
    bundle: PatentGraphEvidenceBundle,
) -> PatentGraphRagPayload:
    diagnostics = dict(bundle.diagnostics or {})
    diagnostics.setdefault("route_family", decision.route_family)
    diagnostics.setdefault("strategy", plan.strategy)
    payload = PatentGraphRagPayload(
        stage1_context_block=_render_stage1_context(decision=decision, plan=plan, bundle=bundle),
        stage2_patent_candidates=_dedupe_preserve_order(bundle.patent_candidates),
        stage2_constraints=tuple(bundle.constraints_for_rag or ()),
        stage2_entity_hints=_collect_entity_hints(bundle),
        stage4_fact_block=_render_stage4_fact_block(bundle),
        stage4_graph_candidate_patent_ids=_dedupe_preserve_order(bundle.patent_candidates),
        cache_fingerprint="pending",
        diagnostics=diagnostics,
    )
    return PatentGraphRagPayload(
        stage1_context_block=payload.stage1_context_block,
        stage2_patent_candidates=payload.stage2_patent_candidates,
        stage2_constraints=payload.stage2_constraints,
        stage2_entity_hints=payload.stage2_entity_hints,
        stage4_fact_block=payload.stage4_fact_block,
        stage4_graph_candidate_patent_ids=payload.stage4_graph_candidate_patent_ids,
        cache_fingerprint=_fingerprint_payload(decision=decision, plan=plan, payload=payload),
        diagnostics=payload.diagnostics,
    )
