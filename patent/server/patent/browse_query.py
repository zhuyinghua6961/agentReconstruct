from __future__ import annotations

from typing import Literal

from server.patent.retrieval_service import _extract_identifier


def resolve_query_type(*, query: str, query_type: str) -> Literal["patent_id", "topic"]:
    normalized = str(query_type or "auto").strip().lower()
    if normalized == "patent_id":
        return "patent_id"
    if normalized == "topic":
        return "topic"
    return "patent_id" if _extract_identifier(query) else "topic"
