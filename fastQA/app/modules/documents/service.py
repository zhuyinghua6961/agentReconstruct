from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from app.core.config import get_settings
from app.modules.documents.reference_preview import (
    build_reference_preview_batch,
    clamp_preview_max_items,
    collect_doi_candidates,
    normalize_dois,
)
from app.modules.qa_pdf.pdf_extractor import extract_pdf_text as extract_pdf_text_impl
from app.modules.storage.service import storage_service


def normalize_doi(value: str) -> str:
    text = str(value or "").strip()
    previous = None
    while previous != text:
        previous = text
        text = unquote(text).strip()
    text = text.replace("\\", "/")
    text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[(/\\s]+|[)\\s]+$", "", text)
    if "papers/" in text:
        text = text.split("papers/", 1)[-1]
    elif (
        text.lower().endswith(".pdf")
        and (
            os.path.isabs(text)
            or text.startswith("./")
            or text.startswith("../")
            or bool(re.match(r"^[A-Za-z]:[\\/]", text))
        )
    ):
        text = Path(text).name or text
    if text.lower().endswith(".pdf"):
        text = text[:-4]
    if "_" in text and "/" not in text and text.startswith("10."):
        text = text.replace("_", "/", 1)
    return text.strip()


class DocumentsService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._papers_dir = Path(self._settings.papers_dir)

    def _resolve_papers_dir(self, papers_dir: str | Path | None = None) -> Path:
        if papers_dir is None:
            return Path(self._papers_dir)
        return Path(papers_dir)

    def _ensure_local_pdf(self, *, doi: str, logger: Any, papers_dir: str | Path | None = None) -> Path | None:
        normalized = normalize_doi(doi)
        return storage_service.ensure_local_paper_pdf(
            doi=normalized,
            papers_dir=self._resolve_papers_dir(papers_dir),
            project_root=str(self._resolve_papers_dir(papers_dir).parent),
            logger=logger,
        )

    @staticmethod
    def _segment_paragraphs(full_text: str) -> list[str]:
        paragraphs: list[str] = []
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", full_text)
        current = ""
        sentence_count = 0
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            current += sentence + " "
            sentence_count += 1
            if (sentence_count >= 2 and len(current) > 150) or len(current) > 400:
                paragraphs.append(current.strip())
                current = ""
                sentence_count = 0
        if current.strip():
            paragraphs.append(current.strip())
        return paragraphs[:100]

    def view_pdf_path(self, doi: str, logger: Any, papers_dir: str | Path | None = None) -> tuple[dict[str, Any], int, Path | None]:
        try:
            normalized = normalize_doi(doi)
            pdf_path = self._ensure_local_pdf(doi=normalized, logger=logger, papers_dir=papers_dir)
            if not pdf_path:
                return {"error": f"PDF文件不存在: {normalized or doi}"}, 404, None
            return {}, 200, pdf_path
        except Exception as exc:
            return {"error": f"查看PDF失败: {exc}"}, 500, None

    def check_pdf(self, doi: str, logger: Any | None = None, papers_dir: str | Path | None = None) -> tuple[dict[str, Any], int]:
        normalized = normalize_doi(doi)
        resolved_papers_dir = self._resolve_papers_dir(papers_dir)
        exists = storage_service.paper_exists(
            doi=normalized,
            papers_dir=resolved_papers_dir,
            project_root=str(resolved_papers_dir.parent),
        )
        filename = storage_service.build_paper_filename(normalized)
        return {"exists": exists, "doi": normalized, "filename": filename if exists else None}, 200

    def extract_pdf_text(
        self,
        doi: str,
        logger: Any,
        papers_dir: str | Path | None = None,
    ) -> tuple[dict[str, Any], int]:
        try:
            normalized = normalize_doi(doi)
            pdf_path = self._ensure_local_pdf(doi=normalized, logger=logger, papers_dir=papers_dir)
            if not pdf_path:
                return {"error": f"PDF文件不存在: {normalized or doi}"}, 404
            try:
                import fitz  # type: ignore

                pdf_support = True
            except Exception:
                fitz = None
                pdf_support = False
            full_text = extract_pdf_text_impl(
                str(pdf_path),
                max_pages=max(1, int(os.getenv("MAX_PDF_PAGES", "50") or "50")),
                exclude_references=True,
                pdf_support=pdf_support,
                fitz_module=fitz,
                logger=logger,
                traceback_module=__import__("traceback"),
            )
            if str(full_text).startswith("[错误]"):
                return {"error": full_text}, 500
            paragraphs = self._segment_paragraphs(str(full_text or ""))
            return {"doi": normalized, "paragraphs": paragraphs, "total": len(paragraphs)}, 200
        except Exception as exc:
            return {"error": f"提取失败: {exc}"}, 500

    def reference_preview(
        self,
        *,
        dois_text: str,
        doi_list: list[str] | None,
        max_items: int | None,
        agent: Any = None,
        logger: Any = None,
        papers_dir: str | Path | None = None,
    ) -> tuple[dict[str, Any], int]:
        clamped_max = clamp_preview_max_items(max_items)
        raw_candidates = collect_doi_candidates(dois_text, doi_list or [])
        dois = normalize_dois(dois_text, doi_list or [], clamped_max)
        if not dois:
            return {
                "success": True,
                "items": [],
                "count": 0,
                "requested_count": 0,
                "max_items": clamped_max,
                "truncated": False,
            }, 200
        items = build_reference_preview_batch(
            dois=dois,
            papers_dir=self._resolve_papers_dir(papers_dir),
            agent=agent,
            logger=logger,
        )
        requested_count = len(dict.fromkeys(raw_candidates))
        return {
            "success": True,
            "items": items,
            "count": len(items),
            "requested_count": requested_count,
            "max_items": clamped_max,
            "truncated": requested_count > len(dois),
        }, 200


documents_service = DocumentsService()
