from __future__ import annotations

from pathlib import Path
from typing import Any

from app.integrations.neo4j.client import run_graph_query
from app.modules.literature_search.doi_utils import metadata_to_doi
from app.modules.literature_search.chroma_metadata import scan_titles_from_chroma
from app.modules.literature_search.embedding_client import embed_fastqa_query, embed_highthinking_query
from app.modules.literature_search.rerank_service import rerank_candidate_limit


def _distance_to_score(distance: Any) -> float:
    try:
        value = float(distance)
    except (TypeError, ValueError):
        return 0.0
    if value < 0:
        return 1.0
    if value > 1:
        return max(0.0, 1.0 / (1.0 + value))
    return max(0.0, 1.0 - value)


def _metadata_doi(metadata: dict[str, Any]) -> str:
    return metadata_to_doi(metadata)


def _metadata_title(metadata: dict[str, Any]) -> str:
    for key in ("title", "paper_title"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _semantic_hits_from_collection(
    *,
    collection: Any,
    query_embedding: list[float],
    match_source: str,
    limit: int,
) -> list[dict[str, Any]]:
    if collection is None or not query_embedding:
        return []
    try:
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            include=["metadatas", "distances"],
        )
    except Exception:
        return []

    metadatas = (result.get("metadatas") or [[]])[0] if isinstance(result, dict) else []
    distances = (result.get("distances") or [[]])[0] if isinstance(result, dict) else []
    merged: dict[str, dict[str, Any]] = {}
    for metadata, distance in zip(metadatas, distances):
        if not isinstance(metadata, dict):
            continue
        doi = _metadata_doi(metadata)
        if not doi:
            continue
        score = _distance_to_score(distance)
        existing = merged.get(doi)
        if existing is None or score > float(existing.get("match_score") or 0.0):
            merged[doi] = {
                "doi": doi,
                "title": _metadata_title(metadata),
                "match_source": match_source,
                "match_score": score,
            }
    return sorted(merged.values(), key=lambda item: float(item.get("match_score") or 0.0), reverse=True)


def _neo4j_title_hits(*, graph: Any, needle: str, mode: str, limit: int, logger: Any) -> list[dict[str, Any]]:
    if graph is None:
        return []
    needle = str(needle or "").strip()
    if not needle:
        return []

    if mode == "exact":
        query = """
        MATCH (n)
        WHERE n.title IS NOT NULL AND toLower(n.title) = toLower($needle)
        RETURN DISTINCT n.doi AS doi, n.title AS title
        LIMIT $limit
        """
    else:
        query = """
        MATCH (n)
        WHERE n.title IS NOT NULL AND toLower(n.title) CONTAINS toLower($needle)
        RETURN DISTINCT n.doi AS doi, n.title AS title
        LIMIT $limit
        """
    try:
        rows = run_graph_query(graph, query, {"needle": needle, "limit": limit})
    except Exception as exc:
        if logger is not None:
            logger.warning("literature_search neo4j title query failed: %s", exc)
        return []

    hits: list[dict[str, Any]] = []
    for row in rows:
        doi = str(row.get("doi") or "").strip()
        if not doi:
            continue
        hits.append(
            {
                "doi": doi,
                "title": str(row.get("title") or ""),
                "match_source": "neo4j",
                "match_score": 1.0 if mode == "exact" else 0.85,
            }
        )
    return hits


def search_by_title(
    *,
    query: str,
    match_mode: str,
    limit: int,
    fastqa_collection: Any,
    fastqa_md_collection: Any,
    highthinking_collection: Any,
    fastqa_db_path: str | Path,
    fastqa_collection_name: str,
    fastqa_md_db_path: str | Path,
    fastqa_md_collection_name: str,
    highthinking_db_path: str | Path,
    highthinking_collection_name: str,
    sources: set[str],
    graph: Any,
    logger: Any,
) -> tuple[list[dict[str, Any]], str | None]:
    mode = str(match_mode or "semantic").strip().lower()
    if mode not in {"semantic", "fuzzy", "exact"}:
        mode = "semantic"

    candidate_limit = rerank_candidate_limit(limit)

    merged: dict[str, dict[str, Any]] = {}

    def _merge(hit: dict[str, Any]) -> None:
        doi = str(hit.get("doi") or "").strip()
        if not doi:
            return
        score = float(hit.get("match_score") or 0.0)
        existing = merged.get(doi)
        if existing is None or score > float(existing.get("match_score") or 0.0):
            merged[doi] = dict(hit)

    error_code: str | None = None

    if mode == "semantic":
        if "fastqa" in sources:
            try:
                embedding = embed_fastqa_query(query)
                for hit in _semantic_hits_from_collection(
                    collection=fastqa_collection,
                    query_embedding=embedding,
                    match_source="fastqa_chroma",
                    limit=candidate_limit,
                ):
                    _merge(hit)
            except Exception as exc:
                if logger is not None:
                    logger.warning("literature_search fastqa semantic failed: %s", exc)
                error_code = "EMBEDDING_UNAVAILABLE"
        if "fastqa_md" in sources:
            try:
                embedding = embed_fastqa_query(query)
                for hit in _semantic_hits_from_collection(
                    collection=fastqa_md_collection,
                    query_embedding=embedding,
                    match_source="fastqa_md_chroma",
                    limit=candidate_limit,
                ):
                    _merge(hit)
            except Exception as exc:
                if logger is not None:
                    logger.warning("literature_search fastqa md semantic failed: %s", exc)
                error_code = "EMBEDDING_UNAVAILABLE"
        if "highthinking" in sources:
            try:
                embedding = embed_highthinking_query(query)
                for hit in _semantic_hits_from_collection(
                    collection=highthinking_collection,
                    query_embedding=embedding,
                    match_source="highthinking_chroma",
                    limit=candidate_limit,
                ):
                    _merge(hit)
            except Exception as exc:
                if logger is not None:
                    logger.warning("literature_search highthinking semantic failed: %s", exc)
                error_code = "EMBEDDING_UNAVAILABLE"
    else:
        text_mode = "exact" if mode == "exact" else "fuzzy"
        if "fastqa" in sources:
            for hit in scan_titles_from_chroma(
                db_path=fastqa_db_path,
                collection_name=fastqa_collection_name,
                needle=query,
                mode=text_mode,
                limit=candidate_limit,
            ):
                hit["match_source"] = "fastqa_chroma"
                _merge(hit)
        if "fastqa_md" in sources:
            for hit in scan_titles_from_chroma(
                db_path=fastqa_md_db_path,
                collection_name=fastqa_md_collection_name,
                needle=query,
                mode=text_mode,
                limit=candidate_limit,
            ):
                hit["match_source"] = "fastqa_md_chroma"
                _merge(hit)
        if "highthinking" in sources:
            for hit in scan_titles_from_chroma(
                db_path=highthinking_db_path,
                collection_name=highthinking_collection_name,
                needle=query,
                mode=text_mode,
                limit=candidate_limit,
            ):
                hit["match_source"] = "highthinking_chroma"
                _merge(hit)
        for hit in _neo4j_title_hits(graph=graph, needle=query, mode=text_mode, limit=candidate_limit, logger=logger):
            _merge(hit)

    hits = sorted(merged.values(), key=lambda item: float(item.get("match_score") or 0.0), reverse=True)
    if mode == "semantic" and not hits and error_code:
        return [], error_code
    return hits, error_code
