from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from typing import Any

from app.modules.storage.paper_storage import (
    build_paper_filename,
    ensure_local_paper_pdf as ensure_local_paper_pdf_impl,
    find_local_paper_pdf,
    normalize_doi as normalize_doi_impl,
)


class StorageService:
    @staticmethod
    def normalize_doi(value: str) -> str:
        return normalize_doi_impl(value)

    @staticmethod
    def build_pdf_url(doi: str) -> str:
        normalized = normalize_doi_impl(doi)
        if not normalized:
            return "/api/v1/view_pdf/"
        encoded_path = "/".join(quote(part, safe="") for part in normalized.split("/"))
        return f"/api/v1/view_pdf/{encoded_path}"

    def build_pdf_links(self, references: list[str] | tuple[str, ...]) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        for item in references:
            doi = self.normalize_doi(str(item or '').strip())
            if not doi:
                continue
            links.append({"doi": doi, "pdf_url": self.build_pdf_url(doi)})
        return links

    @staticmethod
    def build_paper_filename(doi: str) -> str:
        return build_paper_filename(doi)

    def paper_exists(
        self,
        *,
        doi: str,
        papers_dir: str | Path,
        project_root: str | None = None,
        logger: Any | None = None,
    ) -> bool:
        _ = project_root
        return find_local_paper_pdf(doi=doi, papers_dir=papers_dir, logger=logger) is not None

    def ensure_local_paper_pdf(
        self,
        *,
        doi: str,
        papers_dir: str | Path,
        project_root: str | None = None,
        logger: Any | None = None,
    ) -> Path | None:
        _ = project_root
        return ensure_local_paper_pdf_impl(doi=doi, papers_dir=papers_dir, logger=logger)


storage_service = StorageService()
