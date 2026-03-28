from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import config
from server.runtime.ordered_task_dispatcher import get_default_dispatcher
from server.services.conversation.conversation_service import conversation_service


logger = logging.getLogger(__name__)
_PENDING_OVERLAY_TTL_SECONDS = 1800


def _get_authority_client():
    from server.services.conversation_authority_client import ConversationAuthorityClient

    return ConversationAuthorityClient(
        base_url=str(os.getenv("PUBLIC_SERVICE_INTERNAL_BASE_URL", "http://127.0.0.1:8102") or "http://127.0.0.1:8102").strip(),
        service_token=str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip(),
    )


def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _payload_value(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _normalize_chat_history(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    recent_turns = snapshot.get("recent_turns") if isinstance(snapshot.get("recent_turns"), list) else []
    normalized: list[dict[str, Any]] = []
    for item in recent_turns:
        if not isinstance(item, dict):
            continue
        role = _normalize_text(item.get("role"))
        if not role:
            continue
        normalized.append(
            {
                "role": role,
                "content": str(item.get("content") or ""),
                "trace_id": _normalize_text(item.get("trace_id")),
                "created_at": _normalize_text(item.get("created_at")),
                "message_id": _normalize_text(item.get("message_id")),
            }
        )
    return normalized


def _normalize_reference_payload(summary: dict[str, Any]) -> list[dict[str, Any]]:
    reference_objects = summary.get("reference_objects")
    if isinstance(reference_objects, list):
        return [dict(item) for item in reference_objects if isinstance(item, dict)]
    references = summary.get("references")
    if not isinstance(references, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in references:
        if isinstance(item, dict):
            normalized.append(dict(item))
            continue
        doi = _normalize_text(item)
        if doi:
            normalized.append({"doi": doi})
    return normalized


def _normalize_reference_links(summary: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = summary.get(key)
    if not isinstance(values, list):
        return []
    return [dict(item) for item in values if isinstance(item, dict)]


def _normalize_doi_locations(summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    values = summary.get("doi_locations")
    if not isinstance(values, dict):
        return {}
    normalized: dict[str, list[dict[str, Any]]] = {}
    for key, items in values.items():
        doi = _normalize_text(key)
        if not doi:
            continue
        normalized[doi] = [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    return normalized


def _normalize_steps(summary: dict[str, Any]) -> list[dict[str, Any]]:
    steps = summary.get("steps")
    if not isinstance(steps, list):
        return []
    return [dict(item) for item in steps if isinstance(item, dict)]


def _normalize_used_files(summary: dict[str, Any]) -> list[dict[str, Any]]:
    used_files = summary.get("used_files")
    if not isinstance(used_files, list):
        return []
    return [dict(item) for item in used_files if isinstance(item, dict)]


def _persistence_key(*, user_id: int, conversation_id: int) -> str:
    return f"conversation:{int(user_id)}:{int(conversation_id)}"


def _pending_overlay_dir() -> Path:
    root = Path(str(getattr(config, "APP_RUNTIME_ROOT", getattr(config, "SERVICE_RUNTIME_ROOT", ".runtime")) or ".runtime"))
    path = root / "pending_overlay"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pending_overlay_path(*, user_id: int, conversation_id: int) -> Path:
    return _pending_overlay_dir() / f"assistant_{int(user_id)}_{int(conversation_id)}.json"


def _build_pending_assistant_overlay(*, trace_id: str, route: str, assistant_content: str) -> dict[str, Any] | None:
    normalized_trace_id = _normalize_text(trace_id)
    normalized_content = _normalize_text(assistant_content)
    if not normalized_trace_id or not normalized_content:
        return None
    return {
        "trace_id": normalized_trace_id,
        "route": _normalize_text(route),
        "assistant_content": normalized_content,
        "stored_at": int(time.time()),
    }


def _pending_overlay_enabled() -> bool:
    return bool(getattr(config, "CONVERSATION_OVERLAY_ENABLED", False))


def _store_pending_assistant_overlay(
    *,
    user_id: int,
    conversation_id: int,
    trace_id: str,
    route: str,
    assistant_content: str,
    ttl_seconds: int = _PENDING_OVERLAY_TTL_SECONDS,
) -> bool:
    overlay = _build_pending_assistant_overlay(trace_id=trace_id, route=route, assistant_content=assistant_content)
    if overlay is None:
        return False
    overlay["ttl_seconds"] = int(ttl_seconds)
    target = _pending_overlay_path(user_id=user_id, conversation_id=conversation_id)
    fd, temp_path = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(overlay, handle, ensure_ascii=False)
        Path(temp_path).replace(target)
        return True
    except Exception:
        try:
            Path(temp_path).unlink(missing_ok=True)
        except Exception:
            pass
        logger.warning("highThinking pending overlay store failed", exc_info=True)
        return False


def _load_pending_assistant_overlay(*, user_id: int, conversation_id: int) -> dict[str, Any] | None:
    target = _pending_overlay_path(user_id=user_id, conversation_id=conversation_id)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("highThinking pending overlay read failed", exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None
    ttl_seconds = _safe_positive_int(payload.get("ttl_seconds")) or _PENDING_OVERLAY_TTL_SECONDS
    stored_at = _safe_positive_int(payload.get("stored_at")) or 0
    if stored_at and stored_at + ttl_seconds < int(time.time()):
        _clear_pending_assistant_overlay(user_id=user_id, conversation_id=conversation_id)
        return None
    return _build_pending_assistant_overlay(
        trace_id=payload.get("trace_id"),
        route=payload.get("route"),
        assistant_content=payload.get("assistant_content"),
    )


def _clear_pending_assistant_overlay(*, user_id: int, conversation_id: int) -> bool:
    target = _pending_overlay_path(user_id=user_id, conversation_id=conversation_id)
    try:
        target.unlink(missing_ok=True)
        return True
    except Exception:
        logger.warning("highThinking pending overlay clear failed", exc_info=True)
        return False


def _snapshot_has_converged(*, snapshot: dict[str, Any], overlay: dict[str, Any]) -> bool:
    trace_id = _normalize_text(overlay.get("trace_id"))
    if not trace_id:
        return False
    conversation_state = snapshot.get("conversation_state") if isinstance(snapshot.get("conversation_state"), dict) else {}
    if _normalize_text(conversation_state.get("last_assistant_trace_id")) == trace_id:
        return True
    for item in snapshot.get("recent_turns") if isinstance(snapshot.get("recent_turns"), list) else []:
        if not isinstance(item, dict):
            continue
        if _normalize_text(item.get("role")).lower() != "assistant":
            continue
        if _normalize_text(item.get("trace_id")) == trace_id:
            return True
    return False


def _merge_pending_assistant_overlay(
    *,
    snapshot: dict[str, Any],
    chat_history: list[dict[str, Any]],
    overlay: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], bool, bool]:
    normalized_history = [dict(item) for item in chat_history if isinstance(item, dict)]
    normalized_overlay = _build_pending_assistant_overlay(
        trace_id=(overlay or {}).get("trace_id"),
        route=(overlay or {}).get("route"),
        assistant_content=(overlay or {}).get("assistant_content"),
    )
    if normalized_overlay is None:
        return normalized_history, False, False
    if _snapshot_has_converged(snapshot=snapshot, overlay=normalized_overlay):
        return normalized_history, False, True
    trace_id = normalized_overlay["trace_id"]
    for item in normalized_history:
        if _normalize_text(item.get("role")).lower() != "assistant":
            continue
        if _normalize_text(item.get("trace_id")) == trace_id:
            return normalized_history, False, False
    normalized_history.append(
        {
            "role": "assistant",
            "content": normalized_overlay["assistant_content"],
            "trace_id": trace_id,
            "created_at": "",
            "message_id": "",
        }
    )
    return normalized_history, True, False


def _load_legacy_context(*, user_id: int, conversation_id: int) -> dict[str, Any] | None:
    # Deprecated: legacy local conversation fallback kept only as a compatibility path
    # during the public-service persistence migration.
    result = conversation_service.get_conversation_context_snapshot(user_id=user_id, conversation_id=conversation_id)
    if not isinstance(result, dict) or not result.get("success"):
        return None
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    messages = data.get("messages") if isinstance(data.get("messages"), list) else []
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    snapshot = {
        "conversation_id": int(data.get("conversation_id") or conversation_id),
        "user_id": int(data.get("user_id") or user_id),
        "snapshot_version": None,
        "summary": dict(summary),
        "recent_turns": _normalize_chat_history({"recent_turns": messages}),
        "conversation_state": {},
    }
    return {
        "snapshot": snapshot,
        "chat_history": snapshot["recent_turns"],
        "conversation_state": {},
        "summary": dict(summary),
        "snapshot_version": None,
        "pending_overlay": None,
    }


def _persist_user_message_legacy(*, user_id: int, conversation_id: int, question: str) -> dict[str, Any]:
    # Deprecated: legacy local conversation write path kept only as a compatibility path
    # during the public-service persistence migration.
    return conversation_service.add_message(
        user_id=user_id,
        conversation_id=conversation_id,
        role="user",
        content=question,
        metadata={"source": "ask_stream"},
    )


def _persist_assistant_summary_legacy(*, user_id: int, conversation_id: int, assistant_content: str, summary: dict[str, Any]) -> None:
    # Deprecated: legacy local assistant summary write path kept only as a compatibility
    # path during the public-service persistence migration.
    normalized_references = _normalize_reference_payload(summary)
    meta = {
        "source": "ask_stream",
        "query_mode": str(summary.get("query_mode") or ""),
        "references": normalized_references,
        "reference_objects": normalized_references,
        "reference_links": _normalize_reference_links(summary, "reference_links"),
        "pdf_links": _normalize_reference_links(summary, "pdf_links"),
        "doi_locations": _normalize_doi_locations(summary),
        "steps": summary.get("steps") or [],
        "route": str(summary.get("route") or ""),
        "timings": summary.get("timings") or {},
        "trace_id": str(summary.get("trace_id") or ""),
        "done_seen": bool(summary.get("done_seen")),
    }
    result = conversation_service.add_message(
        user_id=user_id,
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_content,
        metadata=meta,
    )
    if isinstance(result, dict) and result.get("success"):
        conversation_service.refresh_conversation_summary(user_id=user_id, conversation_id=conversation_id)


def _persist_user_message_authority(*, user_id: int, conversation_id: int, question: str, trace_id: str, route: str, requested_mode: str, actual_mode: str) -> dict[str, Any]:
    return _get_authority_client().write_user_turn(
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        route=route,
        requested_mode=requested_mode,
        actual_mode=actual_mode,
        content=question,
    )


def _persist_assistant_summary_authority(*, user_id: int, conversation_id: int, trace_id: str, route: str, requested_mode: str, actual_mode: str, assistant_content: str, summary: dict[str, Any]) -> dict[str, Any]:
    safe_summary = dict(summary or {})
    return _get_authority_client().accept_assistant_turn_async(
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id=_normalize_text(safe_summary.get("trace_id") or trace_id),
        route=_normalize_text(safe_summary.get("route") or route),
        requested_mode=requested_mode,
        actual_mode=actual_mode,
        answer_text=assistant_content,
        steps=_normalize_steps(safe_summary),
        references=_normalize_reference_payload(safe_summary),
        reference_objects=_normalize_reference_payload(safe_summary),
        reference_links=_normalize_reference_links(safe_summary, "reference_links"),
        pdf_links=_normalize_reference_links(safe_summary, "pdf_links"),
        doi_locations=_normalize_doi_locations(safe_summary),
        used_files=_normalize_used_files(safe_summary),
        timings=dict(safe_summary.get("timings") or {}),
    )


def _submit_shadow_task(*, key: str, fn, kwargs: dict[str, Any]) -> None:
    def _run_shadow() -> None:
        try:
            fn(**kwargs)
        except Exception:
            logger.warning("highThinking shadow authority task failed", exc_info=True)

    get_default_dispatcher().submit(key=key, fn=_run_shadow)


def load_conversation_context(
    *,
    user_id: int | None,
    conversation_id: int | None,
    trace_id: str,
    route: str,
    requested_mode: str,
    actual_mode: str,
    payload: Any,
) -> dict[str, Any] | None:
    resolved_user_id = _safe_positive_int(user_id)
    resolved_conversation_id = _safe_positive_int(conversation_id)
    if resolved_user_id is None or resolved_conversation_id is None:
        return None

    target = str(getattr(config, "CONVERSATION_EXECUTION_CONTEXT_READ_TARGET", "legacy") or "legacy").strip().lower()
    if target == "legacy":
        return _load_legacy_context(user_id=resolved_user_id, conversation_id=resolved_conversation_id)

    if target == "shadow_public_service":
        local_result = _load_legacy_context(user_id=resolved_user_id, conversation_id=resolved_conversation_id)
        try:
            _get_authority_client().read_context_snapshot(
                user_id=resolved_user_id,
                conversation_id=resolved_conversation_id,
                trace_id=trace_id,
                route=route,
                requested_mode=requested_mode,
                actual_mode=actual_mode,
            )
        except Exception:
            logger.warning("highThinking shadow authority context read failed", exc_info=True)
        return local_result

    snapshot = _get_authority_client().read_context_snapshot(
        user_id=resolved_user_id,
        conversation_id=resolved_conversation_id,
        trace_id=trace_id,
        route=route,
        requested_mode=requested_mode,
        actual_mode=actual_mode,
    )
    chat_history = _normalize_chat_history(snapshot)
    pending_overlay = None
    if _pending_overlay_enabled():
        pending_overlay = _load_pending_assistant_overlay(user_id=resolved_user_id, conversation_id=resolved_conversation_id)
        chat_history, overlay_applied, overlay_should_clear = _merge_pending_assistant_overlay(
            snapshot=snapshot,
            chat_history=chat_history,
            overlay=pending_overlay,
        )
        if overlay_should_clear:
            _clear_pending_assistant_overlay(user_id=resolved_user_id, conversation_id=resolved_conversation_id)
        if not overlay_applied:
            pending_overlay = pending_overlay if not overlay_should_clear else None
    return {
        "snapshot": snapshot,
        "chat_history": chat_history,
        "conversation_state": dict(snapshot.get("conversation_state") or {}),
        "summary": dict(snapshot.get("summary") or {}),
        "snapshot_version": snapshot.get("snapshot_version"),
        "pending_overlay": pending_overlay,
    }


def persist_user_message(
    *,
    user_id: int | None,
    conversation_id: int | None,
    question: str,
    trace_id: str,
    route: str,
    requested_mode: str,
    actual_mode: str,
    payload: Any,
    async_enabled: bool = False,
) -> None:
    resolved_user_id = _safe_positive_int(user_id)
    resolved_conversation_id = _safe_positive_int(conversation_id)
    content = _normalize_text(question)
    if resolved_user_id is None or resolved_conversation_id is None or not content:
        return

    target = str(getattr(config, "CONVERSATION_EXECUTION_USER_WRITE_TARGET", "legacy") or "legacy").strip().lower()
    key = _persistence_key(user_id=resolved_user_id, conversation_id=resolved_conversation_id)

    if target == "public_service":
        _persist_user_message_authority(
            user_id=resolved_user_id,
            conversation_id=resolved_conversation_id,
            question=content,
            trace_id=trace_id,
            route=route,
            requested_mode=requested_mode,
            actual_mode=actual_mode,
        )
        return

    if target == "shadow_public_service":
        _persist_user_message_legacy(user_id=resolved_user_id, conversation_id=resolved_conversation_id, question=content)
        _submit_shadow_task(
            key=key,
            fn=_persist_user_message_authority,
            kwargs={
                "user_id": resolved_user_id,
                "conversation_id": resolved_conversation_id,
                "question": content,
                "trace_id": trace_id,
                "route": route,
                "requested_mode": requested_mode,
                "actual_mode": actual_mode,
            },
        )
        return

    kwargs = {
        "user_id": resolved_user_id,
        "conversation_id": resolved_conversation_id,
        "question": content,
    }
    if async_enabled:
        get_default_dispatcher().submit(key=key, fn=_persist_user_message_legacy, kwargs=kwargs)
        return
    _persist_user_message_legacy(**kwargs)


def persist_assistant_summary(
    *,
    user_id: int | None,
    conversation_id: int | None,
    trace_id: str,
    route: str,
    requested_mode: str,
    actual_mode: str,
    summary: dict[str, Any],
    async_enabled: bool = True,
) -> None:
    resolved_user_id = _safe_positive_int(user_id)
    resolved_conversation_id = _safe_positive_int(conversation_id)
    safe_summary = dict(summary or {})
    assistant_content = _normalize_text(safe_summary.get("assistant_content"))
    if resolved_user_id is None or resolved_conversation_id is None:
        return
    if not bool(safe_summary.get("done_seen")) or not assistant_content:
        return

    target = str(getattr(config, "CONVERSATION_ASSISTANT_WRITE_TARGET", "legacy") or "legacy").strip().lower()
    key = _persistence_key(user_id=resolved_user_id, conversation_id=resolved_conversation_id)

    if target == "public_service":
        if _pending_overlay_enabled():
            _store_pending_assistant_overlay(
                user_id=resolved_user_id,
                conversation_id=resolved_conversation_id,
                trace_id=_normalize_text(safe_summary.get("trace_id") or trace_id),
                route=_normalize_text(safe_summary.get("route") or route),
                assistant_content=assistant_content,
            )

        def _run_accept() -> None:
            try:
                _persist_assistant_summary_authority(
                    user_id=resolved_user_id,
                    conversation_id=resolved_conversation_id,
                    trace_id=trace_id,
                    route=route,
                    requested_mode=requested_mode,
                    actual_mode=actual_mode,
                    assistant_content=assistant_content,
                    summary=safe_summary,
                )
            except Exception:
                logger.warning("highThinking authority assistant accept failed", exc_info=True)

        if async_enabled:
            get_default_dispatcher().submit(key=key, fn=_run_accept)
            return
        _run_accept()
        return

    if target == "shadow_public_service":
        _persist_assistant_summary_legacy(
            user_id=resolved_user_id,
            conversation_id=resolved_conversation_id,
            assistant_content=assistant_content,
            summary=safe_summary,
        )
        _submit_shadow_task(
            key=key,
            fn=_persist_assistant_summary_authority,
            kwargs={
                "user_id": resolved_user_id,
                "conversation_id": resolved_conversation_id,
                "trace_id": trace_id,
                "route": route,
                "requested_mode": requested_mode,
                "actual_mode": actual_mode,
                "assistant_content": assistant_content,
                "summary": safe_summary,
            },
        )
        return

    kwargs = {
        "user_id": resolved_user_id,
        "conversation_id": resolved_conversation_id,
        "assistant_content": assistant_content,
        "summary": safe_summary,
    }
    if async_enabled:
        get_default_dispatcher().submit(key=key, fn=_persist_assistant_summary_legacy, kwargs=kwargs)
        return
    _persist_assistant_summary_legacy(**kwargs)
