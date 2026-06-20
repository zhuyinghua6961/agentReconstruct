from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def _resolve_sqlite_path(db_path: str | Path) -> Path:
    path = Path(db_path)
    if path.suffix == ".sqlite3":
        return path
    return path / "chroma.sqlite3"


def _collection_id(conn: sqlite3.Connection, collection_name: str) -> str | None:
    cur = conn.cursor()
    cur.execute("SELECT id FROM collections WHERE name = ? LIMIT 1", (collection_name,))
    row = cur.fetchone()
    return str(row[0]) if row else None


def _load_metadata_rows(
    *,
    sqlite_path: Path,
    collection_name: str,
    metadata_keys: tuple[str, ...],
) -> list[dict[str, str]]:
    if not sqlite_path.exists():
        return []

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(sqlite_path), timeout=5)
        collection_id = _collection_id(conn, collection_name)
        if not collection_id:
            return []

        placeholders = ",".join("?" for _ in metadata_keys)
        query = f"""
        SELECT e.id, em.key, em.string_value
        FROM embeddings e
        JOIN segments s ON e.segment_id = s.id
        JOIN embedding_metadata em ON em.id = e.id
        WHERE s.collection = ?
          AND em.key IN ({placeholders})
          AND em.string_value IS NOT NULL
        """
        cur = conn.cursor()
        cur.execute(query, (collection_id, *metadata_keys))
        grouped: dict[str, dict[str, str]] = {}
        for embedding_id, key, value in cur.fetchall():
            row = grouped.setdefault(str(embedding_id), {})
            row[str(key)] = str(value or "")
        return list(grouped.values())
    finally:
        if conn is not None:
            conn.close()


from app.modules.literature_search.doi_utils import metadata_to_doi


def _metadata_doi(metadata: dict[str, str]) -> str:
    return metadata_to_doi(metadata)


def _metadata_title(metadata: dict[str, str]) -> str:
    for key in ("title", "paper_title"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def scan_dois_from_chroma(
    *,
    db_path: str | Path,
    collection_name: str,
    needle: str,
    mode: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = _load_metadata_rows(
        sqlite_path=_resolve_sqlite_path(db_path),
        collection_name=collection_name,
        metadata_keys=("doi", "DOI", "source_doi", "document_name", "filename", "title", "paper_title"),
    )
    needle_norm = str(needle or "").strip().lower()
    if not needle_norm:
        return []

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for metadata in rows:
        doi = _metadata_doi(metadata)
        if not doi or doi in seen:
            continue
        doi_cmp = doi.lower()
        matched = False
        if mode == "exact":
            matched = doi_cmp == needle_norm
        elif mode == "prefix":
            matched = doi_cmp.startswith(needle_norm)
        else:
            matched = needle_norm in doi_cmp
        if not matched:
            continue
        seen.add(doi)
        hits.append(
            {
                "doi": doi,
                "title": _metadata_title(metadata),
                "match_source": "chroma_metadata",
                "match_score": 1.0 if mode == "exact" else 0.9,
            }
        )
        if len(hits) >= limit:
            break
    return hits


def scan_titles_from_chroma(
    *,
    db_path: str | Path,
    collection_name: str,
    needle: str,
    mode: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = _load_metadata_rows(
        sqlite_path=_resolve_sqlite_path(db_path),
        collection_name=collection_name,
        metadata_keys=("doi", "DOI", "source_doi", "document_name", "filename", "title", "paper_title"),
    )
    needle_norm = str(needle or "").strip().lower()
    if not needle_norm:
        return []

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for metadata in rows:
        doi = _metadata_doi(metadata)
        title = _metadata_title(metadata)
        if not doi or doi in seen:
            continue
        title_cmp = title.lower()
        matched = False
        if mode == "exact":
            matched = title_cmp == needle_norm
        else:
            matched = bool(title_cmp) and needle_norm in title_cmp
        if not matched:
            continue
        seen.add(doi)
        hits.append(
            {
                "doi": doi,
                "title": title,
                "match_source": "chroma_metadata",
                "match_score": 1.0 if mode == "exact" else 0.85,
            }
        )
        if len(hits) >= limit:
            break
    return hits
