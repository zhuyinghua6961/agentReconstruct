"""PDF text extraction helpers."""

# Deprecated: retained only to support the retired highThinkingQA document service.


from __future__ import annotations

import re
from typing import Any, Callable


def exclude_references_section(pages_text: list[tuple[int, str]], logger: Any) -> list[tuple[int, str]]:
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
    for index in range(len(pages_text) - 1, -1, -1):
        page_num, text = pages_text[index]
        text_lower = text.lower()
        for keyword in reference_keywords:
            if keyword not in text_lower:
                continue
            pattern = rf"^\s*{re.escape(keyword)}\s*[：:]*\s*$"
            if re.search(pattern, text_lower, re.MULTILINE | re.IGNORECASE):
                reference_start_idx = index
                logger.info("reference section detected at page %s via keyword %s", page_num, keyword)
                break
        if reference_start_idx is not None:
            break

    if reference_start_idx is None:
        return pages_text

    reference_text = "\n".join(text for _, text in pages_text[reference_start_idx:])
    doi_count = len(re.findall(r"10\.\d+/[^\s]+", reference_text, re.IGNORECASE))
    url_count = len(re.findall(r"https?://[^\s]+", reference_text, re.IGNORECASE))
    year_count = len(re.findall(r"\b(19|20)\d{2}\b", reference_text))
    if doi_count >= 3 or url_count >= 3 or (year_count >= 5 and doi_count >= 1):
        return pages_text[:reference_start_idx]
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
    exclude_references_section_fn: Callable[[list[tuple[int, str]], Any], list[tuple[int, str]]] = exclude_references_section,
) -> str:
    if not pdf_support:
        return "[错误] PyMuPDF未安装，无法提取PDF内容"

    try:
        doc = fitz_module.open(pdf_path)
        text_content: list[str] = []
        metadata = doc.metadata
        if metadata.get("title"):
            text_content.append(f"标题: {metadata['title']}")
        if metadata.get("author"):
            text_content.append(f"作者: {metadata['author']}")
        text_content.append("\n" + "=" * 60 + "\n")

        total_page_count = doc.page_count
        num_pages = min(total_page_count, max_pages)
        pages_with_text = 0
        all_pages_text: list[tuple[int, str]] = []

        for page_num in range(num_pages):
            page = doc[page_num]
            text = page.get_text()
            if not text.strip():
                continue
            pages_with_text += 1
            all_pages_text.append((page_num + 1, text))

        doc.close()

        if exclude_references and all_pages_text:
            all_pages_text = exclude_references_section_fn(all_pages_text, logger)

        for page_num, text in all_pages_text:
            text_content.append(f"\n--- 第 {page_num} 页 ---\n{text}")

        full_text = "\n".join(text_content)
        logger.info(
            "pdf extracted: total_pages=%s extracted_pages=%s pages_with_text=%s chars=%s",
            total_page_count,
            num_pages,
            pages_with_text,
            len(full_text),
        )
        if len(full_text) < 200:
            return f"[警告] PDF内容提取过少({len(full_text)}字符)，可能是扫描版PDF。提取的内容：\n\n{full_text}"
        return full_text
    except Exception as exc:
        logger.error("pdf extraction failed: %s", exc)
        logger.error(traceback_module.format_exc())
        return f"[错误] PDF提取失败: {str(exc)}"
