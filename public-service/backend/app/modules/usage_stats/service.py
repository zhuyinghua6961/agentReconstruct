from __future__ import annotations

import csv
import io
import time
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from fastapi.responses import Response

from app.core.spreadsheet import build_xlsx
from app.core.timezone import ensure_beijing_datetime, now_beijing
from app.modules.admin_users.service import AdminUsersService, admin_users_service
from app.modules.usage_stats.helpers import (
    normalize_event_type,
    normalize_usage_stats_sort_by,
    normalize_usage_stats_sort_order,
)
from app.modules.usage_stats.redis_cache import (
    resolve_usage_stats_lock_manager,
    resolve_usage_stats_redis_service,
)
from app.modules.usage_stats.repository import UsageStatsRepository


HEARTBEAT_INTERVAL_SECONDS = 60
IDLE_TIMEOUT_SECONDS = 900
SESSION_REDIS_TTL_SECONDS = 3600
HEARTBEAT_LOCK_TTL_SECONDS = 10
HEARTBEAT_LOCK_WAIT_SECONDS = 3.0


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    return ensure_beijing_datetime(parsed)


def _heartbeat_delta_seconds(gap_seconds: float) -> int:
    if gap_seconds > float(IDLE_TIMEOUT_SECONDS):
        return 0
    return int(min(max(0.0, gap_seconds), float(HEARTBEAT_INTERVAL_SECONDS)))


def _isoformat_dt(value: datetime) -> str:
    return ensure_beijing_datetime(value).isoformat(timespec="seconds")


def _merge_interaction_at(*values: datetime | None) -> datetime | None:
    candidates = [value for value in values if value is not None]
    return max(candidates) if candidates else None


def _interaction_idle_seconds(*, last_interaction: datetime | None, now: datetime) -> float:
    if last_interaction is None:
        return float("inf")
    return max(0.0, (now - last_interaction).total_seconds())


def _has_recent_interaction(*, last_interaction: datetime | None, now: datetime) -> bool:
    return _interaction_idle_seconds(last_interaction=last_interaction, now=now) <= float(IDLE_TIMEOUT_SECONDS)


def _format_duration_label(seconds: int) -> str:
    total = max(0, int(seconds or 0))
    hours = total // 3600
    minutes = (total % 3600) // 60
    if hours > 0:
        return f"{hours}小时{minutes}分"
    return f"{minutes}分"


def _format_datetime_label(value: Any) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).replace("T", " ").strip()
    return text[:19] if text else "-"


def _build_usage_stats_item(row: dict[str, Any], *, admin_users: AdminUsersService) -> dict[str, Any]:
    user_payload = admin_users._build_user_payload(row)
    ask_query_count = int(row.get("ask_query_count") or 0)
    file_qa_count = int(row.get("file_qa_count") or 0)
    return {
        **user_payload,
        "ask_query_count": ask_query_count,
        "file_qa_count": file_qa_count,
        "ask_total_count": ask_query_count + file_qa_count,
        "literature_search_count": int(row.get("literature_search_count") or 0),
        "patent_search_count": int(row.get("patent_search_count") or 0),
        "active_seconds": int(row.get("active_seconds") or 0),
        "last_active_at": row.get("last_active_at"),
    }


def _usage_stats_query_kwargs(
    *,
    stat_from: date,
    stat_to: date,
    keyword: str | None,
    primary_department_id: int | None,
    secondary_department_id: int | None,
    tertiary_department_id: int | None,
    sort_by: str | None,
    sort_order: str | None,
) -> dict[str, Any]:
    return {
        "stat_from": stat_from,
        "stat_to": stat_to,
        "keyword": str(keyword or "").strip() or None,
        "primary_department_id": primary_department_id,
        "secondary_department_id": secondary_department_id,
        "tertiary_department_id": tertiary_department_id,
        "sort_by": normalize_usage_stats_sort_by(sort_by),
        "sort_order": normalize_usage_stats_sort_order(sort_order),
    }


EXPORT_HEADERS = [
    "账号",
    "绑定人员",
    "部门",
    "普通问答",
    "文件问答",
    "问答合计",
    "文献检索",
    "专利检索",
    "活跃使用",
    "活跃秒数",
    "最后活跃",
]


