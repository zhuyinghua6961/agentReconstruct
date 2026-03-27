#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage-4 synthesis helpers for generation-driven RAG."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Set, Tuple

from app.modules.generation_pipeline.feature_flags import env_bool, env_int
from app.modules.generation_pipeline.doi_validation import build_doi_variants, canonicalize_doi, extract_valid_dois


ELEMENT_SYNONYM_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Ti", ("ti", "钛")),
    ("Mg", ("mg", "镁")),
    ("Mn", ("mn", "锰")),
    ("Zn", ("zn", "锌")),
    ("V", ("v", "钒")),
    ("F", ("f", "氟")),
    ("Cu", ("cu", "铜")),
    ("Al", ("al", "铝")),
)

DOI_BRACKET_PATTERN = re.compile(
    r"\((?:doi\s*=|DOI:\s*)(10\.(?:[^\s,()]+|\([^\s,()]+\))+)\)",
    re.IGNORECASE,
)
DOI_INLINE_PATTERN = re.compile(
    r"\bdoi\s*=\s*(10\.(?:[^\s,()]+|\([^\s,()]+\))+)",
    re.IGNORECASE,
)


def resolve_stage4_reference_policy(
    *,
    topk: int | None = None,
    min_citations: int | None = None,
    element_guard: bool | None = None,
) -> Tuple[int, int, bool]:
    """Resolve stage4 citation policy from optional params + env."""
    resolved_topk = env_int("QA_STAGE4_REFERENCE_TOPK", 5, minimum=3, maximum=20) if topk is None else max(3, min(int(topk), 20))
    resolved_min_citations = (
        env_int("QA_STAGE4_MIN_CITATIONS", 10, minimum=1, maximum=20)
        if min_citations is None
        else max(1, min(int(min_citations), 20))
    )
    if resolved_min_citations > resolved_topk:
        resolved_min_citations = resolved_topk
    resolved_element_guard = env_bool("QA_STAGE4_ELEMENT_GUARD", True) if element_guard is None else bool(element_guard)
    return resolved_topk, resolved_min_citations, resolved_element_guard


def _extract_question_elements(question: str) -> List[Tuple[str, Tuple[str, ...]]]:
    text = str(question or "").lower()
    return [(canonical, aliases) for canonical, aliases in ELEMENT_SYNONYM_GROUPS if any(alias in text for alias in aliases)]


def _doi_contains_any_alias(
    *,
    doi: str,
    aliases: Iterable[str],
    pdf_chunks: Dict[str, List[Dict[str, Any]]] | None,
) -> bool:
    if not pdf_chunks or doi not in pdf_chunks:
        return False
    chunks = pdf_chunks.get(doi) or []
    if not chunks:
        return False
    lowered_aliases = [a.lower() for a in aliases if a]
    for chunk in chunks:
        text = str(chunk.get("text") or "").lower()
        if any(alias in text for alias in lowered_aliases):
            return True
    return False


def _compute_doi_scores_from_retrieval(retrieval_results: Dict[str, Any] | None) -> List[Tuple[str, float]]:
    if retrieval_results is None:
        return []
    doi_scores: Dict[str, List[float]] = defaultdict(list)
    for _, claim_result in retrieval_results.get("claim_to_results", {}).items():
        distances = claim_result.get("distances", [])
        metadatas = claim_result.get("metadatas", [])
        for i, dist in enumerate(distances):
            if i >= len(metadatas):
                continue
            metadata = metadatas[i]
            doi = str(metadata.get("doi", "")).strip()
            if not doi:
                continue
            try:
                similarity = 1.0 - float(dist) if float(dist) <= 1.0 else 0.0
            except Exception:
                similarity = 0.0
            doi_scores[doi].append(similarity)

    doi_avg_scores = []
    for doi, scores in doi_scores.items():
        if not scores:
            continue
        doi_avg_scores.append((doi, sum(scores) / len(scores)))
    doi_avg_scores.sort(key=lambda item: item[1], reverse=True)
    return doi_avg_scores


