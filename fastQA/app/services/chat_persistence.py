from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.integrations.redis import RedisService, build_redis_bindings
from app.services import pending_overlay as pending_overlay_service
from app.services.conversation_authority_client import ConversationAuthorityClient
from app.services.ordered_dispatcher import get_default_dispatcher


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_authority_client() -> ConversationAuthorityClient:
    return ConversationAuthorityClient(
        base_url=str(os.getenv("PUBLIC_SERVICE_INTERNAL_BASE_URL", "http://127.0.0.1:8102") or "http://127.0.0.1:8102").strip(),
        service_token=str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip(),
    )


@lru_cache(maxsize=1)
def _get_pending_overlay_redis_service() -> RedisService:
    settings = get_settings()
    bindings = build_redis_bindings(settings=settings)
    return RedisService.from_prefix(
        client=bindings.client,
        key_prefix=str(bindings.key_prefix or settings.redis_key_prefix or "agentcode"),
    )


def _persistence_key(*, user_id: int, conversation_id: int) -> str:
    return f"conversation:{int(user_id)}:{int(conversation_id)}"


def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _pending_overlay_enabled() -> bool:
    try:
        return bool(get_settings().conversation_overlay_enabled)
    except Exception:
        return False


def _store_pending_assistant_overlay(
    *,
    user_id: int,
    conversation_id: int,
    trace_id: str,
    route: str,
    assistant_content: str,
) -> bool:
    if not _pending_overlay_enabled():
        return False
    return pending_overlay_service.store_pending_assistant_overlay(
        redis_service=_get_pending_overlay_redis_service(),
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        route=route,
        assistant_content=assistant_content,
    )


def _load_pending_assistant_overlay(
    *,
    user_id: int,
    conversation_id: int,
) -> dict[str, str] | None:
    if not _pending_overlay_enabled():
        return None
    return pending_overlay_service.read_pending_assistant_overlay(
        redis_service=_get_pending_overlay_redis_service(),
        user_id=user_id,
        conversation_id=conversation_id,
    )


def _clear_pending_assistant_overlay(*, user_id: int, conversation_id: int) -> bool:
    if not _pending_overlay_enabled():
        return False
    return pending_overlay_service.clear_pending_assistant_overlay(
        redis_service=_get_pending_overlay_redis_service(),
        user_id=user_id,
        conversation_id=conversation_id,
    )


