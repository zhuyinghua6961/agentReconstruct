from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.integrations.neo4j.client import run_graph_query
from app.integrations.storage.factory import get_storage_backend
from app.integrations.storage.minio import MinIOStorageBackend
from app.modules.literature_search.doi_utils import doi_to_document_name
from app.modules.storage.service import storage_service


def _chroma_exact_hit(*, collection: Any, doi: str, match_source: str) -> dict[str, Any] | None:
    if collection is None:
        return None
    try:
        result = collection.get(where={"doi": doi}, include=["metadatas"])
    except Exception:
        return None
    ids = result.get("ids") if isinstance(result, dict) else None
    metadatas = result.get("metadatas") if isinstance(result, dict) else None
    if not ids or not metadatas:
        return None
    metadata = metadatas[0] or {}
    return {
        "doi": doi,
        "title": str(metadata.get("title") or metadata.get("paper_title") or ""),
        "match_source": match_source,
        "match_score": 1.0,
    }


def _md_chroma_exact_hit(*, collection: Any, doi: str, match_source: str) -> dict[str, Any] | None:
    hit = _chroma_exact_hit(collection=collection, doi=doi, match_source=match_source)
    if hit is not None:
        return hit
    if collection is None:
        return None
    document_name = doi_to_document_name(doi)
    if not document_name:
        return None
    try:
        result = collection.get(where={"document_name": document_name}, include=["metadatas"], limit=1)
    except Exception:
        return None
    ids = result.get("ids") if isinstance(result, dict) else None
    if not ids:
        return None
    return {
        "doi": doi,
        "title": "",
        "match_source": match_source,
        "match_score": 1.0,
    }


def _neo4j_doi_hit(*, graph: Any, doi: str, limit: int, logger: Any) -> dict[str, Any] | None:
    if graph is None:
        return None
    query = """
    MATCH (n)
    WHERE n.doi = $needle
    RETURN DISTINCT coalesce(n.doi, $needle) AS doi, n.title AS title
    LIMIT $limit
    """
    try:
        rows = run_graph_query(graph, query, {"needle": doi, "limit": limit})
    except Exception as exc:
        if logger is not None:
            logger.warning("literature_search neo4j doi query failed: %s", exc)
        return None
    if not rows:
        return None
    row = rows[0]
    found_doi = str(row.get("doi") or doi).strip()
    if not found_doi:
        return None
    return {
        "doi": found_doi,
        "title": str(row.get("title") or ""),
        "match_source": "neo4j",
        "match_score": 1.0,
    }


def _minio_doi_hit(*, doi: str, logger: Any) -> dict[str, Any] | None:
    backend = get_storage_backend(project_root=str(get_settings().local_storage_root))
    if not isinstance(backend, MinIOStorageBackend):
        return None
    object_name = storage_service.resolve_minio_paper_object_name(
        backend=backend,
        normalized_doi=doi,
        logger=logger,
    )
    if not object_name:
        return None
    return {
        "doi": doi,
        "title": "",
        "match_source": "minio",
        "match_score": 1.0,
    }


def search_by_doi(
    *,
    query: str,
    limit: int,
    fastqa_collection: Any,
    fastqa_md_collection: Any,
    highthinking_collection: Any,
    sources: set[str],
    graph: Any,
    logger: Any,
) -> list[dict[str, Any]]:
    normalized = storage_service.normalize_doi(query)
    needle = normalized or str(query or "").strip()
    if not needle:
        return []

    merged: dict[str, dict[str, Any]] = {}

    def _merge(hit: dict[str, Any] | None) -> None:
        if hit is None:
            return
        doi = str(hit.get("doi") or "").strip()
        if not doi:
            return
        existing = merged.get(doi)
        score = float(hit.get("match_score") or 0.0)
        if existing is None or score > float(existing.get("match_score") or 0.0):
            merged[doi] = dict(hit)

    if "fastqa" in sources:
        _merge(_chroma_exact_hit(collection=fastqa_collection, doi=needle, match_source="fastqa_chroma"))
    if "fastqa_md" in sources:
        _merge(_md_chroma_exact_hit(collection=fastqa_md_collection, doi=needle, match_source="fastqa_md_chroma"))
    if "highthinking" in sources:
        _merge(_chroma_exact_hit(collection=highthinking_collection, doi=needle, match_source="highthinking_chroma"))
    _merge(_neo4j_doi_hit(graph=graph, doi=needle, limit=limit, logger=logger))
    _merge(_minio_doi_hit(doi=needle, logger=logger))

    return list(merged.values())[:limit]
