from __future__ import annotations

import logging
import os
import re
import traceback
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any, Callable, Iterator

from app.core.config import get_settings
from app.core.runtime import generation_runtime_is_ready
from app.integrations.llm import SharedHttpPoolConfig, build_chat_adapter
from app.modules.generation_pipeline.pdf_pipeline import find_pdf_path
from app.modules.generation_pipeline.runtime_bootstrap import resolve_generation_runtime_inputs
from app.modules.qa_kb.service import qa_kb_service
from app.modules.qa_pdf.engine import answer_from_pdf as answer_from_pdf_impl
from app.modules.qa_pdf.pdf_extractor import extract_pdf_text as extract_pdf_text_impl
from app.modules.qa_pdf.service import pdf_qa_service
from app.modules.qa_tabular.service import qa_tabular_service
from app.services.request_adapter import GatewayAskRequest


def _filter_literature_markers_for_streaming(content: str) -> str:
    return re.sub(r"\[需要文献支撑:[^\[\]]*(?:\]|$)", "", str(content or ""), flags=re.IGNORECASE)


def _clean_answer_for_frontend(answer: str, *, lightweight: bool = False) -> str:
    cleaned = str(answer or "")
    cleaned = re.sub(r"\(do+i\s*=", r"(doi=", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bd[0o]+i+\s*=", r"doi=", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"📄\s*查看原文", "", cleaned)
    cleaned = re.sub(r"·\s*查看原文", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if lightweight:
        cleaned = re.sub(r"\(\s*\)", "", cleaned)
        cleaned = re.sub(r"\[\s*\]", "", cleaned)
    return cleaned.strip()


def _identity_log(**_kwargs: Any) -> None:
    return None


def _has_invoke(value: Any) -> bool:
    return value is not None and hasattr(value, "invoke")


def resolve_app_owned_llm(*, app_state: Any, logger: Any) -> Any:
    shared_llm = getattr(app_state, "shared_llm_adapter", None)
    if _has_invoke(shared_llm):
        return shared_llm

    aux_llm = getattr(app_state, "aux_llm", None)
    if _has_invoke(aux_llm):
        app_state.shared_llm_adapter = aux_llm
        return aux_llm

    resolved = resolve_generation_runtime_inputs(api_key=None, base_url=None, model=None, config=None)
    if not str(resolved.api_key or "").strip():
        raise RuntimeError("LLM_API_KEY is required for file QA")
    if not str(resolved.base_url or "").strip():
        raise RuntimeError("LLM_BASE_URL is required for file QA")

    shared_http_client = None
    shared_http_pool = getattr(app_state, "shared_llm_http_pool", None)
    pool_client = getattr(shared_http_pool, "client", None)
    if callable(pool_client):
        shared_http_client = pool_client()
    transport_config = SharedHttpPoolConfig.from_env()

    llm = build_chat_adapter(
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        model=resolved.model,
        temperature=0.3,
        top_p=0.95,
        max_tokens=max(1024, int(str(os.getenv("PDF_QA_MAX_TOKENS", "2500") or "2500").strip())),
        logger=logger,
        connect_timeout_seconds=transport_config.connect_timeout_seconds,
        read_timeout_seconds=transport_config.read_timeout_seconds,
        stream_read_timeout_seconds=transport_config.stream_read_timeout_seconds,
        write_timeout_seconds=transport_config.write_timeout_seconds,
        pool_timeout_seconds=transport_config.pool_timeout_seconds,
        keepalive_expiry_seconds=transport_config.keepalive_expiry_seconds,
        max_connections=transport_config.max_connections,
        max_keepalive_connections=transport_config.max_keepalive_connections,
        http_client=shared_http_client,
    )
    app_state.shared_llm_adapter = llm
    app_state.shared_llm_adapter_ready = True
    app_state.aux_llm = llm
    return llm


class FileRouteService:
    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self._llm_lock = Lock()

    def _max_pdf_chars(self) -> int:
        try:
            return max(4000, int(str(os.getenv("PDF_QA_MAX_PDF_CHARS", "50000") or "50000").strip()))
        except Exception:
            return 50000

    def _resolve_llm(self, *, app_state: Any):
        shared_llm = getattr(app_state, "shared_llm_adapter", None)
        if _has_invoke(shared_llm):
            return shared_llm
        with self._llm_lock:
            return resolve_app_owned_llm(app_state=app_state, logger=self._logger)

    def _extract_pdf_text(self, pdf_path: str, *, max_pages: int = 10, exclude_references: bool = True) -> str:
        try:
            import fitz  # type: ignore

            pdf_support = True
        except Exception:
            fitz = None
            pdf_support = False
        return extract_pdf_text_impl(
            pdf_path,
            max_pages=max_pages,
            exclude_references=exclude_references,
            pdf_support=pdf_support,
            fitz_module=fitz,
            logger=self._logger,
            traceback_module=traceback,
        )

    def _load_pdf_content_for_streaming(self, *, question: str, pdf_path: str) -> tuple[str | None, str | None]:
        _ = question
        content = self._extract_pdf_text(pdf_path, max_pages=10, exclude_references=True)
        if isinstance(content, str) and content.startswith("[错误]"):
            return None, content
        return str(content or ""), None

    def _answer_from_pdf(
        self,
        question: str,
        pdf_content: str,
        *,
        app_state: Any,
        kb_verification: dict[str, Any] | None = None,
        stream: bool = False,
        first_token_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> Any:
        return answer_from_pdf_impl(
            question,
            pdf_content,
            llm=self._resolve_llm(app_state=app_state),
            max_pdf_chars=self._max_pdf_chars(),
            smart_truncate_fn=lambda content, max_chars, is_summary=False, question="": content[:max_chars],
            logger=self._logger,
            traceback_module=traceback,
            kb_verification=kb_verification,
            stream=stream,
            first_token_timeout_sec=first_token_timeout_sec,
            is_cancelled=is_cancelled,
        )

    def _build_pdf_agent(self, *, app_state: Any):
        service = self
        settings = get_settings()

        class _PdfAgent:
            # Compatibility shim for legacy MaterialScienceAgent entrypoints kept during V2 rollout.
            llm = service._resolve_llm(app_state=app_state)

            def smart_query(self, question: str, use_dual_retrieval: bool = False) -> dict[str, Any]:
                _ = use_dual_retrieval
                if not generation_runtime_is_ready(app_state):
                    return {"success": False, "error": "generation_runtime_unavailable"}
                result = qa_kb_service.run_generation_pipeline(
                    question=question,
                    generation_runtime=app_state.generation_runtime,
                    redis_service=getattr(app_state, "redis_service", None),
                    n_results_per_claim=10,
                    logger=service._logger,
                )
                return {
                    "success": bool(result.success),
                    "final_answer": result.final_answer,
                    "raw_data": result.raw,
                    "query_mode": result.metadata.query_mode,
                }

            def query_pdf_directly(self, user_question: str, doi: str) -> dict[str, Any]:
                pdf_path = find_pdf_path(doi=doi, papers_dir=settings.papers_dir, logger=service._logger)
                if not pdf_path:
                    return {"success": False, "error": f"PDF文件不存在: {doi}"}
                pdf_content, error = service._load_pdf_content_for_streaming(question=user_question, pdf_path=pdf_path)
                if error or not pdf_content:
                    return {"success": False, "error": error or "pdf_content_unavailable"}
                answer_output = service._answer_from_pdf(
                    user_question,
                    pdf_content,
                    app_state=app_state,
                    kb_verification=None,
                    stream=False,
                )
                if isinstance(answer_output, str):
                    final_answer = answer_output
                else:
                    final_answer = "".join(str(item or "") for item in answer_output)
                return {
                    "success": True,
                    "final_answer": _clean_answer_for_frontend(final_answer),
                    "query_mode": "PDF直接查询",
                }

        return _PdfAgent()

    def _selected_pdf_files(self, request: GatewayAskRequest) -> list[dict[str, Any]]:
        files = request.execution_files or request.used_files
        return [
            item for item in files
            if isinstance(item, dict) and str(item.get("file_type") or "").strip().lower() == "pdf"
        ]

    def _pdf_path(self, request: GatewayAskRequest) -> str:
        if str(request.pdf_path or "").strip():
            return str(request.pdf_path or "").strip()
        pdf_files = self._selected_pdf_files(request)
        if pdf_files:
            return str(pdf_files[0].get("local_path") or "").strip()
        return ""

    def iter_events(
        self,
        *,
        request: GatewayAskRequest,
        app_state: Any,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        route = str(request.route or "kb_qa").strip().lower() or "kb_qa"
        if route == "pdf_qa":
            pdf_files = self._selected_pdf_files(request)
            yield {
                "type": "step",
                "step": "dispatch",
                "status": "success",
                "message": "📄 已进入 PDF 问答链路",
            }
            yield from pdf_qa_service.iter_route_answer_events(
                question=request.question,
                pdf_path=self._pdf_path(request),
                performance_mode="speed",
                allow_kb_verification=bool(request.allow_kb_verification),
                turn_mode=request.turn_mode,
                selected_pdf_files=pdf_files,
                agent=self._build_pdf_agent(app_state=app_state),
                executor=None,
                timeout_error_cls=TimeoutError,
                sse_event=lambda event: event,
                answer_from_pdf_fn=lambda question, pdf_content, **kwargs: self._answer_from_pdf(
                    question,
                    pdf_content,
                    app_state=app_state,
                    **kwargs,
                ),
                clean_answer_for_frontend=_clean_answer_for_frontend,
                filter_literature_markers_for_streaming=_filter_literature_markers_for_streaming,
                log_qa_interaction=_identity_log,
                cache_key_mode="pdf_qa",
                cache_key_question=request.question,
                cache_set_fn=lambda *_args, **_kwargs: None,
                is_cancelled=should_cancel,
                env_get=os.getenv,
                logger=self._logger,
                load_pdf_content_fn=self._load_pdf_content_for_streaming,
            )
            return

        if route in {"tabular_qa", "hybrid_qa"}:
            yield {
                "type": "step",
                "step": "dispatch",
                "status": "success",
                "message": "📊 已进入表格/混合文件问答链路",
            }
            yield from qa_tabular_service.iter_answer_events(
                question=request.question,
                used_files=request.execution_files or request.used_files,
                route_hint=route,
                agent=SimpleNamespace(llm=self._resolve_llm(app_state=app_state)),
                sse_event=lambda event: event,
                clean_answer_for_frontend=_clean_answer_for_frontend,
                filter_literature_markers_for_streaming=_filter_literature_markers_for_streaming,
                log_qa_interaction=_identity_log,
                is_cancelled=should_cancel,
                logger=self._logger,
                trace_id=request.trace_id,
                extract_pdf_text_fn=lambda pdf_path: self._extract_pdf_text(
                    pdf_path,
                    max_pages=10,
                    exclude_references=True,
                ),
            )
            return

        raise RuntimeError(f"unsupported file route: {route}")


file_route_service = FileRouteService()