class UsageStatsService:
    def __init__(
        self,
        *,
        repository: UsageStatsRepository | None = None,
        admin_users: AdminUsersService | None = None,
    ) -> None:
        self._repo = repository or UsageStatsRepository()
        self._admin_users = admin_users or admin_users_service

    def _session_redis_key(self, *, user_id: int) -> str:
        redis_service = resolve_usage_stats_redis_service()
        if redis_service is None:
            return ""
        return redis_service.prefixed("usage_stats", "session", int(user_id))

    def _load_session_state(self, *, user_id: int) -> dict[str, Any] | None:
        redis_service = resolve_usage_stats_redis_service()
        if redis_service is None:
            return None
        key = self._session_redis_key(user_id=user_id)
        if not key:
            return None
        payload = redis_service.get_json(key, default=None)
        return payload if isinstance(payload, dict) else None

    def _save_session_state(self, *, user_id: int, state: dict[str, Any] | None) -> None:
        redis_service = resolve_usage_stats_redis_service()
        if redis_service is None:
            return
        key = self._session_redis_key(user_id=user_id)
        if not key:
            return
        if state is None:
            redis_service.delete(key)
            return
        redis_service.set_json(key, state, ttl_seconds=SESSION_REDIS_TTL_SECONDS)

    def _close_session(
        self,
        *,
        user_id: int,
        state: dict[str, Any],
        ended_at: datetime,
        extra_seconds: int = 0,
    ) -> None:
        started_at = _parse_iso_datetime(state.get("started_at")) or ended_at
        session_id = str(state.get("session_id") or "").strip() or "unknown"
        pending_seconds = max(0, int(state.get("pending_seconds") or 0))
        active_seconds = pending_seconds + max(0, int(extra_seconds))
        if active_seconds > 0:
            self._repo.insert_online_session(
                user_id=user_id,
                session_id=session_id,
                started_at=started_at,
                ended_at=ended_at,
                active_seconds=active_seconds,
            )

    def _flush_daily_active_seconds(
        self,
        *,
        user_id: int,
        occurred_at: datetime,
        active_seconds: int,
    ) -> None:
        if int(active_seconds) <= 0:
            return
        self._repo.add_daily_active_seconds(
            user_id=user_id,
            occurred_at=occurred_at,
            active_seconds=int(active_seconds),
        )

    def record_event(
        self,
        *,
        user_id: int,
        event_type: str,
        occurred_at: datetime | None = None,
        trace_id: str | None = None,
        conversation_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_event_type(event_type)
        if normalized is None:
            return {"success": False, "error": "invalid_event_type", "code": "VALIDATION_ERROR"}
        when = occurred_at or now_beijing()
        try:
            self._repo.insert_activity_event(
                user_id=int(user_id),
                event_type=normalized,
                occurred_at=when,
                trace_id=trace_id,
                conversation_id=conversation_id,
                metadata=metadata,
            )
            self._repo.increment_daily_event_count(
                user_id=int(user_id),
                event_type=normalized,
                occurred_at=when,
            )
            self.touch_user_interaction(user_id=int(user_id), occurred_at=when)
            return {"success": True}
        except Exception as exc:
            if exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}:
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "USAGE_STATS_RECORD_ERROR"}

    def _heartbeat_lock_key(self, *, user_id: int) -> str:
        redis_service = resolve_usage_stats_redis_service()
        if redis_service is None:
            return ""
        return redis_service.key_factory.lock("usage_stats", "heartbeat", int(user_id))

    @contextmanager
    def _heartbeat_lock(self, *, user_id: int):
        lock_manager = resolve_usage_stats_lock_manager()
        if not lock_manager.available:
            yield True
            return
        key = self._heartbeat_lock_key(user_id=user_id)
        if not key:
            yield True
            return
        handle = None
        deadline = time.monotonic() + HEARTBEAT_LOCK_WAIT_SECONDS
        while handle is None and time.monotonic() < deadline:
            handle = lock_manager.acquire(key, ttl_seconds=HEARTBEAT_LOCK_TTL_SECONDS)
            if handle is None:
                time.sleep(0.05)
        if handle is None:
            yield False
            return
        try:
            yield True
        finally:
            lock_manager.release(handle)

    def touch_user_interaction(self, *, user_id: int, occurred_at: datetime | None = None) -> None:
        when = ensure_beijing_datetime(occurred_at or now_beijing())
        with self._heartbeat_lock(user_id=int(user_id)) as acquired:
            if not acquired:
                return
            state = self._load_session_state(user_id=int(user_id))
            if state is None:
                state = self._new_session_state(
                    session_id="event",
                    now=when,
                    last_interaction=when,
                )
            else:
                merged = _merge_interaction_at(
                    _parse_iso_datetime(state.get("last_interaction_at")),
                    when,
                )
                if merged is not None:
                    state = {**state, "last_interaction_at": _isoformat_dt(merged)}
            self._save_session_state(user_id=int(user_id), state=state)

    def _new_session_state(
        self,
        *,
        session_id: str,
        now: datetime,
        last_interaction: datetime,
    ) -> dict[str, Any]:
        return {
            "session_id": str(session_id or "").strip()[:64] or "unknown",
            "started_at": _isoformat_dt(last_interaction),
            "last_seen_at": _isoformat_dt(now),
            "last_interaction_at": _isoformat_dt(last_interaction),
            "pending_seconds": 0,
        }

    def _response_payload(self, **data: Any) -> dict[str, Any]:
        return {"success": True, "data": data}

    def process_heartbeat(
        self,
        *,
        user_id: int,
        session_id: str,
        finalize: bool = False,
        last_interaction_at: str | None = None,
    ) -> dict[str, Any]:
        with self._heartbeat_lock(user_id=int(user_id)) as acquired:
            if not acquired:
                return {"success": False, "error": "heartbeat_busy", "code": "USAGE_STATS_BUSY"}
            return self._process_heartbeat_locked(
                user_id=user_id,
                session_id=session_id,
                finalize=finalize,
                client_interaction_at=_parse_iso_datetime(last_interaction_at),
            )

    def _process_heartbeat_locked(
        self,
        *,
        user_id: int,
        session_id: str,
        finalize: bool = False,
        client_interaction_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = now_beijing()
        normalized_session_id = str(session_id or "").strip()[:64]
        if not normalized_session_id and not finalize:
            return {"success": False, "error": "session_id_required", "code": "VALIDATION_ERROR"}

        state = self._load_session_state(user_id=user_id)
        if state is None:
            if finalize:
                return self._response_payload(finalized=True, active_seconds=0)
            if not _has_recent_interaction(last_interaction=client_interaction_at, now=now):
                return self._response_payload(skipped=True, reason="no_recent_interaction")
            last_interaction = client_interaction_at or now
            state = self._new_session_state(
                session_id=normalized_session_id,
                now=now,
                last_interaction=last_interaction,
            )
            self._save_session_state(user_id=user_id, state=state)
            return self._response_payload(
                session_started=True,
                last_interaction_at=state["last_interaction_at"],
            )

        last_interaction = _merge_interaction_at(
            _parse_iso_datetime(state.get("last_interaction_at")),
            client_interaction_at,
        )
        if not _has_recent_interaction(last_interaction=last_interaction, now=now):
            ended_at = last_interaction or _parse_iso_datetime(state.get("last_seen_at")) or now
            self._close_session(user_id=user_id, state=state, ended_at=ended_at, extra_seconds=0)
            self._save_session_state(user_id=user_id, state=None)
            if finalize or not _has_recent_interaction(last_interaction=client_interaction_at, now=now):
                return self._response_payload(
                    finalized=True,
                    active_seconds=0,
                    reason="interaction_idle",
                )
            last_interaction = client_interaction_at or now
            state = self._new_session_state(
                session_id=normalized_session_id,
                now=now,
                last_interaction=last_interaction,
            )
            self._save_session_state(user_id=user_id, state=state)
            return self._response_payload(
                session_restarted=True,
                last_interaction_at=state["last_interaction_at"],
            )

        last_seen = _parse_iso_datetime(state.get("last_seen_at")) or now
        gap_seconds = max(0.0, (now - last_seen).total_seconds())
        pending_seconds = max(0, int(state.get("pending_seconds") or 0))
        delta_seconds = _heartbeat_delta_seconds(gap_seconds)
        pending_seconds += delta_seconds

        if finalize:
            self._flush_daily_active_seconds(
                user_id=user_id,
                occurred_at=now,
                active_seconds=delta_seconds,
            )
            self._close_session(
                user_id=user_id,
                state={
                    **state,
                    "pending_seconds": pending_seconds,
                    "last_interaction_at": _isoformat_dt(last_interaction or now),
                },
                ended_at=now,
                extra_seconds=0,
            )
            self._save_session_state(user_id=user_id, state=None)
            return self._response_payload(
                finalized=True,
                active_seconds=pending_seconds,
                last_interaction_at=_isoformat_dt(last_interaction or now),
            )

        state = {
            **state,
            "session_id": normalized_session_id or str(state.get("session_id") or "unknown"),
            "last_seen_at": _isoformat_dt(now),
            "last_interaction_at": _isoformat_dt(last_interaction or now),
            "pending_seconds": pending_seconds,
        }
        self._save_session_state(user_id=user_id, state=state)
        self._flush_daily_active_seconds(
            user_id=user_id,
            occurred_at=now,
            active_seconds=delta_seconds,
        )
        return self._response_payload(
            active_seconds=pending_seconds,
            last_interaction_at=state["last_interaction_at"],
        )

    def list_usage_stats(
        self,
        *,
        stat_from: date,
        stat_to: date,
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
        primary_department_id: int | None = None,
        secondary_department_id: int | None = None,
        tertiary_department_id: int | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> dict[str, Any]:
        if stat_to < stat_from:
            return {"success": False, "error": "invalid_date_range", "code": "VALIDATION_ERROR"}
        page = max(1, int(page))
        page_size = int(page_size)
        if page_size < 1 or page_size > 100:
            page_size = 20
        offset = (page - 1) * page_size
        query_kwargs = _usage_stats_query_kwargs(
            stat_from=stat_from,
            stat_to=stat_to,
            keyword=keyword,
            primary_department_id=primary_department_id,
            secondary_department_id=secondary_department_id,
            tertiary_department_id=tertiary_department_id,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        try:
            rows, total = self._repo.list_users_with_stats(
                offset=offset,
                limit=page_size,
                **query_kwargs,
            )
            items = [_build_usage_stats_item(row, admin_users=self._admin_users) for row in rows]
            return {
                "success": True,
                "data": items,
                "pagination": {"page": page, "page_size": page_size, "total": total},
                "range": {"from": stat_from.isoformat(), "to": stat_to.isoformat()},
                "sort": {
                    "sort_by": query_kwargs["sort_by"],
                    "sort_order": query_kwargs["sort_order"],
                },
            }
        except Exception as exc:
            if exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}:
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "USAGE_STATS_FETCH_ERROR"}

    def export_usage_stats(
        self,
        *,
        stat_from: date,
        stat_to: date,
        fmt: str,
        keyword: str | None = None,
        primary_department_id: int | None = None,
        secondary_department_id: int | None = None,
        tertiary_department_id: int | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> Response | dict[str, Any]:
        if stat_to < stat_from:
            return {"success": False, "error": "invalid_date_range", "code": "VALIDATION_ERROR"}
        normalized_fmt = str(fmt or "xlsx").strip().lower()
        if normalized_fmt not in {"csv", "xlsx"}:
            return {"success": False, "error": "unsupported_format", "code": "VALIDATION_ERROR"}
        query_kwargs = _usage_stats_query_kwargs(
            stat_from=stat_from,
            stat_to=stat_to,
            keyword=keyword,
            primary_department_id=primary_department_id,
            secondary_department_id=secondary_department_id,
            tertiary_department_id=tertiary_department_id,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        try:
            rows = self._repo.list_users_with_stats_for_export(**query_kwargs)
            export_rows: list[list[str]] = []
            for row in rows:
                item = _build_usage_stats_item(row, admin_users=self._admin_users)
                export_rows.append(
                    [
                        str(item.get("username") or ""),
                        str(item.get("personnel_display") or "未绑定"),
                        str(item.get("department_display") or "未填写"),
                        str(item.get("ask_query_count") or 0),
                        str(item.get("file_qa_count") or 0),
                        str(item.get("ask_total_count") or 0),
                        str(item.get("literature_search_count") or 0),
                        str(item.get("patent_search_count") or 0),
                        _format_duration_label(int(item.get("active_seconds") or 0)),
                        str(item.get("active_seconds") or 0),
                        _format_datetime_label(item.get("last_active_at")),
                    ]
                )
            filename = f"usage_stats_{stat_from.isoformat()}_{stat_to.isoformat()}.{normalized_fmt}"
            if normalized_fmt == "csv":
                buffer = io.StringIO()
                writer = csv.writer(buffer, lineterminator="\n")
                writer.writerow(EXPORT_HEADERS)
                writer.writerows(export_rows)
                return Response(
                    content=buffer.getvalue().encode("utf-8-sig"),
                    media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
            return Response(
                content=build_xlsx(headers=EXPORT_HEADERS, rows=export_rows, sheet_name="数据统计"),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as exc:
            if exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}:
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "USAGE_STATS_EXPORT_ERROR"}


usage_stats_service = UsageStatsService()


def set_usage_stats_service(service: UsageStatsService) -> None:
    global usage_stats_service
    usage_stats_service = service
