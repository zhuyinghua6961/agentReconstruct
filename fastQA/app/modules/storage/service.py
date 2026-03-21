from __future__ import annotations

from pathlib import Path
from typing import Any

from app.modules.storage.paper_storage import (
    build_paper_filename,
    ensure_local_paper_pdf as ensure_local_paper_pdf_impl,
    find_local_paper_pdf,
)


class StorageService:
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
