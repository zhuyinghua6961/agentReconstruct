from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import Any, Callable
from collections import Counter

import httpx

from server.patent.pdf_contract import (
    CompareBudgetError,
    MULTI_DOC_HEADER_PATTERN,
    PDF_QA_SYSTEM_MESSAGE,
    build_compare_failure_message,
    build_extractive_fallback_summary,
    build_kb_section,
    build_patent_pdf_answer_prompt,
    detect_targeted_document_index,
    format_multi_pdf_sections,
    is_compare_question,
    is_summary_question,
    smart_truncate_pdf_content,
    validate_compare_context,
)
from server.patent.file_models import PatentFileContract
from server.patent.streaming import emit_text_chunks, iter_text_output
from server.services.mode_profiles import get_patent_mode_profile

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover - dependency guard
    fitz = None


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return str(default or "").strip()


_WHITESPACE_PATTERN = re.compile(r"\s+")
_KB_BOUNDARY_PLACEHOLDER = "当前无额外知识库验证结果。"


def _collapse_whitespace(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", str(value or "")).strip()


def _truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _find_markdown_support_points(text: str, *, max_items: int = 3, min_chars: int = 18) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw_items = re.split(r"(?<=[。！？.!?])\s+|\n+", normalized)
    points: list[str] = []
    for item in raw_items:
        line = _collapse_whitespace(re.sub(r"^[#>\-\*\d\.\)\s]+", "", item))
        if len(line) < min_chars:
            continue
        if line in points:
            continue
        points.append(_truncate(line, 220))
        if len(points) >= max_items:
            break
    return points


def _find_section_position(text: str, patterns: tuple[str, ...], *, last_end: int) -> int:
    normalized = str(text or "")
    best_position = -1
    best_end = -1
    for pattern in patterns:
        matched = re.search(pattern, normalized, flags=re.MULTILINE | re.IGNORECASE)
        if matched is None:
            continue
        if matched.start() <= last_end:
            continue
        if best_position < 0 or matched.start() < best_position:
            best_position = matched.start()
            best_end = matched.end()
    return best_position if best_end >= 0 else -1


def _has_fastqa_summary_sections(text: str) -> bool:
    normalized = str(text or "")
    patterns = (
        (r"(^|\n)\s*(?:#{1,6}\s*)?结论\s*[：:]?",),
        (r"(^|\n)\s*(?:#{1,6}\s*)?证据\s*[：:]?",),
        (r"(^|\n)\s*(?:#{1,6}\s*)?对比\s*[：:]?",),
        (r"(^|\n)\s*(?:#{1,6}\s*)?限制\s*[：:]?",),
    )
    last_end = -1
    for group in patterns:
        position = _find_section_position(normalized, group, last_end=last_end)
        if position < 0:
            return False
        last_end = position
    return True


def _ensure_fastqa_pdf_summary_structure(
    *,
    answer: str,
    prepared_pdf_text: str,
    include_kb: bool,
    route_hint: str = "pdf_qa",
    source_scope: str = "pdf",
) -> str:
    normalized_answer = str(answer or "").strip()
    if not normalized_answer:
        return normalized_answer
    if _has_fastqa_summary_sections(normalized_answer):
        return normalized_answer

    evidence_points = _find_markdown_support_points(prepared_pdf_text, max_items=3)
    if not evidence_points:
        evidence_points = _find_markdown_support_points(normalized_answer, max_items=3, min_chars=10)
    if not evidence_points:
        evidence_points = ["当前可读原文证据有限，仅能保留模型回答中的主结论。"]

    hybrid_mode = str(route_hint or "pdf_qa").strip().lower() == "hybrid_qa"
    normalized_scope = str(source_scope or "pdf").strip() or "pdf"
    comparison_lines = (
        [
            "- 当前为混合问答中的 PDF 证据子结论；可用于后续与表格或知识库交叉验证，不能单独替代全局综合结论。",
            f"- 当前 source_scope={normalized_scope}；本段只描述这份 PDF 原文能够直接支持的对照点。",
        ]
        if hybrid_mode
        else ["- PDF中未提供跨文献对比对象；当前回答仅基于单篇文件证据。"]
    )
    limitation_lines = (
        [
            "- 当前结论仅基于本次上传 PDF 的可读原文整理，仍需与其他已选文件或知识库证据综合判断。",
            (
                "- 知识库若参与，仅可用于验证已在 PDF 中出现的内容，不能补充新的文件结论。"
                if include_kb
                else "- 当前未引入知识库补充；若后续纳入其他来源，综合结论可能继续收敛。"
            ),
        ]
        if hybrid_mode
        else [
            "- 当前结论仅基于本次上传 PDF 的可读原文整理，未引入文件外新证据。",
            (
                "- 知识库若参与，仅可用于验证已在 PDF 中出现的内容，不能补充新的文件结论。"
                if include_kb
                else "- 当前未引入知识库补充，本回答不代表跨来源统一结论。"
            ),
        ]
    )

    sections = [
        "## 结论",
        normalized_answer,
        "",
        "## 证据",
        *[f"- {item}" for item in evidence_points],
        "",
        "## 对比",
        *comparison_lines,
        "",
        "## 限制",
        *limitation_lines,
    ]
    return "\n".join(sections).strip()


class _NoopLogger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None


class PatentPdfAnswerClient:
    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_seconds: float = 30.0) -> None:
        self._api_key = str(api_key or "").strip()
        self._base_url = str(base_url or "").strip()
        self._model = str(model or "").strip()
        self._timeout_seconds = float(timeout_seconds)
        self._client = httpx.Client(timeout=self._timeout_seconds)

    @classmethod
    def from_env(cls) -> "PatentPdfAnswerClient | None":
        use_shared_env = _env_flag("PATENT_OPENAI_USE_SHARED_ENV", default=False)
        api_key = _first_env(
            "PATENT_OPENAI_API_KEY",
            default=(os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")) if use_shared_env else "",
        )
        base_url = _first_env(
            "PATENT_OPENAI_BASE_URL",
            default=(os.getenv("OPENAI_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL")) if use_shared_env else "",
        )
        model = _first_env(
            "PATENT_OPENAI_MODEL",
            default=(os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL")) if use_shared_env else "",
        )
        if not api_key or not base_url or not model:
            return None
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=float(str(os.getenv("PATENT_OPENAI_TIMEOUT_SECONDS") or "30").strip()),
        )

    def close(self) -> None:
        self._client.close()

    def _build_request_payload(
        self,
        *,
        question: str,
        pdf_text: str,
        file_name: str,
        include_kb: bool,
        stream: bool,
        selected_file_labels: list[str] | None = None,
        route_hint: str = "pdf_qa",
        source_scope: str = "pdf",
    ) -> dict[str, Any]:
        labels = [str(item).strip() for item in list(selected_file_labels or []) if str(item).strip()]
        compare_mode = is_compare_question(question, selected_pdf_count=len(labels) or 1)
        kb_section = build_kb_section({"kb_answer": _KB_BOUNDARY_PLACEHOLDER}) if include_kb else ""
        prompt = build_patent_pdf_answer_prompt(
            question=question,
            pdf_content=pdf_text,
            kb_section=kb_section,
            is_summary=is_summary_question(question),
            is_compare=compare_mode,
            selected_file_labels=labels or [str(file_name or "").strip() or "unknown.pdf"],
            route_hint=route_hint,
            source_scope=source_scope,
        )
        return {
            "model": self._model,
            "temperature": 0.2,
            "stream": bool(stream),
            "messages": [
                {
                    "role": "system",
                    "content": PDF_QA_SYSTEM_MESSAGE,
                },
                {"role": "user", "content": prompt},
            ],
        }

    @staticmethod
    def _extract_delta_text(payload: dict[str, Any]) -> str:
        choices = list(payload.get("choices") or [])
        pieces: list[str] = []
        for choice in choices:
            delta = dict((choice or {}).get("delta") or {})
            content = delta.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = str(item.get("text") or "")
                        if text:
                            pieces.append(text)
                continue
            text = str(content or "")
            if text:
                pieces.append(text)
        return "".join(pieces)

    def stream_answer(
        self,
        *,
        question: str,
        pdf_text: str,
        file_name: str,
        include_kb: bool,
        selected_file_labels: list[str] | None = None,
        route_hint: str = "pdf_qa",
        source_scope: str = "pdf",
    ) -> Any:
        request_payload = self._build_request_payload(
            question=question,
            pdf_text=pdf_text,
            file_name=file_name,
            include_kb=include_kb,
            stream=True,
            selected_file_labels=selected_file_labels,
            route_hint=route_hint,
            source_scope=source_scope,
        )
        with self._client.stream(
            "POST",
            f"{self._base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=request_payload,
        ) as response:
            response.raise_for_status()
            for raw_line in response.iter_lines():
                line = str(raw_line or "").strip()
                if not line or not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if not body or body == "[DONE]":
                    continue
                payload = json.loads(body)
                if isinstance(payload, dict) and payload.get("error"):
                    message = str(dict(payload.get("error") or {}).get("message") or "patent_pdf_stream_error").strip()
                    raise RuntimeError(message)
                if not isinstance(payload, dict):
                    continue
                text = self._extract_delta_text(payload)
                if text:
                    yield text

    def answer(
        self,
        *,
        question: str,
        pdf_text: str,
        file_name: str,
        include_kb: bool,
        selected_file_labels: list[str] | None = None,
        route_hint: str = "pdf_qa",
        source_scope: str = "pdf",
    ) -> str:
        response = self._client.post(
            f"{self._base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=self._build_request_payload(
                question=question,
                pdf_text=pdf_text,
                file_name=file_name,
                include_kb=include_kb,
                stream=False,
                selected_file_labels=selected_file_labels,
                route_hint=route_hint,
                source_scope=source_scope,
            ),
        )
        response.raise_for_status()
        payload = response.json()
        choices = list(payload.get("choices") or [])
        message = dict((choices[0] or {}).get("message") or {}) if choices else {}
        return str(message.get("content") or "").strip()


