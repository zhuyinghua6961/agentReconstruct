from __future__ import annotations

import json
import os
from concurrent.futures import TimeoutError as FutureTimeoutError
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Iterator

from app.modules.file_context.service import resolve_request_file_context
from app.modules.qa_pdf.service import pdf_qa_service
from app.modules.qa_tabular.service import qa_tabular_service
from app.modules.generation_pipeline.synthesis_postprocess import build_top_reference_context
from app.modules.storage.uploaded_file_storage import materialize_uploaded_files
from app.services.file_route_service import resolve_app_owned_llm
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



def _scope_tokens(value: object) -> set[str]:
    return {part.strip().lower() for part in str(value or "").split("+") if part.strip()}


def _extract_kb_dois_from_metadatas(metadatas: list[object], *, limit: int = 15) -> list[str]:
    dois: list[str] = []
    for item in list(metadatas or []):
        if not isinstance(item, dict):
            continue
        doi = str(item.get("doi") or "").strip()
        if not doi or doi in dois:
            continue
        dois.append(doi)
        if len(dois) >= limit:
            break
    return dois


def _format_kb_evidence_context(*, retrieval_results: dict[str, object] | None, limit: int = 6) -> str:
    if not retrieval_results:
        return ""
    docs = list(retrieval_results.get("documents") or [])
    metas = list(retrieval_results.get("metadatas") or [])
    rows: list[str] = []
    for idx in range(min(limit, len(docs), len(metas))):
        meta = metas[idx] if isinstance(metas[idx], dict) else {}
        doi = str((meta or {}).get("doi") or "").strip()
        title = str((meta or {}).get("title") or (meta or {}).get("file_name") or "").strip()
        section = str((meta or {}).get("section") or "").strip()
        header = f"[KB{idx + 1}]"
        if title:
            header += f" {title}"
        if doi:
            header += f" | DOI={doi}"
        if section:
            header += f" | {section}"
        rows.append(header)
        content = str(docs[idx] or "").strip()
        if content:
            rows.append(content[:900])
    return "\n".join(rows).strip()


def _kb_reference_instruction(*, retrieval_results: dict[str, object] | None, logger: object, user_question: str) -> tuple[list[str], str]:
    ranked, instruction = build_top_reference_context(
        retrieval_results=retrieval_results,
        logger=logger,
        user_question=user_question,
        topk=None,
        min_citations=None,
        element_guard=None,
        pdf_chunks=None,
    )
    dois = [doi for doi, _score in ranked if doi]
    return dois, str(instruction or "")
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
        if getattr(app_state, "shared_llm_adapter", None) is None and hasattr(runtime_llm, "invoke"):
            app_state.shared_llm_adapter = runtime_llm
        if getattr(app_state, "aux_llm", None) is None and hasattr(runtime_llm, "invoke"):
            app_state.aux_llm = runtime_llm
        return runtime_llm

    llm = getattr(app_state, "aux_llm", None)
    if llm is not None and hasattr(llm, "invoke"):
        if getattr(app_state, "shared_llm_adapter", None) is None:
            app_state.shared_llm_adapter = llm
        return llm

    try:
        llm = resolve_app_owned_llm(app_state=app_state, logger=logger)
    except Exception as exc:
        raise FileRouteRuntimeError(str(exc)) from exc
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


