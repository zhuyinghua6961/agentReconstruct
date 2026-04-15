from __future__ import annotations

import inspect
import logging
import os
import re
import threading
import time
from typing import Any, Callable

from server.patent.cache_keys import build_file_route_cache_fingerprint
from server.patent.file_models import PatentFileContract, PatentFileRoutePlan
from server.patent.hybrid_synthesis import HYBRID_SYNTHESIS_PROMPT_VERSION, build_patent_hybrid_synthesis_contract
from server.patent.pdf_contract import is_summary_question
from server.patent.pdf_service import PatentPdfService, build_pdf_synthesis_context
from server.patent.summary_formatting import LITERATURE_SUMMARY_NOTE
from server.patent.streaming import emit_text_chunks
from server.patent.tabular_service import PatentTabularService
from server.services.mode_profiles import get_patent_mode_profile


_HYBRID_SCOPE_TO_PLAN = {
    "pdf+kb": ("pdf", ("pdf",), True),
    "table+kb": ("tabular", ("table",), True),
    "pdf+table": ("hybrid", ("pdf", "table"), False),
    "pdf+table+kb": ("hybrid", ("pdf", "table"), True),
}

_LOGGER = logging.getLogger("patent.file_routes")
_LITERATURE_SUMMARY_NOTE = LITERATURE_SUMMARY_NOTE
_PATENT_TABLE_PLANNER_VERSION = "patent-tabular-planner-v2"
_PATENT_TABLE_SUMMARY_CONTEXT_VERSION = "patent-tabular-summary-context-v2"


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name) or default).strip())
    except Exception:
        return int(default)


def _table_hybrid_context_limit() -> int:
    return max(1000, _env_int("PATENT_HYBRID_TABLE_CONTEXT_CHARS", 6000))


def _plan_uses_table(plan: PatentFileRoutePlan) -> bool:
    return "table" in {str(item or "").strip().lower() for item in tuple(plan.file_families or ())}


def _trim_text(value: object, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _public_hybrid_synthesis_contract(contract: dict[str, Any]) -> dict[str, Any]:
    public_keys = {
        "question",
        "source_scope",
        "pdf_answer",
        "tabular_answer",
        "kb_answer",
        "pdf_evidence_context",
        "table_execution_context",
        "kb_evidence_context",
        "kb_reference_instruction",
        "include_kb",
        "file_precedence",
        "available_sources",
        "source_answer_modes",
        "synthesis_prompt_version",
    }
    return {
        key: value
        for key, value in dict(contract or {}).items()
        if key in public_keys
    }


def _hybrid_synthesis_context_chars(contract: dict[str, Any]) -> int:
    normalized = dict(contract or {})
    return sum(
        len(str(normalized.get(key) or ""))
        for key in ("pdf_synthesis_context", "table_synthesis_context", "kb_synthesis_context")
    )


def _clone_payload_without_internal_state(payload: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(payload or {})
    cloned.pop("_hybrid_internal_state", None)
    cloned.pop("_table_synthesis_context", None)
    cloned.pop("_skip_file_route_cache", None)
    metadata = dict(cloned.get("metadata") or {})
    metadata.pop("_hybrid_internal_state", None)
    cloned["metadata"] = metadata
    return cloned


def _should_skip_file_route_cache(payload: dict[str, Any]) -> bool:
    normalized = dict(payload or {})
    if bool(normalized.get("_skip_file_route_cache")):
        return True
    metadata = dict(normalized.get("metadata") or {})
    return bool(metadata.get("_skip_file_route_cache"))


def _file_route_runtime_signature(
    *,
    plan: PatentFileRoutePlan,
    pdf_service: PatentPdfService,
    tabular_service: PatentTabularService,
    hybrid_synthesis_service: Any | None = None,
) -> dict[str, Any]:
    tabular_runtime_signature = getattr(tabular_service, "runtime_signature", None)
    hybrid_runtime_signature = getattr(hybrid_synthesis_service, "runtime_signature", None)
    runtime_signature = {
        "handler": plan.handler,
        "include_kb": bool(plan.include_kb),
        "pdf_service_type": type(pdf_service).__name__,
        "hybrid_synthesis_backend": "llm" if hybrid_synthesis_service is not None else "fallback_rules",
        "hybrid_synthesis_prompt_version": HYBRID_SYNTHESIS_PROMPT_VERSION,
        "hybrid_runtime_signature": dict(hybrid_runtime_signature() or {}) if callable(hybrid_runtime_signature) else {},
    }
    if _plan_uses_table(plan):
        runtime_signature.update(
            {
                "tabular_service_type": type(tabular_service).__name__,
                "tabular_answer_backend": getattr(tabular_service, "answer_backend", lambda: "fallback")(),
                "tabular_prompt_version": getattr(tabular_service, "prompt_version", lambda: "")(),
                "tabular_runtime_signature": dict(tabular_runtime_signature() or {}) if callable(tabular_runtime_signature) else {},
                "tabular_max_context_chars": int(getattr(tabular_service, "_max_table_chars", 0) or 0),
                "hybrid_table_context_chars": _table_hybrid_context_limit(),
                "table_parity_signature": {
                    "planner_version": _PATENT_TABLE_PLANNER_VERSION,
                    "summary_context_version": _PATENT_TABLE_SUMMARY_CONTEXT_VERSION,
                    "prompt_version": getattr(tabular_service, "prompt_version", lambda: "")(),
                    "table_context_budget": max(
                        int(getattr(tabular_service, "_max_table_chars", 0) or 0),
                        _table_hybrid_context_limit(),
                    ),
                },
            }
        )
    return runtime_signature


def _mark_file_route_cache_metadata(
    *,
    result: dict[str, Any],
    fingerprint: str,
    cache_hit: bool,
) -> dict[str, Any]:
    payload = dict(result or {})
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "cache_hit": bool(cache_hit),
            "cache_namespace": "file-route",
            "cache_fingerprint": str(fingerprint or "").strip(),
        }
    )
    payload["metadata"] = metadata
    payload["cache_hit"] = bool(cache_hit)
    return payload


def _emit_cached_content(
    *,
    answer_text: str,
    content_callback: Callable[[str], None] | None,
    content_source: str,
) -> int:
    if not callable(content_callback):
        return 0
    emit_snapshot = getattr(content_callback, "emit_snapshot", None)
    if callable(emit_snapshot):
        emit_snapshot(answer_text)
        return 1 if str(answer_text or "") else 0
    emit_final_snapshot = getattr(content_callback, "emit_final_snapshot", None)
    if callable(emit_final_snapshot):
        emit_final_snapshot(content=answer_text, content_source=content_source)
        return 1 if str(answer_text or "") else 0
    return emit_text_chunks(str(answer_text or ""), content_callback=content_callback)


