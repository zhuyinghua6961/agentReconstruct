from __future__ import annotations

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
    except Exception:
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
