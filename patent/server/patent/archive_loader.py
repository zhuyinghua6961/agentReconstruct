from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from server.patent.original_assets_tooling import (
    _build_structured_bibliography,
    _build_structured_claims,
    _build_structured_description,
)
from server.patent.retrieval_models import (
    PatentCatalogRecord,
    PatentClaim,
    PatentDescriptionSnippet,
    PatentTableSupplement,
)


class PatentArchiveLoader:
    def __init__(self, archive_root: str | Path) -> None:
        self._archive_root = Path(archive_root).resolve()
        self._dir_index = self._build_dir_index()

    def _build_dir_index(self) -> dict[str, Path]:
        if not self._archive_root.is_dir():
            return {}
        index: dict[str, Path] = {}
        for path in sorted(self._archive_root.iterdir()):
            if not path.is_dir():
                continue
            canonical_patent_id = path.name.strip().upper()
            if canonical_patent_id:
                index[canonical_patent_id] = path
        return index

    def patent_dir(self, canonical_patent_id: str) -> Path | None:
        return self._dir_index.get(str(canonical_patent_id or "").strip().upper())

    def build_catalog_records(self) -> list[PatentCatalogRecord]:
        return [self.load_catalog_record(canonical_patent_id) for canonical_patent_id in sorted(self._dir_index)]

    @lru_cache(maxsize=4096)
    def load_catalog_record(self, canonical_patent_id: str) -> PatentCatalogRecord:
        normalized = str(canonical_patent_id or "").strip().upper()
        base_dir = self.patent_dir(normalized)
        if base_dir is None:
            return PatentCatalogRecord(
                canonical_patent_id=normalized,
                publication_number=normalized,
                application_number=None,
                title=normalized,
                abstract_text="",
            )

        bibliography_payload = self._load_json(base_dir / "著录项目.json")
        bibliography = _build_structured_bibliography(normalized, bibliography_payload)
        bib = dict(bibliography.get("bibliography") or {})
        publication_date = str((((bibliography_payload.get("data") or [{}])[0]).get("bibliographic_data") or {}).get("publication_reference", {}).get("date") or "")
        return PatentCatalogRecord(
            canonical_patent_id=normalized,
            publication_number=str(bib.get("publication_number") or normalized).strip().upper(),
            application_number=str(bib.get("application_number") or "").strip() or None,
            title=str(bibliography.get("title") or normalized).strip(),
            abstract_text=str(bibliography.get("abstract_text") or "").strip(),
            country=str(bib.get("country") or ""),
            kind_code=str(bib.get("kind_code") or ""),
            publication_date=publication_date,
            provider="patent_archive",
            original_available=True,
        )

    def build_identity_registry(self) -> dict[str, str]:
        registry: dict[str, str] = {}
        for record in self.build_catalog_records():
            for candidate in (record.canonical_patent_id, record.publication_number, record.application_number):
                text = str(candidate or "").strip()
                if not text:
                    continue
                registry[text] = record.canonical_patent_id
        return registry

    @lru_cache(maxsize=4096)
    def load_claims(self, canonical_patent_id: str) -> list[PatentClaim]:
        normalized = str(canonical_patent_id or "").strip().upper()
        base_dir = self.patent_dir(normalized)
        if base_dir is None:
            return []
        structured = _build_structured_claims(normalized, self._load_json(base_dir / "权利要求.json"))
        claims = []
        for item in list(structured.get("claims") or []):
            if not isinstance(item, dict):
                continue
            claim_number = int(item.get("claim_number") or 0)
            text = str(item.get("text") or "").strip()
            if claim_number > 0 and text:
                claims.append(PatentClaim(claim_number=claim_number, text=text))
        return claims

    @lru_cache(maxsize=4096)
    def load_description_snippets(self, canonical_patent_id: str) -> list[PatentDescriptionSnippet]:
        normalized = str(canonical_patent_id or "").strip().upper()
        base_dir = self.patent_dir(normalized)
        if base_dir is None:
            return []
        structured = _build_structured_description(normalized, self._load_json(base_dir / "说明书.json"))
        snippets = []
        for item in list(structured.get("paragraphs") or []):
            if not isinstance(item, dict):
                continue
            paragraph_id = str(item.get("paragraph_id") or "").strip()
            text = str(item.get("text") or "").strip()
            if paragraph_id and text:
                snippets.append(PatentDescriptionSnippet(paragraph_id=paragraph_id, text=text))
        return snippets

    @lru_cache(maxsize=4096)
    def load_tables(self, canonical_patent_id: str) -> list[PatentTableSupplement]:
        normalized = str(canonical_patent_id or "").strip().upper()
        base_dir = self.patent_dir(normalized)
        if base_dir is None:
            return []
        tables_path = next(iter(sorted(base_dir.glob("*_tables.json"))), None)
        if tables_path is None or not tables_path.is_file():
            return []
        payload = self._load_json(tables_path)
        tables: list[PatentTableSupplement] = []
        for item in list(payload if isinstance(payload, list) else []):
            if not isinstance(item, dict):
                continue
            rows = []
            for row in list(item.get("rows") or []):
                if not isinstance(row, dict):
                    continue
                rows.append({str(key): str(value) for key, value in row.items()})
            if not rows:
                continue
            tables.append(
                PatentTableSupplement(
                    table_title=str(item.get("table_title") or "").strip(),
                    columns=[str(value) for value in list(item.get("columns") or []) if str(value).strip()],
                    rows=rows,
                    source_image=str(item.get("_source_image") or "").strip() or None,
                )
            )
        return tables

    def load_bibliography(self, canonical_patent_id: str) -> dict[str, Any]:
        normalized = str(canonical_patent_id or "").strip().upper()
        base_dir = self.patent_dir(normalized)
        if base_dir is None:
            return {}
        return _build_structured_bibliography(normalized, self._load_json(base_dir / "著录项目.json"))

    def load_fulltext(self, canonical_patent_id: str) -> dict[str, Any]:
        normalized = str(canonical_patent_id or "").strip().upper()
        return {
            "bibliography": self.load_bibliography(normalized),
            "claims": self.load_claims(normalized),
            "description": self.load_description_snippets(normalized),
            "tables": self.load_tables(normalized),
        }

    @lru_cache(maxsize=4096)
    def load_pdf_document(self, canonical_patent_id: str) -> dict[str, Any] | None:
        normalized = str(canonical_patent_id or "").strip().upper()
        base_dir = self.patent_dir(normalized)
        if base_dir is None:
            return None
        pdf_path = next(iter(sorted(path for path in base_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")), None)
        if pdf_path is None:
            return None
        content = pdf_path.read_bytes()
        return {
            "path": str(pdf_path),
            "filename": pdf_path.name,
            "size_bytes": len(content),
        }

    @staticmethod
    def _load_json(path: Path) -> Any:
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
