from __future__ import annotations

import logging
import re
from typing import Any, Callable

from app.modules.qa_pdf.prompting import is_summary_question
from app.modules.qa_pdf.truncation import smart_truncate_pdf_content


def extract_doi_from_filename(file_name: str) -> str:
    text = str(file_name or "").strip()
    if not text:
        return ""
    if "." in text.rsplit("/", 1)[-1]:
        stem, suffix = text.rsplit(".", 1)
        if suffix.lower() == "pdf":
            text = stem
    match = re.search(r"(10\.\d+[/_][-._;()/:A-Za-z0-9]+)", text)
    if not match:
        return ""
    return match.group(1).replace("_", "/", 1).rstrip(").,;")


def _format_pdf_preview_fallback(pdf_files: list[dict[str, Any]], *, limit: int = 3) -> str:
    rows: list[str] = []
    for item in pdf_files[:limit]:
        file_name = str(item.get("file_name") or "").strip()
        file_meta = item.get("file_meta") if isinstance(item.get("file_meta"), dict) else {}
        preview = str(file_meta.get("parsed_preview") or "").strip()
        doi = extract_doi_from_filename(file_name)
        title = file_name or "uploaded.pdf"
        if doi:
            title = f"{title} (DOI: {doi})"
        rows.append(title)
        if preview:
            rows.append(preview[:600])
    return "\n".join(rows).strip()


def build_merged_pdf_context(
    *,
    pdf_files: list[dict[str, Any]],
    load_pdf_content_fn: Callable[..., tuple[str | None, str | None]],
    question: str,
    max_pdf_chars: int,
    logger: Any | None = None,
    max_files: int = 6,
) -> tuple[str, list[str], int]:
    """Prepare PDF context for hybrid QA using the same merge path as multi-PDF pdf_qa."""
    active_logger = logger or logging.getLogger(__name__)
    merged_parts: list[str] = []
    references: list[str] = []
    seen_doi: set[str] = set()
    loaded_count = 0

    for idx, item in enumerate(pdf_files[:max_files], start=1):
        if not isinstance(item, dict):
            continue
        file_path = str(item.get("local_path") or "").strip()
        if not file_path or not callable(load_pdf_content_fn):
            continue
        content, error_message = load_pdf_content_fn(question=question, pdf_path=file_path)
        if error_message or not content:
            continue
        loaded_count += 1
        file_no = str(item.get("file_no") or "").strip()
        file_name = str(item.get("file_name") or f"pdf_{idx}")
        label = f"#{file_no}" if file_no else f"#{idx}"
        merged_parts.append(f"\n\n===== 文献 {label}: {file_name} =====\n{content}\n")
        doi = extract_doi_from_filename(str(item.get("file_name") or "")) or extract_doi_from_filename(file_path)
        if doi and doi not in seen_doi:
            seen_doi.add(doi)
            references.append(doi)

    if not merged_parts:
        preview_context = _format_pdf_preview_fallback(pdf_files)
        if preview_context:
            for item in pdf_files[:max_files]:
                doi = extract_doi_from_filename(str(item.get("file_name") or ""))
                if doi and doi not in seen_doi:
                    seen_doi.add(doi)
                    references.append(doi)
            return preview_context, references, 0

        return "", references, 0

    merged_text = "\n".join(merged_parts).strip()
    if len(merged_text) > max_pdf_chars:
        merged_text = smart_truncate_pdf_content(
            merged_text,
            max_pdf_chars,
            logger=active_logger,
            is_summary=is_summary_question(question),
            question=question,
        )
    return merged_text, references, loaded_count


__all__ = ["build_merged_pdf_context", "extract_doi_from_filename"]
