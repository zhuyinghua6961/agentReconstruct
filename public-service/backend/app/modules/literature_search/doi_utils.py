from __future__ import annotations

import re
from pathlib import Path

from app.modules.storage.service import storage_service


_DOI_HEURISTIC = re.compile(r"^10\.\d{4,9}/", re.IGNORECASE)


def metadata_to_doi(metadata: dict[str, object] | None) -> str:
    if not isinstance(metadata, dict):
        return ""
    for key in ("doi", "DOI", "source_doi"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return storage_service.normalize_doi(value) or value
    for key in ("document_name", "filename"):
        value = str(metadata.get(key) or "").strip()
        if not value:
            continue
        doi = extract_doi_from_filename(value if value.lower().endswith(".md") else f"{value}.md")
        if doi:
            return doi
    return ""


def doi_to_document_name(doi: str) -> str:
    normalized = storage_service.normalize_doi(doi)
    if not normalized:
        return ""
    return normalized.replace("/", "_", 1)


def extract_doi_from_filename(filename: str) -> str:
    stem = Path(str(filename or "")).stem
    if not stem:
        return ""
    if stem.startswith("10.") and "_" in stem:
        return stem.replace("_", "/", 1)
    return storage_service.normalize_doi(stem)


def looks_like_doi_query(text: str) -> bool:
    normalized = storage_service.normalize_doi(text)
    if not normalized:
        return False
    if _DOI_HEURISTIC.match(normalized):
        return True
    return normalized.lower().startswith("10.")


def resolve_query_type(*, query: str, query_type: str) -> str:
    normalized_type = str(query_type or "auto").strip().lower()
    if normalized_type in {"doi", "title"}:
        return normalized_type
    return "doi" if looks_like_doi_query(query) else "title"
