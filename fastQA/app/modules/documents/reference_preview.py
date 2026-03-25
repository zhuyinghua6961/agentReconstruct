#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Service helpers for batched DOI reference preview."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence
from app.modules.storage.service import storage_service

DEFAULT_PREVIEW_MAX_ITEMS = 30
MAX_PREVIEW_MAX_ITEMS = 100


def clamp_preview_max_items(value: Any) -> int:
    """Clamp client preview batch size into a safe bounded range."""
    try:
        n = int(value)
    except Exception:
        return DEFAULT_PREVIEW_MAX_ITEMS
    if n <= 0:
        return DEFAULT_PREVIEW_MAX_ITEMS
    if n > MAX_PREVIEW_MAX_ITEMS:
        return MAX_PREVIEW_MAX_ITEMS
    return n


def collect_doi_candidates(dois_text: str, doi_list: Sequence[str]) -> List[str]:
    """Collect raw DOI candidates from query string and repeated params."""
    candidates: List[str] = []
    if dois_text:
        candidates.extend([item.strip() for item in dois_text.split(",") if item.strip()])
    for item in doi_list:
        clean = str(item or "").strip()
        if clean:
            candidates.append(clean)
    return candidates


def normalize_dois(dois_text: str, doi_list: Sequence[str], max_items: int = 30) -> List[str]:
    """Normalize DOI candidates from query string and repeated params."""
    candidates = collect_doi_candidates(dois_text, doi_list)

    unique: List[str] = []
    seen = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
        if len(unique) >= max_items:
            break
    return unique


def build_pdf_filename(doi: str) -> str:
    return storage_service.build_paper_filename(doi)


def build_pdf_url(doi: str) -> str:
    return storage_service.build_pdf_url(doi)


def query_graph_reference_metadata(agent: Any, doi: str, logger: Any) -> Dict[str, Any]:
    """Query graph store for minimal reference metadata."""
    if not agent or not getattr(agent, "graph", None):
        return {}
    try:
        query = """
        MATCH (n)
        WHERE n.material_name CONTAINS $doi OR n.doi = $doi
        RETURN
          n.title AS title,
          n.journal AS journal,
          coalesce(n.publication_date, n.date) AS publication_date
        LIMIT 1
        """
        data = agent.graph.run(query, doi=doi).data()
        if not data:
            return {}
        row = data[0]
        return {
            "title": row.get("title") or "",
            "journal": row.get("journal") or "",
            "publication_date": row.get("publication_date") or "",
            "source": "neo4j",
        }
    except Exception as exc:
        logger.warning(f"reference_preview graph query failed for DOI={doi}: {exc}")
        return {}


def query_chroma_reference_metadata(agent: Any, doi: str, logger: Any) -> Dict[str, Any]:
    """Query vector store for minimal reference metadata."""
    semantic_expert = getattr(agent, "semantic_expert", None) if agent else None
    collection = getattr(semantic_expert, "collection", None)
    if not collection:
        return {}

    try:
        result = collection.get(where={"doi": doi})
        ids = result.get("ids") if isinstance(result, dict) else None
        metadatas = result.get("metadatas") if isinstance(result, dict) else None
        if not ids or not metadatas:
            return {}
        metadata = metadatas[0] or {}
        return {
            "title": metadata.get("title", ""),
            "journal": metadata.get("journal", ""),
            "publication_date": metadata.get("date", ""),
            "source": "chromadb",
        }
    except Exception as exc:
        logger.warning(f"reference_preview chroma query failed for DOI={doi}: {exc}")
        return {}


def build_reference_preview_item(
    doi: str,
    metadata: Dict[str, Any],
    papers_dir: Path,
    logger: Any | None = None,
) -> Dict[str, Any]:
    """Build one reference preview payload."""
    filename = build_pdf_filename(doi)
    pdf_exists = storage_service.paper_exists(
        doi=doi,
        papers_dir=papers_dir,
        project_root=str(Path(__file__).resolve().parents[4]),
        logger=logger,
    )
    return {
        "doi": doi,
        "title": metadata.get("title", ""),
        "journal": metadata.get("journal", ""),
        "publication_date": metadata.get("publication_date", ""),
        "source": metadata.get("source", "unknown"),
        "pdf_exists": bool(pdf_exists),
        "pdf_url": build_pdf_url(doi),
    }


def build_reference_preview_batch(*, dois: Sequence[str], agent: Any, papers_dir: Path, logger: Any) -> List[Dict[str, Any]]:
    """Build a stable ordered preview list for DOI batch."""
    items: List[Dict[str, Any]] = []
    for doi in dois:
        metadata = query_graph_reference_metadata(agent, doi, logger)
        if not metadata:
            metadata = query_chroma_reference_metadata(agent, doi, logger)
        items.append(
            build_reference_preview_item(
                doi=doi,
                metadata=metadata,
                papers_dir=papers_dir,
                logger=logger,
            )
        )
    return items
