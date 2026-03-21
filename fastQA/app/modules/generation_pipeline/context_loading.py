from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.modules.storage.paper_storage import ensure_local_paper_pdf


def load_vector_db_topics(*, topic_index_path: str | Path | None = None, logger: Any) -> dict[str, Any] | None:
    explicit_env = str(os.getenv("TOPIC_INDEX_PATH", "") or "").strip()
    path = Path(topic_index_path or explicit_env or get_settings().topic_index_path).resolve()
    if not path.exists():
        logger.warning("vector DB topic index not found: %s", path)
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:
        logger.warning("vector DB topic index load failed: %s", exc)
        return None

    logger.info("vector DB topic index loaded: %s", path)
    return payload if isinstance(payload, dict) else None


def build_vector_db_context_for_prompt(vector_db_topics: dict[str, Any] | None) -> str:
    if not vector_db_topics:
        return ""
    top_keywords = vector_db_topics.get("top_keywords") or []
    topic_distribution = vector_db_topics.get("topic_distribution") or []
    keywords = ", ".join(str(item.get("keyword") or "").strip() for item in top_keywords[:20] if str(item.get("keyword") or "").strip())
    topics = ", ".join(
        f"{str(item.get('topic') or '').strip()}({int(item.get('doi_count') or 0)}篇)"
        for item in topic_distribution[:10]
        if str(item.get("topic") or "").strip()
    )
    return (
        f"文献总数: {int(vector_db_topics.get('total_json_files') or 0)}\n"
        f"高频关键词: {keywords}\n"
        f"主题分布: {topics}"
    ).strip()


def _split_pdf_sentences(text: str, *, limit: int = 200) -> list[str]:
    sentences = re.split(r"(?<=[。！？?!\.])\s+", str(text or ""))
    cleaned: list[str] = []
    for sentence in sentences:
        item = str(sentence or "").strip()
        if not item or len(item) < 20:
            continue
        cleaned.append(item)
        if len(cleaned) >= limit:
            break
    return cleaned


@lru_cache(maxsize=512)
def _load_pdf_sentences_cached(doi: str, max_pages: int, max_chars: int, papers_dir: str) -> tuple[str, ...] | None:
    import fitz  # type: ignore

    pdf_path = ensure_local_paper_pdf(doi=doi, papers_dir=papers_dir, logger=None)
    if not pdf_path:
        return None

    doc = fitz.open(str(pdf_path))
    try:
        texts: list[str] = []
        total_chars = 0
        num_pages = min(getattr(doc, "page_count", 0) or 0, max_pages)
        for index in range(num_pages):
            page = doc[index]
            text = str(page.get_text() or "")
            if not text.strip():
                continue
            texts.append(text)
            total_chars += len(text)
            if total_chars >= max_chars:
                break
    finally:
        close = getattr(doc, "close", None)
        if callable(close):
            close()

    if not texts:
        return None

    sentences = _split_pdf_sentences("\n".join(texts), limit=200)
    if not sentences:
        return None
    return tuple(sentences)


def load_pdf_sentences(*, doi: str, max_pages: int, max_chars: int, logger: Any):
    try:
        try:
            import fitz  # noqa: F401  # type: ignore
        except Exception:
            logger.debug("⚠️ PyMuPDF 未安装，无法从本地PDF提取句子")
            return None

        papers_dir = str(get_settings().papers_dir)
        sentences = _load_pdf_sentences_cached(str(doi or "").strip(), int(max_pages), int(max_chars), papers_dir)
        if sentences is None:
            logger.debug("📄 PDF未找到或未提取到句子: %s", doi)
            return None

        logger.info("   ℹ️ 从本地PDF提取句子: %s -> %s 条句子", doi, len(sentences))
        return list(sentences)
    except Exception as e:
        logger.debug(f"⚠️ 提取 PDF 句子失败 ({doi}): {e}")
        return None