def _build_reference_instruction_text(
    *,
    top_refs_with_scores: List[Tuple[str, float]],
    topk: int,
    min_citations: int,
) -> str:
    if not top_refs_with_scores:
        return "【参考文献】（无检索结果）\n"

    lines = [
        "【参考文献列表（请在答案中相关句子的末尾插入DOI引用）】",
        f"以下{topk}篇文献与问题高度相关，请根据句子内容插入对应DOI：",
        "",
    ]
    for idx, (doi, _) in enumerate(top_refs_with_scores, 1):
        lines.append(f"{idx}. {doi}")
    lines.extend(
        [
            "",
            "⭐ 重要要求：",
            f"- 必须至少引用 {min_citations} 篇不同文献（最多 {topk} 篇）",
            "- 每句话只插入 1 个最相关 DOI",
            "- 尽量优先引用与用户问题核心元素一致的文献",
            "",
            "示例：",
            '"葡萄糖和PEG作为混合碳源的最佳质量比为3:1 (doi=10.1016_j.matchemphys.2017.10.021)"',
        ]
    )
    return "\n".join(lines) + "\n"


def build_top_reference_context(
    *,
    retrieval_results: Dict[str, Any] | None,
    logger: Any,
    topk: int | None = None,
    min_citations: int | None = None,
    element_guard: bool | None = None,
    user_question: str = "",
    pdf_chunks: Dict[str, List[Dict[str, Any]]] | None = None,
) -> Tuple[List[Tuple[str, float]], str]:
    """Build top-k DOI ranking and instruction text for stage-4 prompting."""
    resolved_topk, resolved_min_citations, resolved_element_guard = resolve_stage4_reference_policy(
        topk=topk,
        min_citations=min_citations,
        element_guard=element_guard,
    )

    ranked = _compute_doi_scores_from_retrieval(retrieval_results)
    if not ranked and pdf_chunks:
        # fallback if retrieval structure is unavailable but chunk evidence exists
        ranked = [(doi, float(len(chunks))) for doi, chunks in pdf_chunks.items() if chunks]
        ranked.sort(key=lambda item: item[1], reverse=True)

    if resolved_element_guard and ranked:
        element_groups = _extract_question_elements(user_question)
        if element_groups:
            preferred: List[Tuple[str, float]] = []
            rest: List[Tuple[str, float]] = []
            for doi, score in ranked:
                matched = any(
                    _doi_contains_any_alias(doi=doi, aliases=aliases, pdf_chunks=pdf_chunks)
                    for _, aliases in element_groups
                )
                if matched:
                    preferred.append((doi, score))
                else:
                    rest.append((doi, score))
            if preferred:
                ranked = preferred + rest
                logger.info(
                    f"🔒 Stage4元素一致性保护已启用: 关键词元素={','.join(c for c, _ in element_groups)}，优先 {len(preferred)} 篇文献"
                )

    top_refs_with_scores = ranked[:resolved_topk]
    reference_text = _build_reference_instruction_text(
        top_refs_with_scores=top_refs_with_scores,
        topk=resolved_topk,
        min_citations=resolved_min_citations,
    )
    return top_refs_with_scores, reference_text


def build_top5_reference_context(
    retrieval_results: Dict[str, Any] | None,
    logger: Any,
    **kwargs: Any,
) -> Tuple[List[Tuple[str, float]], str]:
    """Backward-compatible wrapper."""
    return build_top_reference_context(
        retrieval_results=retrieval_results,
        logger=logger,
        topk=kwargs.get("topk", 5),
        min_citations=kwargs.get("min_citations", 10),
        element_guard=kwargs.get("element_guard"),
        user_question=kwargs.get("user_question", ""),
        pdf_chunks=kwargs.get("pdf_chunks"),
    )


