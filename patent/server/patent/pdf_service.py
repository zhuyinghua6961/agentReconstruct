from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import Any, Callable

import httpx

from server.patent.pdf_contract import (
    CompareBudgetError,
    PDF_QA_SYSTEM_MESSAGE,
    build_compare_failure_message,
    build_extractive_fallback_summary,
    build_kb_section,
    build_patent_pdf_answer_prompt,
    format_multi_pdf_sections,
    is_compare_question,
    is_summary_question,
    smart_truncate_pdf_content,
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
    ) -> Any:
        request_payload = self._build_request_payload(
            question=question,
            pdf_text=pdf_text,
            file_name=file_name,
            include_kb=include_kb,
            stream=True,
            selected_file_labels=selected_file_labels,
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
        used_files = [item.as_payload() for item in contract.selected_execution_files if item.family == "pdf"]
        profile = get_patent_mode_profile(contract.route)
        selected_labels = [
            str(item.get("file_name") or f"file:{item.get('file_id') or 'unknown'}").strip()
            for item in used_files
        ]
        compare_mode = is_compare_question(contract.question, selected_pdf_count=len(selected_labels))
        steps: list[dict[str, Any]] = []

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

        pdf_documents = self._load_pdf_documents(contract=contract)
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
            self._record_step(
                steps,
                progress_callback=progress_callback,
                payload={
                    "step": "pdf_answer",
                    "title": "生成文件答案",
                    "message": "✍️ 正在基于 PDF 原文生成答案...",
                    "status": "running",
                },
            )
            answer_text, answer_mode = self._build_answer(
                question=contract.question,
                pdf_text=pdf_text,
                file_name=", ".join(selected_labels) if len(selected_labels) > 1 else (selected_labels[0] if selected_labels else "unknown.pdf"),
                selected_file_labels=selected_labels,
                available_file_labels=available_labels,
                include_kb=include_kb,
                compare_mode=compare_mode,
                content_callback=content_callback,
            )
            self._record_step(
                steps,
                progress_callback=progress_callback,
                payload={
                    "step": "pdf_answer",
                    "title": "生成文件答案",
                    "message": "✍️ 已基于 PDF 原文生成答案",
                    "status": "success",
                },
            )
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
            if callable(content_callback):
                emit_text_chunks(answer_text, content_callback=content_callback)
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

    def _load_pdf_documents(self, *, contract: PatentFileContract) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        for item in contract.selected_execution_files:
            if item.family != "pdf":
                continue
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
            sections.append({"label": label, "text": _truncate(extracted, self._max_pdf_chars)})
        return sections

    def _build_answer(
        self,
        *,
        question: str,
        pdf_text: str,
        file_name: str,
        selected_file_labels: list[str],
        available_file_labels: list[str],
        include_kb: bool,
        compare_mode: bool,
        content_callback: Callable[[str], None] | None = None,
    ) -> tuple[str, str]:
        answer_parts: list[str] = []
        summary_mode = is_summary_question(question)
        missing_labels = [label for label in selected_file_labels if label not in set(available_file_labels)]

        def _emit_stream_piece(piece: str) -> None:
            text = str(piece or "")
            if not text:
                return
            answer_parts.append(text)
            if callable(content_callback):
                content_callback(text)

        def _emit_buffered_text(text: str) -> str:
            normalized = str(text or "").strip()
            if not normalized:
                return ""
            if callable(content_callback):
                emit_text_chunks(normalized, content_callback=content_callback)
            return normalized

        if compare_mode and (len(available_file_labels) < 2 or missing_labels):
            message = build_compare_failure_message(
                question=question,
                available_docs=available_file_labels,
                missing_docs=missing_labels,
                reason="参与比较的文献正文不完整",
            )
            return _emit_buffered_text(message), "pdf_compare_unavailable"

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
            return _emit_buffered_text(message), "pdf_compare_unavailable"

        if callable(self._answer_question_fn):
            output = self._answer_question_fn(
                question=question,
                pdf_text=prepared_pdf_text,
                file_name=file_name,
                include_kb=include_kb,
            )
            if isinstance(output, (str, bytes)):
                answer = _emit_buffered_text(str(output or ""))
                if answer:
                    return answer, "pdf_text_compare" if compare_mode else "pdf_text_summary"
            else:
                for piece in iter_text_output(output):
                    _emit_stream_piece(piece)
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
                        )
                    ):
                        _emit_stream_piece(piece)
            except Exception:
                answer_parts = []
            if not "".join(answer_parts).strip():
                try:
                    answer = _emit_buffered_text(
                        self._client.answer(
                            question=question,
                            pdf_text=prepared_pdf_text,
                            file_name=file_name,
                            include_kb=include_kb,
                            selected_file_labels=selected_file_labels,
                        )
                    )
                    if answer:
                        return answer, "pdf_text_compare" if compare_mode else "pdf_text_summary"
                except Exception:
                    answer_parts = []

        answer = "".join(answer_parts).strip()
        if answer:
            return answer, "pdf_text_compare" if compare_mode else "pdf_text_summary"

        fallback = (
            build_compare_failure_message(
                question=question,
                available_docs=available_file_labels,
                missing_docs=missing_labels,
                reason="模型未返回可用的比较结果",
            )
            if compare_mode
            else build_extractive_fallback_summary(question=question, pdf_text=pdf_text)
        )
        emit_text_chunks(fallback, content_callback=content_callback)
        return fallback, "pdf_compare_unavailable" if compare_mode else "pdf_text_summary"

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
