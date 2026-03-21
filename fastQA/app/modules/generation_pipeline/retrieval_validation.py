#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retrieval relevance validation helpers for generation-driven RAG."""

from typing import Any, Dict

from app.modules.generation_pipeline.text_processing import normalize_chemical_notation


def validate_retrieval_relevance(
    search_results: Dict[str, Any],
    query: str,
    claim_text: str,
    logger: Any,
) -> Dict[str, Any]:
    """Filter retrieval results by keyword coverage plus vector similarity."""
    if not search_results or "documents" not in search_results:
        return search_results

    documents = search_results["documents"]
    metadatas = search_results.get("metadatas", [])
    distances = search_results.get("distances", [])

    if distances:
        valid_distances = [d for d in distances if d is not None and isinstance(d, (int, float))]
        if valid_distances:
            min_dist = min(valid_distances)
            max_dist = max(valid_distances)
            avg_dist = sum(valid_distances) / len(valid_distances)
            logger.info(
                f"      📊 Distance统计: 最小={min_dist:.4f}, 最大={max_dist:.4f}, "
                f"平均={avg_dist:.4f}, 样本数={len(valid_distances)}"
            )
            sample_distances = valid_distances[:5]
            logger.info(f"      📊 Distance样本: {[f'{d:.4f}' for d in sample_distances]}")

    validation_keywords = set()
    normalized_query = normalize_chemical_notation(query)
    for word in normalized_query.split():
        if len(word) > 1:
            validation_keywords.add(word.lower())

    normalized_claim = normalize_chemical_notation(claim_text)
    for word in normalized_claim.split():
        if len(word) > 1:
            validation_keywords.add(word.lower())

    core_keywords = {
        "fe2p",
        "Fe2P",
        "磷酸铁锂",
        "lifepo4",
        "LiFePO4",
        "lfp",
        "LFP",
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

    validated_docs = []
    validated_metas = []
    validated_distances = []

    for i, doc in enumerate(documents):
        if not doc:
            continue

        if isinstance(doc, list):
            doc = " ".join(str(item) for item in doc)
        elif not isinstance(doc, str):
            doc = str(doc)

        doc_text = doc.lower()
        matched_keywords = sum(1 for kw in validation_keywords if kw in doc_text)
        match_ratio = matched_keywords / len(validation_keywords) if validation_keywords else 0
        distance = distances[i] if i < len(distances) else 1.0

        if distance <= 0:
            vector_similarity = 1.0
        else:
            vector_similarity = 1.0 - (distance * distance) / 2.0
            vector_similarity = max(0.0, min(1.0, vector_similarity))

        logger.debug(f"      文档{i + 1} distance: {distance:.4f}, vector_similarity: {vector_similarity:.4f}")

        relevance_score = match_ratio * 0.4 + vector_similarity * 0.6
        relevance_threshold = 0.3

        if relevance_score >= relevance_threshold:
            validated_docs.append(doc)
            validated_metas.append(metadatas[i] if i < len(metadatas) else {})
            validated_distances.append(distances[i] if i < len(distances) else 1.0)
            logger.info(
                f"   ✅ 保留文档 {i + 1}: 相关性评分={relevance_score:.3f} "
                f"(关键词匹配={match_ratio:.3f}, 向量相似度={vector_similarity:.3f})"
            )
        else:
            logger.warning(
                f"   ❌ 过滤文档 {i + 1}: 相关性评分过低={relevance_score:.3f} "
                f"(关键词匹配={match_ratio:.3f}, 向量相似度={vector_similarity:.3f})"
            )

    if len(validated_docs) < 3 and len(documents) >= 3:
        logger.warning(f"   ⚠️ 验证后结果过少({len(validated_docs)}个)，保留前3个最相关文档")
        sorted_indices = sorted(range(len(distances)), key=lambda idx: distances[idx])
        validated_docs = [documents[i] for i in sorted_indices[:3]]
        validated_metas = [metadatas[i] if i < len(metadatas) else {} for i in sorted_indices[:3]]
        validated_distances = [distances[i] for i in sorted_indices[:3]]

    logger.info(f"   📊 检索验证完成: {len(documents)} -> {len(validated_docs)} 个文档")
    return {
        "documents": validated_docs,
        "metadatas": validated_metas,
        "distances": validated_distances,
    }
