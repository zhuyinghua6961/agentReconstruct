#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF text extraction helpers (vendored from fastQA qa_pdf.pdf_extractor)."""

from __future__ import annotations

import re
from typing import Any, Callable, List, Tuple


def exclude_references_section(pages_text: List[Tuple[int, str]], logger: Any) -> List[Tuple[int, str]]:
    """Remove likely references section pages from extracted PDF pages."""
    if not pages_text:
        return pages_text

    reference_keywords = [
        "references",
        "bibliography",
        "参考文献",
        "参考书目",
        "cited references",
        "literature cited",
        "works cited",
        "引用文献",
        "相关文献",
        "文献列表",
    ]

    reference_start_idx = None
    for i in range(len(pages_text) - 1, -1, -1):
        page_num, text = pages_text[i]
        text_lower = text.lower()
        for keyword in reference_keywords:
            if keyword in text_lower:
                pattern = rf"^\s*{re.escape(keyword)}\s*[：:]*\s*$"
                if re.search(pattern, text_lower, re.MULTILINE | re.IGNORECASE):
                    reference_start_idx = i
                    logger.info("detected references section start page=%s keyword=%s", page_num, keyword)
                    break
        if reference_start_idx is not None:
            break

    if reference_start_idx is not None:
        reference_page_text = "\n".join([text for _, text in pages_text[reference_start_idx:]])
        doi_count = len(re.findall(r"10\.\d+/[^\s]+", reference_page_text, re.IGNORECASE))
        url_count = len(re.findall(r"https?://[^\s]+", reference_page_text, re.IGNORECASE))
        year_count = len(re.findall(r"\b(19|20)\d{2}\b", reference_page_text))

        if doi_count >= 3 or url_count >= 3 or (year_count >= 5 and doi_count >= 1):
            logger.info(
                "confirmed references section doi_count=%s url_count=%s year_count=%s",
                doi_count,
                url_count,
                year_count,
            )
            return pages_text[:reference_start_idx]
        logger.info("suspected references section but features weak; keeping all pages")

    return pages_text


def extract_pdf_text(
    pdf_path: str,
    *,
    max_pages: int = 50,
    exclude_references: bool = True,
    pdf_support: bool,
    fitz_module: Any,
    logger: Any,
    traceback_module: Any,
    exclude_references_section_fn: Callable[[List[Tuple[int, str]], Any], List[Tuple[int, str]]] = exclude_references_section,
) -> str:
    """Extract text from PDF with optional reference-section removal."""
    if not pdf_support:
        return "[错误] PyMuPDF未安装，无法提取PDF内容"

    try:
        doc = fitz_module.open(pdf_path)
        text_content = []

        metadata = doc.metadata
        if metadata.get("title"):
            text_content.append(f"标题: {metadata['title']}")
            logger.info("pdf title=%s", metadata["title"])
        if metadata.get("author"):
            text_content.append(f"作者: {metadata['author']}")
            logger.info("pdf author=%s", metadata["author"])
        text_content.append("\n" + "=" * 60 + "\n")

        total_page_count = doc.page_count
        num_pages = min(total_page_count, max_pages)
        logger.info("pdf extract start total_pages=%s extracting_pages=%s", total_page_count, num_pages)

        total_chars = 0
        pages_with_text = 0
        all_pages_text: List[Tuple[int, str]] = []

        for page_num in range(num_pages):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                pages_with_text += 1
                all_pages_text.append((page_num + 1, text))
                total_chars += len(text)

        doc.close()

        if exclude_references and all_pages_text:
            all_pages_text = exclude_references_section_fn(all_pages_text, logger)
            logger.info("references excluded pages_kept=%s", len(all_pages_text))

        for page_num, text in all_pages_text:
            page_text = f"\n--- 第 {page_num} 页 ---\n{text}"
            text_content.append(page_text)

        full_text = "\n".join(text_content)
        logger.info(
            "pdf extract done total_pages=%s extracted_pages=%s pages_with_text=%s full_chars=%s body_chars=%s",
            total_page_count,
            num_pages,
            pages_with_text,
            len(full_text),
            total_chars,
        )

        if len(full_text) < 200:
            logger.warning("pdf extract too little content chars=%s", len(full_text))
            return f"[警告] PDF内容提取过少({len(full_text)}字符)，可能是扫描版PDF。提取的内容：\n\n{full_text}"

        preview = full_text[:300].replace("\n", " ")
        logger.info("pdf extract preview=%s", preview)
        return full_text
    except Exception as exc:
        logger.error("pdf extract failed: %s", exc)
        logger.error(traceback_module.format_exc())
        return f"[错误] PDF提取失败: {str(exc)}"
