from __future__ import annotations

from typing import Any

from app.modules.generation_pipeline.text_processing import normalize_chemical_notation


def validate_retrieval_relevance(
    search_results: dict[str, Any],
    query: str,
    claim_text: str,
    logger: Any,
) -> dict[str, Any]:
    if not search_results or "documents" not in search_results:
        return search_results

    documents = list(search_results.get("documents") or [])
    metadatas = list(search_results.get("metadatas") or [])
    distances = list(search_results.get("distances") or [])

    validation_keywords: set[str] = set()
    normalized_query = normalize_chemical_notation(query)
    normalized_claim = normalize_chemical_notation(claim_text)
    for text in (normalized_query, normalized_claim):
        for word in text.split():
            if len(word) > 1:
                validation_keywords.add(word.lower())

    core_keywords = {
        "fe2p",
        "磷酸铁锂",
        "lifepo4",
        "lfp",
        "杂相",
        "抑制",
        "氧化",
        "添加剂",
        "碳热还原",
        "合成",
        "温度",
        "磷化铁",
        "iron phosphide",
    }
    validation_keywords.update(word.lower() for word in core_keywords)

    validated_docs: list[Any] = []
    validated_metas: list[Any] = []
    validated_distances: list[Any] = []

    for index, doc in enumerate(documents):
        if not doc:
            continue
        if isinstance(doc, list):
            doc_text = " ".join(str(item) for item in doc)
        else:
            doc_text = str(doc)
        lowered = doc_text.lower()
        matched_keywords = sum(1 for kw in validation_keywords if kw in lowered)
        match_ratio = matched_keywords / len(validation_keywords) if validation_keywords else 0.0
        distance = distances[index] if index < len(distances) else 1.0
        if isinstance(distance, (int, float)):
            vector_similarity = 1.0 if distance <= 0 else max(0.0, min(1.0, 1.0 - (distance * distance) / 2.0))
        else:
            vector_similarity = 0.0
        relevance_score = match_ratio * 0.4 + vector_similarity * 0.6
        if relevance_score >= 0.3:
            validated_docs.append(doc)
            validated_metas.append(metadatas[index] if index < len(metadatas) else {})
            validated_distances.append(distance)
        else:
            logger.warning(
                "stage2 filtered retrieval hit index=%s relevance_score=%.3f match_ratio=%.3f vector_similarity=%.3f",
                index + 1,
                relevance_score,
                match_ratio,
                vector_similarity,
            )

    if len(validated_docs) < 3 and len(documents) >= 3:
        sortable = [
            (idx, distances[idx] if idx < len(distances) and isinstance(distances[idx], (int, float)) else 999999.0)
            for idx in range(len(documents))
        ]
        sortable.sort(key=lambda item: item[1])
        top_indexes = [idx for idx, _ in sortable[:3]]
        validated_docs = [documents[idx] for idx in top_indexes]
        validated_metas = [metadatas[idx] if idx < len(metadatas) else {} for idx in top_indexes]
        validated_distances = [distances[idx] if idx < len(distances) else None for idx in top_indexes]

    return {
        "documents": validated_docs,
        "metadatas": validated_metas,
        "distances": validated_distances,
    }
