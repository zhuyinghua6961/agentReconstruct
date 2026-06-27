from __future__ import annotations

import math
import time
from typing import Any, Callable

from app.integrations.llm import Stage2UpstreamGateCancelled


def normalize_chroma_query_result(results: dict[str, Any]) -> dict[str, list[Any]]:
    raw_docs = results.get("documents", [])
    raw_distances = results.get("distances", [])
    raw_metadatas = results.get("metadatas", [])
    raw_ids = results.get("ids", [])
    return {
        "documents": raw_docs[0] if raw_docs else [],
        "distances": raw_distances[0] if raw_distances else [],
        "metadatas": raw_metadatas[0] if raw_metadatas else [],
        "ids": raw_ids[0] if raw_ids else [],
    }


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _vector_diagnostics(vectors: Any) -> dict[str, Any]:
    first: list[Any] = []
    if isinstance(vectors, list) and vectors:
        candidate = vectors[0]
        first = list(candidate) if isinstance(candidate, list) else []
    numeric: list[float] = []
    has_nan = False
    has_inf = False
    for item in first:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        has_nan = has_nan or math.isnan(value)
        has_inf = has_inf or math.isinf(value)
        if not math.isnan(value) and not math.isinf(value):
            numeric.append(value)
    norm = math.sqrt(sum(value * value for value in numeric)) if numeric else 0.0
    return {
        "count": len(vectors) if isinstance(vectors, list) else 0,
        "dim": len(first),
        "norm": norm,
        "has_nan": has_nan,
        "has_inf": has_inf,
        "empty": not bool(first),
    }


def _numeric_distances(values: list[Any]) -> list[float]:
    out: list[float] = []
    for value in values:
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def _distance_summary(values: list[Any]) -> dict[str, Any]:
    nums = _numeric_distances(values)
    if not nums:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {"count": len(nums), "min": min(nums), "max": max(nums), "avg": sum(nums) / len(nums)}


