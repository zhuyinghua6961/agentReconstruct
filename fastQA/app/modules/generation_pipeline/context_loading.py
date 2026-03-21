from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.core.config import get_settings


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


def load_pdf_sentences(*, doi: str, max_pages: int, max_chars: int, logger: Any):
    logger.info("load_pdf_sentences not wired yet for doi=%s", doi)
    return None
