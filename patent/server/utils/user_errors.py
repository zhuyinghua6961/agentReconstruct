"""User-facing error message helpers."""

from __future__ import annotations

import re
from typing import Any

CODE_MESSAGES: dict[str, str] = {
    "ASK_CANCELLED": "已取消生成",
    "INTERNAL_ERROR": "服务器内部错误",
    "UPSTREAM_TIMEOUT": "专利问答执行超时",
    "EMBEDDING_UNAVAILABLE": "语义检索依赖的向量服务不可用",
    "RETRIEVAL_RUNTIME_UNAVAILABLE": "专利检索运行时不可用",
    "PATENT_FILE_ROUTE_DISABLED": "专利文件问答路由已禁用",
    "SERVICE_NOT_READY": "专利问答服务未就绪",
    "DURABLE_MODE_DISABLED": "持久化专利模式已禁用",
    "PATENT_BUSY": "当前专利流式请求过多，请稍后重试",
    "INVALID_REQUEST": "请求参数无效",
}

_TECHNICAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"patent execution timed out", re.I), CODE_MESSAGES["UPSTREAM_TIMEOUT"]),
    (re.compile(r"internal server error", re.I), CODE_MESSAGES["INTERNAL_ERROR"]),
    (re.compile(r"cancelled", re.I), CODE_MESSAGES["ASK_CANCELLED"]),
    (re.compile(r"patent ask service is not ready", re.I), "专利问答服务未就绪"),
    (re.compile(r"patent file routes are disabled", re.I), "专利文件问答路由已禁用"),
    (re.compile(r"durable patent mode is disabled", re.I), "持久化专利模式已禁用"),
    (re.compile(r"too many running patent streams", re.I), "当前专利流式请求过多，请稍后重试"),
    (re.compile(r"request body must be valid json", re.I), "请求体必须是合法 JSON"),
    (re.compile(r"request body must be a json object", re.I), "请求体必须是 JSON 对象"),
]


def _looks_chinese(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def _is_machine_message(message: str, error: str = "") -> bool:
    raw = str(message or "").strip()
    if not raw:
        return True
    error_name = str(error or "").strip().lower()
    if error_name and raw.lower() == error_name:
        return True
    return bool(re.fullmatch(r"[a-z0-9_:-]+", raw)) and not _looks_chinese(raw)


def user_message_for_code(code: str, *, fallback: str = "") -> str:
    normalized = str(code or "").strip().upper()
    if normalized in CODE_MESSAGES:
        return CODE_MESSAGES[normalized]
    clean_fallback = str(fallback or "").strip()
    if clean_fallback and not _is_machine_message(clean_fallback):
        return clean_fallback
    return "处理失败，请稍后重试"


def humanize_exception(exc: BaseException | str | Any, *, code: str = "", error: str = "") -> str:
    text = str(exc or "").strip()
    normalized_code = str(code or "").strip().upper()
    if normalized_code in CODE_MESSAGES and (not text or _is_machine_message(text, error)):
        return CODE_MESSAGES[normalized_code]
    if text and _looks_chinese(text) and not _is_machine_message(text, error):
        return text
    for pattern, message in _TECHNICAL_PATTERNS:
        if pattern.search(text):
            return message
    if normalized_code in CODE_MESSAGES:
        return CODE_MESSAGES[normalized_code]
    return user_message_for_code(normalized_code, fallback=text)
