from __future__ import annotations

import json
import os
from concurrent.futures import TimeoutError as FutureTimeoutError
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Iterator

from app.modules.file_context.service import resolve_request_file_context
from app.modules.qa_pdf.llm_factory import init_llm
from app.modules.qa_pdf.service import pdf_qa_service
from app.modules.qa_tabular.service import qa_tabular_service
from app.services.file_qa_helpers import (
    clean_answer_for_frontend,
    filter_literature_markers_for_streaming,
    load_pdf_content_for_streaming,
    log_qa_interaction,
)


class FileRouteRuntimeError(RuntimeError):
    pass


def _env_int(*names: str, default: int) -> int:
    for name in names:
        raw = str(os.getenv(name, "") or "").strip()
        if not raw:
            continue
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        return parsed if parsed > 0 else default
    return default


def _iter_decoded_events(events: Iterable[Any]) -> Iterator[dict[str, Any]]:
    for item in events:
        if isinstance(item, dict):
            yield item
            continue
        text = str(item or "")
        if not text.strip():
            continue
        for frame in text.split("\n\n"):
            frame = frame.strip()
            if not frame:
                continue
            data_lines: list[str] = []
            for line in frame.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if not data_lines:
                continue
            raw_payload = "\n".join(data_lines)
            try:
                decoded = json.loads(raw_payload)
            except Exception:
                yield {"type": "content", "content": raw_payload}
                continue
            if isinstance(decoded, dict):
                yield decoded
            else:
                yield {"type": "content", "content": str(decoded)}


def _get_runtime_llm(app_state: Any) -> Any:
    for attr in ("shared_llm_adapter", "aux_llm"):
        cached = getattr(app_state, attr, None)
        if cached is not None and hasattr(cached, "invoke"):
            return cached
    runtime = getattr(app_state, "generation_runtime", None)
    if runtime is None:
        return None
    for attr in ("llm", "chat_model", "answer_llm"):
        value = getattr(runtime, attr, None)
        if value is not None and hasattr(value, "invoke"):
            return value
    return None


def get_aux_llm(app_state: Any, logger: Any) -> Any:
    runtime_llm = _get_runtime_llm(app_state)
    if runtime_llm is not None:
        return runtime_llm

    llm = getattr(app_state, "aux_llm", None)
    if llm is not None and hasattr(llm, "invoke"):
        return llm

    try:
        llm = init_llm(logger)
    except Exception as exc:
        raise FileRouteRuntimeError(str(exc)) from exc
    app_state.aux_llm = llm
    return llm


def get_pdf_bindings(app_state: Any, logger: Any):
    bindings = getattr(app_state, "pdf_web_bindings", None)
    if bindings is not None:
        return bindings

    try:
        import fitz  # type: ignore

        pdf_support = True
    except Exception:
        fitz = None
        pdf_support = False

    bindings = pdf_qa_service.build_web_bindings(
        allowed_extensions={"pdf"},
        pdf_support=pdf_support,
        fitz_module=fitz,
        max_pdf_chars=_env_int("PDF_QA_MAX_PDF_CHARS", default=12000),
        get_agent_llm_fn=lambda: get_aux_llm(app_state, logger),
    )
    app_state.pdf_web_bindings = bindings
    return bindings


def resolve_gateway_file_context(*, adapted_request: Any, logger: Any) -> dict[str, Any] | None:
    candidate_files = adapted_request.execution_files or adapted_request.used_files
    current_pdf_path = str(adapted_request.current_pdf_path or adapted_request.pdf_path or "").strip()
    if not candidate_files and not adapted_request.pdf_context and not adapted_request.use_pdf and not current_pdf_path:
        return None
    synthetic_conversation_id = adapted_request.conversation_id or (1 if candidate_files else None)
    return resolve_request_file_context(
        question=adapted_request.question,
        conversation_id=synthetic_conversation_id,
        pdf_context=adapted_request.pdf_context,
        current_pdf_path=current_pdf_path,
        list_uploaded_files_fn=(lambda _cid: list(candidate_files)) if candidate_files else None,
        logger=logger,
    )


