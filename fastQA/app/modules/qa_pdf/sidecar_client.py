from __future__ import annotations

import uuid
from typing import Any, Callable, Iterator

from app.modules.qa_pdf.common import (
    IncrementalCleanState,
    StreamCancelledError,
    build_done_event_payload,
    extract_doi_from_pdf_filename,
    incremental_clean_events_for_piece,
    raise_if_cancelled,
)


def iter_uploaded_pdf_answer_events_via_sidecar_compatible(
    *,
    question: str,
    pdf_path: str,
    pdf_content: str,
    allow_kb_verification: bool,
    turn_mode: str,
    sse_event: Callable[[dict], str],
    clean_answer_for_frontend: Callable[[str], str],
    filter_literature_markers_for_streaming: Callable[[str], str],
    log_qa_interaction: Callable[..., None],
    cache_key_mode: str = "",
    cache_key_question: str = "",
    cache_set_fn: Callable[[str, str, str], None] | None = None,
    logger: Any = None,
    trace_id: str = "",
    conversation_id: int | None = None,
    turn_id: str | None = None,
    first_token_timeout_sec: float | None = None,
    model: str | None = None,
    max_output_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    client: Any | None = None,
) -> Iterator[str]:
    from pdfqa_sidecar.adapter import build_sidecar_request_payload
    from pdfqa_sidecar.sync_client import SyncPdfQaSidecarClient

    raise_if_cancelled(is_cancelled)

    resolved_trace_id = str(trace_id or uuid.uuid4().hex)
    payload = build_sidecar_request_payload(
        trace_id=resolved_trace_id,
        question=question,
        pdf_items=[
            {
                "filename": pdf_path.rsplit("/", 1)[-1] if pdf_path else "uploaded.pdf",
                "doi": extract_doi_from_pdf_filename(pdf_path),
                "content": pdf_content,
            }
        ],
        conversation_id=conversation_id,
        turn_id=turn_id,
        allow_kb_verification=allow_kb_verification,
        turn_mode=turn_mode,
        first_token_timeout_sec=first_token_timeout_sec,
        model=model,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        top_p=top_p,
    )

    owned_client = False
    if client is None:
        import httpx
        from pdfqa_sidecar.flask_bridge import SidecarBridgeSettings

        settings = SidecarBridgeSettings.from_env()
        http_client = httpx.Client(timeout=settings.build_timeout())
        client = SyncPdfQaSidecarClient(base_url=settings.base_url, http_client=http_client)
        owned_client = True
    else:
        http_client = None

    clean_state = IncrementalCleanState()
    raw_parts: list[str] = []
    references: list[str] = []

    try:
        if logger is not None:
            logger.info(
                "PDFQA sidecar compat request: trace_id=%s turn_mode=%s allow_kb_verification=%s",
                resolved_trace_id,
                turn_mode,
                int(bool(allow_kb_verification)),
            )

        for event in client.iter_stream_events(payload=payload):
            raise_if_cancelled(is_cancelled)
            event_type = str(event.get("type") or "").strip()

            if event_type in {"thinking", "metadata"}:
                yield sse_event(event)
                continue

            if event_type == "chunk":
                piece = str(event.get("content") or "")
                if not piece:
                    continue
                raw_parts.append(piece)
                yield from incremental_clean_events_for_piece(
                    piece,
                    state=clean_state,
                    clean_answer_for_frontend=clean_answer_for_frontend,
                    filter_literature_markers_for_streaming=filter_literature_markers_for_streaming,
                    sse_event=sse_event,
                )
                continue

            if event_type == "error":
                yield sse_event(event)
                return

            if event_type == "done":
                event_refs = event.get("references") or []
                for item in event_refs:
                    if isinstance(item, dict):
                        doi = str(item.get("doi") or "").strip()
                    else:
                        doi = str(item or "").strip()
                    if doi:
                        references.append(doi)
                break

        answer = str(clean_state.accumulated_cleaned or "")
        if not answer and raw_parts:
            answer = clean_answer_for_frontend("".join(raw_parts))

        if not references:
            fallback_doi = extract_doi_from_pdf_filename(pdf_path)
            if fallback_doi:
                references.append(fallback_doi)

        try:
            log_qa_interaction(
                question=question,
                answer=answer,
                query_mode=cache_key_mode or "PDF文献查询",
                references=references[:15],
                extra={
                    "pdf_used": True,
                    "kb_verification_used": bool(allow_kb_verification),
                    "turn_mode": str(turn_mode or ""),
                    "streaming": True,
                    "trace_id": resolved_trace_id,
                    "sidecar": True,
                },
            )
            if cache_set_fn is not None:
                cache_set_fn(cache_key_question or question, answer, cache_key_mode or "PDF文献查询")
        except Exception as exc:
            if logger is not None:
                logger.warning("PDFQA sidecar compat post-processing failed: %s", exc)

        yield sse_event(build_done_event_payload(references))
    except StreamCancelledError:
        raise
    except Exception as exc:
        if logger is not None:
            logger.warning("PDFQA sidecar compat failed: %s", exc)
        yield sse_event({"type": "error", "code": "SIDECAR_COMPAT_ERROR", "error": str(exc)})
    finally:
        if owned_client and http_client is not None:
            http_client.close()


def probe_pdfqa_sidecar_health(*, base_url: str | None = None) -> dict[str, Any]:
    from pdfqa_sidecar.flask_bridge import probe_sidecar_health

    return probe_sidecar_health(base_url=base_url)
