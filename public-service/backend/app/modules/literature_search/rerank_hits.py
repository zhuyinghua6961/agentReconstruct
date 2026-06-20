from __future__ import annotations

from typing import Any

from app.modules.literature_search.rerank_service import rerank_configured, rerank_documents


_GARBAGE_TITLES = {"```html", "```HTML", "html"}


def hit_rerank_text(hit: dict[str, Any]) -> str:
    title = str(hit.get("title") or "").strip()
    doi = str(hit.get("doi") or "").strip()
    if title and title not in _GARBAGE_TITLES:
        return title
    if doi:
        return doi
    return title or doi or "unknown"


def apply_literature_rerank(
    *,
    query: str,
    hits: list[dict[str, Any]],
    limit: int,
    logger: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata: dict[str, Any] = {
        "enabled": rerank_configured(),
        "applied": False,
        "fallback": False,
    }
    if len(hits) <= 1 or not rerank_configured():
        return hits[:limit], metadata

    documents = [hit_rerank_text(hit) for hit in hits]
    result = rerank_documents(
        query=query,
        documents=documents,
        metadatas=[dict(hit) for hit in hits],
        top_n=limit,
        logger=logger,
    )
    if bool(result.get("fallback")):
        metadata["fallback"] = True
        metadata["fallback_reason"] = str(result.get("fallback_reason") or "")
        return hits[:limit], metadata

    ranked_hits: list[dict[str, Any]] = []
    scores = list(result.get("rerank_scores") or [])
    for index, hit in enumerate(list(result.get("metadatas") or [])):
        if not isinstance(hit, dict):
            continue
        ordered = dict(hit)
        if index < len(scores):
            ordered["match_score"] = float(scores[index])
        ordered["match_source"] = str(ordered.get("match_source") or "rerank")
        ranked_hits.append(ordered)

    if not ranked_hits:
        metadata["fallback"] = True
        metadata["fallback_reason"] = "empty_rerank_mapping"
        return hits[:limit], metadata

    metadata["applied"] = True
    return ranked_hits[:limit], metadata
