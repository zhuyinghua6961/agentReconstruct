#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage-2.5 MD evidence expansion (optional)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.modules.generation_pipeline.feature_flags import env_bool, env_int

try:
    import chromadb
except Exception:  # pragma: no cover - runtime fallback branch
    chromadb = None


def _resolve_md_runtime(
    *,
    enabled: Optional[bool],
    db_path: Optional[str],
    collection_name: Optional[str],
    max_dois: Optional[int],
    n_md_chunks_per_doi: Optional[int],
) -> Dict[str, Any]:
    resolved_enabled = env_bool("QA_STAGE25_MD_EXPANSION_ENABLED", True) if enabled is None else bool(enabled)
    resolved_db_path = str(db_path or os.getenv("VECTOR_DB_MD_PATH", "vector_database_md")).strip()
    resolved_collection = str(collection_name or os.getenv("VECTOR_DB_MD_COLLECTION", "md_papers")).strip() or "md_papers"
    resolved_max_dois = env_int("QA_STAGE25_MD_MAX_DOIS", 20, minimum=1, maximum=100) if max_dois is None else max(1, min(int(max_dois), 100))
    resolved_chunks_per_doi = (
        env_int("QA_STAGE25_MD_CHUNKS_PER_DOI", 5, minimum=1, maximum=20)
        if n_md_chunks_per_doi is None
        else max(1, min(int(n_md_chunks_per_doi), 20))
    )
    resolved_global_enabled = env_bool("QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED", True)
    resolved_global_topk = env_int("QA_STAGE25_MD_GLOBAL_TOPK", 20, minimum=1, maximum=100)
    resolved_global_max_new_dois = env_int("QA_STAGE25_MD_GLOBAL_MAX_NEW_DOIS", 5, minimum=0, maximum=50)
    raw_global_min_score = os.getenv("QA_STAGE25_MD_GLOBAL_MIN_SCORE", "0")
    try:
        resolved_global_min_score = float(str(raw_global_min_score).strip())
    except Exception:
        resolved_global_min_score = 0.0
    resolved_global_min_score = max(0.0, min(resolved_global_min_score, 1.0))
    return {
        "enabled": resolved_enabled,
        "db_path": resolved_db_path,
        "collection_name": resolved_collection,
        "max_dois": resolved_max_dois,
        "n_md_chunks_per_doi": resolved_chunks_per_doi,
        "global_enabled": resolved_global_enabled,
        "global_topk": resolved_global_topk,
        "global_max_new_dois": resolved_global_max_new_dois,
        "global_min_score": resolved_global_min_score,
    }


def _normalize_query_embedding(embedding_model: Any, query: str) -> Optional[List[float]]:
    if embedding_model is None or not query:
        return None
    if not hasattr(embedding_model, "encode"):
        return None
    encoded = embedding_model.encode([query])
    if hasattr(encoded, "tolist"):
        encoded = encoded.tolist()
    if not encoded:
        return None
    first = encoded[0]
    if hasattr(first, "tolist"):
        first = first.tolist()
    try:
        return [float(x) for x in first]
    except Exception:
        return None


def _safe_query_collection(
    *,
    collection: Any,
    query_embedding: List[float],
    n_results: int,
    where: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where
    return collection.query(**kwargs)


def _extract_rows(query_result: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any], float]]:
    docs = (query_result or {}).get("documents", [[]])
    metas = (query_result or {}).get("metadatas", [[]])
    dists = (query_result or {}).get("distances", [[]])
    rows: List[Tuple[str, Dict[str, Any], float]] = []
    doc_list = docs[0] if docs else []
    meta_list = metas[0] if metas else []
    dist_list = dists[0] if dists else []
    for idx, text in enumerate(doc_list):
        meta = meta_list[idx] if idx < len(meta_list) and isinstance(meta_list[idx], dict) else {}
        dist = float(dist_list[idx]) if idx < len(dist_list) else 0.0
        rows.append((str(text or ""), meta, dist))
    return rows