def _payload_value(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _normalize_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    normalized: list[int] = []
    seen: set[int] = set()
    for item in value:
        parsed = _safe_positive_int(item)
        if parsed is None or parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
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
        doi = str(item or "").strip()
        if doi:
            normalized.append({"doi": doi})
    return normalized


def _normalize_steps(summary: dict[str, Any]) -> list[dict[str, Any]]:
    steps = summary.get("steps")
    if not isinstance(steps, list):
        return []
    return [dict(item) for item in steps if isinstance(item, dict)]


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
        doi = str(key or "").strip()
        if not doi:
            continue
        normalized[doi] = [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    return normalized


def _normalize_used_files(summary: dict[str, Any]) -> list[dict[str, Any]]:
    used_files = summary.get("used_files")
    if not isinstance(used_files, list):
        return []
    return [dict(item) for item in used_files if isinstance(item, dict)]


def _normalize_chat_history(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    recent_turns = snapshot.get("recent_turns")
    if not isinstance(recent_turns, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in recent_turns:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "")
        if not role:
            continue
        normalized.append(
            {
                "role": role,
                "content": content,
                "trace_id": str(item.get("trace_id") or "").strip(),
                "created_at": str(item.get("created_at") or "").strip(),
                "message_id": str(item.get("message_id") or "").strip(),
            }
        )
    return normalized


def _persist_user_message_sync(
    *,
    user_id: int,
    conversation_id: int,
    question: str,
    trace_id: str,
    route: str,
    requested_mode: str,
    actual_mode: str,
    payload: Any,
) -> dict[str, Any]:
    return _get_authority_client().write_user_turn(
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        route=route,
        requested_mode=requested_mode,
        actual_mode=actual_mode,
        content=question,
        selected_file_ids=_normalize_int_list(_payload_value(payload, "selected_file_ids")),
        last_turn_route_hint=str(_payload_value(payload, "route") or _payload_value(payload, "route_hint") or "").strip() or None,
    )


def _persist_assistant_summary_sync(
    *,
    user_id: int,
    conversation_id: int,
    trace_id: str,
    route: str,
    requested_mode: str,
    actual_mode: str,
    assistant_content: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    safe_summary = dict(summary or {})
    return _get_authority_client().accept_assistant_turn_async(
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id=str(safe_summary.get("trace_id") or trace_id or "").strip(),
        route=str(safe_summary.get("route") or route or "").strip(),
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
    try:
        pending_overlay = _load_pending_assistant_overlay(
            user_id=resolved_user_id,
            conversation_id=resolved_conversation_id,
        )
    except Exception:
        logger.warning("fastqa pending overlay read skipped", exc_info=True)
        pending_overlay = None
    pending_overlay_metadata = None
    if isinstance(pending_overlay, dict):
        chat_history, overlay_applied, overlay_should_clear = pending_overlay_service.merge_pending_assistant_overlay(
            snapshot=snapshot,
            chat_history=chat_history,
            overlay=pending_overlay,
        )
        if overlay_should_clear:
            try:
                _clear_pending_assistant_overlay(
                    user_id=resolved_user_id,
                    conversation_id=resolved_conversation_id,
                )
            except Exception:
                logger.warning("fastqa pending overlay clear skipped", exc_info=True)
        if overlay_applied:
            pending_overlay_metadata = dict(pending_overlay)
    return {
        "snapshot": snapshot,
        "chat_history": chat_history,
        "conversation_state": dict(snapshot.get("conversation_state") or {}),
        "summary": dict(snapshot.get("summary") or {}),
        "snapshot_version": snapshot.get("snapshot_version"),
        "pending_overlay": pending_overlay_metadata,
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
    content = str(question or "").strip()
    if resolved_user_id is None or resolved_conversation_id is None or not content:
        return
    kwargs = {
        "user_id": resolved_user_id,
        "conversation_id": resolved_conversation_id,
        "question": content,
        "trace_id": trace_id,
        "route": route,
        "requested_mode": requested_mode,
        "actual_mode": actual_mode,
        "payload": payload,
    }
    if async_enabled:
        get_default_dispatcher().submit(
            key=_persistence_key(user_id=resolved_user_id, conversation_id=resolved_conversation_id),
            fn=_persist_user_message_sync,
            kwargs=kwargs,
        )
        return
    _persist_user_message_sync(**kwargs)


def persist_assistant_summary(
    *,
    user_id: int | None,
    conversation_id: int | None,
    trace_id: str,
    route: str,
    requested_mode: str,
    actual_mode: str,
    assistant_content: str,
    summary: dict[str, Any],
    payload: Any,
    async_enabled: bool = False,
) -> None:
    resolved_user_id = _safe_positive_int(user_id)
    resolved_conversation_id = _safe_positive_int(conversation_id)
    content = str(assistant_content or "").strip()
    safe_summary = dict(summary or {})
    done_seen = bool(safe_summary.get("done_seen"))
    if resolved_user_id is None or resolved_conversation_id is None or not done_seen or not content:
        return
    resolved_trace_id = str(safe_summary.get("trace_id") or trace_id or "").strip()
    resolved_route = str(safe_summary.get("route") or route or "").strip()
    try:
        _store_pending_assistant_overlay(
            user_id=resolved_user_id,
            conversation_id=resolved_conversation_id,
            trace_id=resolved_trace_id,
            route=resolved_route,
            assistant_content=content,
        )
    except Exception:
        logger.warning("fastqa pending overlay store skipped", exc_info=True)
    kwargs = {
        "user_id": resolved_user_id,
        "conversation_id": resolved_conversation_id,
        "trace_id": trace_id,
        "route": route,
        "requested_mode": requested_mode,
        "actual_mode": actual_mode,
        "assistant_content": content,
        "summary": safe_summary,
    }
    if async_enabled:
        get_default_dispatcher().submit(
            key=_persistence_key(user_id=resolved_user_id, conversation_id=resolved_conversation_id),
            fn=_persist_assistant_summary_sync,
            kwargs=kwargs,
        )
        return
    try:
        _persist_assistant_summary_sync(**kwargs)
    except Exception:
        logger.warning("fastqa persist_assistant_summary skipped", exc_info=True)