def extract_cited_dois(final_answer: str, logger: Any) -> Tuple[List[str], Set[str]]:
    """Extract DOI values from model answer with multiple regex patterns."""
    cited_dois_set: Set[str] = set()
    for pattern in [DOI_BRACKET_PATTERN, DOI_INLINE_PATTERN]:
        for match in pattern.finditer(str(final_answer or "")):
            for normalized in extract_valid_dois(match.group(1)):
                cited_dois_set.add(normalized)

    cited_dois = list(cited_dois_set)
    logger.info(f"🔍 提取到的DOI列表: {cited_dois}")
    return cited_dois, cited_dois_set


def log_topk_coverage(
    cited_dois_set: Set[str],
    top_refs_with_scores: List[Tuple[str, float]],
    logger: Any,
    *,
    label: str = "top-k",
) -> None:
    """Log citation coverage between answer DOI set and ranked DOI candidates."""
    target_dois = [doi for doi, _ in top_refs_with_scores]
    missing_dois = set(target_dois) - cited_dois_set

    if missing_dois:
        logger.warning(f"⚠️ LLM未引用以下 {len(missing_dois)} 篇参考文献（{label}）:")
        for doi in missing_dois:
            logger.warning(f"   - {doi}")

        cited_target = len(cited_dois_set.intersection(set(target_dois)))
        logger.info(f"📊 LLM在答案中引用了 {cited_target}/{len(target_dois)} 篇{label}文献")
        return

    logger.info(f"✅ LLM成功引用了全部 {len(target_dois)} 篇{label}文献")


def log_top5_coverage(
    cited_dois_set: Set[str],
    top5_with_scores: List[Tuple[str, float]],
    logger: Any,
) -> None:
    """Backward-compatible wrapper for top-5 coverage logging."""
    log_topk_coverage(cited_dois_set, top5_with_scores, logger, label="top-5")


def _select_reference_preview_chunk(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    preferred = None
    for chunk in chunks:
        text = str((chunk or {}).get("text", "") or "")
        source = str((chunk or {}).get("source", "") or "")
        text_head = text.lstrip()[:32].lower()
        is_html_like = text_head.startswith("```html") or text_head.startswith("<html")
        is_md_source = source.startswith("md_expansion")
        if not is_html_like and not is_md_source:
            return chunk
        if preferred is None and not is_html_like:
            preferred = chunk
        if preferred is None:
            preferred = chunk
    return preferred or (chunks[0] if chunks else {})


def build_references_from_pdf_chunks(
    cited_dois: List[str],
    pdf_chunks: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Build simplified reference metadata list from cited DOIs."""
    references: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    chunk_keys = {str(key or "").strip(): key for key in pdf_chunks.keys()}
    canonical_keys = {canonicalize_doi(key): key for key in chunk_keys.keys() if canonicalize_doi(key)}

    for doi in cited_dois:
        resolved_key = None
        for variant in build_doi_variants(doi):
            if variant in chunk_keys:
                resolved_key = chunk_keys[variant]
                break
        if resolved_key is None:
            resolved_key = canonical_keys.get(canonicalize_doi(doi))
        if resolved_key is None:
            continue
        resolved_chunks = pdf_chunks.get(resolved_key) or []
        if not resolved_chunks:
            continue
        canonical = canonicalize_doi(str(resolved_key or doi))
        if canonical in seen:
            continue
        seen.add(canonical)
        preview_chunk = _select_reference_preview_chunk(resolved_chunks)
        references.append(
            {
                "doi": canonical or str(doi),
                "chunk_count": len(resolved_chunks),
                "sample_text": str(preview_chunk.get("text", ""))[:400] + "...",
            }
        )
    return references


__all__ = [
    "build_references_from_pdf_chunks",
    "build_top5_reference_context",
    "build_top_reference_context",
    "extract_cited_dois",
    "log_top5_coverage",
    "log_topk_coverage",
    "resolve_stage4_reference_policy",
]
