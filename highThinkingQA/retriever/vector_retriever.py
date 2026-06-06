"""
向量检索模块
用「子问题 + 预回答」拼接作为 query，从 Chroma 向量库检索相关文献文段。
"""

import logging
from dataclasses import dataclass, asdict
from typing import Optional

import config
from ingest.embedder import embed_single, embed_texts, get_embedding_client
from ingest.vector_store import batch_query_collection, get_or_create_collection, query_collection
from server.services.stage_cache import (
    cache_retrieve_query,
    get_cached_retrieve_query,
    get_or_compute_retrieve_query,
    stage_cache_enabled,
)
from server.services.redis_client import get_redis_service

logger = logging.getLogger(__name__)


def _short_query(value: str, *, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _top_doi_summary(chunks: list["RetrievedChunk"], *, limit: int = 5) -> list[str]:
    seen: list[str] = []
    for chunk in chunks:
        doi = str(chunk.doi or "").strip()
        if not doi or doi in seen:
            continue
        seen.append(doi)
        if len(seen) >= limit:
            break
    return seen


@dataclass
class RetrievedChunk:
    """检索到的文段"""
    text: str
    doi: str
    title: str
    section_name: str
    chunk_index: int
    distance: float  # 余弦距离（越小越相似）

    def format_citation(self) -> str:
        """格式化引用信息"""
        return f"[{self.doi}, {self.section_name}]"

    def format_for_prompt(self) -> str:
        """格式化为 LLM prompt 中的文段引用"""
        return (
            f"--- Source: {self.doi} | Section: {self.section_name} ---\n"
            f"{self.text}\n"
            f"--- End Source ---"
        )


def _chunk_to_dict(chunk: RetrievedChunk) -> dict:
    return asdict(chunk)


def _dict_to_chunk(payload: dict) -> RetrievedChunk:
    return RetrievedChunk(
        text=str(payload.get("text") or ""),
        doi=str(payload.get("doi") or ""),
        title=str(payload.get("title") or ""),
        section_name=str(payload.get("section_name") or ""),
        chunk_index=int(payload.get("chunk_index") or 0),
        distance=float(payload.get("distance") or 0.0),
    )


def retrieve(
    query: str,
    top_k: int = None,
    collection=None,
    embedding_client=None,
) -> list[RetrievedChunk]:
    """
    检索与 query 最相关的文献文段。

    Args:
        query: 检索查询文本（子问题 + 预回答 拼接）
        top_k: 返回结果数量
        collection: Chroma collection
        embedding_client: OpenAI embedding 客户端

    Returns:
        检索到的文段列表，按相似度降序排列
    """
    if top_k is None:
        top_k = config.RETRIEVAL_TOP_K

    query_embedding = embed_single(query, client=embedding_client)
    results = query_collection(
        query_embedding=query_embedding,
        top_k=top_k,
        collection=collection,
    )

    chunks = []
    if results and results["ids"] and results["ids"][0]:
        for i, _doc_id in enumerate(results["ids"][0]):
            metadata = results["metadatas"][0][i] if results["metadatas"] else {}
            chunks.append(RetrievedChunk(
                text=results["documents"][0][i],
                doi=metadata.get("doi", ""),
                title=metadata.get("title", ""),
                section_name=metadata.get("section_name", ""),
                chunk_index=metadata.get("chunk_index", 0),
                distance=results["distances"][0][i] if results["distances"] else 0.0,
            ))

    logger.info("检索完成: query=%s query长度=%s top_k=%s 返回=%s top_dois=%s", _short_query(query), len(query), top_k, len(chunks), _top_doi_summary(chunks))
    return chunks


def _parse_retrieved_chunks(results: dict, *, index: int) -> list[RetrievedChunk]:
    chunks = []
    ids = results.get("ids") or []
    documents = results.get("documents") or []
    metadatas = results.get("metadatas") or []
    distances = results.get("distances") or []

    query_ids = ids[index] if index < len(ids) else []
    query_docs = documents[index] if index < len(documents) else []
    query_metas = metadatas[index] if index < len(metadatas) else []
    query_distances = distances[index] if index < len(distances) else []

    for i, _doc_id in enumerate(query_ids):
        metadata = query_metas[i] if i < len(query_metas) else {}
        chunks.append(RetrievedChunk(
            text=query_docs[i] if i < len(query_docs) else "",
            doi=metadata.get("doi", ""),
            title=metadata.get("title", ""),
            section_name=metadata.get("section_name", ""),
            chunk_index=metadata.get("chunk_index", 0),
            distance=query_distances[i] if i < len(query_distances) else 0.0,
        ))
    return chunks


def _batch_retrieve_without_stage_cache(
    *,
    normalized_queries: list[str],
    top_k: int,
    collection,
    embedding_client,
) -> list[list[RetrievedChunk]]:
    cached_or_pending: list[list[RetrievedChunk] | None] = [None] * len(normalized_queries)
    missed_queries: list[str] = []
    missed_indexes: list[int] = []

    for idx, query in enumerate(normalized_queries):
        payload = get_cached_retrieve_query(redis_service=get_redis_service(), query=query, top_k=top_k)
        if payload is None:
            missed_queries.append(query)
            missed_indexes.append(idx)
            continue
        cached_or_pending[idx] = [_dict_to_chunk(item) for item in payload]

    if missed_queries:
        query_embeddings = embed_texts(missed_queries, client=embedding_client)
        raw_results = batch_query_collection(
            query_embeddings=query_embeddings,
            top_k=top_k,
            collection=collection,
        )
        for batch_index, query in enumerate(missed_queries):
            parsed = _parse_retrieved_chunks(raw_results, index=batch_index)
            logger.info("批量检索完成: query=%s query长度=%s top_k=%s 返回=%s top_dois=%s", _short_query(query), len(query), top_k, len(parsed), _top_doi_summary(parsed))
            serialized = [_chunk_to_dict(chunk) for chunk in parsed]
            cache_retrieve_query(redis_service=get_redis_service(), query=query, top_k=top_k, results=serialized)
            cached_or_pending[missed_indexes[batch_index]] = [_dict_to_chunk(item) for item in serialized]

    return [item or [] for item in cached_or_pending]


def _batch_retrieve_with_stage_cache(
    *,
    normalized_queries: list[str],
    top_k: int,
    collection,
    embedding_client,
) -> list[list[RetrievedChunk]]:
    ordered_unique_queries = list(dict.fromkeys(normalized_queries))
    resolved_by_query: dict[str, list[RetrievedChunk]] = {}

    for query in ordered_unique_queries:
        serialized = get_or_compute_retrieve_query(
            query=query,
            top_k=top_k,
            compute_fn=lambda query=query: [
                _chunk_to_dict(chunk)
                for chunk in retrieve(
                    query,
                    top_k=top_k,
                    collection=collection,
                    embedding_client=embedding_client,
                )
            ],
        )
        parsed = [_dict_to_chunk(item) for item in serialized]
        logger.info("检索(stage-cache)完成: query=%s query长度=%s top_k=%s 返回=%s top_dois=%s", _short_query(query), len(query), top_k, len(parsed), _top_doi_summary(parsed))
        resolved_by_query[query] = parsed

    return [list(resolved_by_query.get(query) or []) for query in normalized_queries]


def batch_retrieve(
    queries: list[str],
    top_k: int = None,
    collection=None,
    embedding_client=None,
) -> list[list[RetrievedChunk]]:
    """
    批量检索多个 query。

    Args:
        queries: 查询列表
        top_k: 每个 query 返回的结果数量

    Returns:
        每个 query 的检索结果列表
    """
    if not queries:
        return []
    if top_k is None:
        top_k = config.RETRIEVAL_TOP_K
    if collection is None:
        collection = get_or_create_collection()
    if embedding_client is None:
        embedding_client = get_embedding_client()

    normalized_queries = [str(item or "") for item in queries]
    redis_service = get_redis_service()
    if redis_service is not None and redis_service.available and stage_cache_enabled():
        return _batch_retrieve_with_stage_cache(
            normalized_queries=normalized_queries,
            top_k=top_k,
            collection=collection,
            embedding_client=embedding_client,
        )

    return _batch_retrieve_without_stage_cache(
        normalized_queries=normalized_queries,
        top_k=top_k,
        collection=collection,
        embedding_client=embedding_client,
    )
