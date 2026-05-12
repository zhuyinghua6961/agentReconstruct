from __future__ import annotations

import logging
import os
import re
import time
import traceback
from urllib.parse import urlparse
from typing import Any, Iterator

from app.modules.qa_pdf.common import IncrementalCleanState, incremental_clean_events_for_piece
from app.modules.qa_pdf.sidecar_client import iter_uploaded_pdf_answer_events_via_sidecar_compatible
from app.modules.qa_pdf.engine import answer_from_pdf as legacy_answer_from_pdf
from app.modules.qa_pdf.streaming import _iter_answer_pieces, iter_uploaded_pdf_answer_events
from app.modules.qa_pdf.truncation import smart_truncate_pdf_content


_PDFQA_SIDECAR_HEALTH_TTL_SEC = 5.0
_PDFQA_SIDECAR_HEALTH_CACHE: dict[str, Any] = {
    "checked_at": 0.0,
    "ok": False,
    "base_url": "",
}


def _env_bool(env_get: Any, name: str, default: bool) -> bool:
    raw = str(env_get(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_str(env_get: Any, name: str, default: str) -> str:
    value = str(env_get(name, default) or "").strip()
    return value or default


def _is_self_sidecar_target(*, env_get: Any, base_url: str) -> bool:
    parsed = urlparse(str(base_url or "").strip())
    host = str(parsed.hostname or "").strip().lower()
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    if host not in {"127.0.0.1", "localhost"}:
        return False
    current_port_raw = (
        env_get("PDFQA_SIDECAR_SELF_PORT")
        or env_get("FASTAPI_PORT")
        or env_get("BACKEND_PORT")
        or "8012"
    )
    try:
        current_port = int(str(current_port_raw).strip())
    except Exception:
        current_port = 8012
    return port == current_port


class PdfQaService:
    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)

    def answer_from_pdf(self, **kwargs: Any) -> Any:
        return legacy_answer_from_pdf(**kwargs)

    def extract_dois_from_question(self, question: str) -> list[str]:
        return re.findall(r"10\.\d+/[A-Za-z0-9._/-]+", str(question or ""))

    def iter_uploaded_pdf_answer_events(self, **kwargs: Any) -> Iterator[str]:
        yield from iter_uploaded_pdf_answer_events(**kwargs)

    def iter_uploaded_pdf_answer_events_via_sidecar(self, **kwargs: Any) -> Iterator[str]:
        yield from iter_uploaded_pdf_answer_events_via_sidecar_compatible(**kwargs)

    def should_use_sidecar(
        self,
        *,
        env_get: Any = os.getenv,
        turn_mode: str,
        allow_kb_verification: bool,
        selected_pdf_files: list[dict[str, Any]],
        pdf_path: str | None = None,
    ) -> bool:
        mode = _env_str(env_get, "UPLOAD_QA_SIDECAR_MODE", "file_only").lower()
        if mode in {"off", "0", "false"}:
            return False

        has_direct_pdf_path = bool(str(pdf_path or "").strip())
        if len(selected_pdf_files) != 1 and not has_direct_pdf_path:
            return False

        effective_turn_mode = str(turn_mode or "").strip().lower()
        if has_direct_pdf_path and not selected_pdf_files and effective_turn_mode == "kb_only":
            effective_turn_mode = "file_only"

        if effective_turn_mode != "file_only":
            return False
        if allow_kb_verification:
            return False

        return mode in {"file_only", "all_pdf", "single_pdf"}

    def probe_sidecar_health(
        self,
        *,
        env_get: Any = os.getenv,
        logger: Any | None = None,
        probe_fn: Any | None = None,
    ) -> bool:
        now = time.monotonic()
        base_url = _env_str(env_get, "PDFQA_SIDECAR_BASE_URL_INTERNAL", "http://127.0.0.1:8012")
        if _is_self_sidecar_target(env_get=env_get, base_url=base_url):
            if logger is not None:
                logger.info("PDFQA sidecar probe skipped: base_url points to current FastAPI service (%s)", base_url)
            _PDFQA_SIDECAR_HEALTH_CACHE.update(
                {
                    "checked_at": now,
                    "ok": False,
                    "base_url": base_url,
                }
            )
            return False
        if (
            _PDFQA_SIDECAR_HEALTH_CACHE.get("base_url") == base_url
            and now - float(_PDFQA_SIDECAR_HEALTH_CACHE.get("checked_at") or 0.0) < _PDFQA_SIDECAR_HEALTH_TTL_SEC
        ):
            return bool(_PDFQA_SIDECAR_HEALTH_CACHE.get("ok"))

        ok = False
        try:
            if probe_fn is None:
                from pdfqa_sidecar.flask_bridge import probe_sidecar_health as probe_fn

            payload = probe_fn(base_url=base_url)
            ok = str(payload.get("status") or "").strip().lower() == "ok"
            if not ok and logger is not None:
                logger.warning("PDFQA sidecar health check failed: %s", payload)
        except Exception as exc:
            if logger is not None:
                logger.warning("PDFQA sidecar unavailable, falling back to local PDF QA: %s", exc)
            ok = False

        _PDFQA_SIDECAR_HEALTH_CACHE.update(
            {
                "checked_at": now,
                "ok": bool(ok),
                "base_url": base_url,
            }
        )
        return bool(ok)

    def iter_dispatched_uploaded_pdf_answer_events(self, **kwargs: Any) -> Iterator[Any]:
        selected_pdf_files = kwargs.get("selected_pdf_files")
        files = selected_pdf_files if isinstance(selected_pdf_files, list) else []
        env_get = kwargs.get("env_get") or os.getenv
        logger = kwargs.get("logger") or self._logger
        if self.should_use_sidecar(
            env_get=env_get,
            turn_mode=str(kwargs.get("turn_mode") or ""),
            allow_kb_verification=bool(kwargs.get("allow_kb_verification")),
            selected_pdf_files=files,
            pdf_path=str(kwargs.get("pdf_path") or ""),
        ) and self.probe_sidecar_health(env_get=env_get, logger=logger):
            try:
                yield from self.iter_uploaded_pdf_answer_events_via_sidecar(**kwargs)
                return
            except Exception as exc:
                if logger is not None:
                    logger.warning("PDFQA sidecar execution failed, falling back to local PDF QA: %s", exc)
        yield from self.iter_uploaded_pdf_answer_events(**kwargs)

    def iter_multi_pdf_answer_events(self, **kwargs: Any) -> Iterator[Any]:
        question = str(kwargs.get("question") or "").strip()
        selected_pdf_files = kwargs.get("selected_pdf_files")
        files = [item for item in (selected_pdf_files or []) if isinstance(item, dict)]
        load_pdf_content_fn = kwargs.get("load_pdf_content_fn")
        answer_from_pdf_fn = kwargs.get("answer_from_pdf_fn")
        sse_event = kwargs.get("sse_event")
        clean_answer_for_frontend = kwargs.get("clean_answer_for_frontend") or (lambda text: text)
        filter_literature_markers_for_streaming = kwargs.get("filter_literature_markers_for_streaming") or (lambda text: text)
        log_qa_interaction = kwargs.get("log_qa_interaction") or (lambda **_kwargs: None)
        cache_key_question = str(kwargs.get("cache_key_question") or "").strip()
        cache_key_mode = str(kwargs.get("cache_key_mode") or "多文献PDF问答").strip() or "多文献PDF问答"
        cache_set_fn = kwargs.get("cache_set_fn") or (lambda *_args, **_kwargs: None)
        is_cancelled = kwargs.get("is_cancelled")
        logger = kwargs.get("logger") or self._logger

        def _emit(payload: dict[str, Any]) -> Any:
            return sse_event(payload) if callable(sse_event) else payload

        def _cancelled() -> bool:
            if not callable(is_cancelled):
                return False
            try:
                return bool(is_cancelled())
            except Exception:
                return False

        if not callable(load_pdf_content_fn) or not callable(answer_from_pdf_fn):
            yield _emit({"type": "error", "error": "multi_pdf_backend_unavailable"})
            return

        yield _emit({"type": "metadata", "query_mode": "多文献PDF问答"})
        yield _emit({"type": "thinking", "content": f"📚 已匹配 {len(files)} 篇文献，正在提取与合并原文..."})

        merged_parts: list[str] = []
        references: list[str] = []
        seen_doi: set[str] = set()
        for idx, item in enumerate(files[:6], start=1):
            if _cancelled():
                return
            file_path = str(item.get("local_path") or "").strip()
            if not file_path:
                continue
            content, error_message = load_pdf_content_fn(question=question, pdf_path=file_path)
            if error_message or not content:
                continue
            file_no = str(item.get("file_no") or "").strip()
            file_name = str(item.get("file_name") or f"pdf_{idx}")
            label = f"#{file_no}" if file_no else f"#{idx}"
            merged_parts.append(f"\n\n===== 文献 {label}: {file_name} =====\n{content}\n")
            doi = self._extract_doi_from_filename(str(item.get("file_name") or "")) or self._extract_doi_from_filename(file_path)
            if doi and doi not in seen_doi:
                seen_doi.add(doi)
                references.append(doi)

        if not merged_parts:
            yield _emit({"type": "error", "error": "未提取到可用的PDF内容，请检查文件是否可读"})
            return

        yield _emit({"type": "thinking", "content": "✍️ 正在生成多文献综合答案..."})
        try:
            answer_output = answer_from_pdf_fn(
                question,
                "\n".join(merged_parts),
                stream=True,
                first_token_timeout_sec=_resolve_multi_pdf_first_token_timeout(),
                is_cancelled=is_cancelled,
            )
        except TypeError:
            logger.warning("multi_pdf answer backend does not accept streaming kwargs, falling back to direct invocation")
            answer_output = answer_from_pdf_fn(question, "\n".join(merged_parts))

        clean_state = IncrementalCleanState()
        raw_parts: list[str] = []
        for piece in _iter_answer_pieces(answer_output):
            if _cancelled():
                return
            raw_text = str(piece or "")
            if not raw_text:
                continue
            raw_parts.append(raw_text)
            for event in incremental_clean_events_for_piece(
                raw_text,
                state=clean_state,
                clean_answer_for_frontend=clean_answer_for_frontend,
                filter_literature_markers_for_streaming=filter_literature_markers_for_streaming,
                sse_event=lambda payload: payload,
            ):
                yield _emit(event)

        answer = str(clean_answer_for_frontend("".join(raw_parts)) or "").strip()

        try:
            log_qa_interaction(
                question=question,
                answer=answer,
                query_mode=cache_key_mode,
                references=references[:15],
                extra={
                    "pdf_used": True,
                    "multi_pdf_mode": True,
                    "selected_pdf_count": len(files),
                    "streaming": True,
                },
            )
            cache_set_fn(cache_key_question or question, answer, cache_key_mode)
        except Exception:
            pass

        yield _emit({"type": "done", "references": references, "route": "pdf_qa"})

    def iter_doi_direct_query_events(self, **kwargs: Any) -> Iterator[Any]:
        question = str(kwargs.get("question") or "").strip()
        doi = str(kwargs.get("doi") or "").strip()
        agent = kwargs.get("agent")
        sse_event = kwargs.get("sse_event")
        clean_answer_for_frontend = kwargs.get("clean_answer_for_frontend") or (lambda text: text)
        filter_literature_markers_for_streaming = kwargs.get("filter_literature_markers_for_streaming") or (lambda text: text)
        log_qa_interaction = kwargs.get("log_qa_interaction") or (lambda **_kwargs: None)

        def _emit(payload: dict[str, Any]) -> Any:
            return sse_event(payload) if callable(sse_event) else payload

        if agent is None or not hasattr(agent, "query_pdf_directly"):
            yield _emit({"type": "error", "error": "PDF查询失败"})
            return

        result = agent.query_pdf_directly(question, doi)
        if not isinstance(result, dict) or not result.get("success"):
            yield _emit({"type": "error", "error": result.get("error", "PDF查询失败") if isinstance(result, dict) else "PDF查询失败"})
            return

        answer = str(clean_answer_for_frontend(result.get("final_answer", "")) or "").strip()
        query_mode = str(result.get("query_mode") or "PDF直接查询")
        yield _emit({"type": "metadata", "query_mode": query_mode})
        yield _emit({"type": "thinking", "content": "✍️ 正在生成答案..."})
        for index in range(0, len(answer), 10):
            piece = str(filter_literature_markers_for_streaming(answer[index : index + 10]) or "")
            if piece:
                yield _emit({"type": "content", "content": piece})

        try:
            log_qa_interaction(
                question=question,
                answer=answer,
                query_mode=query_mode,
                references=[doi],
                extra={"pdf_direct_query": True, "doi": doi, "streaming": True},
            )
        except Exception:
            pass

        yield _emit({"type": "done", "references": [doi], "route": "pdf_qa"})

    def iter_route_answer_events(self, **kwargs: Any) -> Iterator[Any]:
        question = str(kwargs.get("question") or "").strip()
        pdf_path = str(kwargs.get("pdf_path") or "").strip()
        selected_pdf_files = kwargs.get("selected_pdf_files")
        files = [item for item in (selected_pdf_files or []) if isinstance(item, dict)]
        load_pdf_content_fn = kwargs.get("load_pdf_content_fn")

        if not pdf_path:
            extracted = self.extract_dois_from_question(question)
            if extracted:
                yield from self.iter_doi_direct_query_events(
                    **{
                        key: value
                        for key, value in kwargs.items()
                        if key not in {"load_pdf_content_fn", "selected_pdf_files", "pdf_content"}
                    },
                    doi=extracted[0],
                )
                return

        if len(files) > 1:
            yield from self.iter_multi_pdf_answer_events(
                **{
                    key: value
                    for key, value in kwargs.items()
                    if key not in {"pdf_content", "selected_pdf_files", "load_pdf_content_fn"}
                },
                selected_pdf_files=files,
                load_pdf_content_fn=load_pdf_content_fn,
            )
            return

        if not callable(load_pdf_content_fn):
            yield {
                "type": "error",
                "error": "pdf_loader_unavailable",
            }
            return

        pdf_content = kwargs.get("pdf_content")
        if not isinstance(pdf_content, str) or not pdf_content:
            pdf_content, error_message = load_pdf_content_fn(question=question, pdf_path=pdf_path)
            if error_message or not pdf_content:
                yield {
                    "type": "error",
                    "error": error_message or "pdf_content_unavailable",
                }
                return

        yield from self.iter_dispatched_uploaded_pdf_answer_events(
            **{
                key: value
                for key, value in kwargs.items()
                if key not in {"pdf_content", "selected_pdf_files"}
            },
            pdf_content=pdf_content,
            selected_pdf_files=files,
        )

    def _extract_doi_from_filename(self, value: str) -> str:
        text = str(value or "").strip()
        if "." in text.rsplit("/", 1)[-1]:
            stem, suffix = text.rsplit(".", 1)
            if suffix.lower() == "pdf":
                text = stem
        matched = re.search(r"(10\.\d+[/_][-._;()/:A-Za-z0-9]+)", text)
        if not matched:
            return ""
        return matched.group(1).replace("_", "/", 1).rstrip(").,;")

    def build_web_bindings(
        self,
        *,
        allowed_extensions: set[str],
        pdf_support: bool,
        fitz_module: Any,
        max_pdf_chars: int,
        get_agent_llm_fn: Any,
    ) -> Any:
        from app.modules.qa_pdf.web_bindings import build_web_pdf_bindings

        return build_web_pdf_bindings(
            allowed_extensions=allowed_extensions,
            pdf_support=pdf_support,
            fitz_module=fitz_module,
            logger=self._logger,
            traceback_module=traceback,
            max_pdf_chars=max_pdf_chars,
            get_agent_llm_fn=get_agent_llm_fn,
        )


pdf_qa_service = PdfQaService()


def _resolve_multi_pdf_first_token_timeout() -> float:
    raw = str(os.getenv("UPLOAD_QA_FIRST_TOKEN_TIMEOUT_SEC", "25") or "25").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 25.0
    if value < 1.0:
        return 1.0
    if value > 180.0:
        return 180.0
    return value
