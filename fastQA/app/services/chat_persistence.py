from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from app.services.ordered_dispatcher import get_default_dispatcher


logger = logging.getLogger(__name__)


def _get_conversation_service():
    try:
        from server.services.conversation.conversation_service import conversation_service

        return conversation_service
    except ModuleNotFoundError:
        repo_root = Path(__file__).resolve().parents[4]
        repo_root_text = str(repo_root)
        if repo_root_text not in sys.path:
            sys.path.append(repo_root_text)
        from server.services.conversation.conversation_service import conversation_service

        return conversation_service


def _persistence_key(*, user_id: int, conversation_id: int) -> str:
    return f"conversation:{int(user_id)}:{int(conversation_id)}"


def _persist_user_message_sync(
    *,
    user_id: int,
    conversation_id: int,
    question: str,
    trace_id: str,
    route: str,
    requested_mode: str,
    actual_mode: str,
) -> None:
    result = _get_conversation_service().add_message(
        user_id=user_id,
        conversation_id=conversation_id,
        role='user',
        content=question,
        metadata={
            'source': 'fastqa_ask',
            'trace_id': str(trace_id or '').strip(),
            'route': str(route or '').strip(),
            'requested_mode': str(requested_mode or '').strip(),
            'actual_mode': str(actual_mode or '').strip(),
        },
    )
    if not result.get('success'):
        logger.warning('fastqa persist_user_message skipped: %s', result)


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
) -> None:
    metadata = {
        'source': 'fastqa_ask',
        'trace_id': str((summary or {}).get('trace_id') or trace_id or '').strip(),
        'query_mode': str((summary or {}).get('query_mode') or route or '').strip(),
        'references': list((summary or {}).get('references') or []),
        'reference_objects': list((summary or {}).get('reference_objects') or []),
        'steps': list((summary or {}).get('steps') or []),
        'done_seen': bool((summary or {}).get('done_seen')),
        'route': str((summary or {}).get('route') or route or '').strip(),
        'used_files': list((summary or {}).get('used_files') or []),
        'timings': dict((summary or {}).get('timings') or {}),
        'file_selection': dict((summary or {}).get('file_selection') or {}),
        'requested_mode': str(requested_mode or '').strip(),
        'actual_mode': str(actual_mode or '').strip(),
    }
    result = _get_conversation_service().add_message(
        user_id=user_id,
        conversation_id=conversation_id,
        role='assistant',
        content=assistant_content,
        metadata=metadata,
    )
    if not result.get('success'):
        logger.warning('fastqa persist_assistant_summary skipped: %s', result)
        return
    refresh = _get_conversation_service().refresh_conversation_summary(
        user_id=user_id,
        conversation_id=conversation_id,
    )
    if not refresh.get('success'):
        logger.warning('fastqa refresh_conversation_summary skipped: %s', refresh)


def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
    content = str(question or '').strip()
    if resolved_user_id is None or resolved_conversation_id is None or not content:
        return
    kwargs = {
        'user_id': resolved_user_id,
        'conversation_id': resolved_conversation_id,
        'question': content,
        'trace_id': trace_id,
        'route': route,
        'requested_mode': requested_mode,
        'actual_mode': actual_mode,
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
    content = str(assistant_content or '').strip()
    done_seen = bool((summary or {}).get('done_seen'))
    if resolved_user_id is None or resolved_conversation_id is None or not done_seen or not content:
        return
    kwargs = {
        'user_id': resolved_user_id,
        'conversation_id': resolved_conversation_id,
        'trace_id': trace_id,
        'route': route,
        'requested_mode': requested_mode,
        'actual_mode': actual_mode,
        'assistant_content': content,
        'summary': dict(summary or {}),
    }
    if async_enabled:
        get_default_dispatcher().submit(
            key=_persistence_key(user_id=resolved_user_id, conversation_id=resolved_conversation_id),
            fn=_persist_assistant_summary_sync,
            kwargs=kwargs,
        )
        return
    _persist_assistant_summary_sync(**kwargs)