def _row_doi(meta: Dict[str, Any]) -> str:
    doi = str(meta.get("doi") or meta.get("DOI") or meta.get("source_doi") or "").strip()
    if doi:
        return doi
    doc_name = str(meta.get("document_name") or "").strip()
    if doc_name.endswith(".md"):
        return doc_name[:-3].replace("_", "/", 1)
    return ""


def _convert_rows_to_chunks(
    *,
    rows: List[Tuple[str, Dict[str, Any], float]],
    target_doi: str,
    limit: int,
) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    seen = set()
    for idx, (text, meta, dist) in enumerate(rows):
        if not text:
            continue
        row_doi = _row_doi(meta)
        if row_doi and row_doi != target_doi:
            continue
        key = text[:200]
        if key in seen:
            continue
        seen.add(key)
        chunks.append(
            {
                "doi": target_doi,
                "text": text,
                "page": int(meta.get("page", 0) or 0),
                "chunk_id": str(meta.get("chunk_id", meta.get("id", idx))),
                "distance": float(dist),
                "source": "md_expansion",
            }
        )
        if len(chunks) >= limit:
            break
    return chunks


def _search_md_chunks_for_doi(
    *,
    collection: Any,
    query_embedding: List[float],
    doi: str,
    n_results: int,
) -> List[Dict[str, Any]]:
    where_candidates = [
        {"doi": doi},
        {"DOI": doi},
        {"source_doi": doi},
        {"document_name": doi.replace("/", "_", 1) + ".md"},
    ]
    for where in where_candidates:
        try:
            rows = _extract_rows(
                _safe_query_collection(
                    collection=collection,
                    query_embedding=query_embedding,
                    n_results=n_results,
                    where=where,
                )
            )
            chunks = _convert_rows_to_chunks(rows=rows, target_doi=doi, limit=n_results)
            if chunks:
                return chunks
        except Exception:
            continue

    try:
        rows = _extract_rows(
            _safe_query_collection(
                collection=collection,
                query_embedding=query_embedding,
                n_results=max(n_results * 5, 30),
                where=None,
            )
        )
    except Exception:
        return []
    return _convert_rows_to_chunks(rows=rows, target_doi=doi, limit=n_results)


def _search_md_global_supplement(
    *,
    collection: Any,
    query_embedding: List[float],
    existing_dois: set[str],
    n_results: int,
    max_new_dois: int,
    min_score: float,
) -> Tuple[Dict[str, List[Dict[str, Any]]], int]:
    if max_new_dois <= 0 or n_results <= 0:
        return {}, 0
    try:
        rows = _extract_rows(
            _safe_query_collection(
                collection=collection,
                query_embedding=query_embedding,
                n_results=n_results,
                where=None,
            )
        )
    except Exception:
        return {}, 0
    if not rows:
        return {}, 0

    by_doi: Dict[str, List[Dict[str, Any]]] = {}
    seen_text_per_doi: Dict[str, set[str]] = {}
    candidate_count = 0
    for idx, (text, meta, dist) in enumerate(rows):
        doi = _row_doi(meta)
        if not doi:
            continue
        candidate_count += 1
        if doi in existing_dois or doi in by_doi or not text:
            continue
        quality_score = max(0.0, 1.0 - float(dist))
        if quality_score < min_score:
            continue
        text_key = text[:200]
        doi_seen = seen_text_per_doi.setdefault(doi, set())
        if text_key in doi_seen:
            continue
        doi_seen.add(text_key)
        by_doi[doi] = [
            {
                "doi": doi,
                "text": text,
                "page": int(meta.get("page", 0) or 0),
                "chunk_id": str(meta.get("chunk_id", meta.get("id", idx))),
                "distance": float(dist),
                "score": float(quality_score),
                "source": "md_expansion_global",
            }
        ]
        if len(by_doi) >= max_new_dois:
            break
    return by_doi, candidate_count


