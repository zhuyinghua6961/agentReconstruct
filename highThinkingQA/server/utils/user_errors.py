"""User-facing error message helpers."""

from __future__ import annotations

import re
from typing import Any

CODE_MESSAGES: dict[str, str] = {
    "UPSTREAM_STREAM_UNAVAILABLE": "上游流式服务暂时不可用，请稍后重试",
    "UPSTREAM_ERROR": "上游模型服务异常，请稍后重试",
    "UPSTREAM_TIMEOUT": "模型响应超时，请稍后重试",
    "UPSTREAM_POOL_TIMEOUT": "模型连接繁忙，请稍后重试",
    "LLM_UNAVAILABLE": "LLM 服务不可用",
    "EMBEDDING_UNAVAILABLE": "Embedding 模型不可用",
    "RETRIEVAL_FAILED": "文献检索失败",
    "UPSTREAM_STREAM_INTERRUPTED": "模型流式输出中断",
    "RERANK_DEGRADED": "重排序服务不可用，已按向量相似度排序继续",
    "ASK_CANCELLED": "已取消生成",
    "INTERNAL_ERROR": "服务器内部错误",
    "NOT_IMPLEMENTED": "该模式尚未实现",
    "MODE_NOT_SUPPORTED": "不支持的模式",
    "MODE_MISMATCH": "请求模式不匹配",
    "INVALID_REQUEST": "请求参数无效",
}

_TECHNICAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"upstream_pool_timeout", re.I), CODE_MESSAGES["UPSTREAM_POOL_TIMEOUT"]),
    (re.compile(r"upstream model timeout", re.I), CODE_MESSAGES["UPSTREAM_TIMEOUT"]),
    (re.compile(r"upstream_error", re.I), CODE_MESSAGES["UPSTREAM_ERROR"]),
    (re.compile(r"empty execution result", re.I), "执行未产生有效结果"),
    (re.compile(r"too many running requests", re.I), "当前并发请求过多，请稍后重试"),
    (re.compile(r"user_id in token and body are inconsistent", re.I), "令牌与请求体中的 user_id 不一致"),
    (re.compile(r"unsupported mode:", re.I), "不支持的模式"),
    (re.compile(r"mode not implemented yet:", re.I), "模式尚未实现"),
    (re.compile(r"internal server error", re.I), CODE_MESSAGES["INTERNAL_ERROR"]),
    (re.compile(r"cancelled", re.I), CODE_MESSAGES["ASK_CANCELLED"]),
    (re.compile(r"read timed out|readtimeout", re.I), CODE_MESSAGES["UPSTREAM_TIMEOUT"]),
    (re.compile(r"connect timeout|connection timed out", re.I), CODE_MESSAGES["UPSTREAM_TIMEOUT"]),
    (re.compile(r"connection refused|failed to establish a new connection", re.I), "无法连接上游服务，请稍后重试"),
    (re.compile(r"pool timeout|pooltimeout", re.I), CODE_MESSAGES["UPSTREAM_POOL_TIMEOUT"]),
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


def build_upstream_error_message(component: str, *, status_code: int | None = None, detail: str = "") -> str:
    component_key = str(component or "").strip().lower()
    if component_key == "llm":
        base = CODE_MESSAGES["LLM_UNAVAILABLE"]
    elif component_key == "embedding":
        base = CODE_MESSAGES["EMBEDDING_UNAVAILABLE"]
    elif component_key == "retrieval":
        base = CODE_MESSAGES["RETRIEVAL_FAILED"]
    elif component_key == "stream":
        base = CODE_MESSAGES["UPSTREAM_STREAM_INTERRUPTED"]
    elif component_key == "rerank":
        base = CODE_MESSAGES["RERANK_DEGRADED"]
    else:
        base = CODE_MESSAGES["UPSTREAM_ERROR"]
    if status_code is not None:
        base = f"{base}（HTTP {int(status_code)}）"
    clean_detail = str(detail or "").strip()
    if clean_detail and _looks_chinese(clean_detail) and clean_detail not in base:
        return f"{base}：{clean_detail}"
    return base


def humanize_exception(exc: BaseException | str | Any, *, code: str = "", error: str = "") -> str:
    from server.utils.upstream_errors import UpstreamCallError, coerce_upstream_error

    if isinstance(exc, UpstreamCallError):
        return str(exc.message)
    coerced = coerce_upstream_error(exc)
    if coerced is not None:
        return str(coerced.message)
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