def _pdf_agent_for_request(*, app_state: Any, logger: Any, allow_kb_verification: bool) -> Any:
    if not allow_kb_verification:
        return SimpleNamespace(llm=get_aux_llm(app_state, logger))
    try:
        from app.services.file_route_service import file_route_service

        # Provides `smart_query()` for KB verification in PDF streaming when generation runtime is ready.
        return file_route_service._build_pdf_agent(app_state=app_state)
    except Exception:
        return SimpleNamespace(llm=get_aux_llm(app_state, logger))


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
    execution_files = materialize_uploaded_files(
        file_items=list(
            (file_context or {}).get("execution_files")
            or adapted_request.execution_files
            or adapted_request.used_files
            or []
        ),
        logger=logger,
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
    if pdf_files and not pdf_path:
        yield {
            "type": "error",
            "error": "execution_file_unavailable",
            "message": "uploaded file is not ready for direct reading yet; retry later or refresh file metadata",
        }
        return
    if not pdf_path and not pdf_files:
        yield {"type": "error", "error": "pdf_path_missing", "message": "PDF branch selected but no readable PDF source is available"}
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
            allow_kb_verification=(allow_kb_verification := bool(
                (file_context or {}).get("allow_kb_verification", adapted_request.allow_kb_verification)
            )),
            turn_mode=str((file_context or {}).get("turn_mode") or adapted_request.turn_mode or "file_only"),
            selected_pdf_files=pdf_files,
            agent=_pdf_agent_for_request(app_state=app_state, logger=logger, allow_kb_verification=allow_kb_verification),
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
    execution_files = materialize_uploaded_files(
        file_items=list(
            (file_context or {}).get("execution_files")
            or adapted_request.execution_files
            or adapted_request.used_files
            or []
        ),
        logger=logger,
    )
    bindings = get_pdf_bindings(app_state, logger)

    source_scope = str(
        getattr(adapted_request, "source_scope", "")
        or (file_context or {}).get("source_scope")
        or ""
    ).strip()
    tokens = _scope_tokens(source_scope)
    wants_kb = ("kb" in tokens) or bool(getattr(adapted_request, "kb_enabled", False))

    table_files = [
        item
        for item in execution_files
        if isinstance(item, dict) and str(item.get("file_type") or "").strip().lower() in {"excel", "csv", "table", "xls", "xlsx"}
    ]
    readable_table_files = [item for item in table_files if str(item.get("local_path") or "").strip()]
    if table_files and not readable_table_files:
        yield {
            "type": "error",
            "error": "execution_file_unavailable",
            "message": "uploaded file is not ready for direct reading yet; retry later or refresh file metadata",
        }
        return

    # Allow KB verification in mixed file routes when KB is part of the declared scope.
    allow_kb_verification = bool(
        (file_context or {}).get("allow_kb_verification", getattr(adapted_request, "allow_kb_verification", False))
        or wants_kb
    )

    kb_evidence_context = ""
    kb_reference_instruction = ""
    kb_references: list[str] = []

    if wants_kb:
        runtime = getattr(app_state, "generation_runtime", None)
        if runtime is None:
            yield {
                "type": "step",
                "step": "kb_retrieval",
                "status": "error",
                "message": "KB 已开启，但 generation_runtime 不可用",
            }
        else:
            try:
                yield {
                    "type": "step",
                    "step": "kb_planning",
                    "status": "running",
                    "message": "正在生成检索规划（KB）",
                }
                stage1 = runtime.stage1_pre_answer_and_planning(str(adapted_request.question or ""))
                retrieval_claims = list((stage1 or {}).get("retrieval_claims") or [])

                yield {
                    "type": "step",
                    "step": "kb_retrieval",
                    "status": "running",
                    "message": "正在检索知识库（KB）",
                }
                retrieval_results = runtime.stage2_targeted_retrieval(
                    retrieval_claims=retrieval_claims,
                    n_results_per_claim=int(getattr(adapted_request, "n_results_per_claim", None) or 10),
                    user_question=str(adapted_request.question or ""),
                    should_cancel=is_cancelled,
                    active_stream_count=getattr(adapted_request, "active_stream_count", None),
                )
                kb_evidence_context = _format_kb_evidence_context(retrieval_results=retrieval_results, limit=6)
                _ranked_dois, kb_reference_instruction = _kb_reference_instruction(
                    retrieval_results=retrieval_results,
                    logger=logger,
                    user_question=str(adapted_request.question or ""),
                )
                kb_references = _extract_kb_dois_from_metadatas(
                    list((retrieval_results or {}).get("metadatas") or []),
                    limit=15,
                )
                yield {
                    "type": "step",
                    "step": "kb_retrieval",
                    "status": "success",
                    "message": f"KB 检索完成：候选DOI={len(kb_references)}",
                }
            except Exception as exc:
                yield {
                    "type": "step",
                    "step": "kb_retrieval",
                    "status": "error",
                    "message": f"KB 检索失败: {exc}",
                }

    yield {"type": "step", "step": "dispatch", "route": route, "message": "进入表格/混合问答分支"}
    for event in _iter_decoded_events(
        qa_tabular_service.iter_answer_events(
            question=adapted_request.question,
            used_files=execution_files,
            route_hint=route,
            source_scope=source_scope,
            kb_enabled=wants_kb,
            kb_evidence_context=kb_evidence_context,
            kb_reference_instruction=kb_reference_instruction,
            kb_references=kb_references,
            agent=_pdf_agent_for_request(app_state=app_state, logger=logger, allow_kb_verification=allow_kb_verification),
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
