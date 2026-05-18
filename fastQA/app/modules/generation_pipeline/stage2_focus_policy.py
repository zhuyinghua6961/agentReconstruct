"""Reorder Stage2 DOI candidates using lexical focus alignment (not embedding alone).

Mitigates OR-merge dilution from auxiliary claims while avoiding empty gates via relaxation."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Literal, Tuple

from app.modules.generation_pipeline.feature_flags import env_bool, env_int


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    try:
        parsed = float(str(raw).strip()) if raw is not None else float(default)
    except Exception:
        parsed = float(default)
    return max(minimum, min(maximum, parsed))

ClaimAxis = Literal["primary", "auxiliary"]

DensityIntent = Literal["tapping", "compaction", "both", "ambiguous", "none"]

# Synonym bundles: any hit expands to the whole bundle for lexical matching only.
# Kinds avoid mixing **电极压实密度** vs **粉末振实密度** when the user question is ambiguous
# (e.g. only「高压实型」): `tapping_axis` / `compaction_axis` are gated in ``expand_focus_evidence_terms``.
_FOCUS_SYNONYM_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("neutral", ("高压实", "高压实型", "致密化", "粉末致密")),
    ("compaction_axis", ("压实密度", "压片密度", "极片压实", "电极压实")),
    ("tapping_axis", ("振实密度", "敲击密度", "tap density", "tap-density", "粉末振实")),
    ("neutral", ("辊压", "calendering", "极片辊压")),
    ("neutral", ("孔隙率", "孔隙度", "porosity")),
    ("neutral", ("堆积密度", "粉末密度", "填充密度")),
    ("neutral", ("球形颗粒", "球形粉末", "二次颗粒", "spherical particle")),
    ("neutral", ("粒径分布", "粒度分布", "级配", "particle size distribution", "psa")),
    ("neutral", ("体积容量", "体积能量密度", "volumetric capacity", "volumetric energy")),
    ("neutral", ("喷雾干燥", "喷雾造粒")),
    ("neutral", ("碳包覆", "导电碳")),
)

_AUX_SUBSTRINGS: tuple[str, ...] = (
    "xps",
    "缺陷",
    "掺杂",
    "安全性",
    "热失控",
    "催化",
    "枝晶",
    "界面副反应",
    "热稳定性验证",
)


def _norm_blob(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _density_metric_intent(*, scope_text: str) -> DensityIntent:
    """Classify whether the user (or Stage1) pins **tapping** vs **compaction-sheet** density."""
    text = str(scope_text or "")
    tap = bool(
        re.search(
            r"(振实密度|振实\s*仪|敲击密度|tap[-\s]*density|tapping\s+density|粉末振实)",
            text,
            flags=re.I,
        )
    )
    comp = bool(
        re.search(
            r"(压实密度|压片密度|极片压实|电极压实|电极片压实|极片辊压|compaction\s+density)",
            text,
            flags=re.I,
        )
    )
    if tap and comp:
        return "both"
    if tap:
        return "tapping"
    if comp:
        return "compaction"
    if re.search(r"(高压实型|高压实|粉末致密|致密化|球形.*颗|高堆积)", text):
        return "ambiguous"
    return "none"


def extract_doi_from_metadata(meta: Any) -> str:
    if not isinstance(meta, dict):
        return ""
    for key in ("doi", "DOI", "source_doi"):
        raw = str(meta.get(key) or "").strip()
        if raw.startswith("10."):
            return raw
    return ""


def expand_focus_evidence_terms(
    *,
    query_focus_terms: List[str] | None,
    user_question: str,
) -> List[str]:
    """Union Stage1 focus terms, user-question cues, and synonym expansions."""
    seen: set[str] = set()
    out: List[str] = []

    def _add(term: str) -> None:
        t = str(term or "").strip()
        if len(t) < 2:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(t)

    for t in query_focus_terms or []:
        _add(t)

    scope_for_intent = str(user_question or "")
    if isinstance(query_focus_terms, list):
        scope_for_intent = f"{scope_for_intent}\n" + "\n".join(str(x or "") for x in query_focus_terms)
    intent = _density_metric_intent(scope_text=scope_for_intent)

    hint_res = (
        r"(高压实|压实密度|振实密度|辊压|孔隙率|堆积密度"
        r"|致密化|球形|粒径|体积能量|spray\s*drying|tap\s*density|calender)"
    )
    for m in re.finditer(hint_res, user_question or "", flags=re.I):
        _add(m.group(0))

    uq_lc = str(user_question or "").lower()
    seed_lc = " ".join(str(s).lower() for s in out)
    combined_lc = seed_lc + " " + uq_lc

    for kind, group in _FOCUS_SYNONYM_GROUPS:
        if not any(g.lower() in combined_lc for g in group):
            continue

        if kind == "tapping_axis" and intent == "compaction":
            continue

        if kind == "compaction_axis":
            if intent == "tapping":
                continue
            if intent in {"ambiguous", "none"}:
                if not re.search(
                    r"(压实密度|压片密度|极片压实|电极压实|电极片压实|极片辊压|compaction\s+density)",
                    scope_for_intent,
                    flags=re.I,
                ):
                    continue

        for g in group:
            _add(g)

    # Vague 「高压实型」questions align with powder tap-density literature; do not require
    # the user to spell「振实密度」before we add tapping-side lexical hints.
    if intent == "ambiguous":
        for kind, group in _FOCUS_SYNONYM_GROUPS:
            if kind != "tapping_axis":
                continue
            for g in group:
                _add(g)

    return out


def _claim_axis_heuristic(*, claim_key: str, user_question: str, focus_terms: List[str]) -> ClaimAxis:
    ck = claim_key  # preserve CJK casing for substring checks below
    ck_lower = ck.lower()

    # Avoid substring "压实" matching inside「高压实」; use phrase-level anchors.
    compaction_markers = (
        "振实密度",
        "压实密度",
        "振实",
        "辊压",
        "致密",
        "高压实",
        "孔隙",
        "堆积",
        "球形",
        "粒径",
        "喷雾干燥",
        "体积容量",
        "致密化",
        "级配",
        "颗粒",
        "粉末",
        "电极",
        "极片",
    )
    has_compaction_anchor = any(m in ck for m in compaction_markers)

    aux_hit = any(pat in ck_lower for pat in _AUX_SUBSTRINGS)
    if aux_hit and not has_compaction_anchor:
        return "auxiliary"

    for term in focus_terms:
        tl = term.strip()
        if len(tl) >= 2 and tl in ck:
            return "primary"

    if has_compaction_anchor:
        return "primary"

    return "primary"


def lexical_focus_hit_count(*, text: str, expanded_terms: List[str]) -> int:
    if not expanded_terms or not text:
        return 0
    blob = _norm_blob(text)
    hits = 0
    for term in expanded_terms:
        t = _norm_blob(term)
        if len(t) >= 2 and t in blob:
            hits += 1
    return hits


def build_doi_support_stats(
    *,
    claim_to_results: Dict[str, Any],
    user_question: str,
    focus_terms: List[str],
    expanded_terms: List[str],
) -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}
    for claim_key, bundle in (claim_to_results or {}).items():
        if not isinstance(bundle, dict):
            continue
        axis = _claim_axis_heuristic(claim_key=str(claim_key), user_question=user_question, focus_terms=focus_terms)
        docs = list(bundle.get("documents") or [])
        metas = list(bundle.get("metadatas") or [])
        dists = list(bundle.get("distances") or [])
        limit = min(len(docs), len(metas))
        for idx in range(limit):
            doi = extract_doi_from_metadata(metas[idx])
            if not doi:
                continue
            row = stats.setdefault(
                doi,
                {
                    "min_dist": 1e9,
                    "max_focus_hits": 0,
                    "primary_hits": 0,
                    "aux_hits": 0,
                    "primary_focus_hits": 0,
                },
            )
            try:
                dist = float(dists[idx]) if idx < len(dists) else 1.0
            except (TypeError, ValueError):
                dist = 1.0
            row["min_dist"] = min(float(row["min_dist"]), dist)

            doc_text = str(docs[idx] or "")
            meta = metas[idx] if isinstance(metas[idx], dict) else {}
            title = str(meta.get("title") or meta.get("paper_title") or "")
            blob = f"{doc_text}\n{title}"
            fh = lexical_focus_hit_count(text=blob, expanded_terms=expanded_terms)
            row["max_focus_hits"] = max(int(row["max_focus_hits"]), fh)
            if axis == "primary":
                row["primary_hits"] = int(row["primary_hits"]) + 1
                if fh > 0:
                    row["primary_focus_hits"] = int(row["primary_focus_hits"]) + 1
            else:
                row["aux_hits"] = int(row["aux_hits"]) + 1

    return stats


def focus_policy_relaxed(*, expanded_terms: List[str], stats: Dict[str, Dict[str, Any]], threshold: int) -> bool:
    if not expanded_terms:
        return True
    pool = sum(1 for s in stats.values() if int(s.get("primary_focus_hits") or 0) > 0 or int(s.get("max_focus_hits") or 0) > 0)
    return pool < int(threshold)


def _doi_score_components(
    *,
    stats_row: Dict[str, Any],
    rank_index: int,
    relaxed: bool,
    no_focus_penalty: float,
    aux_only_penalty: float,
    primary_focus_bonus: float,
) -> float:
    min_dist_raw = stats_row.get("min_dist")
    try:
        min_dist = float(min_dist_raw)
    except (TypeError, ValueError):
        min_dist = 0.5
    if min_dist >= 1e8:
        min_dist = 0.5

    dist_score = 1.0 / (1.0 + max(0.0, min_dist))
    rank_score = 1.0 / (1.0 + 0.04 * float(rank_index))

    base = 0.55 * dist_score + 0.45 * rank_score
    mh = int(stats_row.get("max_focus_hits") or 0)
    pfh = int(stats_row.get("primary_focus_hits") or 0)
    ph = int(stats_row.get("primary_hits") or 0)
    ah = int(stats_row.get("aux_hits") or 0)

    mult = 1.0
    if mh <= 0 and not relaxed:
        mult *= max(0.05, float(no_focus_penalty))
    elif mh <= 0 and relaxed:
        mult *= max(0.55, float(no_focus_penalty) ** 0.5)

    if ah > 0 and ph <= 0 and mh <= 0:
        mult *= max(0.05, float(aux_only_penalty))

    if pfh > 0:
        mult *= max(1.0, float(primary_focus_bonus))

    return base * mult


def rerank_dois_for_focus_evidence(
    *,
    ordered_dois: List[str],
    retrieval_results: Dict[str, Any],
    user_question: str,
    query_focus_terms: List[str] | None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Stable reorder of Stage2 dois using focus-aligned scores + quotas (soft-cap aux-only degenerate dois)."""
    audit: Dict[str, Any] = {"enabled": False}
    ordered = [_ for _ in ordered_dois if str(_).strip()]
    if not ordered:
        return [], audit

    if not env_bool("QA_STAGE2_FOCUS_POLICY_ENABLED", True):
        audit["enabled"] = False
        audit["skipped"] = "disabled"
        return list(ordered), audit

    focus_terms_clean = [str(t).strip() for t in (query_focus_terms or []) if str(t or "").strip()]
    expanded = expand_focus_evidence_terms(query_focus_terms=focus_terms_clean, user_question=str(user_question or ""))

    intent_blob = _norm_blob(
        "".join(focus_terms_clean)
        + " "
        + str(user_question or "")
    )
    has_user_intent = bool(
        re.search(
            r"(高压实|压实密度|振实密度|辊压|孔隙率|堆积密度|致密化)",
            intent_blob,
            flags=re.I,
        )
    )
    if not expanded and not has_user_intent:
        audit["enabled"] = False
        audit["skipped"] = "no_focus_signals"
        return list(ordered), audit

    if not expanded:
        expanded = expand_focus_evidence_terms(
            query_focus_terms=[],
            user_question=str(user_question or ""),
        )

    claim_to_results = retrieval_results.get("claim_to_results") if isinstance(retrieval_results, dict) else None
    if not isinstance(claim_to_results, dict) or not claim_to_results:
        audit["enabled"] = False
        audit["skipped"] = "no_claim_map"
        return list(ordered), audit

    stats = build_doi_support_stats(
        claim_to_results=claim_to_results,
        user_question=str(user_question or ""),
        focus_terms=focus_terms_clean,
        expanded_terms=expanded,
    )
    relax_thresh = env_int("QA_FOCUS_POLICY_RELAX_MIN_FOCUS_DOIS", 4, minimum=0, maximum=50)
    relaxed = focus_policy_relaxed(expanded_terms=expanded, stats=stats, threshold=relax_thresh)

    rank_index_map = {doi: idx for idx, doi in enumerate(ordered)}
    no_focus_penalty = _env_float("QA_FOCUS_POLICY_NO_FOCUS_PENALTY", 0.42, minimum=0.05, maximum=1.0)
    aux_only_penalty = _env_float("QA_FOCUS_POLICY_AUX_ONLY_PENALTY", 0.48, minimum=0.05, maximum=1.0)
    primary_bonus = _env_float("QA_FOCUS_POLICY_PRIMARY_FOCUS_BONUS", 1.28, minimum=1.0, maximum=3.0)
    max_degen = env_int("QA_FOCUS_POLICY_MAX_AUX_DEGENERATE_DOIS", 6, minimum=0, maximum=100)

    scored_pairs: List[Tuple[float, str]] = []
    for doi in ordered:
        stats_row = stats.get(doi) or {}
        rid = rank_index_map.get(doi, 999)
        sc = _doi_score_components(
            stats_row=stats_row,
            rank_index=rid,
            relaxed=relaxed,
            no_focus_penalty=no_focus_penalty,
            aux_only_penalty=aux_only_penalty,
            primary_focus_bonus=primary_bonus,
        )
        scored_pairs.append((sc, doi))

    mh = lambda d: int((stats.get(d) or {}).get("max_focus_hits") or 0)
    pfh = lambda d: int((stats.get(d) or {}).get("primary_focus_hits") or 0)

    def _sort_pri(item: Tuple[float, str]) -> Tuple[int, int, float, str]:
        sc, doi = item
        return (1 if mh(doi) > 0 else 0, 1 if pfh(doi) > 0 else 0, sc, doi)

    scored_pairs.sort(key=_sort_pri, reverse=True)

    def _aux_degenerate(d: str) -> bool:
        row = stats.get(d) or {}
        return (
            int(row.get("aux_hits") or 0) > 0
            and int(row.get("primary_hits") or 0) <= 0
            and int(row.get("max_focus_hits") or 0) <= 0
        )

    capped: List[str] = []
    seen: set[str] = set()
    degenerate = 0
    for _, doi in scored_pairs:
        if doi in seen:
            continue
        if _aux_degenerate(doi) and not relaxed and degenerate >= max_degen:
            continue
        capped.append(doi)
        seen.add(doi)
        if _aux_degenerate(doi):
            degenerate += 1

    for doi in ordered:
        if doi not in seen:
            capped.append(doi)

    audit = {
        "enabled": True,
        "mode": "relaxed" if relaxed else "balanced",
        "expanded_terms_sample": expanded[:25],
        "focus_terms_used": focus_terms_clean,
        "relax_threshold": relax_thresh,
        "relaxed": relaxed,
        "max_aux_degenerate_slots": max_degen,
        "top_after_policy": capped[: min(25, len(capped))],
    }
    return capped, audit


__all__ = [
    "build_doi_support_stats",
    "expand_focus_evidence_terms",
    "extract_doi_from_metadata",
    "focus_policy_relaxed",
    "lexical_focus_hit_count",
    "rerank_dois_for_focus_evidence",
]