def _run_cached_file_route(
    *,
    execution_cache: Any | None,
    fingerprint: str,
    compute,
    cache_ttl_seconds: int,
    singleflight_ttl_seconds: int,
    singleflight_poll_interval_seconds: float,
    singleflight_renew_interval_seconds: float,
    singleflight_wait_timeout_seconds: float | None,
) -> dict[str, Any]:
    cache = execution_cache
    if cache is None or not bool(getattr(cache, "available", True)):
        return _mark_file_route_cache_metadata(
            result=dict(compute() or {}),
            fingerprint=fingerprint,
            cache_hit=False,
        )

    try:
        cached = cache.get_file_route_cache(fingerprint=fingerprint)
    except Exception:
        return _mark_file_route_cache_metadata(
            result=dict(compute() or {}),
            fingerprint=fingerprint,
            cache_hit=False,
        )
    if cached is not None:
        _LOGGER.info("patent file-route cache hit fingerprint=%s", str(fingerprint or "")[:16])
        return _mark_file_route_cache_metadata(
            result=dict(cached or {}),
            fingerprint=fingerprint,
            cache_hit=True,
        )

    try:
        token = cache.claim_file_route_singleflight(
            fingerprint=fingerprint,
            ttl_seconds=singleflight_ttl_seconds,
        )
    except Exception:
        return _mark_file_route_cache_metadata(
            result=dict(compute() or {}),
            fingerprint=fingerprint,
            cache_hit=False,
        )

    claimed = bool(token)
    renew_stop: threading.Event | None = None
    renew_thread: threading.Thread | None = None
    renew_error: list[str] = []
    try:
        if not claimed:
            wait_timeout = (
                float(singleflight_ttl_seconds)
                if singleflight_wait_timeout_seconds is None
                else float(singleflight_wait_timeout_seconds)
            )
            deadline = time.monotonic() + wait_timeout
            while True:
                try:
                    cached = cache.get_file_route_cache(fingerprint=fingerprint)
                except Exception:
                    return _mark_file_route_cache_metadata(
                        result=dict(compute() or {}),
                        fingerprint=fingerprint,
                        cache_hit=False,
                    )
                if cached is not None:
                    _LOGGER.info("patent file-route cache hit after singleflight wait fingerprint=%s", str(fingerprint or "")[:16])
                    return _mark_file_route_cache_metadata(
                        result=dict(cached or {}),
                        fingerprint=fingerprint,
                        cache_hit=True,
                    )
                try:
                    owner = str(
                        getattr(cache, "get_file_route_singleflight_owner", lambda **_kwargs: "")(
                            fingerprint=fingerprint,
                        )
                        or ""
                    ).strip()
                except Exception:
                    return _mark_file_route_cache_metadata(
                        result=dict(compute() or {}),
                        fingerprint=fingerprint,
                        cache_hit=False,
                    )
                if not owner:
                    try:
                        token = cache.claim_file_route_singleflight(
                            fingerprint=fingerprint,
                            ttl_seconds=singleflight_ttl_seconds,
                        )
                    except Exception:
                        return _mark_file_route_cache_metadata(
                            result=dict(compute() or {}),
                            fingerprint=fingerprint,
                            cache_hit=False,
                        )
                    claimed = bool(token)
                    if claimed:
                        break
                    try:
                        cached = cache.get_file_route_cache(fingerprint=fingerprint)
                    except Exception:
                        return _mark_file_route_cache_metadata(
                            result=dict(compute() or {}),
                            fingerprint=fingerprint,
                            cache_hit=False,
                        )
                    if cached is not None:
                        _LOGGER.info("patent file-route cache hit after contention handoff fingerprint=%s", str(fingerprint or "")[:16])
                        return _mark_file_route_cache_metadata(
                            result=dict(cached or {}),
                            fingerprint=fingerprint,
                            cache_hit=True,
                        )
                    try:
                        owner = str(
                            getattr(cache, "get_file_route_singleflight_owner", lambda **_kwargs: "")(
                                fingerprint=fingerprint,
                            )
                            or ""
                        ).strip()
                    except Exception:
                        return _mark_file_route_cache_metadata(
                            result=dict(compute() or {}),
                            fingerprint=fingerprint,
                            cache_hit=False,
                        )
                    if owner and singleflight_wait_timeout_seconds is None:
                        deadline = time.monotonic() + float(singleflight_ttl_seconds)
                elif singleflight_wait_timeout_seconds is None:
                    deadline = time.monotonic() + float(singleflight_ttl_seconds)
                if time.monotonic() > deadline:
                    raise TimeoutError("singleflight wait timed out for file-route")
                time.sleep(singleflight_poll_interval_seconds)

        renew = getattr(cache, "renew_file_route_singleflight", None)
        if claimed and callable(renew):
            renew_stop = threading.Event()

            def _renew_loop() -> None:
                while renew_stop is not None and not renew_stop.wait(singleflight_renew_interval_seconds):
                    try:
                        renewed = renew(
                            fingerprint=fingerprint,
                            token=str(token or ""),
                            ttl_seconds=singleflight_ttl_seconds,
                        )
                    except Exception as exc:
                        renew_error.append(str(exc))
                        renew_stop.set()
                        return
                    if renewed:
                        continue
                    renew_error.append(
                        str(getattr(cache, "last_error", "") or "file-route singleflight renew failed").strip()
                    )
                    renew_stop.set()
                    return

            renew_thread = threading.Thread(
                target=_renew_loop,
                name="patent-file-route-singleflight-renew",
                daemon=True,
            )
            renew_thread.start()

        computed = dict(compute() or {})
        skip_cache = _should_skip_file_route_cache(computed)
        preserve_internal_state = bool(computed.get("_hybrid_internal_state")) or (
            bool(computed.get("_table_synthesis_context")) and bool(computed.get("kb_enabled"))
        )
        computed = _clone_payload_without_internal_state(computed) if not preserve_internal_state else {
            **dict(computed),
            "_skip_file_route_cache": None,
        }
        computed.pop("_skip_file_route_cache", None)
        if renew_stop is not None:
            renew_stop.set()
        if renew_thread is not None and renew_thread.is_alive():
            renew_thread.join(timeout=0.05)
            if renew_thread.is_alive() and not renew_error:
                renew_error.append("file-route singleflight renew completion pending")
        if not renew_error and not skip_cache:
            try:
                cache.set_file_route_cache(
                    fingerprint=fingerprint,
                    payload=dict(computed),
                    ttl_seconds=cache_ttl_seconds,
                )
            except Exception:
                pass
        return _mark_file_route_cache_metadata(
            result=computed,
            fingerprint=fingerprint,
            cache_hit=False,
        )
    finally:
        if renew_stop is not None:
            renew_stop.set()
        if renew_thread is not None and renew_thread.is_alive():
            renew_thread.join(timeout=0.05)
        if claimed:
            try:
                cache.clear_file_route_singleflight(
                    fingerprint=fingerprint,
                    token=str(token or ""),
                )
            except Exception:
                pass


def plan_patent_file_route(contract: PatentFileContract) -> PatentFileRoutePlan:
    if contract.route == "pdf_qa":
        return PatentFileRoutePlan(
            route=contract.route,
            source_scope=contract.source_scope,
            handler="pdf",
            file_families=("pdf",),
            include_kb=False,
        )
    if contract.route == "tabular_qa":
        return PatentFileRoutePlan(
            route=contract.route,
            source_scope=contract.source_scope,
            handler="tabular",
            file_families=("table",),
            include_kb=False,
        )
    handler, file_families, include_kb = _HYBRID_SCOPE_TO_PLAN[contract.source_scope]
    return PatentFileRoutePlan(
        route=contract.route,
        source_scope=contract.source_scope,
        handler=handler,
        file_families=file_families,
        include_kb=include_kb,
    )