def evaluate_stage3_pdf_skip(
    *,
    md_expansion_result: Dict[str, Any],
    enabled: Optional[bool] = None,
    min_hit_dois: Optional[int] = None,
    min_chunks: Optional[int] = None,
) -> Dict[str, Any]:
    resolved_enabled = env_bool("QA_STAGE3_SKIP_PDF_WHEN_MD_HIT", False) if enabled is None else bool(enabled)
    resolved_min_hit_dois = (
        env_int("QA_STAGE3_SKIP_PDF_MIN_MD_HIT_DOIS", 1, minimum=1, maximum=50)
        if min_hit_dois is None
        else max(1, min(int(min_hit_dois), 50))
    )
    resolved_min_chunks = (
        env_int("QA_STAGE3_SKIP_PDF_MIN_MD_CHUNKS", 3, minimum=1, maximum=200)
        if min_chunks is None
        else max(1, min(int(min_chunks), 200))
    )

    stats = md_expansion_result.get("stats", {}) if isinstance(md_expansion_result, dict) else {}
    hit_doi_count = int(stats.get("hit_doi_count", 0) or 0)
    total_md_chunks = int(stats.get("total_md_chunks", 0) or 0)
    applied = bool(md_expansion_result.get("applied")) if isinstance(md_expansion_result, dict) else False
    has_chunks = bool(md_expansion_result.get("md_chunks_by_doi")) if isinstance(md_expansion_result, dict) else False

    decision = {
        "enabled": resolved_enabled,
        "should_skip": False,
        "reason": "",
        "hit_doi_count": hit_doi_count,
        "total_md_chunks": total_md_chunks,
        "min_hit_dois": resolved_min_hit_dois,
        "min_chunks": resolved_min_chunks,
    }
    if not resolved_enabled:
        decision["reason"] = "switch_off"
        return decision
    if not applied or not has_chunks:
        decision["reason"] = "md_not_applied"
        return decision
    if hit_doi_count < resolved_min_hit_dois:
        decision["reason"] = "hit_doi_below_threshold"
        return decision
    if total_md_chunks < resolved_min_chunks:
        decision["reason"] = "chunk_below_threshold"
        return decision
    decision["should_skip"] = True
    decision["reason"] = "threshold_matched"
    return decision


