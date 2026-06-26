from __future__ import annotations

VALID_EVENT_TYPES = frozenset(
    {
        "ask_query",
        "file_qa",
        "literature_search",
        "patent_search",
    }
)

EVENT_DAILY_COUNT_COLUMNS = {
    "ask_query": "ask_query_count",
    "file_qa": "file_qa_count",
    "literature_search": "literature_search_count",
    "patent_search": "patent_search_count",
}

USAGE_STATS_SORT_FIELDS = frozenset(
    {
        "ask_query_count",
        "file_qa_count",
        "ask_total",
        "literature_search_count",
        "patent_search_count",
        "active_seconds",
        "last_active_at",
        "username",
    }
)

DEFAULT_USAGE_STATS_SORT_BY = "last_active_at"
DEFAULT_USAGE_STATS_SORT_ORDER = "desc"
EXPORT_USAGE_STATS_ROW_LIMIT = 50000


def normalize_event_type(value: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_EVENT_TYPES:
        return normalized
    return None


def normalize_usage_stats_sort_by(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in USAGE_STATS_SORT_FIELDS:
        return normalized
    return DEFAULT_USAGE_STATS_SORT_BY


def normalize_usage_stats_sort_order(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"asc", "desc"}:
        return normalized
    return DEFAULT_USAGE_STATS_SORT_ORDER


def should_count_search_response(*, payload: dict | None, status_code: int) -> bool:
    if int(status_code or 500) >= 400:
        return False
    if not isinstance(payload, dict):
        return False
    code = str(payload.get("code") or "").strip().upper()
    if code in {"EMBEDDING_UNAVAILABLE", "RETRIEVAL_RUNTIME_UNAVAILABLE"}:
        return False
    items = payload.get("items")
    has_items = isinstance(items, list) and len(items) > 0
    if payload.get("error") and not has_items:
        return False
    return True
