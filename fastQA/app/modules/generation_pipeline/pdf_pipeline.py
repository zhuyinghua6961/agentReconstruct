#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF lookup and chunk extraction helpers for generation-driven RAG."""

import glob
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.modules.generation_pipeline.feature_flags import env_bool, env_int
from app.modules.storage.service import storage_service


def _strict_original_minio_only() -> bool:
    raw = str(os.getenv("FASTQA_UPLOAD_MINIO_ONLY", "") or os.getenv("QA_ORIGINAL_MINIO_ONLY", "true") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _stage3_diag_enabled() -> bool:
    return env_bool("QA_STAGE3_DIAGNOSTIC_LOG", True)


def _stage3_source_details_enabled() -> bool:
    return _stage3_diag_enabled() and env_bool("QA_STAGE3_LOG_SOURCE_DETAILS", True)


def _stage3_chunk_details_enabled() -> bool:
    return _stage3_diag_enabled() and env_bool("QA_STAGE3_LOG_CHUNK_DETAILS", True)


def _stage3_log_chunk_max() -> int:
    return env_int("QA_STAGE3_LOG_CHUNK_MAX", 5, minimum=0, maximum=50)


def _stage3_log_text_max_chars() -> int:
    return env_int("QA_STAGE3_LOG_TEXT_MAX_CHARS", 1000, minimum=80, maximum=12000)


def _preview_text(value: Any, *, max_chars: int | None = None) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    limit = _stage3_log_text_max_chars() if max_chars is None else max(0, int(max_chars))
    if limit and len(text) > limit:
        return text[:limit] + "..."
    return text


def _file_size_bytes(path: str | Path | None) -> int | None:
    if not path:
        return None
    try:
        return int(Path(path).stat().st_size)
    except Exception:
        return None


def find_pdf_path(*, doi: str, papers_dir: str | Path, logger: Any) -> Optional[str]:
    """Find paper PDF path by DOI using exact and glob patterns."""
    base_dir = Path(papers_dir).resolve()

    resolved = storage_service.ensure_local_paper_pdf(doi=doi, papers_dir=base_dir, logger=logger)
    if resolved:
        logger.debug(f"   📄 找到PDF（MinIO/本地）: {resolved.name}")
        return str(resolved)

    if _strict_original_minio_only():
        logger.debug(f"   ⚠️ strict MinIO-only mode skipped local PDF fallback: {doi}")
        return None

    doi_clean = str(doi or "").strip()
    if not doi_clean:
        return None

    possible_names = [
        f"{doi_clean}.pdf",
        doi_clean.replace("/", "_") + ".pdf",
    ]

    for filename in possible_names:
        pdf_path = base_dir / filename
        if pdf_path.exists():
            logger.debug(f"   📄 找到PDF: {filename}")
            return str(pdf_path)

    parts = doi_clean.split("/")
    if len(parts) >= 2:
        prefix = parts[0]
        suffix = parts[-1]
        pattern = f"{prefix}_{suffix}*.pdf"
        matches = glob.glob(str(base_dir / pattern))
        if matches:
            logger.debug(f"   📄 通过glob找到PDF: {Path(matches[0]).name}")
            return matches[0]

    logger.debug(f"   ⚠️ 未找到PDF: {doi}")
    return None


def extract_chunks_from_pdf(
    *,
    pdf_path: str,
    doi: str,
    max_chunks: int,
    logger: Any,
) -> List[Dict[str, Any]]:
    """Extract paragraph chunks from PDF pages for one DOI."""
    try:
        import fitz
    except Exception:
        logger.warning("   ⚠️ PyMuPDF 未安装，无法读取PDF")
        return []

    chunks: List[Dict[str, Any]] = []
    try:
        doc = fitz.open(pdf_path)
        max_pages = min(doc.page_count, 15)
        chunk_id = 0
        skip_first_page_chars = 1500

        total_paragraphs = 0
        valid_paragraphs = 0
        total_chars = 0
        current_chunk = ""

        for page_num in range(max_pages):
            page = doc[page_num]
            text = page.get_text()
            if not text or not text.strip():
                continue
            if page_num == 0:
                if len(text) > skip_first_page_chars:
                    text = text[skip_first_page_chars:]
                else:
                    continue

            paragraphs = text.split("\n\n")
            total_paragraphs += len(paragraphs)
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                total_chars += len(para)
                if len(para) >= 50:
                    valid_paragraphs += 1

        logger.debug(
            f"       📊 PDF统计: 页数={max_pages}, 总段落={total_paragraphs}, "
            f"有效段落={valid_paragraphs}, 总字符={total_chars}"
        )
        if _stage3_source_details_enabled():
            logger.info(
                "Stage3 PDF text diagnostic doi=%s pdf_path=%s file_size_bytes=%s pages_scanned=%s "
                "total_paragraphs=%s valid_paragraphs=%s text_chars=%s",
                doi,
                str(pdf_path),
                _file_size_bytes(pdf_path),
                max_pages,
                total_paragraphs,
                valid_paragraphs,
                total_chars,
            )

        for page_num in range(max_pages):
            page = doc[page_num]
            text = page.get_text()
            if not text or not text.strip():
                continue
            if page_num == 0:
                if len(text) > skip_first_page_chars:
                    text = text[skip_first_page_chars:]
                else:
                    continue

            paragraphs = text.split("\n\n")
            current_chars = 0
            chunk_max_chars = 800

            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if len(para) < 50:
                    continue

                if current_chars + len(para) > chunk_max_chars and current_chunk:
                    chunks.append(
                        {
                            "doi": doi,
                            "page": page_num + 1,
                            "chunk_id": chunk_id,
                            "chunk_type": "paragraph",
                            "text": current_chunk.strip(),
                            "word_count": len(current_chunk.split()),
                        }
                    )
                    chunk_id += 1
                    current_chunk = para
                    current_chars = len(para)
                    if len(chunks) >= max_chunks:
                        break
                else:
                    if current_chunk:
                        current_chunk += "\n\n" + para
                    else:
                        current_chunk = para
                    current_chars += len(para)
            if len(chunks) >= max_chunks:
                break

        doc.close()

        if current_chunk and len(chunks) < max_chunks:
            chunks.append(
                {
                    "doi": doi,
                    "page": max_pages,
                    "chunk_id": chunk_id,
                    "chunk_type": "paragraph",
                    "text": current_chunk.strip(),
                    "word_count": len(current_chunk.split()),
                }
            )

        if chunks:
            first_chunk_len = len(chunks[0].get("text", ""))
            logger.debug(f"       📦 提取完成: {len(chunks)} 个chunk, 首个chunk长度={first_chunk_len}字符")
        else:
            logger.debug("       ⚠️ 未提取到任何chunk")
    except Exception as e:
        logger.warning(f"   ⚠️ 处理PDF失败: {e}")

    return chunks


def stage3_load_pdf_chunks(
    *,
    dois: List[str],
    papers_dir: str | Path,
    max_chunks_per_doi: int,
    logger: Any,
    should_cancel: Optional[Callable[[], bool]] = None,
    find_pdf_path_fn: Optional[Callable[..., Optional[str]]] = None,
    extract_chunks_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load DOI PDFs and return extracted chunk map."""
    logger.info(f"📋 待处理 {len(dois)} 个DOI")
    doi_to_chunks: Dict[str, List[Dict[str, Any]]] = {}
    requested_count = len(dois)
    missing_pdf_count = 0
    empty_chunk_count = 0

    if _stage3_diag_enabled():
        logger.info(
            "Stage3 diagnostic start doi_count=%s max_chunks_per_doi=%s papers_dir=%s "
            "strict_original_minio_only=%s doi_sample=%s",
            requested_count,
            max_chunks_per_doi,
            str(Path(papers_dir)),
            _strict_original_minio_only(),
            list(dois or [])[:10],
        )

    def _cancelled() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            return False

    finder = find_pdf_path_fn or find_pdf_path
    extractor = extract_chunks_fn or extract_chunks_from_pdf

    for i, doi in enumerate(dois, 1):
        if _cancelled():
            logger.info("🛑 Stage3 已取消，提前结束PDF溯源")
            break
        logger.info(f"   [{i}/{len(dois)}] 处理 DOI: {doi}")
        pdf_path = finder(doi=doi, papers_dir=papers_dir, logger=logger)
        chunks: List[Dict[str, Any]] = []
        status = "no_pdf"
        if pdf_path:
            if _cancelled():
                logger.info("🛑 Stage3 已取消，停止当前PDF处理")
                break
            chunks = extractor(pdf_path=pdf_path, doi=doi, max_chunks=max_chunks_per_doi, logger=logger)
            if chunks:
                doi_to_chunks[doi] = chunks
                status = "success"
                logger.info(f"       ✅ 成功提取 {len(chunks)} 个chunks")
            else:
                empty_chunk_count += 1
                status = "empty_chunks"
                logger.warning("       ⚠️ PDF中未提取到有效chunks")
        else:
            missing_pdf_count += 1
            logger.warning("       ⚠️ 未找到PDF文件")
        if _stage3_source_details_enabled():
            first_chunk = chunks[0] if chunks and isinstance(chunks[0], dict) else {}
            logger.info(
                "Stage3 source diagnostic doi=%s source_index=%s/%s pdf_found=%s pdf_path=%s "
                "file_size_bytes=%s chunk_count=%s first_chunk_chars=%s status=%s",
                doi,
                i,
                requested_count,
                bool(pdf_path),
                str(pdf_path or ""),
                _file_size_bytes(pdf_path),
                len(chunks),
                len(str(first_chunk.get("text") or "")),
                status,
            )
        if chunks and _stage3_chunk_details_enabled():
            for chunk_index, chunk in enumerate(chunks[: _stage3_log_chunk_max()], start=1):
                if not isinstance(chunk, dict):
                    continue
                text = str(chunk.get("text") or "")
                logger.info(
                    "Stage3 chunk diagnostic doi=%s chunk_index=%s page=%s chunk_id=%s chunk_type=%s "
                    "text_chars=%s preview=%s",
                    doi,
                    chunk_index,
                    chunk.get("page"),
                    chunk.get("chunk_id"),
                    chunk.get("chunk_type"),
                    len(text),
                    _preview_text(text),
                )

    total_chunks = sum(len(chunks) for chunks in doi_to_chunks.values())
    if _stage3_diag_enabled():
        logger.info(
            "Stage3 diagnostic completed requested=%s successful=%s missing_pdf=%s empty_chunks=%s "
            "total_chunks=%s successful_doi_sample=%s",
            requested_count,
            len(doi_to_chunks),
            missing_pdf_count,
            empty_chunk_count,
            total_chunks,
            list(doi_to_chunks.keys())[:10],
        )
    logger.info(f"\n✅ PDF溯源完成：成功处理 {len(doi_to_chunks)} 个DOI")
    return doi_to_chunks


__all__ = ["extract_chunks_from_pdf", "find_pdf_path", "stage3_load_pdf_chunks"]