def dispatch_patent_file_route(
    *,
    contract: PatentFileContract,
    pdf_service: PatentPdfService | None = None,
    tabular_service: PatentTabularService | None = None,
    hybrid_synthesis_service: Any | None = None,
    execution_cache: Any | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    content_callback: Callable[[str], None] | None = None,
    cache_ttl_seconds: int = 300,
    singleflight_ttl_seconds: int = 30,
    singleflight_poll_interval_seconds: float = 0.01,
    singleflight_renew_interval_seconds: float | None = None,
    singleflight_wait_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    plan = plan_patent_file_route(contract)
    pdf_handler = pdf_service or PatentPdfService()
    tabular_handler = tabular_service or PatentTabularService()
    dispatch_step = _route_dispatch_step(plan.handler)
    _LOGGER.info(
        "patent file-route dispatch route=%s source_scope=%s handler=%s include_kb=%s content_callback=%s",
        contract.route,
        contract.source_scope,
        plan.handler,
        plan.include_kb,
        callable(content_callback),
    )
    renew_interval = (
        min(float(max(1, int(singleflight_ttl_seconds))) / 3.0, 10.0)
        if singleflight_renew_interval_seconds is None
        else float(singleflight_renew_interval_seconds)
    )
    cache_fingerprint = build_file_route_cache_fingerprint(
        question=contract.question,
        route=contract.route,
        source_scope=contract.source_scope,
        selected_file_ids=list(contract.selected_file_ids),
        primary_file_id=contract.primary_file_id,
        selected_execution_files=[item.as_payload() for item in contract.selected_execution_files],
        file_selection=dict(contract.file_selection),
        runtime_signature=_file_route_runtime_signature(
            plan=plan,
            pdf_service=pdf_handler,
            tabular_service=tabular_handler,
            hybrid_synthesis_service=hybrid_synthesis_service,
        ),
    )
    if callable(progress_callback):
        progress_callback(dict(dispatch_step))
    if plan.handler == "pdf":
        service = pdf_handler
        result = _run_cached_file_route(
            execution_cache=execution_cache,
            fingerprint=cache_fingerprint,
            compute=lambda: _with_leading_steps(
                result=_call_with_supported_kwargs(
                    service.execute,
                    contract=contract,
                    include_kb=plan.include_kb,
                    progress_callback=progress_callback,
                    content_callback=content_callback,
                ),
                steps=[dispatch_step],
            ),
            cache_ttl_seconds=max(1, int(cache_ttl_seconds)),
            singleflight_ttl_seconds=max(1, int(singleflight_ttl_seconds)),
            singleflight_poll_interval_seconds=max(0.0, float(singleflight_poll_interval_seconds)),
            singleflight_renew_interval_seconds=max(0.001, renew_interval),
            singleflight_wait_timeout_seconds=(
                None if singleflight_wait_timeout_seconds is None else max(0.0, float(singleflight_wait_timeout_seconds))
            ),
        )
        if (
            callable(content_callback)
            and bool(dict(result.get("metadata") or {}).get("cache_hit"))
            and not plan.include_kb
        ):
            emitted = _emit_cached_content(
                answer_text=str(result.get("answer_text") or ""),
                content_callback=content_callback,
                content_source="pdf",
            )
            _LOGGER.info(
                "patent file-route cached replay route=%s handler=pdf emitted_chunks=%s answer_chars=%s",
                contract.route,
                emitted,
                len(str(result.get("answer_text") or "")),
            )
        return result
    if plan.handler == "tabular":
        service = tabular_handler
        result = _run_cached_file_route(
            execution_cache=execution_cache,
            fingerprint=cache_fingerprint,
            compute=lambda: _with_leading_steps(
                result=_call_with_supported_kwargs(
                    service.execute,
                    contract=contract,
                    include_kb=plan.include_kb,
                    progress_callback=progress_callback,
                    content_callback=content_callback,
                ),
                steps=[dispatch_step],
            ),
            cache_ttl_seconds=max(1, int(cache_ttl_seconds)),
            singleflight_ttl_seconds=max(1, int(singleflight_ttl_seconds)),
            singleflight_poll_interval_seconds=max(0.0, float(singleflight_poll_interval_seconds)),
            singleflight_renew_interval_seconds=max(0.001, renew_interval),
            singleflight_wait_timeout_seconds=(
                None if singleflight_wait_timeout_seconds is None else max(0.0, float(singleflight_wait_timeout_seconds))
            ),
        )
        if (
            callable(content_callback)
            and bool(dict(result.get("metadata") or {}).get("cache_hit"))
            and not plan.include_kb
        ):
            emitted = _emit_cached_content(
                answer_text=str(result.get("answer_text") or ""),
                content_callback=content_callback,
                content_source="table",
            )
            _LOGGER.info(
                "patent file-route cached replay route=%s handler=tabular emitted_chunks=%s answer_chars=%s",
                contract.route,
                emitted,
                len(str(result.get("answer_text") or "")),
            )
        return result
    result = _run_cached_file_route(
        execution_cache=execution_cache,
        fingerprint=cache_fingerprint,
        compute=lambda: _build_hybrid_result(
            contract=contract,
            include_kb=plan.include_kb,
            pdf_service=pdf_handler,
            tabular_service=tabular_handler,
            hybrid_synthesis_service=hybrid_synthesis_service,
            progress_callback=progress_callback,
            content_callback=content_callback,
            dispatch_step=dispatch_step,
        ),
        cache_ttl_seconds=max(1, int(cache_ttl_seconds)),
        singleflight_ttl_seconds=max(1, int(singleflight_ttl_seconds)),
        singleflight_poll_interval_seconds=max(0.0, float(singleflight_poll_interval_seconds)),
        singleflight_renew_interval_seconds=max(0.001, renew_interval),
        singleflight_wait_timeout_seconds=(
            None if singleflight_wait_timeout_seconds is None else max(0.0, float(singleflight_wait_timeout_seconds))
        ),
    )
    if callable(content_callback) and bool(dict(result.get("metadata") or {}).get("cache_hit")) and not plan.include_kb:
        emitted = _emit_cached_content(
            answer_text=str(result.get("answer_text") or ""),
            content_callback=content_callback,
            content_source="hybrid",
        )
        _LOGGER.info(
            "patent file-route cached replay route=%s handler=hybrid emitted_chunks=%s answer_chars=%s include_kb=%s",
            contract.route,
            emitted,
            len(str(result.get("answer_text") or "")),
            plan.include_kb,
        )
    return result


def _build_hybrid_result(
    *,
    contract: PatentFileContract,
    include_kb: bool,
    pdf_service: PatentPdfService,
    tabular_service: PatentTabularService,
    hybrid_synthesis_service: Any | None,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    content_callback: Callable[[str], None] | None,
    dispatch_step: dict[str, Any],
) -> dict[str, Any]:
    used_files = [item.as_payload() for item in contract.selected_execution_files]
    profile = get_patent_mode_profile(contract.route)
    build_preview_emitter = getattr(content_callback, "preview_emitter", None)
    build_final_emitter = getattr(content_callback, "final_emitter", None)
    pdf_preview_emitter = (
        build_preview_emitter(content_source="pdf", content_stream_id="pdf:primary")
        if callable(build_preview_emitter)
        else None
    )
    table_preview_emitter = (
        build_preview_emitter(content_source="table", content_stream_id="table:selected")
        if callable(build_preview_emitter)
        else None
    )

    pdf_branch_succeeded = False
    try:
        pdf_result = _call_with_supported_kwargs(
            pdf_service.execute,
            contract=contract,
            include_kb=False,
            progress_callback=progress_callback,
            content_callback=pdf_preview_emitter,
        )
        pdf_branch_succeeded = True
    finally:
        if pdf_preview_emitter is not None:
            if pdf_branch_succeeded:
                pdf_preview_emitter.close()
            else:
                pdf_preview_emitter.abort()

    table_branch_succeeded = False
    try:
        tabular_result = _call_with_supported_kwargs(
            tabular_service.execute,
            contract=contract,
            include_kb=False,
            progress_callback=progress_callback,
            content_callback=table_preview_emitter,
        )
        table_branch_succeeded = True
    finally:
        if table_preview_emitter is not None:
            if table_branch_succeeded:
                table_preview_emitter.close()
            else:
                table_preview_emitter.abort()
    _LOGGER.info(
        "patent hybrid file-route branch results route=%s source_scope=%s include_kb=%s outer_content_callback=%s pdf_branch_stream=%s tabular_branch_stream=%s pdf_chars=%s tabular_chars=%s",
        contract.route,
        contract.source_scope,
        include_kb,
        callable(content_callback),
        pdf_preview_emitter is not None,
        table_preview_emitter is not None,
        len(str(pdf_result.get("answer_text") or "")),
        len(str(tabular_result.get("answer_text") or "")),
    )
    if callable(progress_callback) and not include_kb:
        progress_callback(
            {
                "step": "hybrid_answer",
                "title": "整合文件答案",
                "message": "🧩 正在整合 PDF 与表格原始内容...",
                "status": "running",
            }
        )
    pdf_answer = str(pdf_result.get("answer_text") or "").strip()
    tabular_answer = str(tabular_result.get("answer_text") or "").strip()
    pdf_metadata = dict(pdf_result.get("metadata") or {})
    tabular_metadata = dict(tabular_result.get("metadata") or {})
    synthesis_contract = build_patent_hybrid_synthesis_contract(
        question=contract.question,
        source_scope=contract.source_scope,
        pdf_answer=pdf_answer,
        tabular_answer=tabular_answer,
        pdf_evidence_context=str(pdf_metadata.get("pdf_evidence_context") or ""),
        table_execution_context=str(tabular_metadata.get("table_evidence_context") or ""),
        pdf_synthesis_context=build_pdf_synthesis_context(
            prepared_pdf_text=str(pdf_metadata.get("prepared_pdf_text") or ""),
            pdf_text="",
        ),
        table_synthesis_context=_trim_text(
            tabular_result.get("_table_synthesis_context") or tabular_metadata.get("table_evidence_context") or "",
            limit=_table_hybrid_context_limit(),
        ),
        include_kb=include_kb,
        available_sources=["pdf", "table"],
        source_answer_modes={
            "pdf": str(pdf_metadata.get("answer_mode") or ""),
            "table": str(tabular_metadata.get("answer_mode") or ""),
        },
    )
    hybrid_backend = "fallback_rules"
    skip_cache = False
    answer_text = synthesize_patent_hybrid_answer(synthesis_contract=synthesis_contract)
    if (
        not include_kb
        and hybrid_synthesis_service is not None
        and _has_usable_hybrid_evidence(synthesis_contract=synthesis_contract)
    ):
        try:
            candidate = str(
                _call_with_supported_kwargs(
                    hybrid_synthesis_service.answer,
                    synthesis_contract=synthesis_contract,
                )
                or ""
            ).strip()
            if candidate:
                answer_text, used_fallback_rules = _normalize_patent_hybrid_answer(
                    answer=candidate,
                    synthesis_contract=synthesis_contract,
                )
                hybrid_backend = "fallback_rules" if used_fallback_rules else "llm"
        except Exception:
            skip_cache = True
            _LOGGER.warning(
                "patent hybrid synthesis service failed; degrading to fallback rules route=%s source_scope=%s",
                contract.route,
                contract.source_scope,
                exc_info=True,
            )
    hybrid_success = _has_usable_hybrid_evidence(synthesis_contract=synthesis_contract)
    hybrid_step = {
        "step": "hybrid_answer",
        "title": "整合文件答案",
        "message": (
            f"🧩 已整合 PDF 与表格原始内容，共 {len(used_files)} 个文件"
            if hybrid_success
            else "🧩 文件统一合成失败：当前没有可用于联合回答的文件证据"
        ),
        "status": "success" if hybrid_success else "error",
        "data": {"count": len(used_files)},
    }
    include_file_hybrid_step = not include_kb
    if callable(content_callback) and include_file_hybrid_step:
        if callable(build_final_emitter):
            final_emitter = build_final_emitter(content_source="hybrid")
            final_succeeded = False
            try:
                emitted = emit_text_chunks(answer_text, content_callback=final_emitter)
                final_succeeded = True
            finally:
                if final_succeeded:
                    final_emitter.close()
                else:
                    final_emitter.abort()
        else:
            emitted = emit_text_chunks(answer_text, content_callback=content_callback)
        _LOGGER.info(
            "patent hybrid synthesized replay route=%s source_scope=%s emitted_chunks=%s answer_chars=%s",
            contract.route,
            contract.source_scope,
            emitted,
            len(answer_text),
        )
    if callable(progress_callback) and include_file_hybrid_step:
        progress_callback(dict(hybrid_step))
    hybrid_steps = [dict(hybrid_step)] if include_file_hybrid_step else []
    return {
        "handler": "hybrid",
        "answer_text": answer_text,
        "route": contract.route,
        "query_mode": profile.query_mode,
        "source_scope": contract.source_scope,
        "steps": [
            dict(dispatch_step),
            *[dict(item) for item in list(pdf_result.get("steps") or []) if isinstance(item, dict)],
            *[dict(item) for item in list(tabular_result.get("steps") or []) if isinstance(item, dict)],
            *hybrid_steps,
        ],
        "metadata": {
            "handler": "hybrid",
            "source_scope": contract.source_scope,
            "selected_file_count": len(used_files),
            "kb_enabled": bool(include_kb),
            "answer_mode": "hybrid_unified_synthesis",
            "pdf_answer_mode": str(pdf_metadata.get("answer_mode") or ""),
            "tabular_answer_mode": str(tabular_metadata.get("answer_mode") or ""),
            "hybrid_synthesis_backend": hybrid_backend,
            "hybrid_synthesis_prompt_version": HYBRID_SYNTHESIS_PROMPT_VERSION,
            "hybrid_synthesis_context_chars": _hybrid_synthesis_context_chars(synthesis_contract),
            "synthesis_contract": _public_hybrid_synthesis_contract(synthesis_contract),
            "steps": [
                dict(dispatch_step),
                *[dict(item) for item in list(pdf_result.get("steps") or []) if isinstance(item, dict)],
                *[dict(item) for item in list(tabular_result.get("steps") or []) if isinstance(item, dict)],
                *hybrid_steps,
            ],
        },
        "timings": {
            **dict(pdf_result.get("timings") or {}),
            **dict(tabular_result.get("timings") or {}),
            "patent_hybrid_route_ms": 1,
        },
        "used_files": used_files,
        "selected_file_ids": list(contract.selected_file_ids),
        "file_selection": dict(contract.file_selection),
        "kb_enabled": bool(include_kb),
        "_skip_file_route_cache": bool(skip_cache),
        **(
            {"_hybrid_internal_state": {"synthesis_contract": dict(synthesis_contract)}}
            if include_kb
            else {}
        ),
    }


def _route_dispatch_step(handler: str) -> dict[str, Any]:
    normalized = str(handler or "").strip().lower()
    if normalized == "pdf":
        return {
            "step": "dispatch",
            "title": "进入 PDF 分支",
            "message": "进入 PDF 问答分支",
            "status": "success",
        }
    return {
        "step": "dispatch",
        "title": "进入文件分支",
        "message": "进入表格/混合问答分支",
        "status": "success",
    }


def _with_leading_steps(*, result: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    payload = dict(result or {})
    payload["steps"] = [
        *[dict(item) for item in steps if isinstance(item, dict)],
        *[dict(item) for item in list(payload.get("steps") or []) if isinstance(item, dict)],
    ]
    metadata = dict(payload.get("metadata") or {})
    metadata["steps"] = [dict(item) for item in payload["steps"]]
    payload["metadata"] = metadata
    return payload

def _collect_hybrid_points(*values: str, max_items: int = 4) -> list[str]:
    points: list[str] = []
    skipped_titles = {"研究目的和背景", "研究方法/实验设计", "主要发现和结果", "结论和意义", "结论", "证据", "对比", "限制"}
    for value in values:
        normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            continue
        for raw_line in normalized.splitlines():
            line = re.sub(r"^[#>\-\*\d\.\)\s]+", "", raw_line).strip()
            if len(line) < 8:
                continue
            if line in skipped_titles or line.startswith("注*"):
                continue
            if line in points:
                continue
            points.append(_clip_lead_text(line, limit=220))
            if len(points) >= max_items:
                return points
    return points


def _strip_pdf_context_headers(text: str) -> str:
    lines = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("==== 文献 "):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_hybrid_summary_candidates(*values: str, max_items: int = 6, min_chars: int = 10) -> list[str]:
    candidates: list[str] = []
    skipped_titles = {
        "研究目的和背景",
        "研究方法/实验设计",
        "主要发现和结果",
        "结论和意义",
        "局限性",
        "结论",
        "证据",
        "对比",
        "限制",
    }
    for value in values:
        normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            continue
        for raw_part in re.split(r"(?<=[。！？.!?])\s*|\n+", normalized):
            line = re.sub(r"^[#>\-\*\d\.\)\s]+", "", raw_part).strip()
            line = re.sub(r"^(?:PDF 原文证据：|表格执行结果：|知识库证据：|知识库补充：|真实表格总结：|真实 PDF 总结：)", "", line).strip()
            if not line or line.startswith("==== 文献 "):
                continue
            if _is_tabular_structure_line(line):
                continue
            if "壳子" in line or "不应主导最终格式" in line:
                continue
            if _is_gap_wording(line):
                continue
            if len(line) < int(min_chars):
                continue
            if line in skipped_titles or line.startswith("注*"):
                continue
            if line in candidates:
                continue
            candidates.append(_clip_lead_text(line, limit=220))
            if len(candidates) >= int(max_items):
                return candidates
    return candidates


def _is_tabular_structure_line(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    inline_markers = (
        "匹配工作表:",
        "执行操作:",
        "聚合方式:",
        "分组列:",
        "指标列:",
        "返回列:",
        "过滤条件:",
        "命中行数:",
        "空结果原因:",
        "结果样例:",
        "代表性行:",
        "数据行数:",
    )
    if any(marker in normalized for marker in inline_markers):
        return True
    if re.match(
        r"^(?:文件|工作表|匹配工作表|执行操作|聚合方式|分组列|指标列|返回列|过滤条件|命中行数|空结果原因|结果样例|列|数据行数|代表性行)\s*:",
        normalized,
    ):
        return True
    return bool(re.match(r"^(?:样例)\s*\d+\s*:", normalized))


def _parse_tabular_sample_point(text: str) -> str:
    raw_text = str(text or "").strip()
    matched = re.search(r"(样例\s*\d+\s*:\s*.+)$", raw_text)
    normalized = matched.group(1) if matched else raw_text
    normalized = re.sub(r"^[#>\-\*\s]*样例\s*\d+\s*:\s*", "", normalized)
    if not normalized:
        return ""
    pairs: list[tuple[str, str]] = []
    for raw_part in normalized.split(";"):
        if "=" not in raw_part:
            continue
        key, value = raw_part.split("=", 1)
        clean_key = str(key or "").strip()
        clean_value = str(value or "").strip()
        if not clean_key or not clean_value:
            continue
        pairs.append((clean_key, clean_value))
    if not pairs:
        return ""

    values = {key.lower(): value for key, value in pairs}
    material = values.get("material", "")
    capacity = values.get("capacity_mah", "")
    note = values.get("note", "")
    if material and capacity:
        summary = f"{material} {capacity}mAh"
        if note:
            summary += f"（{note}）"
        return summary

    score = values.get("score", "")
    rate = values.get("rate_c", "")
    temp = values.get("temp_c", "")
    if score and (rate or temp):
        conditions = [item for item in (f"rate_c={rate}" if rate else "", f"temp_c={temp}" if temp else "") if item]
        if conditions:
            return f"{', '.join(conditions)} 时 score={score}"

    formatted_parts = [f"{key}={value}" for key, value in pairs[:3]]
    return "；".join(formatted_parts)


def _extract_tabular_context_points(text: str, *, max_items: int = 3) -> list[str]:
    points: list[str] = []
    normalized_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    sample_matches = re.findall(r"样例\s*\d+\s*:\s*.*?(?=(?:样例\s*\d+\s*:|$))", normalized_text)
    for raw_line in [*sample_matches, *normalized_text.splitlines()]:
        line = str(raw_line or "").strip()
        if not line:
            continue
        parsed = _parse_tabular_sample_point(line)
        if not parsed or parsed in points:
            continue
        points.append(_clip_lead_text(parsed, limit=220))
        if len(points) >= int(max_items):
            break
    return points


def _extract_hybrid_answer_point(text: str, headings: tuple[str, ...]) -> str:
    for heading in headings:
        candidate = _extract_markdown_section_first_bullet(text, heading)
        if candidate and not _is_gap_wording(candidate) and not _is_degraded_answer(candidate):
            return candidate
    candidates = _extract_hybrid_summary_candidates(text, max_items=2, min_chars=10)
    if candidates and not _is_gap_wording(candidates[0]) and not _is_degraded_answer(candidates[0]):
        return candidates[0]
    return ""


def _select_candidates_by_keywords(
    candidates: list[str],
    *,
    keywords: tuple[str, ...],
    max_items: int,
) -> list[str]:
    selected: list[str] = []
    for candidate in candidates:
        lowered = str(candidate or "").lower()
        if not any(keyword in lowered for keyword in keywords):
            continue
        if candidate in selected:
            continue
        selected.append(candidate)
        if len(selected) >= int(max_items):
            break
    return selected


def _build_file_only_hybrid_summary_section(title: str, points: list[str], fallback: str) -> list[str]:
    lines = [f"## {title}"]
    if points:
        lines.extend(f"- {point}" for point in points)
    else:
        lines.append(f"- {fallback}")
    lines.append("")
    return lines


def _extract_markdown_section_first_bullet(text: str, heading: str) -> str:
    normalized = str(text or "")
    marker = f"## {heading}"
    start = normalized.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    next_heading = normalized.find("\n## ", start)
    body = normalized[start:] if next_heading < 0 else normalized[start:next_heading]
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            candidate = re.sub(r"^(?:真实 PDF 总结：|真实表格总结：)", "", line[2:].strip()).strip()
            if (
                not candidate
                or candidate.startswith("==== 文献 ")
                or candidate.startswith("注*")
                or _is_gap_wording(candidate)
                or _is_degraded_answer(candidate)
                or "壳子" in candidate
                or "不应主导最终格式" in candidate
                or _is_tabular_structure_line(candidate)
            ):
                continue
            return candidate
    for raw_line in body.splitlines():
        line = re.sub(r"^(?:真实 PDF 总结：|真实表格总结：)", "", raw_line.strip()).strip()
        if (
            not line
            or line.startswith("## ")
            or line.startswith("==== 文献 ")
            or line.startswith("注*")
            or _is_gap_wording(line)
            or _is_degraded_answer(line)
            or "壳子" in line
            or "不应主导最终格式" in line
            or _is_tabular_structure_line(line)
        ):
            continue
        return line
    return ""


def _is_gap_wording(text: str) -> bool:
    normalized = str(text or "").strip()
    return bool(normalized) and any(
        marker in normalized
        for marker in (
            "PDF中未提及",
            "原文证据不足",
            "表格中未提供足够",
            "表格未提供足够",
            "当前文件证据中未提供足够",
        )
    )


def _file_only_table_summary_point(*, table_execution_context: str, tabular_answer: str) -> str:
    answer_point = _extract_hybrid_answer_point(
        tabular_answer,
        ("主要发现和结果", "结论和意义", "结论", "证据"),
    )
    if answer_point:
        return answer_point
    context_points = _extract_tabular_context_points(table_execution_context, max_items=2)
    if context_points:
        return context_points[0]
    table_lead = _lead_from_table_context(table_execution_context)
    if not table_lead:
        return ""
    if _is_gap_wording(table_lead):
        return ""
    if any(marker in table_lead for marker in ("工作表:", "列:", "数据行数:", "代表性行:", "material=", "capacity_mAh=", "note=")):
        return "表格结果补充了文件侧涉及的关键数据对照信息。"
    return table_lead


def _hybrid_table_evidence_point(*, table_execution_context: str, tabular_answer: str) -> str:
    answer_point = _extract_hybrid_answer_point(
        tabular_answer,
        ("主要发现和结果", "结论和意义", "结论", "证据"),
    )
    if answer_point:
        return answer_point
    context_points = _extract_tabular_context_points(table_execution_context, max_items=2)
    if context_points:
        return "；".join(context_points[:2])
    table_lead = _lead_from_table_context(table_execution_context)
    if not table_lead or _is_gap_wording(table_lead):
        return ""
    return table_lead


def _select_file_only_hybrid_conclusion(
    *,
    pdf_evidence_context: str,
    pdf_answer: str,
    table_execution_context: str,
    tabular_answer: str,
) -> str:
    pdf_conclusion_point = _extract_markdown_section_first_bullet(pdf_answer, "结论和意义")
    if _is_gap_wording(pdf_conclusion_point):
        pdf_conclusion_point = ""
    pdf_result_point = _extract_markdown_section_first_bullet(pdf_answer, "主要发现和结果")
    if _is_gap_wording(pdf_result_point):
        pdf_result_point = ""
    pdf_answer_candidates = _extract_hybrid_summary_candidates(pdf_answer, max_items=2, min_chars=10)
    table_point = _file_only_table_summary_point(
        table_execution_context=table_execution_context,
        tabular_answer=tabular_answer,
    )
    for candidate in (
        pdf_conclusion_point,
        pdf_result_point,
        (pdf_answer_candidates[0] if pdf_answer_candidates and not _is_gap_wording(pdf_answer_candidates[0]) else ""),
        _lead_from_pdf_context(pdf_evidence_context),
        table_point,
    ):
        if candidate:
            return candidate
    return ""


def _synthesize_file_only_hybrid_summary(*, synthesis_contract: dict[str, Any]) -> str:
    contract = dict(synthesis_contract or {})
    pdf_answer = str(contract.get("pdf_answer") or "").strip()
    tabular_answer = str(contract.get("tabular_answer") or "").strip()
    pdf_evidence_context = _strip_pdf_context_headers(str(contract.get("pdf_evidence_context") or ""))
    table_execution_context = str(contract.get("table_execution_context") or "").strip()

    if not _has_usable_hybrid_evidence(
        synthesis_contract={
            "pdf_evidence_context": pdf_evidence_context,
            "table_execution_context": table_execution_context,
            "pdf_answer": pdf_answer,
            "tabular_answer": tabular_answer,
        }
    ):
        return "当前未拿到可读的 PDF 或表格证据，暂时无法生成联合回答。"

    pdf_candidates = _extract_hybrid_summary_candidates(pdf_evidence_context, pdf_answer, max_items=8, min_chars=10)
    table_candidates = _extract_hybrid_summary_candidates(table_execution_context, tabular_answer, max_items=8, min_chars=10)
    table_context_points = _extract_tabular_context_points(table_execution_context, max_items=3)
    merged_candidates = _extract_hybrid_summary_candidates(
        pdf_evidence_context,
        pdf_answer,
        table_execution_context,
        tabular_answer,
        max_items=12,
        min_chars=10,
    )

    background_points = _select_candidates_by_keywords(
        pdf_candidates or merged_candidates,
        keywords=("study", "studies", "研究", "背景", "目的", "挑战", "问题", "motivation", "background", "aim"),
        max_items=2,
    )
    if not background_points and pdf_candidates:
        background_points = pdf_candidates[:2]

    method_points = _select_candidates_by_keywords(
        pdf_candidates,
        keywords=("method", "methods", "实验", "方法", "对比", "测量", "表征", "setup", "compare", "test"),
        max_items=2,
    )
    table_point = _file_only_table_summary_point(
        table_execution_context=table_execution_context,
        tabular_answer=tabular_answer,
    )
    if table_point:
        method_points.append(f"表格结果补充了文件侧涉及的关键数据对照：{table_point}")
    method_points = [item for index, item in enumerate(method_points) if item and item not in method_points[:index]][:3]

    result_points = _select_candidates_by_keywords(
        [*table_context_points, *merged_candidates],
        keywords=("result", "results", "改善", "提升", "更安全", "capacity", "mah", "charging", "性能", "结果", "发现"),
        max_items=3,
    )
    for item in [*table_context_points, table_point]:
        if item and item not in result_points:
            result_points.append(item)
    result_points = result_points[:3]

    lead = _select_file_only_hybrid_conclusion(
        pdf_evidence_context=pdf_evidence_context,
        pdf_answer=pdf_answer,
        table_execution_context=table_execution_context,
        tabular_answer=tabular_answer,
    )
    conclusion_points = [lead] if lead else []
    conclusion_points.append("PDF 原文与表格执行结果在当前回答中相互补充，文件证据优先作为结论依据。")
    conclusion_points = [item for index, item in enumerate(conclusion_points) if item and item not in conclusion_points[:index]][:3]

    limitation_points = _select_candidates_by_keywords(
        merged_candidates,
        keywords=("limited", "limitation", "局限", "不足", "有待", "future", "仍有限", "需要进一步"),
        max_items=2,
    )
    if not limitation_points:
        limitation_points = [
            "当前总结仅基于已上传 PDF 原文与表格执行结果整理，未引入知识库或文件外补充证据。",
            "若 PDF 原文或表格中未提供更完整的长期验证、机理解释或边界条件，当前回答不做补写。",
        ]

    sections = [
        *_build_file_only_hybrid_summary_section("研究目的和背景", background_points, "当前文件证据中未提供足够的研究背景或研究目的信息。"),
        *_build_file_only_hybrid_summary_section("研究方法/实验设计", method_points, "当前文件证据中未提供足够的研究方法、实验设计或验证路径信息。"),
        *_build_file_only_hybrid_summary_section("主要发现和结果", result_points, "当前文件证据中未提供足够的主要发现、关键指标或结果数据。"),
        *_build_file_only_hybrid_summary_section("结论和意义", conclusion_points, "当前文件证据中未提供足够的结论或应用意义描述。"),
        *_build_file_only_hybrid_summary_section("局限性", limitation_points, "当前文件证据中未明确给出局限性或后续工作说明。"),
        _LITERATURE_SUMMARY_NOTE,
    ]
    return "\n".join(sections).strip()


def _build_hybrid_literature_section(title: str, points: list[str], fallback: str) -> list[str]:
    lines = [f"## {title}"]
    if points:
        lines.extend(f"- {point}" for point in points)
    else:
        lines.append(f"- {fallback}")
    lines.append("")
    return lines


def _has_ordered_markdown_sections(text: str, headings: tuple[str, ...]) -> bool:
    normalized = str(text or "")
    last_start = -1
    for heading in headings:
        matched = re.search(
            rf"(^|\n)\s*(?:#{{1,6}}\s*)?{re.escape(heading)}\s*[：:]?",
            normalized,
            flags=re.MULTILINE,
        )
        if matched is None or matched.start() <= last_start:
            return False
        last_start = matched.start()
    return True


def _has_hybrid_fastqa_sections(text: str) -> bool:
    return _has_ordered_markdown_sections(text, ("结论", "证据", "对比", "限制"))


def _has_hybrid_literature_sections(text: str) -> bool:
    return _has_ordered_markdown_sections(
        text,
        ("研究目的和背景", "研究方法/实验设计", "主要发现和结果", "结论和意义", "局限性"),
    )


def _sanitize_hybrid_llm_answer(answer: str) -> str:
    sanitized_lines: list[str] = []
    for raw_line in str(answer or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = str(raw_line or "").strip()
        if not line:
            if sanitized_lines and sanitized_lines[-1] != "":
                sanitized_lines.append("")
            continue
        if "source_scope=" in line:
            continue
        line = re.sub(r"^(?:真实 PDF 总结：|真实表格总结：)", "", line).strip()
        probe = re.sub(r"^[#>\-\*\d\.\)\s]+", "", line).strip()
        if not line or _is_tabular_structure_line(probe):
            continue
        sanitized_lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(sanitized_lines)).strip()


def _normalize_patent_hybrid_answer(*, answer: str, synthesis_contract: dict[str, Any]) -> tuple[str, bool]:
    fallback_answer = synthesize_patent_hybrid_answer(synthesis_contract=synthesis_contract)
    cleaned = _sanitize_hybrid_llm_answer(answer)
    if not cleaned:
        return fallback_answer, True
    if is_summary_question(str(dict(synthesis_contract or {}).get("question") or "")):
        if _has_hybrid_literature_sections(cleaned):
            if _LITERATURE_SUMMARY_NOTE in cleaned:
                return cleaned, False
            return f"{cleaned}\n\n{_LITERATURE_SUMMARY_NOTE}".strip(), False
        return fallback_answer, True
    if _has_hybrid_fastqa_sections(cleaned):
        return cleaned, False
    return fallback_answer, True


def _synthesize_hybrid_literature_answer(*, synthesis_contract: dict[str, Any]) -> str:
    contract = dict(synthesis_contract or {})
    pdf_answer = str(contract.get("pdf_answer") or "").strip()
    tabular_answer = str(contract.get("tabular_answer") or "").strip()
    kb_answer = str(contract.get("kb_answer") or "").strip()
    pdf_evidence_context = str(contract.get("pdf_evidence_context") or "").strip()
    table_execution_context = str(contract.get("table_execution_context") or "").strip()
    kb_evidence_context = str(contract.get("kb_evidence_context") or "").strip()
    kb_reference_instruction = str(contract.get("kb_reference_instruction") or "").strip()
    usable_kb_answer = "" if _is_degraded_answer(kb_answer) else kb_answer

    lead = _select_hybrid_direct_conclusion(
        table_execution_context=table_execution_context,
        pdf_evidence_context=pdf_evidence_context,
        tabular_answer="",
        pdf_answer="",
        kb_answer=usable_kb_answer,
    )
    if not lead:
        return "当前未拿到可读的 PDF、表格或知识库证据，暂时无法生成联合回答。"

    background_points = _collect_hybrid_points(pdf_evidence_context, max_items=2)
    if usable_kb_answer or kb_evidence_context:
        background_points = [*background_points, _clip_lead_text(f"知识库交叉验证：{kb_evidence_context or usable_kb_answer}", limit=220)]

    method_points = []
    if pdf_evidence_context:
        method_points.append(_clip_lead_text(f"PDF 原文证据：{pdf_evidence_context}", limit=220))
    table_evidence_point = _hybrid_table_evidence_point(
        table_execution_context=table_execution_context,
        tabular_answer=tabular_answer,
    )
    if table_evidence_point:
        method_points.append(f"表格结果补充了关键数据对照：{table_evidence_point}")
    if usable_kb_answer or kb_evidence_context:
        method_points.append("知识库结果仅用于交叉验证，不能覆盖文件原文和表格执行结果。")

    result_points = _collect_hybrid_points(
        pdf_evidence_context,
        max_items=3,
    )
    if table_evidence_point and table_evidence_point not in result_points:
        result_points.append(table_evidence_point)
    if usable_kb_answer or kb_evidence_context:
        result_points.append(_clip_lead_text(f"知识库补充：{kb_evidence_context or usable_kb_answer}", limit=220))

    limitation_candidates = _extract_hybrid_summary_candidates(
        pdf_answer,
        tabular_answer,
        usable_kb_answer,
        pdf_evidence_context,
        table_execution_context,
        kb_evidence_context,
        max_items=12,
        min_chars=10,
    )
    limitation_points = _select_candidates_by_keywords(
        limitation_candidates,
        keywords=("limited", "limitation", "局限", "不足", "future", "需要进一步", "仍需", "边界"),
        max_items=2,
    )
    if not limitation_points:
        limitation_points = [
            "当前总结仅基于已上传 PDF 原文、表格执行结果和命中的知识库证据整理，未引入文件外补充事实。",
            "若文件原文、表格或知识库未提供更完整的长期验证、机理解释或边界条件，当前回答不做补写。",
        ]
        if usable_kb_answer or kb_evidence_context:
            limitation_points.append("知识库仅用于交叉验证，不能覆盖文件原文和表格执行结果。")
    limitation_points = [
        item
        for index, item in enumerate(limitation_points)
        if item and item not in limitation_points[:index]
    ][:3]

    conflict_message = _detect_conflict_message(
        file_context="\n".join(part for part in (table_execution_context, pdf_evidence_context) if part),
        kb_context=kb_evidence_context or usable_kb_answer,
    )
    conclusion_points = [lead]
    conclusion_points.append("文件证据优先作为主结论依据，PDF 原文与表格执行结果用于相互校验。")
    if conflict_message:
        conclusion_points.append(conflict_message)
    elif usable_kb_answer:
        conclusion_points.append("当前未检测到明确冲突；知识库只作为补充验证，不替代文件侧结论。")
    if kb_reference_instruction:
        conclusion_points.append(kb_reference_instruction)

    sections = [
        *_build_hybrid_literature_section("研究目的和背景", background_points, "当前文件与知识库证据中未提供足够的研究背景或研究目的信息。"),
        *_build_hybrid_literature_section("研究方法/实验设计", method_points, "当前文件与知识库证据中未提供足够的研究方法、实验设计或验证路径信息。"),
        *_build_hybrid_literature_section("主要发现和结果", result_points, "当前文件与知识库证据中未提供足够的主要发现、关键指标或结果数据。"),
        *_build_hybrid_literature_section("结论和意义", conclusion_points, "当前文件与知识库证据中未提供足够的结论或应用意义描述。"),
        *_build_hybrid_literature_section("局限性", limitation_points, "当前文件与知识库证据中未明确给出局限性或后续工作说明。"),
        _LITERATURE_SUMMARY_NOTE,
    ]
    return "\n".join(sections).strip()


def synthesize_patent_hybrid_answer(*, synthesis_contract: dict[str, Any]) -> str:
    contract = dict(synthesis_contract or {})
    source_scope = str(contract.get("source_scope") or "").strip().lower()
    if is_summary_question(str(contract.get("question") or "")) and source_scope == "pdf+table" and not bool(contract.get("include_kb")):
        return _synthesize_file_only_hybrid_summary(synthesis_contract=contract)
    if is_summary_question(str(contract.get("question") or "")):
        return _synthesize_hybrid_literature_answer(synthesis_contract=contract)

    pdf_answer = str(contract.get("pdf_answer") or "").strip()
    tabular_answer = str(contract.get("tabular_answer") or "").strip()
    kb_answer = str(contract.get("kb_answer") or "").strip()
    pdf_evidence_context = str(contract.get("pdf_evidence_context") or "").strip()
    table_execution_context = str(contract.get("table_execution_context") or "").strip()
    kb_evidence_context = str(contract.get("kb_evidence_context") or "").strip()
    kb_reference_instruction = str(contract.get("kb_reference_instruction") or "").strip()
    usable_kb_answer = "" if _is_degraded_answer(kb_answer) else kb_answer
    file_only_hybrid = source_scope == "pdf+table" and not bool(contract.get("include_kb"))

    lead = _select_hybrid_direct_conclusion(
        table_execution_context=table_execution_context,
        pdf_evidence_context=pdf_evidence_context,
        tabular_answer=tabular_answer,
        pdf_answer=pdf_answer,
        kb_answer=usable_kb_answer,
    )
    if not lead:
        return "当前未拿到可读的 PDF、表格或知识库证据，暂时无法生成联合回答。"

    evidence_lines: list[str] = []
    if file_only_hybrid:
        table_answer_point = _extract_hybrid_answer_point(
            tabular_answer,
            ("结论", "证据", "主要发现和结果", "结论和意义"),
        )
        pdf_answer_point = _extract_hybrid_answer_point(
            pdf_answer,
            ("结论", "证据", "主要发现和结果", "结论和意义"),
        )
        table_context_points = _extract_tabular_context_points(table_execution_context, max_items=2)
        table_evidence = table_answer_point or ("；".join(table_context_points) if table_context_points else _lead_from_table_context(table_execution_context))
        pdf_evidence = pdf_answer_point or _lead_from_pdf_context(pdf_evidence_context or pdf_answer)
        if table_evidence:
            evidence_lines.append(f"- 表格执行结果：{table_evidence}")
        if pdf_evidence:
            evidence_lines.append(f"- PDF 原文证据：{pdf_evidence}")
    else:
        table_evidence = _hybrid_table_evidence_point(
            table_execution_context=table_execution_context,
            tabular_answer=tabular_answer,
        )
        if table_evidence:
            evidence_lines.append(f"- 表格执行结果：{table_evidence}")
        if pdf_evidence_context or (pdf_answer and not _is_degraded_answer(pdf_answer)):
            evidence_lines.append(f"- PDF 原文证据：{pdf_evidence_context or pdf_answer}")
    if usable_kb_answer or kb_evidence_context:
        evidence_lines.append(f"- 知识库证据：{kb_evidence_context or usable_kb_answer}")

    conflict_message = _detect_conflict_message(
        file_context="\n".join(part for part in (table_execution_context, pdf_evidence_context) if part),
        kb_context=kb_evidence_context or usable_kb_answer,
    )

    comparison_lines: list[str] = []
    if table_execution_context or pdf_evidence_context or tabular_answer or pdf_answer:
        comparison_lines.append("- 文件证据优先作为主结论依据，表格执行结果与 PDF 原文用于相互校验。")
    if usable_kb_answer:
        comparison_lines.append("- 知识库补充只用于扩展背景或交叉验证，不能覆盖文件侧的直接证据。")
    if conflict_message:
        comparison_lines.append(f"- {conflict_message}")
    elif usable_kb_answer and (table_execution_context or pdf_evidence_context or tabular_answer or pdf_answer):
        comparison_lines.append("- 当前未检测到明确冲突；若后续文件证据与知识库不一致，仍以文件原文和表格执行结果为准。")
    elif usable_kb_answer:
        comparison_lines.append("- 当前文件证据不足，结论主要依赖知识库补充。")
    else:
        comparison_lines.append("- 当前回答未纳入知识库补充，属于文件侧联合总结。")

    limitation_lines = [
        "- 当前结论受可读 PDF、表格执行结果和知识库命中范围限制，未命中的来源不会被补写为确定事实。",
        (
            f"- {kb_reference_instruction}"
            if kb_reference_instruction
            else "- 若后续补充更多文件或知识库证据，结论可能继续收敛。"
        ),
    ]

    sections = [
        "## 结论",
        lead,
        "",
        "## 证据",
        *(evidence_lines or ["- 当前未拿到足够的文件或知识库证据。"]),
        "",
        "## 对比",
        *comparison_lines,
        "",
        "## 限制",
        *limitation_lines,
    ]
    return "\n".join(sections).strip()


def _detect_conflict_message(*, file_context: str, kb_context: str) -> str:
    file_text = str(file_context or "").strip()
    kb_text = str(kb_context or "").strip()
    if not file_text or not kb_text:
        return ""
    file_metrics = _extract_metric_values(file_text)
    kb_metrics = _extract_metric_values(kb_text)
    shared_metrics = sorted(set(file_metrics) & set(kb_metrics))
    conflicting_metrics = [
        metric
        for metric in shared_metrics
        if file_metrics.get(metric) and kb_metrics.get(metric) and file_metrics.get(metric) != kb_metrics.get(metric)
    ]
    if conflicting_metrics:
        return (
            "冲突说明：文件证据与知识库证据存在冲突。"
            f" 冲突指标：{', '.join(conflicting_metrics)}。"
            " 当前按文件原文和表格执行结果为准。"
        )
    polarity_conflict = _detect_polarity_conflict(file_text=file_text, kb_text=kb_text)
    if polarity_conflict:
        return (
            "冲突说明：文件证据与知识库证据存在冲突。"
            f" 冲突点：{polarity_conflict}。"
            " 当前按文件原文和表格执行结果为准。"
        )
    return ""


def _extract_numbers(text: str) -> list[str]:
    return [value for value in re.findall(r"\d+(?:\.\d+)?", str(text or ""))]


def _extract_metric_values(text: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    normalized = str(text or "")
    patterns = {
        "capacity": r"(?:容量|capacity)[^\d]{0,12}(\d+(?:\.\d+)?)\s*(mAh|mah)",
        "efficiency": r"(?:效率|efficiency)[^\d]{0,12}(\d+(?:\.\d+)?)\s*(%)",
        "voltage": r"(?:电压|voltage)[^\d]{0,12}(\d+(?:\.\d+)?)\s*(V|v)",
        "cycle_life": r"(?:循环寿命|cycle\s*life)[^\d]{0,12}(\d+(?:\.\d+)?)",
    }
    for metric, pattern in patterns.items():
        matched = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not matched:
            continue
        value = str(matched.group(1) or "").strip()
        unit = str(matched.group(2) or "").strip() if matched.lastindex and matched.lastindex >= 2 else ""
        rows[metric] = f"{value}{unit}"
    return rows


def _detect_polarity_conflict(*, file_text: str, kb_text: str) -> str:
    pairs = [
        ("提高", "下降"),
        ("改善", "恶化"),
        ("稳定", "不稳定"),
        ("有效", "无效"),
        ("支持", "不支持"),
        ("increase", "decrease"),
        ("improved", "degraded"),
        ("stable", "unstable"),
        ("effective", "ineffective"),
        ("support", "not support"),
    ]
    file_lower = file_text.lower()
    kb_lower = kb_text.lower()
    for left, right in pairs:
        if (left in file_text and right in kb_text) or (right in file_text and left in kb_text):
            return f"{left}/{right}"
        if (left in file_lower and right in kb_lower) or (right in file_lower and left in kb_lower):
            return f"{left}/{right}"
    return ""


def _is_degraded_answer(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    markers = (
        "found no matching results",
        "未拿到可读",
        "未找到可用的知识库",
        "未找到匹配",
        "无法生成",
        "请稍后重试",
        "文件不可读",
        "暂时无法",
    )
    return any(marker in normalized for marker in markers)


def _is_shell_placeholder_answer(text: str) -> bool:
    normalized = str(text or "").strip()
    return bool(normalized) and ("壳子" in normalized or "不应主导最终格式" in normalized)


def _has_usable_hybrid_evidence(*, synthesis_contract: dict[str, Any]) -> bool:
    contract = dict(synthesis_contract or {})
    candidates = [
        str(contract.get("pdf_evidence_context") or "").strip(),
        str(contract.get("table_execution_context") or "").strip(),
        str(contract.get("pdf_answer") or "").strip(),
        str(contract.get("tabular_answer") or "").strip(),
        str(contract.get("kb_evidence_context") or "").strip(),
        str(contract.get("kb_answer") or "").strip(),
    ]
    return any(candidate and not _is_degraded_answer(candidate) and not _is_shell_placeholder_answer(candidate) for candidate in candidates)


def _select_hybrid_direct_conclusion(
    *,
    table_execution_context: str,
    pdf_evidence_context: str,
    tabular_answer: str,
    pdf_answer: str,
    kb_answer: str,
) -> str:
    tabular_point = _extract_hybrid_answer_point(tabular_answer, ("结论", "结论和意义", "主要发现和结果", "证据"))
    pdf_point = _extract_hybrid_answer_point(pdf_answer, ("结论", "结论和意义", "主要发现和结果", "证据"))
    for candidate in (
        tabular_point,
        _lead_from_table_context(table_execution_context),
        pdf_point,
        _lead_from_pdf_context(pdf_evidence_context),
        kb_answer if kb_answer and not _is_degraded_answer(kb_answer) else "",
    ):
        if candidate:
            return candidate
    return ""


def _lead_from_table_context(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    material_hits = re.findall(r"material=([^;]+);\s*capacity_mAh=([^;]+)", normalized, flags=re.IGNORECASE)
    if material_hits:
        parts = [f"{material.strip()} {capacity.strip()}mAh" for material, capacity in material_hits[:3]]
        return "表格结果显示：" + "，".join(parts) + "。"
    context_points = _extract_tabular_context_points(normalized, max_items=3)
    if context_points:
        return "表格结果显示：" + "，".join(context_points[:3]) + "。"
    if _is_tabular_structure_line(normalized):
        return ""
    return _clip_lead_text(normalized)


def _lead_from_pdf_context(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    body_lines = [line for line in lines if not line.startswith("==== 文献 ")]
    candidate = " ".join(body_lines).strip() if body_lines else normalized
    return _clip_lead_text(candidate)


def _clip_lead_text(text: str, *, limit: int = 120) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(1, limit - 1)].rstrip() + "…"


def _call_with_supported_kwargs(fn, /, **kwargs):
    if not callable(fn):
        raise TypeError("target is not callable")
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(**kwargs)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return fn(**kwargs)
    filtered = {
        key: value
        for key, value in kwargs.items()
        if key in signature.parameters
    }
    return fn(**filtered)
