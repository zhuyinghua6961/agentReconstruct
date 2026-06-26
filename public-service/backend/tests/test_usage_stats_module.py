from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from app.core.timezone import BEIJING_TIMEZONE
from app.modules.usage_stats.helpers import (
    normalize_usage_stats_sort_by,
    normalize_usage_stats_sort_order,
    should_count_search_response,
)
from app.modules.usage_stats.repository import UsageStatsRepository
from app.modules.usage_stats.service import UsageStatsService


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str, ex=None):
        self.store[key] = value
        return True

    def delete(self, *keys: str):
        count = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                count += 1
        return count


class _MemoryUsageStatsRepo(UsageStatsRepository):
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.sessions: list[dict[str, Any]] = []
        self.daily: dict[tuple[int, date], dict[str, Any]] = {}
        self.users = [
            {
                "id": 7,
                "username": "alice",
                "role": "user",
                "status": "active",
                "user_type": 3,
                "personnel_id": None,
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
                "created_at": datetime.now(BEIJING_TIMEZONE),
                "updated_at": datetime.now(BEIJING_TIMEZONE),
            }
        ]

    def insert_activity_event(self, **kwargs: Any) -> int:
        self.events.append(dict(kwargs))
        return len(self.events)

    def insert_online_session(self, **kwargs: Any) -> int:
        self.sessions.append(dict(kwargs))
        return len(self.sessions)

    def increment_daily_event_count(self, *, user_id: int, event_type: str, occurred_at: datetime, increment: int = 1) -> None:
        stat_date = occurred_at.date()
        row = self.daily.setdefault(
            (int(user_id), stat_date),
            {
                "ask_query_count": 0,
                "file_qa_count": 0,
                "literature_search_count": 0,
                "patent_search_count": 0,
                "active_seconds": 0,
                "last_active_at": occurred_at,
            },
        )
        column = {
            "ask_query": "ask_query_count",
            "file_qa": "file_qa_count",
            "literature_search": "literature_search_count",
            "patent_search": "patent_search_count",
        }[event_type]
        row[column] = int(row[column]) + int(increment)
        row["last_active_at"] = occurred_at

    def add_daily_active_seconds(self, *, user_id: int, occurred_at: datetime, active_seconds: int) -> None:
        stat_date = occurred_at.date()
        row = self.daily.setdefault(
            (int(user_id), stat_date),
            {
                "ask_query_count": 0,
                "file_qa_count": 0,
                "literature_search_count": 0,
                "patent_search_count": 0,
                "active_seconds": 0,
                "last_active_at": occurred_at,
            },
        )
        row["active_seconds"] = int(row["active_seconds"]) + int(active_seconds)
        row["last_active_at"] = occurred_at

    def list_users_with_stats(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        stat_from = kwargs["stat_from"]
        stat_to = kwargs["stat_to"]
        totals: dict[int, dict[str, Any]] = {}
        for (user_id, stat_date), row in self.daily.items():
            if stat_date < stat_from or stat_date > stat_to:
                continue
            bucket = totals.setdefault(
                int(user_id),
                {
                    "ask_query_count": 0,
                    "file_qa_count": 0,
                    "literature_search_count": 0,
                    "patent_search_count": 0,
                    "active_seconds": 0,
                    "last_active_at": None,
                },
            )
            for key in bucket:
                if key == "last_active_at":
                    bucket[key] = row.get(key) or bucket[key]
                else:
                    bucket[key] = int(bucket[key]) + int(row.get(key) or 0)
        rows = []
        for user in self.users:
            stats = totals.get(int(user["id"]), {})
            rows.append({**user, **stats})
        return rows, len(rows)


class _FakeAdminUsers:
    def _build_user_payload(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user.get("id"),
            "username": user.get("username"),
            "personnel_display": "未绑定",
            "department_display": "未填写",
        }


def test_normalize_usage_stats_sort_helpers():
    assert normalize_usage_stats_sort_by("ask_total") == "ask_total"
    assert normalize_usage_stats_sort_by("invalid") == "last_active_at"
    assert normalize_usage_stats_sort_order("asc") == "asc"
    assert normalize_usage_stats_sort_order("invalid") == "desc"


def test_usage_stats_sort_sql_for_last_active_at():
    sql = UsageStatsRepository._sort_sql("last_active_at", "desc")
    assert "IS NULL) ASC" in sql
    assert "last_active_at DESC" in sql
    assert " 0," not in sql


def test_should_count_search_response_rules():
    assert should_count_search_response(payload={"items": [{"doi": "x"}]}, status_code=200) is True
    assert should_count_search_response(payload={"error": "x", "items": []}, status_code=200) is False
    assert should_count_search_response(payload={"code": "RETRIEVAL_RUNTIME_UNAVAILABLE"}, status_code=200) is False
    assert should_count_search_response(payload={"items": []}, status_code=500) is False


def test_record_event_updates_daily_stats(monkeypatch):
    repo = _MemoryUsageStatsRepo()
    service = UsageStatsService(repository=repo, admin_users=_FakeAdminUsers())
    result = service.record_event(user_id=7, event_type="ask_query")
    assert result["success"] is True
    assert len(repo.events) == 1
    assert repo.daily[(7, date.today())]["ask_query_count"] == 1


def test_heartbeat_skips_without_recent_interaction(monkeypatch):
    repo = _MemoryUsageStatsRepo()
    service = UsageStatsService(repository=repo, admin_users=_FakeAdminUsers())
    monkeypatch.setattr(
        "app.modules.usage_stats.service.resolve_usage_stats_redis_service",
        lambda: None,
    )
    result = service.process_heartbeat(user_id=7, session_id="sess-1", finalize=False)
    assert result["success"] is True
    assert result["data"]["skipped"] is True
    assert repo.sessions == []


def test_heartbeat_counts_only_after_recent_interaction(monkeypatch):
    repo = _MemoryUsageStatsRepo()
    service = UsageStatsService(repository=repo, admin_users=_FakeAdminUsers())
    fake_redis = _FakeRedis()
    start = datetime.now(BEIJING_TIMEZONE)
    clock = {"step": 0}

    def fake_now_beijing():
        value = start + timedelta(seconds=clock["step"])
        clock["step"] += 61
        return value

    monkeypatch.setattr("app.modules.usage_stats.service.now_beijing", fake_now_beijing)

    class _FakeRedisService:
        def prefixed(self, *segments):
            return ":".join(str(item) for item in segments)

        def get_json(self, key, default=None):
            raw = fake_redis.get(key)
            if not raw:
                return default
            import json

            return json.loads(raw)

        def set_json(self, key, value, ttl_seconds=None):
            import json

            fake_redis.set(key, json.dumps(value))
            return True

        def delete(self, *keys):
            return fake_redis.delete(*keys)

    monkeypatch.setattr(
        "app.modules.usage_stats.service.resolve_usage_stats_redis_service",
        lambda: _FakeRedisService(),
    )

    interaction = start.isoformat(timespec="seconds")
    assert service.process_heartbeat(
        user_id=7,
        session_id="sess-1",
        finalize=False,
        last_interaction_at=interaction,
    )["success"] is True
    assert service.process_heartbeat(
        user_id=7,
        session_id="sess-1",
        finalize=False,
        last_interaction_at=interaction,
    )["success"] is True
    assert repo.daily[(7, date.today())]["active_seconds"] == 60
    assert service.process_heartbeat(
        user_id=7,
        session_id="sess-1",
        finalize=True,
        last_interaction_at=interaction,
    )["success"] is True
    assert repo.daily[(7, date.today())]["active_seconds"] == 120


def test_touch_user_interaction_extends_server_session(monkeypatch):
    repo = _MemoryUsageStatsRepo()
    service = UsageStatsService(repository=repo, admin_users=_FakeAdminUsers())
    fake_redis = _FakeRedis()

    class _FakeRedisService:
        def prefixed(self, *segments):
            return ":".join(str(item) for item in segments)

        def get_json(self, key, default=None):
            raw = fake_redis.get(key)
            if not raw:
                return default
            import json

            return json.loads(raw)

        def set_json(self, key, value, ttl_seconds=None):
            import json

            fake_redis.set(key, json.dumps(value))
            return True

        def delete(self, *keys):
            return fake_redis.delete(*keys)

    monkeypatch.setattr(
        "app.modules.usage_stats.service.resolve_usage_stats_redis_service",
        lambda: _FakeRedisService(),
    )
    when = datetime.now(BEIJING_TIMEZONE)
    service.touch_user_interaction(user_id=7, occurred_at=when)
    result = service.process_heartbeat(
        user_id=7,
        session_id="sess-1",
        finalize=False,
        last_interaction_at=None,
    )
    assert result["success"] is True
    assert result["data"].get("session_restarted") or result["data"].get("active_seconds") is not None


def test_heartbeat_finalize_persists_session(monkeypatch):
    repo = _MemoryUsageStatsRepo()
    service = UsageStatsService(repository=repo, admin_users=_FakeAdminUsers())
    fake_redis = _FakeRedis()
    start = datetime.now(BEIJING_TIMEZONE)
    clock = {"step": 0}

    def fake_now_beijing():
        value = start + timedelta(seconds=clock["step"])
        clock["step"] += 61
        return value

    monkeypatch.setattr("app.modules.usage_stats.service.now_beijing", fake_now_beijing)

    class _FakeRedisService:
        def prefixed(self, *segments):
            return ":".join(str(item) for item in segments)

        def get_json(self, key, default=None):
            raw = fake_redis.get(key)
            if not raw:
                return default
            import json

            return json.loads(raw)

        def set_json(self, key, value, ttl_seconds=None):
            import json

            fake_redis.set(key, json.dumps(value))
            return True

        def delete(self, *keys):
            return fake_redis.delete(*keys)

    monkeypatch.setattr(
        "app.modules.usage_stats.service.resolve_usage_stats_redis_service",
        lambda: _FakeRedisService(),
    )

    first = service.process_heartbeat(
        user_id=7,
        session_id="sess-1",
        finalize=False,
        last_interaction_at=start.isoformat(timespec="seconds"),
    )
    assert first["success"] is True
    finalized = service.process_heartbeat(
        user_id=7,
        session_id="sess-1",
        finalize=True,
        last_interaction_at=(start + timedelta(seconds=61)).isoformat(timespec="seconds"),
    )
    assert finalized["success"] is True
    assert len(repo.sessions) == 1
    assert repo.daily[(7, date.today())]["active_seconds"] == 60


def test_heartbeat_finalize_does_not_double_count_daily_stats(monkeypatch):
    repo = _MemoryUsageStatsRepo()
    service = UsageStatsService(repository=repo, admin_users=_FakeAdminUsers())
    fake_redis = _FakeRedis()
    start = datetime.now(BEIJING_TIMEZONE)
    clock = {"step": 0}

    def fake_now_beijing():
        value = start + timedelta(seconds=clock["step"])
        clock["step"] += 61
        return value

    monkeypatch.setattr("app.modules.usage_stats.service.now_beijing", fake_now_beijing)

    class _FakeRedisService:
        def prefixed(self, *segments):
            return ":".join(str(item) for item in segments)

        def get_json(self, key, default=None):
            raw = fake_redis.get(key)
            if not raw:
                return default
            import json

            return json.loads(raw)

        def set_json(self, key, value, ttl_seconds=None):
            import json

            fake_redis.set(key, json.dumps(value))
            return True

        def delete(self, *keys):
            return fake_redis.delete(*keys)

    monkeypatch.setattr(
        "app.modules.usage_stats.service.resolve_usage_stats_redis_service",
        lambda: _FakeRedisService(),
    )

    assert service.process_heartbeat(
        user_id=7,
        session_id="sess-1",
        finalize=False,
        last_interaction_at=start.isoformat(timespec="seconds"),
    )["success"] is True
    assert service.process_heartbeat(
        user_id=7,
        session_id="sess-1",
        finalize=False,
        last_interaction_at=(start + timedelta(seconds=61)).isoformat(timespec="seconds"),
    )["success"] is True
    assert repo.daily[(7, date.today())]["active_seconds"] == 60
    assert service.process_heartbeat(
        user_id=7,
        session_id="sess-1",
        finalize=True,
        last_interaction_at=(start + timedelta(seconds=122)).isoformat(timespec="seconds"),
    )["success"] is True
    assert repo.daily[(7, date.today())]["active_seconds"] == 120


def test_list_usage_stats_returns_rows(monkeypatch):
    repo = _MemoryUsageStatsRepo()
    service = UsageStatsService(repository=repo, admin_users=_FakeAdminUsers())
    today = date.today()
    repo.daily[(7, today)] = {
        "ask_query_count": 2,
        "file_qa_count": 1,
        "literature_search_count": 3,
        "patent_search_count": 4,
        "active_seconds": 120,
        "last_active_at": datetime.now(BEIJING_TIMEZONE),
    }
    result = service.list_usage_stats(stat_from=today - timedelta(days=1), stat_to=today)
    assert result["success"] is True
    assert result["data"][0]["ask_query_count"] == 2
    assert result["data"][0]["literature_search_count"] == 3