class PatentPdfService:
    def __init__(
        self,
        *,
        extract_pdf_text_fn: Callable[..., str] | None = None,
        answer_question_fn: Callable[..., str] | None = None,
        max_pdf_pages: int = 10,
        max_pdf_chars: int = 12000,
    ) -> None:
        self._extract_pdf_text_fn = extract_pdf_text_fn or self._extract_pdf_text
        self._answer_question_fn = answer_question_fn
        self._client = None if answer_question_fn is not None else PatentPdfAnswerClient.from_env()
        self._max_pdf_pages = max(1, int(max_pdf_pages))
        self._max_pdf_chars = max(1000, int(max_pdf_chars))

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def execute(
        self,
        *,
        contract: PatentFileContract,
        include_kb: bool,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        content_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        pdf_execution_files = [item for item in contract.selected_execution_files if item.family == "pdf"]
        used_files = [item.as_payload() for item in pdf_execution_files]
        profile = get_patent_mode_profile(contract.route)
        selected_labels = [
            str(item.get("file_name") or f"file:{item.get('file_id') or 'unknown'}").strip()
            for item in used_files
        ]
        compare_mode = is_compare_question(contract.question, selected_pdf_count=len(selected_labels))
        targeted_doc_index = None if compare_mode else detect_targeted_document_index(
            contract.question,
            selected_pdf_count=len(selected_labels),
            selected_file_labels=selected_labels,
        )
        candidate_pdf_files = (
            self._select_targeted_execution_files(pdf_execution_files=pdf_execution_files, target_index=targeted_doc_index)
            if targeted_doc_index is not None
            else list(pdf_execution_files)
        )
        steps: list[dict[str, Any]] = []
        prepared_for_generation = ""

        self._record_step(
            steps,
            progress_callback=progress_callback,
            payload={
                "step": "pdf_extract",
                "title": "分析 PDF 原文",
                "message": "📄 正在分析上传的PDF文献...",
                "status": "running",
                "data": {"count": len(used_files)},
            },
        )

        pdf_documents = self._load_pdf_documents(execution_files=candidate_pdf_files)
        if targeted_doc_index is not None:
            selected_labels = self._select_targeted_labels(selected_labels=selected_labels, target_index=targeted_doc_index)
        pdf_text = format_multi_pdf_sections(pdf_documents)
        available_labels = [str(item.get("label") or "").strip() for item in pdf_documents if str(item.get("label") or "").strip()]
        if pdf_text:
            self._record_step(
                steps,
                progress_callback=progress_callback,
                payload={
                    "step": "pdf_extract",
                    "title": "分析 PDF 原文",
                    "message": f"📄 已完成 PDF 原文提取，共 {len(used_files)} 个文件，正文 chars={len(pdf_text)}",
                    "status": "success",
                    "data": {"count": len(used_files), "chars": len(pdf_text)},
                },
            )
            if compare_mode:
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "multi_pdf_compare",
                        "title": "准备多文献比较",
                        "message": f"🔍 已识别多文献比较请求，正在准备 {len(available_labels)} 篇文献证据...",
                        "status": "running",
                        "data": {"count": len(available_labels)},
                    },
                )
            prepared = self._prepare_answer_input(
                question=contract.question,
                pdf_text=pdf_text,
                pdf_documents=pdf_documents,
                selected_file_labels=selected_labels,
                available_file_labels=available_labels,
                compare_mode=compare_mode,
            )
            prepared_for_generation = str(prepared.get("prepared_pdf_text") or "")
            if compare_mode:
                compare_status = "success" if prepared["ok"] else "error"
                compare_message = (
                    f"🔍 已完成多文献比较证据准备，共 {len(available_labels)} 篇文献"
                    if prepared["ok"]
                    else f"🔍 多文献比较准备失败：{prepared['failure_reason']}"
                )
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "multi_pdf_compare",
                        "title": "准备多文献比较",
                        "message": compare_message,
                        "status": compare_status,
                        "error": None if prepared["ok"] else str(prepared["failure_reason"]),
                        "data": {"count": len(available_labels)},
                    },
                )
            if prepared["ok"]:
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_answer",
                        "title": "生成文件答案",
                        "message": "✍️ 正在基于 PDF 原文生成比较答案..." if compare_mode else "✍️ 正在基于 PDF 原文生成答案...",
                        "status": "running",
                    },
                )
                rendered = self._render_answer(
                    question=contract.question,
                    prepared_pdf_text=str(prepared["prepared_pdf_text"]),
                    file_name=", ".join(selected_labels) if len(selected_labels) > 1 else (selected_labels[0] if selected_labels else "unknown.pdf"),
                    selected_file_labels=selected_labels,
                    available_file_labels=available_labels,
                    include_kb=include_kb,
                    compare_mode=compare_mode,
                    route_hint=contract.route,
                    source_scope=contract.source_scope,
                    content_callback=content_callback,
                )
                answer_text = str(rendered["answer_text"])
                answer_mode = str(rendered["answer_mode"])
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_answer",
                        "title": "生成文件答案",
                        "message": (
                            "✍️ 已基于 PDF 原文生成比较答案"
                            if rendered["ok"] and compare_mode
                            else "✍️ 已基于 PDF 原文生成答案"
                            if rendered["ok"]
                            else "✍️ 多文献比较失败，已返回明确失败说明"
                            if compare_mode
                            else "✍️ 文件答案生成失败"
                        ),
                        "status": "success" if rendered["ok"] else "error",
                        "error": None if rendered["ok"] else str(rendered["failure_reason"]),
                    },
                )
                if rendered["ok"] and rendered.get("emit_after_steps") and callable(content_callback):
                    emit_text_chunks(answer_text, content_callback=content_callback)
                if not rendered["ok"] and rendered.get("stream_after_steps") and callable(content_callback):
                    emit_text_chunks(answer_text, content_callback=content_callback)
            else:
                answer_text = str(prepared["answer_text"])
                answer_mode = str(prepared["answer_mode"])
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_answer",
                        "title": "生成文件答案",
                        "message": "✍️ 多文献比较失败，已返回明确失败说明" if compare_mode else "✍️ 文件答案生成失败",
                        "status": "error",
                        "error": str(prepared["failure_reason"]),
                    },
                )
                if callable(content_callback):
                    emit_text_chunks(answer_text, content_callback=content_callback)
        else:
            answer_mode = "pdf_compare_unavailable" if compare_mode else "pdf_text_unavailable"
            answer_text = (
                build_compare_failure_message(
                    question=contract.question,
                    available_docs=[],
                    missing_docs=selected_labels,
                    reason="当前未拿到可读的 PDF 原文内容",
                )
                if compare_mode
                else "当前未拿到可读的 PDF 原文内容，无法生成基于正文的总结。请稍后重试或检查文件处理状态。"
            )
            if compare_mode:
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_extract",
                        "title": "分析 PDF 原文",
                        "message": f"📄 未拿到可读的 PDF 原文内容，当前选择文件数 {len(used_files)}",
                        "status": "error",
                        "error": "当前未拿到可读的 PDF 原文内容",
                        "data": {"count": len(used_files), "chars": 0},
                    },
                )
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "multi_pdf_compare",
                        "title": "准备多文献比较",
                        "message": f"🔍 已识别多文献比较请求，正在准备 {len(used_files)} 篇文献证据...",
                        "status": "running",
                        "data": {"count": len(used_files)},
                    },
                )
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "multi_pdf_compare",
                        "title": "准备多文献比较",
                        "message": "🔍 多文献比较准备失败：当前未拿到可读的 PDF 原文内容",
                        "status": "error",
                        "error": "当前未拿到可读的 PDF 原文内容",
                        "data": {"count": len(used_files)},
                    },
                )
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_answer",
                        "title": "生成文件答案",
                        "message": "✍️ 多文献比较失败，已返回明确失败说明",
                        "status": "error",
                        "error": "当前未拿到可读的 PDF 原文内容",
                    },
                )
                if callable(content_callback):
                    emit_text_chunks(answer_text, content_callback=content_callback)
            else:
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_extract",
                        "title": "分析 PDF 原文",
                        "message": f"📄 未拿到可读的 PDF 原文内容，当前选择文件数 {len(used_files)}",
                        "status": "success",
                        "data": {"count": len(used_files), "chars": 0},
                    },
                )
                self._record_step(
                    steps,
                    progress_callback=progress_callback,
                    payload={
                        "step": "pdf_answer",
                        "title": "生成文件答案",
                        "message": "✍️ 已返回文件不可读的说明",
                        "status": "success",
                    },
                )
                if callable(content_callback):
                    emit_text_chunks(answer_text, content_callback=content_callback)
        return {
            "handler": "pdf",
            "answer_text": answer_text,
            "route": contract.route,
            "query_mode": profile.query_mode,
            "source_scope": contract.source_scope,
            "steps": [dict(item) for item in steps],
            "metadata": {
                "handler": "pdf",
                "source_scope": contract.source_scope,
                "selected_file_count": len(used_files),
                "kb_enabled": bool(include_kb),
                "answer_mode": answer_mode,
                "pdf_text_chars": len(pdf_text),
                "pdf_evidence_context": str(prepared_for_generation or pdf_text or "")[:1200],
                "prepared_pdf_text": str(prepared_for_generation or ""),
                "steps": [dict(item) for item in steps],
            },
            "timings": {
                "patent_pdf_route_ms": 1,
            },
            "used_files": used_files,
            "selected_file_ids": list(contract.selected_file_ids),
            "file_selection": dict(contract.file_selection),
            "kb_enabled": bool(include_kb),
        }

    @staticmethod
    def _record_step(
        steps: list[dict[str, Any]],
        *,
        payload: dict[str, Any],
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        normalized = dict(payload or {})
        step_key = str(normalized.get("step") or "").strip()
        if step_key:
            for index, existing in enumerate(steps):
                if str(existing.get("step") or "").strip() == step_key:
                    merged = dict(existing)
                    merged.update(normalized)
                    steps[index] = merged
                    break
            else:
                steps.append(normalized)
        else:
            steps.append(normalized)
        if callable(progress_callback):
            progress_callback(dict(normalized))

    def _load_pdf_documents(self, *, execution_files: list[Any]) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        for item in execution_files:
            local_path = str(item.payload.get("local_path") or "").strip()
            if not local_path:
                continue
            resolved = Path(local_path)
            if not resolved.exists() or not resolved.is_file():
                continue
            extracted = str(
                self._extract_pdf_text_fn(
                    str(resolved),
                    max_pages=self._max_pdf_pages,
                )
                or ""
            ).strip()
            if not extracted:
                continue
            label = str(item.file_name or resolved.name or f"file:{item.file_id}")
            sections.append({"label": label, "text": extracted})
        return sections

    def _prepare_answer_input(
        self,
        *,
        question: str,
        pdf_text: str,
        pdf_documents: list[dict[str, str]],
        selected_file_labels: list[str],
        available_file_labels: list[str],
        compare_mode: bool,
    ) -> dict[str, Any]:
        summary_mode = is_summary_question(question)
        missing_labels = [label for label in selected_file_labels if label not in set(available_file_labels)]

        if compare_mode and (len(available_file_labels) < 2 or missing_labels):
            message = build_compare_failure_message(
                question=question,
                available_docs=available_file_labels,
                missing_docs=missing_labels,
                reason="参与比较的文献正文不完整",
            )
            return {
                "ok": False,
                "prepared_pdf_text": "",
                "answer_text": message,
                "answer_mode": "pdf_compare_unavailable",
                "failure_reason": "参与比较的文献正文不完整",
            }

        try:
            prepared_pdf_text = smart_truncate_pdf_content(
                pdf_text,
                self._max_pdf_chars,
                logger=_NoopLogger(),
                is_summary=summary_mode,
                question=question,
                is_compare=compare_mode,
            )
        except CompareBudgetError as exc:
            message = build_compare_failure_message(
                question=question,
                available_docs=available_file_labels,
                missing_docs=missing_labels,
                reason=str(exc),
            )
            return {
                "ok": False,
                "prepared_pdf_text": "",
                "answer_text": message,
                "answer_mode": "pdf_compare_unavailable",
                "failure_reason": str(exc),
            }

        if compare_mode:
            try:
                validate_compare_context(prepared_pdf_text, pdf_documents)
            except CompareBudgetError as exc:
                message = build_compare_failure_message(
                    question=question,
                    available_docs=available_file_labels,
                    missing_docs=missing_labels,
                    reason=str(exc),
                )
                return {
                    "ok": False,
                    "prepared_pdf_text": "",
                    "answer_text": message,
                    "answer_mode": "pdf_compare_unavailable",
                    "failure_reason": str(exc),
                }

        return {
            "ok": True,
            "prepared_pdf_text": prepared_pdf_text,
            "answer_text": "",
            "answer_mode": "pdf_text_compare" if compare_mode else "pdf_text_summary",
            "failure_reason": "",
        }

    def _render_answer(
        self,
        *,
        question: str,
        prepared_pdf_text: str,
        file_name: str,
        selected_file_labels: list[str],
        available_file_labels: list[str],
        include_kb: bool,
        compare_mode: bool,
        route_hint: str,
        source_scope: str,
        content_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        answer_parts: list[str] = []
        missing_labels = [label for label in selected_file_labels if label not in set(available_file_labels)]
        live_streamed = False
        stream_mode = "unknown"
        streamed_text = ""
        pending_stream_whitespace = ""
        prompt = build_patent_pdf_answer_prompt(
            question=question,
            pdf_content=prepared_pdf_text,
            kb_section=build_kb_section({"kb_answer": _KB_BOUNDARY_PLACEHOLDER}) if include_kb else "",
            is_summary=is_summary_question(question),
            is_compare=compare_mode,
            selected_file_labels=selected_file_labels or [str(file_name or "").strip() or "unknown.pdf"],
            route_hint=route_hint,
            source_scope=source_scope,
        )

        def _emit_stream_piece(piece: str) -> None:
            nonlocal live_streamed, stream_mode, streamed_text, pending_stream_whitespace
            text = str(piece or "")
            if not text:
                return
            answer_parts.append(text)
            if compare_mode or not callable(content_callback):
                return

            def _emit_live_text(raw_text: str) -> None:
                nonlocal live_streamed, streamed_text, pending_stream_whitespace
                candidate = f"{pending_stream_whitespace}{raw_text}"
                pending_stream_whitespace = ""
                normalized_emit = candidate.rstrip()
                pending_stream_whitespace = candidate[len(normalized_emit) :]
                if not normalized_emit:
                    return
                content_callback(normalized_emit)
                streamed_text += normalized_emit
                live_streamed = True

            if stream_mode == "unknown":
                buffered_text = "".join(answer_parts)
                normalized_stream_text = buffered_text.lstrip()
                normalized_buffer = "".join(answer_parts).lstrip()
                if not normalized_buffer:
                    pending_stream_whitespace = ""
                    return
                looks_like_heading_prefix = bool(
                    normalized_buffer.startswith("##")
                    or re.match(r"^(?:#{1,6}\s*)?(?:结论|证据|对比|限制)\b", normalized_buffer)
                )
                if _has_fastqa_summary_sections(normalized_buffer):
                    stream_mode = "raw_structured"
                    _emit_live_text(normalized_stream_text)
                    return
                elif not looks_like_heading_prefix or len(answer_parts) >= 2 or len(normalized_buffer) >= 120:
                    stream_mode = "wrapped_summary"
                    prefix = "## 结论\n"
                    content_callback(prefix)
                    streamed_text += prefix
                    if normalized_stream_text:
                        _emit_live_text(normalized_stream_text)
                    return
                else:
                    return
            _emit_live_text(text)

        def _buffer_text(text: str) -> str:
            return str(text or "").strip()

        if callable(self._answer_question_fn):
            output = self._answer_question_fn(
                question=question,
                pdf_text=prepared_pdf_text,
                file_name=file_name,
                include_kb=include_kb,
                prompt=prompt,
                route_hint=route_hint,
                source_scope=source_scope,
            )
            if isinstance(output, (str, bytes)):
                answer = _buffer_text(str(output or ""))
                if answer:
                    if compare_mode:
                        answer = _ensure_compare_answer_structure(answer=answer, prepared_pdf_text=prepared_pdf_text)
                    else:
                        answer = _ensure_fastqa_pdf_summary_structure(
                            answer=answer,
                            prepared_pdf_text=prepared_pdf_text,
                            include_kb=include_kb,
                            route_hint=route_hint,
                            source_scope=source_scope,
                        )
                    return {
                        "ok": True,
                        "answer_text": answer,
                        "answer_mode": "pdf_text_compare" if compare_mode else "pdf_text_summary",
                        "failure_reason": "",
                        "emit_after_steps": not live_streamed,
                        "stream_after_steps": False,
                    }
            else:
                try:
                    for piece in iter_text_output(output):
                        _emit_stream_piece(piece)
                except Exception:
                    answer_parts = []
        elif self._client is not None:
            try:
                stream_builder = getattr(self._client, "stream_answer", None)
                if callable(stream_builder):
                    for piece in iter_text_output(
                        stream_builder(
                            question=question,
                            pdf_text=prepared_pdf_text,
                            file_name=file_name,
                            include_kb=include_kb,
                            selected_file_labels=selected_file_labels,
                            route_hint=route_hint,
                            source_scope=source_scope,
                        )
                    ):
                        _emit_stream_piece(piece)
            except Exception:
                answer_parts = []
            if not "".join(answer_parts).strip():
                try:
                    answer = _buffer_text(
                        self._client.answer(
                            question=question,
                            pdf_text=prepared_pdf_text,
                            file_name=file_name,
                            include_kb=include_kb,
                            selected_file_labels=selected_file_labels,
                            route_hint=route_hint,
                            source_scope=source_scope,
                        )
                    )
                    if answer:
                        if compare_mode:
                            answer = _ensure_compare_answer_structure(answer=answer, prepared_pdf_text=prepared_pdf_text)
                        else:
                            answer = _ensure_fastqa_pdf_summary_structure(
                                answer=answer,
                                prepared_pdf_text=prepared_pdf_text,
                                include_kb=include_kb,
                                route_hint=route_hint,
                                source_scope=source_scope,
                            )
                        return {
                            "ok": True,
                            "answer_text": answer,
                            "answer_mode": "pdf_text_compare" if compare_mode else "pdf_text_summary",
                            "failure_reason": "",
                            "emit_after_steps": not live_streamed,
                            "stream_after_steps": False,
                        }
                except Exception:
                    answer_parts = []

        answer = "".join(answer_parts).strip()
        if answer:
            if compare_mode:
                answer = _ensure_compare_answer_structure(answer=answer, prepared_pdf_text=prepared_pdf_text)
            else:
                answer = _ensure_fastqa_pdf_summary_structure(
                    answer=answer,
                    prepared_pdf_text=prepared_pdf_text,
                    include_kb=include_kb,
                    route_hint=route_hint,
                    source_scope=source_scope,
                )
            if callable(content_callback) and not compare_mode and answer_parts:
                if not live_streamed:
                    emit_text_chunks(answer, content_callback=content_callback)
                    live_streamed = True
                    streamed_text = answer
                elif answer.startswith(streamed_text):
                    suffix = answer[len(streamed_text) :]
                    if suffix:
                        emit_text_chunks(suffix, content_callback=content_callback)
                    streamed_text = answer
            return {
                "ok": True,
                "answer_text": answer,
                "answer_mode": "pdf_text_compare" if compare_mode else "pdf_text_summary",
                "failure_reason": "",
                "emit_after_steps": not live_streamed,
                "stream_after_steps": False,
            }

        fallback = (
            build_compare_failure_message(
                question=question,
                available_docs=available_file_labels,
                missing_docs=missing_labels,
                reason="模型未返回可用的比较结果",
            )
            if compare_mode
            else _ensure_fastqa_pdf_summary_structure(
                answer=build_extractive_fallback_summary(question=question, pdf_text=prepared_pdf_text),
                prepared_pdf_text=prepared_pdf_text,
                include_kb=include_kb,
                route_hint=route_hint,
                source_scope=source_scope,
            )
        )
        if compare_mode:
            return {
                "ok": False,
                "answer_text": fallback,
                "answer_mode": "pdf_compare_unavailable",
                "failure_reason": "模型未返回可用的比较结果",
                "stream_after_steps": True,
            }
        emit_text_chunks(fallback, content_callback=content_callback)
        return {
            "ok": True,
            "answer_text": fallback,
            "answer_mode": "pdf_text_summary",
            "failure_reason": "",
            "emit_after_steps": True,
            "stream_after_steps": False,
        }

    @staticmethod
    def _extract_pdf_text(pdf_path: str, *, max_pages: int = 10) -> str:
        if fitz is None:
            return ""
        doc = fitz.open(pdf_path)
        try:
            page_count = min(int(doc.page_count), max(1, int(max_pages)))
            chunks: list[str] = []
            metadata = doc.metadata or {}
            title = _collapse_whitespace(str(metadata.get("title") or ""))
            if title:
                chunks.append(f"标题: {title}")
            for page_index in range(page_count):
                text = _collapse_whitespace(doc[page_index].get_text())
                if not text:
                    continue
                chunks.append(text)
            return "\n".join(chunks).strip()
        finally:
            doc.close()

    @staticmethod
    def _select_targeted_execution_files(*, pdf_execution_files: list[Any], target_index: int | None) -> list[Any]:
        if target_index is None:
            return list(pdf_execution_files)
        if target_index < 0 or target_index >= len(pdf_execution_files):
            return list(pdf_execution_files[:1])
        return [pdf_execution_files[target_index]]

    @staticmethod
    def _select_targeted_labels(*, selected_labels: list[str], target_index: int) -> list[str]:
        if target_index < 0 or target_index >= len(selected_labels):
            return list(selected_labels[:1])
        return [str(selected_labels[target_index]).strip()]


def _ensure_compare_answer_structure(*, answer: str, prepared_pdf_text: str) -> str:
    normalized_answer = str(answer or "").strip()
    if not normalized_answer:
        return normalized_answer
    document_summaries = _extract_document_summaries(prepared_pdf_text=prepared_pdf_text)
    if _has_ordered_compare_sections(normalized_answer) and _has_compare_document_coverage(
        answer=normalized_answer,
        document_summaries=document_summaries,
    ):
        return normalized_answer
    if not document_summaries:
        return normalized_answer

    doc_count = len(document_summaries)
    outlines = "\n".join(
        f"{index}. {label}：{summary}"
        for index, (label, summary) in enumerate(document_summaries, start=1)
    )
    shared_points = (
        f"这 {doc_count} 篇文献都提供了可比较的原文证据，可围绕研究目标、方法、结果和结论进行对照。"
        if doc_count > 1
        else "该文献提供了可比较的原文证据。"
    )
    summary_line = _first_sentence(normalized_answer) or "综合来看，所选文献在关键结果和结论上存在可识别差异。"
    return (
        "各自概要：\n"
        f"{outlines}\n\n"
        "相同点：\n"
        f"- {shared_points}\n\n"
        "差异点：\n"
        f"- {normalized_answer}\n\n"
        "总结：\n"
        f"{summary_line}"
    ).strip()


def _extract_document_summaries(*, prepared_pdf_text: str) -> list[tuple[str, str]]:
    matches = list(MULTI_DOC_HEADER_PATTERN.finditer(str(prepared_pdf_text or "")))
    if not matches:
        return []
    summaries: list[tuple[str, str]] = []
    source_text = str(prepared_pdf_text or "")
    for index, matched in enumerate(matches):
        start = matched.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source_text)
        header = str(matched.group(0) or "").strip().strip("=")
        label = header.split(":", 1)[1].strip() if ":" in header else f"文献 {index + 1}"
        body = re.sub(r"\s+", " ", source_text[start:end]).strip()
        body = re.sub(r"\[注意：.*?\]$", "", body).strip()
        snippet = body[:180].rstrip("，,；; ")
        if len(body) > 180:
            snippet += "…"
        if label and snippet:
            summaries.append((label, snippet))
    return summaries


def _first_sentence(text: str) -> str:
    for raw_line in str(text or "").splitlines():
        line = _collapse_whitespace(raw_line)
        if not line:
            continue
        if re.match(r"^#{1,6}\s*", line):
            continue
        if re.match(r"^\d+[\.\)]\s+", line):
            continue
        if re.match(r"^[\-\*]\s+", line):
            line = re.sub(r"^[\-\*]\s+", "", line).strip()
        parts = re.split(r"(?<=[。！？.!?])\s+", line, maxsplit=1)
        first = str(parts[0] or "").strip()
        if first:
            return first
    return ""


def _has_ordered_compare_sections(text: str) -> bool:
    normalized = str(text or "")
    patterns = (
        (
            r"(^|\n)\s*各自概要\s*[：:]",
            r"(^|\n)\s*(?:#{1,6}\s*)?(?:1[\.\)]\s*)?文献概要\s*[：:]?",
        ),
        (
            r"(^|\n)\s*相同点\s*[：:]",
            r"(^|\n)\s*(?:#{1,6}\s*)?(?:2[\.\)]\s*)?研究主题/目标\s*[：:]?",
            r"(^|\n)\s*(?:#{1,6}\s*)?(?:3[\.\)]\s*)?相同点\s*[：:]?",
        ),
        (
            r"(^|\n)\s*差异点\s*[：:]",
            r"(^|\n)\s*(?:#{1,6}\s*)?(?:4[\.\)]\s*)?差异点\s*[：:]?",
        ),
        (
            r"(^|\n)\s*总结\s*[：:]",
            r"(^|\n)\s*(?:#{1,6}\s*)?(?:5[\.\)]\s*)?总结\s*[：:]?",
        ),
    )
    last_end = -1
    for group in patterns:
        position = _find_section_position(normalized, group, last_end=last_end)
        if position < 0:
            return False
        last_end = position
    return True


def _has_compare_document_coverage(*, answer: str, document_summaries: list[tuple[str, str]]) -> bool:
    if not document_summaries:
        return False
    normalized_answer = str(answer or "")
    token_lists = [_extract_compare_summary_tokens(summary) for _label, summary in document_summaries]
    token_counts = Counter(token.lower() for tokens in token_lists for token in dict.fromkeys(tokens))
    for label, summary in document_summaries:
        summary_tokens = _extract_compare_summary_tokens(summary)
        unique_tokens = [token for token in summary_tokens if token_counts.get(token.lower(), 0) == 1]
        label_present = bool(label and label in normalized_answer)
        candidate_tokens = unique_tokens or summary_tokens
        fact_present = any(token in normalized_answer for token in candidate_tokens[:5])
        if label_present and fact_present:
            continue
        return False
    return True


def _extract_compare_summary_tokens(summary: str) -> list[str]:
    summary_snippet = str(summary or "").strip("…")
    tokens = [token for token in re.split(r"[\s,，。；;:：]+", summary_snippet) if len(token) >= 6]
    ordered: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        marker = token.lower()
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(token)
    return ordered