def iter_pdf_route_events(
    *,
    app_state: Any,
    adapted_request: Any,
    file_context: dict[str, Any] | None,
    sse_event: Callable[[dict[str, Any]], Any] | None,
    is_cancelled: Callable[[], bool] | None = None,
):
    logger = getattr(app_state, "logger", None)
    bindings = get_pdf_bindings(app_state, logger)
    redis_service = getattr(app_state, "redis_service", None)
    execution_files = list(
        (file_context or {}).get("execution_files")
        or adapted_request.execution_files
        or adapted_request.used_files
        or []
    )
    pdf_files = [
        item
        for item in execution_files
        if isinstance(item, dict) and str(item.get("file_type") or "").strip().lower() == "pdf"
    ]
    pdf_file = pdf_files[0] if pdf_files else None
    pdf_path = str(
        (pdf_file or {}).get("local_path")
        or (file_context or {}).get("primary_pdf_path")
        or adapted_request.current_pdf_path
        or adapted_request.pdf_path
        or ""
    ).strip()
    if not pdf_path and not pdf_files:
        yield {"type": "error", "error": "pdf_path_missing", "message": "PDF branch selected but no local PDF path is available"}
        return

    pdf_content = None
    if len(pdf_files) <= 1 and pdf_path:
        pdf_content, error_message = load_pdf_content_for_streaming(
            question=adapted_request.question,
            pdf_path=pdf_path,
            executor=None,
            timeout_error_cls=FutureTimeoutError,
            extract_pdf_text_fn=bindings.extract_pdf_text,
            max_pdf_pages=10,
            logger=logger,
            redis_service=redis_service,
        )
        if error_message or not pdf_content:
            yield {"type": "error", "error": error_message or "pdf_content_unavailable"}
            return

    yield {"type": "step", "step": "dispatch", "route": "pdf_qa", "message": "进入 PDF 问答分支"}
    for event in _iter_decoded_events(
        pdf_qa_service.iter_route_answer_events(
            question=adapted_request.question,
            pdf_path=pdf_path,
            pdf_content=pdf_content,
            performance_mode="speed",
            allow_kb_verification=bool(
                (file_context or {}).get("allow_kb_verification", adapted_request.allow_kb_verification)
            ),
            turn_mode=str((file_context or {}).get("turn_mode") or adapted_request.turn_mode or "file_only"),
            selected_pdf_files=pdf_files,
            agent=SimpleNamespace(llm=get_aux_llm(app_state, logger)),
            executor=None,
            timeout_error_cls=FutureTimeoutError,
            sse_event=sse_event or (lambda event: event),
            sleep_fn=lambda _value: None,
            answer_from_pdf_fn=bindings.answer_from_pdf,
            clean_answer_for_frontend=clean_answer_for_frontend,
            filter_literature_markers_for_streaming=filter_literature_markers_for_streaming,
            log_qa_interaction=lambda **kwargs: log_qa_interaction(logger=logger, **kwargs),
            cache_key_mode="pdf_qa",
            cache_key_question=adapted_request.question,
            cache_set_fn=lambda *_args, **_kwargs: None,
            is_cancelled=is_cancelled,
            env_get=os.getenv,
            logger=logger,
            load_pdf_content_fn=lambda **kwargs: load_pdf_content_for_streaming(
                executor=None,
                timeout_error_cls=FutureTimeoutError,
                extract_pdf_text_fn=bindings.extract_pdf_text,
                max_pdf_pages=10,
                logger=logger,
                redis_service=redis_service,
                **kwargs,
            ),
        )
    ):
        yield event


def iter_tabular_route_events(
    *,
    app_state: Any,
    adapted_request: Any,
    file_context: dict[str, Any] | None,
    route: str,
    sse_event: Callable[[dict[str, Any]], Any] | None,
    is_cancelled: Callable[[], bool] | None = None,
):
    logger = getattr(app_state, "logger", None)
    execution_files = list(
        (file_context or {}).get("execution_files")
        or adapted_request.execution_files
        or adapted_request.used_files
        or []
    )
    bindings = get_pdf_bindings(app_state, logger)
    yield {"type": "step", "step": "dispatch", "route": route, "message": "进入表格/混合问答分支"}
    for event in _iter_decoded_events(
        qa_tabular_service.iter_answer_events(
            question=adapted_request.question,
            used_files=execution_files,
            route_hint=route,
            agent=SimpleNamespace(llm=get_aux_llm(app_state, logger)),
            sse_event=sse_event or (lambda event: event),
            sleep_fn=lambda _value: None,
            clean_answer_for_frontend=clean_answer_for_frontend,
            filter_literature_markers_for_streaming=filter_literature_markers_for_streaming,
            log_qa_interaction=lambda **kwargs: log_qa_interaction(logger=logger, **kwargs),
            is_cancelled=is_cancelled,
            logger=logger,
            trace_id=adapted_request.trace_id,
            extract_pdf_text_fn=bindings.extract_pdf_text,
        )
    ):
        yield event
