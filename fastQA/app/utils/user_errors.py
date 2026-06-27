"""User-facing error message helpers."""

from __future__ import annotations

import re
from typing import Any

CODE_MESSAGES: dict[str, str] = {
    "UPSTREAM_POOL_TIMEOUT": "模型连接繁忙，请稍后重试",
    "UPSTREAM_ERROR": "上游模型服务异常，请稍后重试",
    "UPSTREAM_TIMEOUT": "模型响应超时，请稍后重试",
    "LLM_UNAVAILABLE": "LLM 服务不可用",
    "EMBEDDING_UNAVAILABLE": "Embedding 模型不可用",
    "RETRIEVAL_FAILED": "文献检索失败",
    "UPSTREAM_STREAM_INTERRUPTED": "模型流式输出中断",
    "RERANK_DEGRADED": "重排序服务不可用，已按向量相似度排序继续",
    "STAGE1_JSON_INVALID": "大模型输出 json 不规范，请重试",
    "STAGE1_NO_RETRIEVAL_CLAIMS": "大模型未输出检索词，请重试",
    "STAGE2_NO_DOI": "metadata 无 doi，请重试",
    "FASTQA_NOT_READY": "快速问答生成运行时未就绪",
    "FASTQA_RUNTIME_ERROR": "快速问答执行异常，请稍后重试",
    "FASTQA_ROUTE_INVALID": "不支持的路由",
    "FASTQA_AUTHORITY_PRECONDITION_FAILED": "快速问答权限预检失败",
    "FASTQA_AUTHORITY_HTTP_ERROR": "快速问答权限服务响应异常",
    "FASTQA_AUTHORITY_UNAVAILABLE": "快速问答权限服务不可用",
    "FASTQA_AUTHORITY_CONTRACT_INVALID": "快速问答权限服务返回无效数据",
    "FILE_NOT_READY": "上传文件尚未可读，请稍后重试或刷新文件元数据",
    "EXECUTION_FILE_UNAVAILABLE": "暂时无法读取所选文件",
    "PDF_PATH_MISSING": "已选择 PDF 分支，但没有可读的 PDF 来源",
    "PDF_CONTENT_UNAVAILABLE": "PDF 内容不可用",
    "PDF_ANSWER_BACKEND_UNAVAILABLE": "PDF 作答后端不可用",
    "PDF_QA_FAILED": "PDF 问答失败",
    "MULTI_PDF_BACKEND_UNAVAILABLE": "多 PDF 后端不可用",
    "LOCAL_PDF_PATHS_DISABLED": "文件问答已禁用本地 PDF 路径，请使用 MinIO 存储的文件重试",
    "ASK_STREAM_BUSY": "当前问答请求过多，请稍后重试",
}

_TECHNICAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"upstream_pool_timeout", re.I), CODE_MESSAGES["UPSTREAM_POOL_TIMEOUT"]),
    (re.compile(r"uploaded file is not ready for direct reading yet", re.I), CODE_MESSAGES["FILE_NOT_READY"]),
    (re.compile(r"local pdf paths are disabled", re.I), CODE_MESSAGES["LOCAL_PDF_PATHS_DISABLED"]),
    (re.compile(r"pdf branch selected but no readable pdf source", re.I), CODE_MESSAGES["PDF_PATH_MISSING"]),
    (re.compile(r"pdf_content_unavailable", re.I), CODE_MESSAGES["PDF_CONTENT_UNAVAILABLE"]),
    (re.compile(r"pdf_answer_backend_unavailable", re.I), CODE_MESSAGES["PDF_ANSWER_BACKEND_UNAVAILABLE"]),
    (re.compile(r"pdf_qa_failed", re.I), CODE_MESSAGES["PDF_QA_FAILED"]),
    (re.compile(r"multi_pdf_backend_unavailable", re.I), CODE_MESSAGES["MULTI_PDF_BACKEND_UNAVAILABLE"]),
    (re.compile(r"fastqa generation runtime is not ready", re.I), CODE_MESSAGES["FASTQA_NOT_READY"]),
    (re.compile(r"fastqa authority preflight failed", re.I), CODE_MESSAGES["FASTQA_AUTHORITY_PRECONDITION_FAILED"]),
    (re.compile(r"unsupported route:", re.I), CODE_MESSAGES["FASTQA_ROUTE_INVALID"]),
    (re.compile(r"user_id in header and body are inconsistent", re.I), "请求头与请求体中的 user_id 不一致"),
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
    from app.utils.upstream_errors import UpstreamCallError, coerce_upstream_error

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
