from __future__ import annotations

import logging
import re
import traceback
from typing import Any, Callable

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover - dependency guard
    fitz = None


_LOGGER = logging.getLogger("patent.file_qna.pdf_extraction")


def exclude_references_section(
    pages_text: list[tuple[int, str]],
    logger: Any,
) -> list[tuple[int, str]]:
    """Remove a likely trailing references section from extracted PDF pages."""
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

    reference_start_idx: int | None = None
    for index in range(len(pages_text) - 1, -1, -1):
        page_num, text = pages_text[index]
        text_lower = str(text or "").lower()
        for keyword in reference_keywords:
            pattern = rf"^\s*{re.escape(keyword)}\s*[：:]*\s*$"
            if keyword in text_lower and re.search(pattern, text_lower, re.MULTILINE | re.IGNORECASE):
                reference_start_idx = index
                if logger is not None:
                    logger.info(f"📄 检测到参考文献部分起始于第 {page_num} 页（关键词：{keyword}）")
                break
        if reference_start_idx is not None:
            break

    if reference_start_idx is None:
        return pages_text

    reference_page_text = "\n".join(text for _, text in pages_text[reference_start_idx:])
    doi_count = len(re.findall(r"10\.\d+/[^\s]+", reference_page_text, re.IGNORECASE))
    url_count = len(re.findall(r"https?://[^\s]+", reference_page_text, re.IGNORECASE))
    year_count = len(re.findall(r"\b(?:19|20)\d{2}\b", reference_page_text))

    if doi_count >= 3 or url_count >= 3 or (year_count >= 5 and doi_count >= 1):
        if logger is not None:
            logger.info(f"📄 确认参考文献部分：DOI={doi_count}, URL={url_count}, 年份={year_count}")
        return pages_text[:reference_start_idx]

    if logger is not None:
        logger.info("📄 疑似参考文献部分但特征不明显，保留所有页面")
    return pages_text


def extract_pdf_text(
    pdf_path: str,
    *,
    max_pages: int = 50,
    exclude_references: bool = True,
    pdf_support: bool | None = None,
    fitz_module: Any | None = None,
    logger: Any | None = None,
    traceback_module: Any = traceback,
    exclude_references_section_fn: Callable[[list[tuple[int, str]], Any], list[tuple[int, str]]] = exclude_references_section,
) -> str:
    """Extract text from a PDF while preserving page structure for file-Q&A."""
    support_enabled = ((fitz_module is not None) or (fitz is not None)) if pdf_support is None else bool(pdf_support)
    if not support_enabled:
        resolved_logger = _LOGGER if logger is None else logger
        resolved_logger.warning("⚠️ PyMuPDF未安装，无法提取PDF内容")
        return ""

    resolved_fitz = fitz if fitz_module is None else fitz_module
    resolved_logger = _LOGGER if logger is None else logger
    document = None

    try:
        document = resolved_fitz.open(pdf_path)
        text_content: list[str] = []

        metadata = document.metadata or {}
        if metadata.get("title"):
            text_content.append(f"标题: {metadata['title']}")
            resolved_logger.info(f"📄 PDF标题: {metadata['title']}")
        if metadata.get("author"):
            text_content.append(f"作者: {metadata['author']}")
            resolved_logger.info(f"📄 PDF作者: {metadata['author']}")
        total_page_count = int(document.page_count)
        num_pages = min(total_page_count, max(1, int(max_pages)))
        resolved_logger.info(f"📄 开始提取PDF文本，共{total_page_count}页，提取前{num_pages}页")

        total_chars = 0
        pages_with_text = 0
        all_pages_text: list[tuple[int, str]] = []

        for page_num in range(num_pages):
            page = document[page_num]
            text = str(page.get_text() or "")
            if not text.strip():
                continue
            pages_with_text += 1
            all_pages_text.append((page_num + 1, text))
            total_chars += len(text)
            resolved_logger.debug(f"   第{page_num + 1}页: {len(text)} 字符")

        if exclude_references and all_pages_text:
            all_pages_text = exclude_references_section_fn(all_pages_text, resolved_logger)
            resolved_logger.info(f"📄 已排除参考文献部分，保留 {len(all_pages_text)} 页内容")

        if not all_pages_text:
            resolved_logger.warning("⚠️ PDF未提取到可用正文内容")
            return ""

        text_content.append("\n" + "=" * 60 + "\n")

        for page_num, text in all_pages_text:
            text_content.append(f"\n--- 第 {page_num} 页 ---\n{text}")

        full_text = "\n".join(text_content)
        resolved_logger.info("✅ PDF文本提取完成:")
        resolved_logger.info(f"   - 总页数: {total_page_count}")
        resolved_logger.info(f"   - 提取页数: {num_pages}")
        resolved_logger.info(f"   - 有文本的页数: {pages_with_text}")
        resolved_logger.info(f"   - 总字符数: {len(full_text)}")
        resolved_logger.info(f"   - 纯文本字符数: {total_chars}")

        if len(full_text) < 200:
            resolved_logger.warning(f"⚠️ PDF内容提取过少({len(full_text)}字符)，可能是扫描版PDF或格式问题")
            return full_text

        preview = full_text[:300].replace("\n", " ")
        resolved_logger.info(f"📄 PDF内容预览（前300字符）: {preview}...")
        return full_text
    except Exception as exc:
        resolved_logger.error(f"❌ PDF提取失败: {exc}")
        resolved_logger.error(traceback_module.format_exc())
        return ""
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            close()
