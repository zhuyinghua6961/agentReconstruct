from __future__ import annotations

from typing import Any

from app.integrations.redis import RedisService

_PENDING_OVERLAY_TTL_SECONDS = 1800


def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def build_pending_assistant_overlay(*, trace_id: str, route: str, assistant_content: str) -> dict[str, str] | None:
    normalized_trace_id = _normalize_text(trace_id)
    normalized_route = _normalize_text(route)
    normalized_content = _normalize_text(assistant_content)
    if not normalized_trace_id or not normalized_content:
        return None
    return {
        "trace_id": normalized_trace_id,
        "route": normalized_route,
        "assistant_content": normalized_content,
    }


def _pending_overlay_key(*, redis_service: RedisService, user_id: int, conversation_id: int) -> str:
    return redis_service.key_factory.pending("conversation", "assistant", int(user_id), int(conversation_id))


def read_pending_assistant_overlay(
    *,
    redis_service: RedisService | None,
    user_id: int,
    conversation_id: int,
) -> dict[str, str] | None:
    resolved_user_id = _safe_positive_int(user_id)
    resolved_conversation_id = _safe_positive_int(conversation_id)
    if redis_service is None or not redis_service.available or resolved_user_id is None or resolved_conversation_id is None:
        return None
    payload = redis_service.get_json(
        _pending_overlay_key(redis_service=redis_service, user_id=resolved_user_id, conversation_id=resolved_conversation_id),
        default=None,
    )
    if not isinstance(payload, dict):
        return None
    return build_pending_assistant_overlay(
        trace_id=payload.get("trace_id"),
        route=payload.get("route"),
        assistant_content=payload.get("assistant_content"),
    )


def store_pending_assistant_overlay(
    *,
    redis_service: RedisService | None,
    user_id: int,
    conversation_id: int,
    trace_id: str,
    route: str,
    assistant_content: str,
    ttl_seconds: int = _PENDING_OVERLAY_TTL_SECONDS,
) -> bool:
    resolved_user_id = _safe_positive_int(user_id)
    resolved_conversation_id = _safe_positive_int(conversation_id)
    overlay = build_pending_assistant_overlay(trace_id=trace_id, route=route, assistant_content=assistant_content)
    if redis_service is None or not redis_service.available or resolved_user_id is None or resolved_conversation_id is None or overlay is None:
        return False
    return redis_service.set_json(
        _pending_overlay_key(redis_service=redis_service, user_id=resolved_user_id, conversation_id=resolved_conversation_id),
        overlay,
        ttl_seconds=ttl_seconds,
    )


def clear_pending_assistant_overlay(
    *,
    redis_service: RedisService | None,
    user_id: int,
    conversation_id: int,
) -> bool:
    resolved_user_id = _safe_positive_int(user_id)
    resolved_conversation_id = _safe_positive_int(conversation_id)
    if redis_service is None or not redis_service.available or resolved_user_id is None or resolved_conversation_id is None:
        return False
    deleted = redis_service.delete(
        _pending_overlay_key(redis_service=redis_service, user_id=resolved_user_id, conversation_id=resolved_conversation_id)
    )
    return deleted > 0


def snapshot_has_converged(*, snapshot: dict[str, Any], overlay: dict[str, str]) -> bool:
    trace_id = _normalize_text(overlay.get("trace_id"))
    if not trace_id:
        return False
    conversation_state = snapshot.get("conversation_state") if isinstance(snapshot.get("conversation_state"), dict) else {}
    if _normalize_text(conversation_state.get("last_assistant_trace_id")) == trace_id:
        return True
    recent_turns = snapshot.get("recent_turns") if isinstance(snapshot.get("recent_turns"), list) else []
    for item in recent_turns:
        if not isinstance(item, dict):
            continue
        if _normalize_text(item.get("role")).lower() != "assistant":
            continue
        if _normalize_text(item.get("trace_id")) == trace_id:
            return True
    return False


def merge_pending_assistant_overlay(
    *,
    snapshot: dict[str, Any],
    chat_history: list[dict[str, Any]],
    overlay: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], bool, bool]:
    normalized_history = [dict(item) for item in chat_history if isinstance(item, dict)]
    normalized_overlay = build_pending_assistant_overlay(
        trace_id=(overlay or {}).get("trace_id"),
        route=(overlay or {}).get("route"),
        assistant_content=(overlay or {}).get("assistant_content"),
    )
    if normalized_overlay is None:
        return normalized_history, False, False
    if snapshot_has_converged(snapshot=snapshot, overlay=normalized_overlay):
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
