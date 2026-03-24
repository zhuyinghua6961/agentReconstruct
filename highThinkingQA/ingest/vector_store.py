"""
向量数据库模块
使用 Chroma 进行向量存储和检索。
"""

import logging
import os
from functools import lru_cache
from typing import Optional

import chromadb

import config
from ingest.chunker import Chunk

logger = logging.getLogger(__name__)


def _log_collection_ready(*, path: str, name: str, collection: chromadb.Collection) -> None:
    sqlite_path = os.path.join(path, "chroma.sqlite3") if path else ""
    try:
        count = collection.count()
    except Exception as exc:  # pragma: no cover - diagnostic fallback
        count = f"error:{type(exc).__name__}"
        logger.warning(
            "vector_store collection count failed persist_dir=%s collection=%s error=%s",
            path,
            name,
            exc,
        )
    logger.info(
        "vector_store collection ready persist_dir=%s path_exists=%s sqlite_path=%s sqlite_exists=%s collection=%s count=%s",
        path,
        os.path.isdir(path),
        sqlite_path,
        os.path.exists(sqlite_path) if sqlite_path else False,
        name,
        count,
    )


def get_chroma_client() -> chromadb.PersistentClient:
    """获取 Chroma 持久化客户端"""
    return _get_chroma_client_cached(config.CHROMA_PERSIST_DIR)


@lru_cache(maxsize=4)
def _get_chroma_client_cached(path: str) -> chromadb.PersistentClient:
    os.makedirs(path, exist_ok=True)
    return chromadb.PersistentClient(path=path)


def get_or_create_collection(
    client: Optional[chromadb.PersistentClient] = None,
) -> chromadb.Collection:
    """获取或创建 Chroma Collection"""
    if client is None:
        return _get_default_collection_cached(config.CHROMA_PERSIST_DIR, config.CHROMA_COLLECTION_NAME)
    collection = client.get_or_create_collection(
        name=config.CHROMA_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},  # 使用余弦相似度
    )
    _log_collection_ready(
        path=config.CHROMA_PERSIST_DIR,
        name=config.CHROMA_COLLECTION_NAME,
        collection=collection,
    )
    return collection


@lru_cache(maxsize=8)
def _get_default_collection_cached(path: str, name: str) -> chromadb.Collection:
    client = _get_chroma_client_cached(path)
    collection = client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )
    _log_collection_ready(path=path, name=name, collection=collection)
    return collection


def add_chunks(
    chunks: list[Chunk],
    embeddings: list[list[float]],
    collection: Optional[chromadb.Collection] = None,
) -> None:
    """
    将 chunks 及其 embeddings 写入 Chroma。

    Args:
        chunks: 文本块列表
        embeddings: 对应的嵌入向量列表
        collection: Chroma collection（可选）
    """
    if collection is None:
        collection = get_or_create_collection()

    if len(chunks) != len(embeddings):
        raise ValueError(
            f"chunks ({len(chunks)}) 和 embeddings ({len(embeddings)}) 数量不匹配"
        )

    # 构建 Chroma 需要的数据
    ids = []
    documents = []
    metadatas = []

    for i, chunk in enumerate(chunks):
        # 用 DOI + chunk_index 作为唯一 ID
        chunk_id = f"{chunk.doi}__chunk_{chunk.chunk_index}"
        # 替换 Chroma 不允许的字符
        chunk_id = chunk_id.replace("/", "_").replace(" ", "_")

        ids.append(chunk_id)
        documents.append(chunk.text)
        metadatas.append(chunk.to_metadata())

    # 分批写入（Chroma 单次最多约 5000 条）
    batch_size = 500
    for i in range(0, len(ids), batch_size):
        end = min(i + batch_size, len(ids))
        collection.upsert(
            ids=ids[i:end],
            embeddings=embeddings[i:end],
            documents=documents[i:end],
            metadatas=metadatas[i:end],
        )
        logger.info(f"已写入 Chroma: {end}/{len(ids)} chunks")


def query_collection(
    query_embedding: list[float],
    top_k: int = None,
    collection: Optional[chromadb.Collection] = None,
) -> dict:
    """
    向量检索。

    Args:
        query_embedding: 查询向量
        top_k: 返回结果数量
        collection: Chroma collection

    Returns:
        Chroma 查询结果 dict，包含 ids, documents, metadatas, distances
    """
    if top_k is None:
        top_k = config.RETRIEVAL_TOP_K
    if collection is None:
        collection = get_or_create_collection()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    hit_count = len(results.get("ids", [[]])[0]) if results.get("ids") else 0
    logger.info("vector_store query done top_k=%s hit_count=%s", top_k, hit_count)

    return results


def batch_query_collection(
    query_embeddings: list[list[float]],
    top_k: int = None,
    collection: Optional[chromadb.Collection] = None,
) -> dict:
    """
    批量向量检索。

    Args:
        query_embeddings: 查询向量列表
        top_k: 每个 query 返回结果数量
        collection: Chroma collection

    Returns:
        Chroma 批量查询结果 dict，字段结构与单 query 保持一致，但最外层为 query 列表。
    """
    if top_k is None:
        top_k = config.RETRIEVAL_TOP_K
    if collection is None:
        collection = get_or_create_collection()

    if not query_embeddings:
        return {"ids": [], "documents": [], "metadatas": [], "distances": []}

    results = collection.query(
        query_embeddings=query_embeddings,
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    hit_count = sum(len(items or []) for items in (results.get("ids") or []))
    logger.info(
        "vector_store batch_query done query_count=%s top_k=%s hit_count=%s",
        len(query_embeddings),
        top_k,
        hit_count,
    )

    return results


def get_collection_count(
    collection: Optional[chromadb.Collection] = None,
) -> int:
    """获取 collection 中的文档数量"""
    if collection is None:
        collection = get_or_create_collection()
    return collection.count()


def get_indexed_dois(
    collection: Optional[chromadb.Collection] = None,
) -> set[str]:
    """
    获取已入库的所有 DOI 集合。
    通过查询所有 metadata 中的 doi 字段实现。

    Returns:
        已入库的 DOI 集合
    """
    if collection is None:
        collection = get_or_create_collection()

    count = collection.count()
    if count == 0:
        return set()

    # 分批获取所有 metadata（Chroma get 有数量限制）
    dois = set()
    batch_size = 5000
    offset = 0

    while offset < count:
        results = collection.get(
            limit=batch_size,
            offset=offset,
            include=["metadatas"],
        )
        for meta in results["metadatas"]:
            if meta and "doi" in meta:
                dois.add(meta["doi"])
        offset += batch_size

    return dois