def run_semantic_search(
    *,
    user_question: str,
    n_results: int,
    embedding_model: Any,
    collection: Any,
    translator: Any,
    translate: bool,
    use_rerank: bool = False,
    rerank_candidates: int = 50,
    rerank_fn: Callable[..., dict[str, Any]] | None = None,
    logger: Any | None = None,
    trace_label: str | None = None,
) -> dict[str, Any]:
    started_at = time.monotonic()
    embedding_ms = 0.0
    chroma_query_ms = 0.0
    rerank_ms = 0.0
    try:
        embedding_started_at = time.monotonic()
        query_embedding = embedding_model.encode([user_question]).tolist()
        embedding_ms = (time.monotonic() - embedding_started_at) * 1000.0
        if logger is not None:
            vector_diag = _vector_diagnostics(query_embedding)
            logger.info(
                "stage2 embedding diagnostic trace_label=%s model_class=%s input_count=1 input_chars=%s "
                "input_utf8_bytes=%s embedding_count=%s embedding_dim=%s embedding_norm=%.6f "
                "has_nan=%s has_inf=%s empty_embedding=%s embedding_ms=%.2f query_preview=%s",
                str(trace_label or ""),
                type(embedding_model).__name__,
                len(str(user_question or "")),
                len(str(user_question or "").encode("utf-8", errors="replace")),
                vector_diag["count"],
                vector_diag["dim"],
                float(vector_diag["norm"]),
                _bool_text(bool(vector_diag["has_nan"])),
                _bool_text(bool(vector_diag["has_inf"])),
                _bool_text(bool(vector_diag["empty"])),
                embedding_ms,
                str(user_question or "")[:220],
            )
    except Exception as exc:
        if logger is not None:
            logger.warning(
                "stage2 embedding diagnostic trace_label=%s status=error model_class=%s input_count=1 input_chars=%s error=%s",
                str(trace_label or ""),
                type(embedding_model).__name__,
                len(str(user_question or "")),
                exc,
            )
        return {"documents": [], "metadatas": [], "distances": [], "ids": []}

    candidate_count = int(n_results)
    if use_rerank:
        candidate_count = max(candidate_count, int(rerank_candidates))
        candidate_count = min(candidate_count, max(collection.count(), 1))

    chroma_started_at = time.monotonic()
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=candidate_count,
        include=["metadatas", "distances", "documents"],
    )
    chroma_query_ms = (time.monotonic() - chroma_started_at) * 1000.0
    flattened = normalize_chroma_query_result(results)
    documents = list(flattened["documents"])
    metadatas = list(flattened["metadatas"])
    distances = list(flattened["distances"])
    ids = list(flattened["ids"])
    if logger is not None:
        stats = _distance_summary(distances)
        logger.info(
            "stage2 chroma query diagnostic trace_label=%s requested_results=%s raw_docs=%s raw_metadatas=%s raw_distances=%s "
            "distance_min=%s distance_max=%s distance_avg=%s chroma_query_ms=%.2f id_sample=%s",
            str(trace_label or ""),
            candidate_count,
            len(documents),
            len(metadatas),
            len(distances),
            stats["min"],
            stats["max"],
            stats["avg"],
            chroma_query_ms,
            ids[:5],
        )

    rerank_meta: dict[str, Any] = {
        "enabled": bool(use_rerank),
        "applied": False,
        "fallback": False,
        "reason": "",
    }
    if use_rerank and rerank_fn and documents:
        try:
            rerank_started_at = time.monotonic()
            reranked = rerank_fn(
                query=user_question,
                documents=documents,
                metadatas=metadatas,
                top_n=max(int(n_results), 1),
            )
            rerank_ms = (time.monotonic() - rerank_started_at) * 1000.0
            rerank_docs = list(reranked.get("documents", []))
            rerank_metas = list(reranked.get("metadatas", []))
            rerank_scores = list(reranked.get("rerank_scores", []))
            if rerank_docs:
                documents = rerank_docs
                if rerank_metas:
                    metadatas = rerank_metas
                if rerank_scores:
                    distances = [1.0 - float(score) for score in rerank_scores]
                else:
                    distances = distances[: len(rerank_docs)]
                ids = ids[: len(documents)] if ids else []
                rerank_meta = {
                    "enabled": True,
                    "applied": not bool(reranked.get("fallback", False)),
                    "fallback": bool(reranked.get("fallback", False)),
                    "reason": str(reranked.get("fallback_reason", "")),
                    "provider": str(reranked.get("provider", "")),
                }
                if reranked.get("status_code") is not None:
                    rerank_meta["status_code"] = int(reranked["status_code"])
            else:
                rerank_meta = {
                    "enabled": True,
                    "applied": False,
                    "fallback": True,
                    "reason": "empty_rerank_output",
                }
        except Exception as exc:
            if isinstance(exc, Stage2UpstreamGateCancelled):
                raise
            rerank_meta = {
                "enabled": True,
                "applied": False,
                "fallback": True,
                "reason": f"rerank_exception:{exc}",
            }
            documents = documents[:n_results]
            metadatas = metadatas[:n_results]
            distances = distances[:n_results]
            ids = ids[:n_results]
    else:
        documents = documents[:n_results]
        metadatas = metadatas[:n_results]
        distances = distances[:n_results]
        ids = ids[:n_results]

    payload: dict[str, Any] = {
        "user_question": user_question,
        "documents": documents,
        "metadatas": metadatas,
        "distances": distances,
        "ids": ids,
        "rerank": rerank_meta,
    }
    if translate and translator:
        payload["translated_documents"] = [translator.translate(doc, show_progress=False) for doc in documents]
    if logger is not None:
        logger.info(
            "stage2 semantic search timing trace_label=%s embedding_ms=%.2f chroma_query_ms=%.2f rerank_ms=%.2f total_ms=%.2f candidate_count=%s final_docs=%s rerank_enabled=%s rerank_applied=%s rerank_fallback=%s",
            str(trace_label or ""),
            embedding_ms,
            chroma_query_ms,
            rerank_ms,
            (time.monotonic() - started_at) * 1000.0,
            candidate_count,
            len(documents),
            int(bool(use_rerank)),
            int(bool(rerank_meta.get("applied"))),
            int(bool(rerank_meta.get("fallback"))),
        )
    return payload
