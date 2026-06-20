from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

from app.core.config import get_settings
from app.integrations.neo4j.client import run_graph_query
from app.modules.storage.service import storage_service


DEFAULT_PREVIEW_MAX_ITEMS = 30
MAX_PREVIEW_MAX_ITEMS = 100
DEFAULT_PREVIEW_MAX_WORKERS = 4
MAX_PREVIEW_MAX_WORKERS = 8


def clamp_preview_max_items(value: Any) -> int:
    try:
        n = int(value)
    except Exception:
        return DEFAULT_PREVIEW_MAX_ITEMS
    if n <= 0:
        return DEFAULT_PREVIEW_MAX_ITEMS
    if n > MAX_PREVIEW_MAX_ITEMS:
        return MAX_PREVIEW_MAX_ITEMS
    return n


def get_preview_max_workers() -> int:
    try:
        n = int(str(os.getenv("REFERENCE_PREVIEW_MAX_WORKERS", str(DEFAULT_PREVIEW_MAX_WORKERS))).strip())
    except Exception:
        return DEFAULT_PREVIEW_MAX_WORKERS
    if n <= 0:
        return 1
    if n > MAX_PREVIEW_MAX_WORKERS:
        return MAX_PREVIEW_MAX_WORKERS
    return n


def collect_doi_candidates(dois_text: str, doi_list: Sequence[str]) -> list[str]:
    candidates: list[str] = []
    if dois_text:
        candidates.extend([item.strip() for item in dois_text.split(",") if item.strip()])
    for item in doi_list:
        clean = str(item or "").strip()
        if clean:
            candidates.append(clean)
    return candidates


def normalize_dois(dois_text: str, doi_list: Sequence[str], max_items: int = 30) -> list[str]:
    candidates = collect_doi_candidates(dois_text, doi_list)

    unique: list[str] = []
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
    encoded_path = "/".join(quote(part, safe="") for part in doi.split("/"))
    return f"/api/v1/view_pdf/{encoded_path}"


def query_graph_reference_metadata(agent: Any, doi: str, logger: Any) -> dict[str, Any]:
    if not agent or not getattr(agent, "graph", None):
        return {}
    try:
        query = """
        MATCH (n)
        WHERE n.doi = $doi OR n.material_name = $doi OR n.material_name CONTAINS $doi
        WITH n,
          CASE
            WHEN n.doi = $doi THEN 0
            WHEN n.material_name = $doi THEN 1
            ELSE 2
          END AS match_rank
        RETURN
          n.title AS title,
          n.journal AS journal,
          coalesce(n.publication_date, n.date) AS publication_date,
          match_rank
        ORDER BY match_rank ASC
        LIMIT 1
        """
        data = run_graph_query(agent.graph, query, {"doi": doi})
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


def query_chroma_reference_metadata(agent: Any, doi: str, logger: Any) -> dict[str, Any]:
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
    metadata: dict[str, Any],
    papers_dir: Path,
    logger: Any | None = None,
) -> dict[str, Any]:
    pdf_exists = storage_service.paper_exists(
        doi=doi,
        papers_dir=papers_dir,
        project_root=str(get_settings().local_storage_root),
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


def build_reference_preview_entry(*, doi: str, agent: Any, papers_dir: Path, logger: Any) -> dict[str, Any]:
    metadata = query_graph_reference_metadata(agent, doi, logger)
    if not metadata:
        metadata = query_chroma_reference_metadata(agent, doi, logger)
    return build_reference_preview_item(
        doi=doi,
        metadata=metadata,
        papers_dir=papers_dir,
        logger=logger,
    )


def build_reference_preview_batch(*, dois: Sequence[str], agent: Any, papers_dir: Path, logger: Any) -> list[dict[str, Any]]:
    if not dois:
        return []

    max_workers = min(len(dois), get_preview_max_workers())
    if max_workers <= 1:
        return [
            build_reference_preview_entry(
                doi=doi,
                agent=agent,
                papers_dir=papers_dir,
                logger=logger,
            )
            for doi in dois
        ]

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="reference-preview") as executor:
        return list(
            executor.map(
                lambda doi: build_reference_preview_entry(
                    doi=doi,
                    agent=agent,
                    papers_dir=papers_dir,
                    logger=logger,
                ),
                dois,
            )
        )
