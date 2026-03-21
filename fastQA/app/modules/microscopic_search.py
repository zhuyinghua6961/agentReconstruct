from __future__ import annotations

from typing import Any, Callable


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
) -> dict[str, Any]:
    try:
        query_embedding = embedding_model.encode([user_question]).tolist()
    except Exception:
        return {"documents": [], "metadatas": [], "distances": [], "ids": []}

    candidate_count = int(n_results)
    if use_rerank:
        candidate_count = max(candidate_count, int(rerank_candidates))
        candidate_count = min(candidate_count, max(collection.count(), 1))

    results = collection.query(
        query_embeddings=query_embedding,
        n_results=candidate_count,
        include=["metadatas", "distances", "documents"],
    )
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
            reranked = rerank_fn(
                query=user_question,
                documents=documents,
                metadatas=metadatas,
                top_n=max(int(n_results), 1),
            )
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
    return payload
