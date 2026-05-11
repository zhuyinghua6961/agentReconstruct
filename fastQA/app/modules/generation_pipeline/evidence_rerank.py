from __future__ import annotations

import math
from typing import Any, Dict, List

from app.modules.generation_pipeline.feature_flags import env_bool, env_int


def _as_float_vector(value: Any) -> list[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        return []
    out: list[float] = []
    for item in value:
        try:
            out.append(float(item))
        except Exception:
            out.append(0.0)
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    dot = sum(a[idx] * b[idx] for idx in range(size))
    norm_a = math.sqrt(sum(value * value for value in a[:size]))
    norm_b = math.sqrt(sum(value * value for value in b[:size]))
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _encode_texts(embedding_model: Any, texts: list[str]) -> list[list[float]]:
    if embedding_model is None or not texts or not hasattr(embedding_model, "encode"):
        return []
    try:
        encoded = embedding_model.encode(texts)
    except Exception:
        return []
    if hasattr(encoded, "tolist"):
        encoded = encoded.tolist()
    if not isinstance(encoded, list):
        return []
    return [_as_float_vector(item) for item in encoded]


def _lexical_score(query: str, text: str) -> float:
    q = str(query or "").lower()
    t = str(text or "").lower()
    terms = [term for term in q.replace("，", " ").replace("？", " ").replace("?", " ").split() if term]
    if not terms:
        return 0.0
    matched = sum(1 for term in terms if term in t)
    return matched / max(1, len(terms))


def _flatten_chunks(pdf_chunks: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doi, chunks in (pdf_chunks or {}).items():
        for index, chunk in enumerate(list(chunks or [])):
            if not isinstance(chunk, dict):
                continue
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            rows.append({"doi": str(doi), "index": index, "text": text, "chunk": dict(chunk)})
    return rows


def _comparison_queries(retrieval_results: dict[str, Any] | None) -> list[dict[str, Any]]:
    groups = (retrieval_results or {}).get("comparison_groups") if isinstance(retrieval_results, dict) else None
    if not isinstance(groups, list):
        return []
    queries: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        label = str(group.get("label") or "").strip()
        if not label:
            continue
        aliases = [str(item).strip() for item in list(group.get("aliases") or []) if str(item).strip()]
        query_parts = [label, *aliases]
        for query in list(group.get("queries") or group.get("retrieval_queries") or []):
            if str(query or "").strip():
                query_parts.append(str(query).strip())
        queries.append(
            {
                "label": label,
                "query": " ".join(query_parts),
                "dois": {str(item).strip() for item in list(group.get("doi_candidates") or []) if str(item).strip()},
            }
        )
    return queries


def _score_rows(rows: list[dict[str, Any]], query: str, embedding_model: Any) -> list[dict[str, Any]]:
    texts = [query] + [row["text"] for row in rows]
    vectors = _encode_texts(embedding_model, texts)
    query_vector = vectors[0] if vectors else []
    chunk_vectors = vectors[1:] if len(vectors) == len(texts) else []
    scored: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        score = _cosine(query_vector, chunk_vectors[idx]) if idx < len(chunk_vectors) else _lexical_score(query, row["text"])
        item = dict(row)
        item["score"] = float(score)
        scored.append(item)
    scored.sort(key=lambda item: (item["score"], -item["index"]), reverse=True)
    return scored


def _rows_to_chunk_map(rows: list[dict[str, Any]], *, topk_per_doi: int) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        doi = str(row["doi"])
        chunk = dict(row["chunk"])
        text_key = str(chunk.get("text") or "")[:200]
        key = (doi, text_key)
        if key in seen:
            continue
        if len(grouped.get(doi, [])) >= topk_per_doi:
            continue
        seen.add(key)
        score = float(row.get("score") or 0.0)
        if score > 0:
            chunk["evidence_score"] = score
        grouped.setdefault(doi, []).append(chunk)
    return grouped


def rerank_evidence_chunks(
    *,
    pdf_chunks: dict[str, list[dict[str, Any]]],
    user_question: str,
    retrieval_results: dict[str, Any] | None,
    embedding_model: Any = None,
    enabled: bool | None = None,
    topk_total: int | None = None,
    topk_per_doi: int | None = None,
    topk_per_comparison_object: int | None = None,
) -> dict[str, Any]:
    resolved_enabled = env_bool("QA_STAGE35_EVIDENCE_RERANK_ENABLED", True) if enabled is None else bool(enabled)
    rows = _flatten_chunks(pdf_chunks)
    before_count = len(rows)
    if not resolved_enabled or not rows:
        return {
            "pdf_chunks": pdf_chunks,
            "stats": {
                "enabled": resolved_enabled,
                "before_chunk_count": before_count,
                "after_chunk_count": before_count,
                "comparison_object_count": 0,
            },
        }

    resolved_topk_total = (
        env_int("QA_STAGE35_EVIDENCE_TOPK_TOTAL", 30, minimum=1, maximum=200)
        if topk_total is None
        else max(1, min(int(topk_total), 200))
    )
    resolved_topk_per_doi = (
        env_int("QA_STAGE35_EVIDENCE_TOPK_PER_DOI", 3, minimum=1, maximum=20)
        if topk_per_doi is None
        else max(1, min(int(topk_per_doi), 20))
    )
    resolved_topk_per_object = (
        env_int("QA_STAGE35_EVIDENCE_TOPK_PER_COMPARISON_OBJECT", 8, minimum=1, maximum=50)
        if topk_per_comparison_object is None
        else max(1, min(int(topk_per_comparison_object), 50))
    )

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()
    comparison_groups = _comparison_queries(retrieval_results)
    for group in comparison_groups:
        group_rows = [row for row in rows if not group["dois"] or row["doi"] in group["dois"]]
        for row in _score_rows(group_rows, f"{user_question} {group['query']}", embedding_model)[:resolved_topk_per_object]:
            key = (row["doi"], str(row["chunk"].get("text") or "")[:200])
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)

    for row in _score_rows(rows, user_question, embedding_model):
        key = (row["doi"], str(row["chunk"].get("text") or "")[:200])
        if key in selected_keys:
            continue
        selected.append(row)
        selected_keys.add(key)
        if len(selected) >= resolved_topk_total:
            break

    selected = selected[:resolved_topk_total]
    chunk_map = _rows_to_chunk_map(selected, topk_per_doi=resolved_topk_per_doi)
    after_count = sum(len(chunks) for chunks in chunk_map.values())
    return {
        "pdf_chunks": chunk_map,
        "stats": {
            "enabled": True,
            "before_chunk_count": before_count,
            "after_chunk_count": after_count,
            "comparison_object_count": len(comparison_groups),
            "top_dois": list(chunk_map.keys())[:10],
        },
    }


__all__ = ["rerank_evidence_chunks"]
