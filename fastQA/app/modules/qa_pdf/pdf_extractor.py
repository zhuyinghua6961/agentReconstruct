#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF text extraction helpers."""

import re
from typing import Any, Callable, List, Tuple


def exclude_references_section(pages_text: List[Tuple[int, str]], logger: Any) -> List[Tuple[int, str]]:
    """Remove likely references section pages from extracted PDF pages."""
    if not pages_text:
        return pages_text

    reference_keywords = [
        "references", "bibliography", "参考文献", "参考书目",
        "cited references", "literature cited", "works cited",
        "引用文献", "相关文献", "文献列表",
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
                    logger.info(f"📄 检测到参考文献部分起始于第 {page_num} 页（关键词：{keyword}）")
                    break
        if reference_start_idx is not None:
            break

    if reference_start_idx is not None:
        reference_page_text = "\n".join([text for _, text in pages_text[reference_start_idx:]])
        doi_count = len(re.findall(r"10\.\d+/[^\s]+", reference_page_text, re.IGNORECASE))
        url_count = len(re.findall(r"https?://[^\s]+", reference_page_text, re.IGNORECASE))
        year_count = len(re.findall(r"\b(19|20)\d{2}\b", reference_page_text))

        if doi_count >= 3 or url_count >= 3 or (year_count >= 5 and doi_count >= 1):
            logger.info(f"📄 确认参考文献部分：DOI={doi_count}, URL={url_count}, 年份={year_count}")
            return pages_text[:reference_start_idx]
        logger.info("📄 疑似参考文献部分但特征不明显，保留所有页面")

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
            logger.info(f"📄 PDF标题: {metadata['title']}")
        if metadata.get("author"):
            text_content.append(f"作者: {metadata['author']}")
            logger.info(f"📄 PDF作者: {metadata['author']}")
        text_content.append("\n" + "=" * 60 + "\n")

        total_page_count = doc.page_count
        num_pages = min(total_page_count, max_pages)
        logger.info(f"📄 开始提取PDF文本，共{total_page_count}页，提取前{num_pages}页")

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
                logger.debug(f"   第{page_num + 1}页: {len(text)} 字符")

        doc.close()

        if exclude_references and all_pages_text:
            all_pages_text = exclude_references_section_fn(all_pages_text, logger)
            logger.info(f"📄 已排除参考文献部分，保留 {len(all_pages_text)} 页内容")

        for page_num, text in all_pages_text:
            page_text = f"\n--- 第 {page_num} 页 ---\n{text}"
            text_content.append(page_text)

        full_text = "\n".join(text_content)
        logger.info("✅ PDF文本提取完成:")
        logger.info(f"   - 总页数: {total_page_count}")
        logger.info(f"   - 提取页数: {num_pages}")
        logger.info(f"   - 有文本的页数: {pages_with_text}")
        logger.info(f"   - 总字符数: {len(full_text)}")
        logger.info(f"   - 纯文本字符数: {total_chars}")

        if len(full_text) < 200:
            logger.warning(f"⚠️ PDF内容提取过少({len(full_text)}字符)，可能是扫描版PDF或格式问题")
            return f"[警告] PDF内容提取过少({len(full_text)}字符)，可能是扫描版PDF。提取的内容：\n\n{full_text}"

        preview = full_text[:300].replace("\n", " ")
        logger.info(f"📄 PDF内容预览（前300字符）: {preview}...")
        return full_text
    except Exception as e:
        logger.error(f"❌ PDF提取失败: {e}")
        logger.error(traceback_module.format_exc())
        return f"[错误] PDF提取失败: {str(e)}"