def merge_pdf_chunks_with_md(
    *,
    pdf_chunks: Dict[str, List[Dict[str, Any]]],
    md_chunks: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    merged: Dict[str, List[Dict[str, Any]]] = {}
    all_dois = set(pdf_chunks.keys()) | set(md_chunks.keys())
    for doi in all_dois:
        out: List[Dict[str, Any]] = []
        seen = set()
        for source_list in [md_chunks.get(doi, []), pdf_chunks.get(doi, [])]:
            for chunk in source_list:
                text_key = str((chunk or {}).get("text", ""))[:200]
                if not text_key or text_key in seen:
                    continue
                seen.add(text_key)
                out.append(chunk)
        if out:
            merged[doi] = out
    return merged


def run_stage25_md_expansion(
    *,
    retrieval_results: Dict[str, Any],
    user_question: str,
    dois: List[str],
    literature_expert: Any,
    logger: Any,
    enabled: Optional[bool] = None,
    db_path: Optional[str] = None,
    collection_name: Optional[str] = None,
    max_dois: Optional[int] = None,
    n_md_chunks_per_doi: Optional[int] = None,
    collection_override: Any = None,
) -> Dict[str, Any]:
    cfg = _resolve_md_runtime(
        enabled=enabled,
        db_path=db_path,
        collection_name=collection_name,
        max_dois=max_dois,
        n_md_chunks_per_doi=n_md_chunks_per_doi,
    )

    base_payload: Dict[str, Any] = {
        "enabled": bool(cfg["enabled"]),
        "applied": False,
        "md_chunks_by_doi": {},
        "stats": {
            "candidate_doi_count": len(dois or []),
            "processed_doi_count": 0,
            "hit_doi_count": 0,
            "total_md_chunks": 0,
            "fallback_reason": "",
            "global_candidate_count": 0,
            "global_added_doi_count": 0,
            "global_added_chunk_count": 0,
            "global_fallback_reason": "",
        },
    }
    if not cfg["enabled"]:
        base_payload["stats"]["fallback_reason"] = "disabled"
        return base_payload
    if not dois:
        base_payload["stats"]["fallback_reason"] = "empty_doi_list"
        return base_payload

    if collection_override is not None:
        collection = collection_override
    else:
        if chromadb is None:
            base_payload["stats"]["fallback_reason"] = "chromadb_unavailable"
            return base_payload
        project_root = Path(__file__).resolve().parents[4]
        resolved_db_path = cfg["db_path"]
        if not os.path.isabs(resolved_db_path):
            resolved_db_path = os.path.normpath(str(project_root / resolved_db_path))
        try:
            client = chromadb.PersistentClient(path=resolved_db_path)
            collection = client.get_collection(cfg["collection_name"])
        except Exception as exc:
            base_payload["stats"]["fallback_reason"] = f"collection_unavailable:{exc}"
            return base_payload

    emb_model = getattr(literature_expert, "embedding_model", None)
    query_embedding = _normalize_query_embedding(emb_model, user_question)
    if not query_embedding:
        base_payload["stats"]["fallback_reason"] = "embedding_unavailable"
        return base_payload

    md_chunks_by_doi: Dict[str, List[Dict[str, Any]]] = {}
    target_dois = list(dict.fromkeys([str(d or "").strip() for d in dois if str(d or "").strip()]))[: cfg["max_dois"]]
    base_payload["stats"]["processed_doi_count"] = len(target_dois)
    for doi in target_dois:
        chunks = _search_md_chunks_for_doi(
            collection=collection,
            query_embedding=query_embedding,
            doi=doi,
            n_results=cfg["n_md_chunks_per_doi"],
        )
        if chunks:
            md_chunks_by_doi[doi] = chunks

    global_candidate_count = 0
    global_added_doi_count = 0
    global_added_chunk_count = 0
    global_fallback_reason = ""
    if cfg["global_enabled"]:
        global_by_doi, global_candidate_count = _search_md_global_supplement(
            collection=collection,
            query_embedding=query_embedding,
            existing_dois=set(md_chunks_by_doi.keys()),
            n_results=cfg["global_topk"],
            max_new_dois=cfg["global_max_new_dois"],
            min_score=cfg["global_min_score"],
        )
        if global_by_doi:
            for doi, chunks in global_by_doi.items():
                if doi not in md_chunks_by_doi and chunks:
                    md_chunks_by_doi[doi] = chunks
            global_added_doi_count = len(global_by_doi)
            global_added_chunk_count = sum(len(v) for v in global_by_doi.values())
        elif cfg["global_max_new_dois"] <= 0:
            global_fallback_reason = "global_limit_zero"
        elif global_candidate_count == 0:
            global_fallback_reason = "global_no_candidate"
        else:
            global_fallback_reason = "global_no_new_doi"
    else:
        global_fallback_reason = "global_disabled"

    total_md_chunks = sum(len(v) for v in md_chunks_by_doi.values())
    hit_doi_count = len(md_chunks_by_doi)
    base_payload["md_chunks_by_doi"] = md_chunks_by_doi
    base_payload["stats"]["hit_doi_count"] = hit_doi_count
    base_payload["stats"]["total_md_chunks"] = total_md_chunks
    base_payload["stats"]["global_candidate_count"] = int(global_candidate_count)
    base_payload["stats"]["global_added_doi_count"] = int(global_added_doi_count)
    base_payload["stats"]["global_added_chunk_count"] = int(global_added_chunk_count)
    base_payload["stats"]["global_fallback_reason"] = str(global_fallback_reason)
    if hit_doi_count == 0:
        base_payload["stats"]["fallback_reason"] = "no_md_match"
        return base_payload

    base_payload["applied"] = True
    base_payload["stats"]["fallback_reason"] = ""
    try:
        logger.info(
            "stage25 md expansion hit_doi=%s/%s total_md_chunks=%s global_added_doi=%s global_added_chunks=%s",
            hit_doi_count,
            len(target_dois),
            total_md_chunks,
            global_added_doi_count,
            global_added_chunk_count,
        )
    except Exception:
        pass
    return base_payload


__all__ = ["evaluate_stage3_pdf_skip", "merge_pdf_chunks_with_md", "run_stage25_md_expansion"]
