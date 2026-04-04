from __future__ import annotations

import os
import re
import json
from pathlib import Path
from typing import Any, Callable

import httpx

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


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _is_summary_question(question: str) -> bool:
    lower = str(question or "").strip().lower()
    hints = (
        "总结",
        "概括",
        "摘要",
        "summarize",
        "summary",
        "overview",
        "main points",
    )
    return any(hint in lower for hint in hints)


def _extractive_fallback_summary(*, question: str, pdf_text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(pdf_text or "")).strip()
    if not cleaned:
        return "当前未拿到可用的 PDF 正文内容，无法生成基于原文的总结。"

    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？.!?])\s+", cleaned)
        if item.strip()
    ]
    picked: list[str] = []
    for sentence in sentences:
        if len(sentence) < 20:
            continue
        picked.append(_truncate(sentence, 220))
        if len(picked) >= 4:
            break

    if not picked:
        picked.append(_truncate(cleaned, 400))

    if _is_summary_question(question):
        lines = ["基于 PDF 原文提取，文档要点如下："]
        lines.extend(f"{index}. {item}" for index, item in enumerate(picked, start=1))
        return "\n".join(lines)

    return "\n".join(picked)


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

    def _build_request_payload(self, *, question: str, pdf_text: str, file_name: str, include_kb: bool, stream: bool) -> dict[str, Any]:
        kb_instruction = (
            "可以结合专利领域背景做简短补充，但结论必须优先依据 PDF 原文。"
            if include_kb
            else "只允许基于 PDF 原文回答，不要引入外部知识。"
        )
        prompt = "\n".join(
            [
                f"用户问题: {str(question or '').strip() or '请总结这份PDF'}",
                f"文件名: {str(file_name or '').strip() or 'unknown.pdf'}",
                "要求:",
                "1. 如果用户是在求总结，输出简洁但完整的中文总结。",
                "2. 覆盖主题、方法/方案、关键结果、结论或贡献。",
                "3. 不要说自己无法访问文件。",
                f"4. {kb_instruction}",
                "PDF正文:",
                pdf_text,
            ]
        )
        return {
            "model": self._model,
            "temperature": 0.2,
            "stream": bool(stream),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是专利模式下的文件问答助手。必须以给定 PDF 正文为主回答，"
                        "不允许虚构未出现的事实。输出中文。"
                    ),
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

    def stream_answer(self, *, question: str, pdf_text: str, file_name: str, include_kb: bool) -> Any:
        request_payload = self._build_request_payload(
            question=question,
            pdf_text=pdf_text,
            file_name=file_name,
            include_kb=include_kb,
            stream=True,
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

    def answer(self, *, question: str, pdf_text: str, file_name: str, include_kb: bool) -> str:
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
        primary_file = used_files[0] if used_files else {}
        primary_label = str(primary_file.get("file_name") or f"file:{primary_file.get('file_id') or 'unknown'}")
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

        pdf_text = self._load_pdf_text(contract=contract)
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
            answer_text = self._build_answer(
                question=contract.question,
                pdf_text=pdf_text,
                file_name=primary_label,
                include_kb=include_kb,
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
            answer_mode = "pdf_text_summary"
        else:
            answer_text = "当前未拿到可读的 PDF 原文内容，无法生成基于正文的总结。请稍后重试或检查文件处理状态。"
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
            answer_mode = "pdf_text_unavailable"

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

    def _load_pdf_text(self, *, contract: PatentFileContract) -> str:
        sections: list[str] = []
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
            sections.append(f"文件: {label}\n{_truncate(extracted, self._max_pdf_chars)}")
        return "\n\n".join(section for section in sections if section).strip()

    def _build_answer(
        self,
        *,
        question: str,
        pdf_text: str,
        file_name: str,
        include_kb: bool,
        content_callback: Callable[[str], None] | None = None,
    ) -> str:
        answer_parts: list[str] = []
        
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

        truncated_pdf_text = _truncate(pdf_text, self._max_pdf_chars)
        if callable(self._answer_question_fn):
            output = self._answer_question_fn(
                question=question,
                pdf_text=pdf_text,
                file_name=file_name,
                include_kb=include_kb,
            )
            if isinstance(output, (str, bytes)):
                answer = _emit_buffered_text(str(output or ""))
                if answer:
                    return answer
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
                            pdf_text=truncated_pdf_text,
                            file_name=file_name,
                            include_kb=include_kb,
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
                            pdf_text=truncated_pdf_text,
                            file_name=file_name,
                            include_kb=include_kb,
                        )
                    )
                    if answer:
                        return answer
                except Exception:
                    answer_parts = []

        answer = "".join(answer_parts).strip()
        if answer:
            return answer

        fallback = _extractive_fallback_summary(question=question, pdf_text=pdf_text)
        emit_text_chunks(fallback, content_callback=content_callback)
        return fallback

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
