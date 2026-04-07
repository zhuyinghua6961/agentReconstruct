import json
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime

import pytest
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

from app.core.deps import AuthContext
from app.core.config import get_settings
from app.core.db import Database
from app.core.errors import DatabaseUnavailableError
from app.main import app
from app.modules.auth import deps as auth_deps_module
from app.modules.auth import service as auth_service_module
from app.modules.auth.service import AuthService, TokenService
from app.modules.quota import api as quota_api_module
from app.modules.quota import deps as quota_deps
from app.modules.quota import service as quota_service_module
from app.modules.quota.deps import QuotaCheckFailedError, QuotaConfigMissingError, QuotaExceededError, finalize_quota
from app.modules.quota.schemas import CreateQuotaConfigRequest, UpdateQuotaConfigRequest
from app.modules.quota.service import QuotaService, period_key, quota_service
from app.integrations.redis import RedisService

INTERNAL_TOKEN_ENV = "PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN"
INTERNAL_TOKEN = "authority-test-token"


def _decode(response):
    return json.loads(response.body.decode("utf-8"))


def _internal_headers(service_name: str = "gateway") -> dict[str, str]:
    return {
        "X-Internal-Service-Name": service_name,
        "X-Internal-Service-Token": INTERNAL_TOKEN,
    }


class _FakeQuotaRepo:
    def __init__(self) -> None:
        self.configs = {
            "ask_query": {
                "quota_type": "ask_query",
                "quota_name": "Ask Query",
                "period": "daily",
                "period_days": None,
                "default_limit": 10,
                "daily_limit": 10,
                "weekly_limit": None,
                "monthly_limit": None,
                "is_active": 1,
            },
            "pdf_summary": {
                "quota_type": "pdf_summary",
                "quota_name": "PDF Summary",
                "period": "daily",
                "period_days": None,
                "default_limit": 2,
                "daily_limit": 2,
                "weekly_limit": 6,
                "monthly_limit": 20,
                "is_active": 1,
            },
            "excel_upload": {
                "quota_type": "excel_upload",
                "quota_name": "Excel Upload",
                "period": "custom_days",
                "period_days": 10,
                "default_limit": 5,
                "daily_limit": None,
                "weekly_limit": None,
                "monthly_limit": None,
                "is_active": 0,
            },
        }
        self.usage: dict[tuple[int, str, str], int] = {}
        self.overrides: dict[tuple[int, str], int] = {}
        self.last_create_call: dict | None = None
        self.last_update_call: tuple | None = None
        self.update_calls: list[tuple] = []
        self.last_increment_call: list[tuple[int, str, str]] = []
        self.last_reset_call: list[tuple[int, str, str]] = []
        self.get_config_calls = 0
        self.list_active_calls = 0
        self.list_all_calls = 0
        self.get_override_calls = 0

    def get_quota_config(self, quota_type: str):
        self.get_config_calls += 1
        cfg = self.configs.get(quota_type)
        return dict(cfg) if cfg else None

    def get_user_override_limit(self, *, user_id: int, quota_type: str):
        self.get_override_calls += 1
        return self.overrides.get((user_id, quota_type))

    def get_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int:
        return int(self.usage.get((user_id, quota_type, period_key), 0))

    def increment_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int:
        key = (user_id, quota_type, period_key)
        self.last_increment_call.append(key)
        self.usage[key] = int(self.usage.get(key, 0)) + 1
        return self.usage[key]

    def list_active_configs(self):
        self.list_active_calls += 1
        return [dict(cfg) for cfg in self.configs.values() if int(cfg.get("is_active", 0)) == 1]

    def list_all_configs(self):
        self.list_all_calls += 1
        rows = []
        for index, cfg in enumerate(self.configs.values(), start=1):
            rows.append({"id": index, **dict(cfg), "created_at": None, "updated_at": None})
        return rows

    def create_quota_config(self, **kwargs):
        self.last_create_call = dict(kwargs)
        quota_type = str(kwargs["quota_type"])
        self.configs[quota_type] = {
            "quota_type": quota_type,
            "quota_name": kwargs["quota_name"],
            "period": kwargs["period"],
            "period_days": kwargs["period_days"],
            "default_limit": kwargs["default_limit"],
            "daily_limit": kwargs["daily_limit"],
            "weekly_limit": kwargs["weekly_limit"],
            "monthly_limit": kwargs["monthly_limit"],
            "is_active": 1 if kwargs["is_active"] else 0,
        }
        return 1

    def update_quota_config(self, *, quota_type: str, default_limit: int, daily_limit: int | None, weekly_limit: int | None, monthly_limit: int | None, is_active: bool, period: str, period_days: int | None):
        self.last_update_call = (quota_type, default_limit, daily_limit, weekly_limit, monthly_limit, is_active, period, period_days)
        self.update_calls.append(self.last_update_call)
        existing = self.configs.get(quota_type)
        if not existing:
            return 0
        changed = any([
            int(existing.get("default_limit") or 0) != int(default_limit),
            existing.get("daily_limit") != daily_limit,
            existing.get("weekly_limit") != weekly_limit,
            existing.get("monthly_limit") != monthly_limit,
            int(existing.get("is_active") or 0) != (1 if is_active else 0),
            str(existing.get("period") or "") != str(period or ""),
            existing.get("period_days") != period_days,
        ])
        if not changed:
            return 0
        existing.update(
            {
                "default_limit": default_limit,
                "daily_limit": daily_limit,
                "weekly_limit": weekly_limit,
                "monthly_limit": monthly_limit,
                "is_active": 1 if is_active else 0,
                "period": period,
                "period_days": period_days,
            }
        )
        return 1

    def reset_user_usage(self, *, user_id: int, quota_type: str, period_key: str):
        self.last_reset_call.append((user_id, quota_type, period_key))
        self.usage[(user_id, quota_type, period_key)] = 0
        return 1


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.expirations: dict[str, int] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = int(ex)
        return True

    def delete(self, *keys: str):
        deleted = 0
        for key in keys:
            if key in self.values:
                deleted += 1
                self.values.pop(key, None)
                self.expirations.pop(key, None)
        return deleted

    def expire(self, key: str, seconds: int):
        if key not in self.values:
            return False
        self.expirations[key] = int(seconds)
        return True

    def ttl(self, key: str):
        return self.expirations.get(key)


class _SelectiveSetFailRedis(_FakeRedis):
    def __init__(self, *, fail_fragments: set[str]) -> None:
        super().__init__()
        self.fail_fragments = set(fail_fragments)

    def set(self, key: str, value, ex=None, nx=False):
        if any(fragment in str(key) for fragment in self.fail_fragments):
            return False
        return super().set(key, value, ex=ex, nx=nx)


class _ExpiringFakeRedis(_FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self._expires_at: dict[str, float] = {}

    def _purge(self, key: str) -> None:
        expires_at = self._expires_at.get(key)
        if expires_at is None:
            return
        if time.monotonic() < expires_at:
            return
        self.values.pop(key, None)
        self.expirations.pop(key, None)
        self._expires_at.pop(key, None)

    def get(self, key: str):
        self._purge(key)
        return super().get(key)

    def set(self, key: str, value, ex=None, nx=False):
        self._purge(key)
        if nx and key in self.values:
            return False
        result = super().set(key, value, ex=ex, nx=False)
        if result and ex is not None:
            self._expires_at[key] = time.monotonic() + max(1, int(ex))
        return result

    def delete(self, *keys: str):
        for key in keys:
            self._purge(key)
            self._expires_at.pop(key, None)
        return super().delete(*keys)

    def expire(self, key: str, seconds: int):
        self._purge(key)
        result = super().expire(key, seconds)
        if result:
            self._expires_at[key] = time.monotonic() + max(1, int(seconds))
        return result

    def ttl(self, key: str):
        self._purge(key)
        if key not in self.values:
            return None
        expires_at = self._expires_at.get(key)
        if expires_at is None:
            return None
        return max(0, int(expires_at - time.monotonic()))


def _set_quota_config(
    repo: _FakeQuotaRepo,
    *,
    quota_type: str,
    quota_name: str | None = None,
    default_limit: int = 5,
    daily_limit: int | None = None,
    weekly_limit: int | None = None,
    monthly_limit: int | None = None,
    period: str = "daily",
    period_days: int | None = None,
    is_active: int = 1,
) -> None:
    repo.configs[quota_type] = {
        "quota_type": quota_type,
        "quota_name": quota_name or quota_type.replace("_", " ").title(),
        "period": period,
        "period_days": period_days,
        "default_limit": default_limit,
        "daily_limit": daily_limit if daily_limit is not None else (default_limit if period == "daily" else None),
        "weekly_limit": weekly_limit,
        "monthly_limit": monthly_limit,
        "is_active": is_active,
    }


def test_quota_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/v1/quota/my" in paths
    assert "/api/v1/quota/configs" in paths
    assert "/api/v1/quota/users/{target_user_id}" in paths


def test_period_key_variants():
    assert period_key("none") == "unlimited"
    assert "-W" in period_key("weekly")
    assert len(period_key("monthly")) == 7
    assert period_key("custom_days", 7).endswith(":7d")


def test_service_multi_period_exposes_windows():
    service = QuotaService(repo=_FakeQuotaRepo())
    result = service.check_quota(user_id=1, quota_type="pdf_summary")
    assert result["success"] is True
    assert result["multi_period_enabled"] is True
    assert {item["period"] for item in result["windows"]} == {"daily", "weekly", "monthly"}


def test_service_check_quota_uses_cached_metadata(monkeypatch):
    monkeypatch.setenv("QUOTA_CONFIG_CACHE_TTL_SECONDS", "120")
    monkeypatch.setenv("QUOTA_OVERRIDE_CACHE_TTL_SECONDS", "120")
    repo = _FakeQuotaRepo()
    repo.overrides[(1, "ask_query")] = 7
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.check_quota(user_id=1, quota_type="ask_query")
    second = service.check_quota(user_id=1, quota_type="ask_query")

    assert first["success"] is True
    assert second["success"] is True
    assert repo.get_config_calls == 1
    assert repo.get_override_calls == 1


def test_service_check_quota_caches_missing_canonical_bucket_entries(monkeypatch):
    monkeypatch.setenv("QUOTA_CONFIG_CACHE_TTL_SECONDS", "120")
    repo = _FakeQuotaRepo()
    repo.configs = {
        "pdf_summary": {
            "quota_type": "pdf_summary",
            "quota_name": "PDF Summary",
            "period": "daily",
            "period_days": None,
            "default_limit": 2,
            "daily_limit": 2,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
    }
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.check_quota(user_id=1, quota_type="doc_assist")
    first_calls = repo.get_config_calls
    second = service.check_quota(user_id=1, quota_type="doc_assist")

    assert first["success"] is True
    assert second["success"] is True
    assert first_calls == 6
    assert repo.get_config_calls == first_calls


def test_service_create_internal_quota_grant_returns_token_for_non_exempt_user(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    result = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    assert result["success"] is True
    assert result["data"]["grant_id"]
    assert result["data"]["noop"] is False
    assert result["data"]["quota_type"] == "ask_query"


def test_service_create_internal_quota_grant_returns_noop_for_exempt_user(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 2})
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    result = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    assert result["success"] is True
    assert result["data"]["grant_id"]
    assert result["data"]["noop"] is True


def test_service_finalize_internal_quota_grant_counts_success_once(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    grant_id = str(created["data"]["grant_id"])
    first = service.finalize_internal_quota_grant(grant_id=grant_id, success=True)
    second = service.finalize_internal_quota_grant(grant_id=grant_id, success=True)

    assert first["success"] is True
    assert first["data"]["counted"] is True
    assert second["success"] is True
    assert second["data"]["counted"] is True
    assert second["data"]["idempotent"] is True
    assert len(repo.last_increment_call) == 1


def test_service_finalize_internal_quota_grant_is_concurrency_idempotent(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    grant_id = str(created["data"]["grant_id"])
    original_increment = service.increment_quota
    first_increment_entered = threading.Event()
    release_increment = threading.Event()
    first_thread_id: dict[str, int | None] = {"value": None}
    results: dict[str, dict] = {}
    errors: dict[str, Exception] = {}

    def blocking_increment(*, user_id: int, quota_type: str, anchored_window=None):
        current_thread_id = threading.get_ident()
        if first_thread_id["value"] is None:
            first_thread_id["value"] = current_thread_id
        if current_thread_id == first_thread_id["value"]:
            first_increment_entered.set()
            assert release_increment.wait(timeout=1.0)
        return original_increment(user_id=user_id, quota_type=quota_type, anchored_window=anchored_window)

    service.increment_quota = blocking_increment  # type: ignore[assignment]

    def _run(name: str) -> None:
        try:
            results[name] = service.finalize_internal_quota_grant(grant_id=grant_id, success=True)
        except Exception as exc:
            errors[name] = exc

    first_worker = threading.Thread(target=_run, args=("first",), daemon=True)
    second_worker = threading.Thread(target=_run, args=("second",), daemon=True)
    first_worker.start()
    assert first_increment_entered.wait(timeout=1.0)
    second_worker.start()
    time.sleep(0.05)
    assert second_worker.is_alive() is True
    release_increment.set()
    first_worker.join(timeout=2.0)
    second_worker.join(timeout=2.0)

    assert errors == {}
    assert first_worker.is_alive() is False
    assert second_worker.is_alive() is False
    assert set(results.keys()) == {"first", "second"}
    assert all(item["success"] is True for item in results.values())
    assert len(repo.last_increment_call) == 1
    assert sum(1 for item in results.values() if item["data"]["idempotent"] is True) == 1


def test_service_finalize_internal_quota_grant_keeps_finalize_lock_alive_past_ttl(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(QuotaService, "quota_lock_ttl_seconds", staticmethod(lambda: 1))
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_ExpiringFakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    grant_id = str(created["data"]["grant_id"])
    original_increment = service.increment_quota
    first_increment_entered = threading.Event()
    release_increment = threading.Event()
    first_thread_id: dict[str, int | None] = {"value": None}
    results: dict[str, dict] = {}
    errors: dict[str, Exception] = {}

    def blocking_increment(*, user_id: int, quota_type: str, anchored_window=None):
        current_thread_id = threading.get_ident()
        if first_thread_id["value"] is None:
            first_thread_id["value"] = current_thread_id
        if current_thread_id == first_thread_id["value"]:
            first_increment_entered.set()
            time.sleep(1.4)
            release_increment.set()
        else:
            assert release_increment.wait(timeout=2.0)
        return original_increment(user_id=user_id, quota_type=quota_type, anchored_window=anchored_window)

    service.increment_quota = blocking_increment  # type: ignore[assignment]

    def _run(name: str) -> None:
        try:
            results[name] = service.finalize_internal_quota_grant(grant_id=grant_id, success=True)
        except Exception as exc:
            errors[name] = exc

    first_worker = threading.Thread(target=_run, args=("first",), daemon=True)
    second_worker = threading.Thread(target=_run, args=("second",), daemon=True)
    first_worker.start()
    assert first_increment_entered.wait(timeout=1.0)
    time.sleep(1.1)
    second_worker.start()
    first_worker.join(timeout=4.0)
    second_worker.join(timeout=4.0)

    assert errors == {}
    assert first_worker.is_alive() is False
    assert second_worker.is_alive() is False
    assert all(item["success"] is True for item in results.values())
    assert len(repo.last_increment_call) == 1


def test_service_finalize_internal_quota_grant_releases_without_increment_on_failure(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    finalized = service.finalize_internal_quota_grant(grant_id=str(created["data"]["grant_id"]), success=False)

    assert finalized["success"] is True
    assert finalized["data"]["counted"] is False
    assert repo.last_increment_call == []


def test_service_create_internal_quota_grant_allows_parallel_reservations_when_quota_has_capacity(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(QuotaService, "quota_lock_wait_seconds", staticmethod(lambda: 1))
    monkeypatch.setattr(QuotaService, "quota_lock_retry_interval_ms", staticmethod(lambda: 10))
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    second = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    assert first["success"] is True
    assert second["success"] is True
    assert second["data"]["grant_id"] != first["data"]["grant_id"]


def test_service_create_internal_quota_grant_allows_parallel_file_qa_reservations_when_quota_has_capacity(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(QuotaService, "quota_lock_wait_seconds", staticmethod(lambda: 1))
    monkeypatch.setattr(QuotaService, "quota_lock_retry_interval_ms", staticmethod(lambda: 10))
    repo = _FakeQuotaRepo()
    repo.configs["file_qa"] = {
        "quota_type": "file_qa",
        "quota_name": "File QA",
        "period": "daily",
        "period_days": None,
        "default_limit": 5,
        "daily_limit": 5,
        "weekly_limit": None,
        "monthly_limit": None,
        "is_active": 1,
    }
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.create_internal_quota_grant(user_id=7, quota_type="file_qa")
    second = service.create_internal_quota_grant(user_id=7, quota_type="file_qa")

    assert first["success"] is True
    assert second["success"] is True
    assert second["data"]["grant_id"] != first["data"]["grant_id"]


@pytest.mark.parametrize(("quota_type", "quota_name"), [("file_view", "File View"), ("doc_assist", "Doc Assist")])
def test_service_create_internal_quota_grant_allows_parallel_reservations_for_remaining_canonical_buckets(
    monkeypatch,
    quota_type,
    quota_name,
):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(QuotaService, "quota_lock_wait_seconds", staticmethod(lambda: 1))
    monkeypatch.setattr(QuotaService, "quota_lock_retry_interval_ms", staticmethod(lambda: 10))
    repo = _FakeQuotaRepo()
    _set_quota_config(repo, quota_type=quota_type, quota_name=quota_name, default_limit=5, period="daily")
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.create_internal_quota_grant(user_id=7, quota_type=quota_type)
    second = service.create_internal_quota_grant(user_id=7, quota_type=quota_type)

    assert first["success"] is True
    assert second["success"] is True
    assert second["data"]["grant_id"] != first["data"]["grant_id"]


def test_service_create_internal_quota_grant_noop_does_not_consume_reservation_capacity(monkeypatch):
    repo = _FakeQuotaRepo()
    repo.configs["ask_query"]["default_limit"] = 1
    repo.configs["ask_query"]["daily_limit"] = 1
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 2})
    noop_grant = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    second = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    assert noop_grant["success"] is True
    assert noop_grant["data"]["noop"] is True
    assert second["success"] is True
    assert second["data"]["noop"] is False


def test_service_create_internal_quota_grant_returns_quota_exceeded_when_reservations_fill_limit(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(QuotaService, "quota_lock_wait_seconds", staticmethod(lambda: 1))
    monkeypatch.setattr(QuotaService, "quota_lock_retry_interval_ms", staticmethod(lambda: 10))
    repo = _FakeQuotaRepo()
    repo.configs["ask_query"]["default_limit"] = 1
    repo.configs["ask_query"]["daily_limit"] = 1
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    second = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    assert first["success"] is True
    assert second["success"] is False
    assert second["code"] == "QUOTA_EXCEEDED"


def test_service_create_internal_quota_grant_counts_completed_usage_plus_pending_reservations(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    repo.configs["ask_query"]["default_limit"] = 2
    repo.configs["ask_query"]["daily_limit"] = 2
    repo.usage[(7, "ask_query", period_key("daily"))] = 1
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    second = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    assert first["success"] is True
    assert second["success"] is False
    assert second["code"] == "QUOTA_EXCEEDED"


def test_service_create_internal_quota_grant_enforces_atomic_reservation_decision_under_concurrent_prechecks(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(QuotaService, "quota_lock_wait_seconds", staticmethod(lambda: 1))
    monkeypatch.setattr(QuotaService, "quota_lock_retry_interval_ms", staticmethod(lambda: 10))
    repo = _FakeQuotaRepo()
    repo.configs["ask_query"]["default_limit"] = 1
    repo.configs["ask_query"]["daily_limit"] = 1
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    original_store = service._store_internal_quota_grant
    first_store_entered = threading.Event()
    release_first_store = threading.Event()
    first_thread_id: dict[str, int | None] = {"value": None}
    results: dict[str, dict] = {}
    errors: dict[str, Exception] = {}

    def blocking_store(*, grant_id: str, payload: dict[str, object], ttl_seconds: int) -> None:
        current_thread_id = threading.get_ident()
        if first_thread_id["value"] is None:
            first_thread_id["value"] = current_thread_id
        if current_thread_id == first_thread_id["value"]:
            first_store_entered.set()
            assert release_first_store.wait(timeout=1.0)
        original_store(grant_id=grant_id, payload=payload, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(service, "_store_internal_quota_grant", blocking_store)

    def _run(name: str) -> None:
        try:
            results[name] = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
        except Exception as exc:
            errors[name] = exc

    first_worker = threading.Thread(target=_run, args=("first",), daemon=True)
    second_worker = threading.Thread(target=_run, args=("second",), daemon=True)
    first_worker.start()
    assert first_store_entered.wait(timeout=1.0)
    second_worker.start()
    time.sleep(0.05)
    release_first_store.set()
    first_worker.join(timeout=2.0)
    second_worker.join(timeout=2.0)

    assert errors == {}
    assert first_worker.is_alive() is False
    assert second_worker.is_alive() is False
    assert set(results.keys()) == {"first", "second"}
    successes = [item for item in results.values() if item["success"]]
    failures = [item for item in results.values() if not item["success"]]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0]["code"] == "QUOTA_EXCEEDED"


def test_service_create_internal_quota_grant_scopes_pending_reservations_by_period_key(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    repo.configs["ask_query"]["default_limit"] = 1
    repo.configs["ask_query"]["daily_limit"] = 1
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)
    state = {"period_key": "window-a"}

    monkeypatch.setattr(quota_service_module, "period_key", lambda period, period_days=None: str(state["period_key"]))

    first = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    state["period_key"] = "window-b"
    second = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    assert first["success"] is True
    assert second["success"] is True


def test_service_create_internal_quota_grant_does_not_wait_for_reservation_release(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(QuotaService, "quota_lock_wait_seconds", staticmethod(lambda: 1))
    monkeypatch.setattr(QuotaService, "quota_lock_retry_interval_ms", staticmethod(lambda: 10))
    repo = _FakeQuotaRepo()
    repo.configs["ask_query"]["default_limit"] = 1
    repo.configs["ask_query"]["daily_limit"] = 1
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    release_started = threading.Event()

    def _release_first() -> None:
        release_started.set()
        time.sleep(0.1)
        service.finalize_internal_quota_grant(grant_id=str(first["data"]["grant_id"]), success=False)

    releaser = threading.Thread(target=_release_first, daemon=True)
    releaser.start()
    assert release_started.wait(timeout=1.0)
    second = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    release_still_pending = releaser.is_alive()
    releaser.join(timeout=1.0)

    assert first["success"] is True
    assert second["success"] is False
    assert second["code"] == "QUOTA_EXCEEDED"
    assert release_still_pending is True


def test_service_internal_quota_grant_uses_persistent_fallback_without_redis(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_SERVICE_DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    first_service = QuotaService(repo=repo, redis_service=None)
    created = first_service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    second_service = QuotaService(repo=repo, redis_service=None)
    finalized = second_service.finalize_internal_quota_grant(grant_id=str(created["data"]["grant_id"]), success=True)

    assert created["success"] is True
    assert finalized["success"] is True
    assert finalized["data"]["counted"] is True
    assert len(repo.last_increment_call) == 1
    get_settings.cache_clear()


def test_service_finalize_internal_quota_grant_keeps_pending_when_increment_temporarily_fails(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)
    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    grant_id = str(created["data"]["grant_id"])
    original_increment = service.increment_quota
    state = {"count": 0}

    def flaky_increment(*, user_id: int, quota_type: str, anchored_window=None):
        if state["count"] == 0:
            state["count"] += 1
            return {"success": False, "error": "temporary_failure", "code": "DB_UNAVAILABLE"}
        return original_increment(user_id=user_id, quota_type=quota_type, anchored_window=anchored_window)

    service.increment_quota = flaky_increment  # type: ignore[assignment]
    first = service.finalize_internal_quota_grant(grant_id=grant_id, success=True)
    second = service.finalize_internal_quota_grant(grant_id=grant_id, success=True)

    assert first["success"] is False
    assert first["code"] == "DB_UNAVAILABLE"
    assert second["success"] is True
    assert second["data"]["counted"] is True
    assert len(repo.last_increment_call) == 1


def test_service_finalize_internal_quota_grant_counts_against_grant_period_key(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)
    state = {"period_key": "window-precheck"}

    monkeypatch.setattr(quota_service_module, "period_key", lambda period, period_days=None: str(state["period_key"]))

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    state["period_key"] = "window-finalize"
    finalized = service.finalize_internal_quota_grant(grant_id=str(created["data"]["grant_id"]), success=True)

    assert created["success"] is True
    assert finalized["success"] is True
    assert repo.last_increment_call == [(7, "ask_query", "window-precheck")]


def test_service_create_internal_quota_grant_persists_anchored_window_metadata(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    _set_quota_config(
        repo,
        quota_type="ask_query",
        quota_name="Ask Query",
        default_limit=3,
        period="custom_days",
        period_days=3,
    )
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    grant_id = str(created["data"]["grant_id"])
    pending = service._get_internal_quota_grant(grant_id=grant_id, finalized=False)

    assert created["success"] is True
    assert pending is not None
    assert pending["user_id"] == 7
    assert pending["quota_type"] == "ask_query"
    assert pending["period"] == "custom_days"
    assert pending["period_days"] == 3
    assert pending["period_key"] == period_key("custom_days", 3)
    assert pending["reserved_at"]


def test_service_finalize_internal_quota_grant_counts_two_parallel_successes(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    repo.configs["ask_query"]["default_limit"] = 2
    repo.configs["ask_query"]["daily_limit"] = 2
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    second = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    first_finalized = service.finalize_internal_quota_grant(grant_id=str(first["data"]["grant_id"]), success=True)
    second_finalized = service.finalize_internal_quota_grant(grant_id=str(second["data"]["grant_id"]), success=True)

    assert first["success"] is True
    assert second["success"] is True
    assert first_finalized["success"] is True
    assert second_finalized["success"] is True
    assert len(repo.last_increment_call) == 2


def test_service_finalize_internal_quota_grant_counts_only_successful_parallel_grants(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    repo.configs["ask_query"]["default_limit"] = 2
    repo.configs["ask_query"]["daily_limit"] = 2
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    second = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    first_finalized = service.finalize_internal_quota_grant(grant_id=str(first["data"]["grant_id"]), success=True)
    second_finalized = service.finalize_internal_quota_grant(grant_id=str(second["data"]["grant_id"]), success=False)

    assert first["success"] is True
    assert second["success"] is True
    assert first_finalized["success"] is True
    assert first_finalized["data"]["counted"] is True
    assert second_finalized["success"] is True
    assert second_finalized["data"]["counted"] is False
    assert len(repo.last_increment_call) == 1


def test_service_internal_quota_grant_falls_back_when_pending_redis_write_returns_false(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_SERVICE_DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(
        client=_SelectiveSetFailRedis(fail_fragments={"grant:pending"}),
        key_prefix="agentcode",
    )
    service = QuotaService(repo=repo, redis_service=redis_service)

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    finalized = service.finalize_internal_quota_grant(grant_id=str(created["data"]["grant_id"]), success=True)

    assert created["success"] is True
    assert finalized["success"] is True
    assert finalized["data"]["counted"] is True
    assert len(repo.last_increment_call) == 1
    get_settings.cache_clear()


def test_service_internal_quota_grant_falls_back_when_finalized_redis_write_returns_false(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_SERVICE_DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(
        client=_SelectiveSetFailRedis(fail_fragments={"grant:finalized"}),
        key_prefix="agentcode",
    )
    service = QuotaService(repo=repo, redis_service=redis_service)

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    grant_id = str(created["data"]["grant_id"])
    first = service.finalize_internal_quota_grant(grant_id=grant_id, success=True)
    second = service.finalize_internal_quota_grant(grant_id=grant_id, success=True)

    assert first["success"] is True
    assert first["data"]["counted"] is True
    assert second["success"] is True
    assert second["data"]["counted"] is True
    assert second["data"]["idempotent"] is True
    assert len(repo.last_increment_call) == 1
    get_settings.cache_clear()


def test_service_internal_quota_grant_expiry_releases_reservation_capacity(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_SERVICE_DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(QuotaService, "quota_grant_ttl_seconds", staticmethod(lambda: 1))
    repo = _FakeQuotaRepo()
    repo.configs["ask_query"]["default_limit"] = 1
    repo.configs["ask_query"]["daily_limit"] = 1
    redis_service = RedisService.from_prefix(client=_ExpiringFakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    time.sleep(1.4)
    retried = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    assert created["success"] is True
    assert retried["success"] is True
    get_settings.cache_clear()


def test_service_finalize_internal_quota_grant_returns_not_found_after_expired_reservation(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_SERVICE_DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(QuotaService, "quota_grant_ttl_seconds", staticmethod(lambda: 1))
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_ExpiringFakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    time.sleep(1.4)
    finalized = service.finalize_internal_quota_grant(grant_id=str(created["data"]["grant_id"]), success=True)

    assert created["success"] is True
    assert finalized["success"] is False
    assert finalized["code"] == "NOT_FOUND"
    assert repo.last_increment_call == []
    get_settings.cache_clear()


def test_service_default_internal_quota_grant_survives_long_running_task(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_SERVICE_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("QUOTA_GRANT_TTL_SECONDS", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})

    fake_now = {"value": 1_000.0}
    original_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: fake_now["value"])
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_ExpiringFakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    created = service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    fake_now["value"] += 180.0
    finalized = service.finalize_internal_quota_grant(grant_id=str(created["data"]["grant_id"]), success=True)

    assert created["success"] is True
    assert finalized["success"] is True
    assert finalized["data"]["counted"] is True
    assert repo.last_increment_call != []
    monkeypatch.setattr(time, "monotonic", original_monotonic)
    get_settings.cache_clear()


def test_service_cleanup_pending_internal_quota_grants_releases_reservation_capacity(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    repo = _FakeQuotaRepo()
    repo.configs["ask_query"]["default_limit"] = 1
    repo.configs["ask_query"]["daily_limit"] = 1
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    first_service = QuotaService(repo=repo, redis_service=redis_service)
    second_service = QuotaService(repo=repo, redis_service=redis_service)

    created = first_service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    overlapping = second_service.create_internal_quota_grant(user_id=7, quota_type="ask_query")
    cleaned = second_service.cleanup_pending_internal_quota_grants()
    retried = second_service.create_internal_quota_grant(user_id=7, quota_type="ask_query")

    assert created["success"] is True
    assert overlapping["success"] is False
    assert overlapping["code"] == "QUOTA_EXCEEDED"
    assert cleaned["success"] is True
    assert cleaned["data"]["cleaned"] == 1
    assert cleaned["data"]["failed"] == 0
    assert retried["success"] is True


def test_internal_quota_grant_routes_require_trusted_headers(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client:
        response = client.post("/internal/quota/grants/precheck", json={"user_id": 7, "quota_type": "ask_query"})

    assert response.status_code == 401
    assert response.json()["code"] == "INTERNAL_AUTH_MISSING"


def test_internal_quota_grant_routes_reject_non_gateway_callers(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)

    with TestClient(app) as client:
        response = client.post(
            "/internal/quota/grants/precheck",
            json={"user_id": 7, "quota_type": "ask_query"},
            headers=_internal_headers("fastQA"),
        )

    assert response.status_code == 403
    assert response.json()["code"] == "INTERNAL_SOURCE_SERVICE_FORBIDDEN"


def test_internal_quota_grant_precheck_and_finalize_contract(monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, INTERNAL_TOKEN)
    monkeypatch.setattr(
        quota_service_module.quota_service,
        "create_internal_quota_grant",
        lambda **kwargs: {"success": True, "data": {"grant_id": "grant-1", "noop": False, "quota_type": kwargs["quota_type"]}},
    )
    monkeypatch.setattr(
        quota_service_module.quota_service,
        "finalize_internal_quota_grant",
        lambda **kwargs: {"success": True, "data": {"grant_id": kwargs["grant_id"], "counted": True, "idempotent": False}},
    )

    with TestClient(app) as client:
        monkeypatch.setattr(quota_service_module, "quota_service", client.app.state.quota_service)
        monkeypatch.setattr(quota_api_module.quota_service_module, "quota_service", client.app.state.quota_service)
        monkeypatch.setattr(
            client.app.state.quota_service,
            "create_internal_quota_grant",
            lambda **kwargs: {"success": True, "data": {"grant_id": "grant-1", "noop": False, "quota_type": kwargs["quota_type"]}},
        )
        monkeypatch.setattr(
            client.app.state.quota_service,
            "finalize_internal_quota_grant",
            lambda **kwargs: {"success": True, "data": {"grant_id": kwargs["grant_id"], "counted": True, "idempotent": False}},
        )
        precheck = client.post(
            "/internal/quota/grants/precheck",
            json={"user_id": 7, "quota_type": "ask_query"},
            headers=_internal_headers(),
        )
        assert precheck.status_code == 200
        assert precheck.json()["data"]["grant_id"] == "grant-1"

        finalized = client.post(
            "/internal/quota/grants/grant-1/finalize",
            json={"success": True},
            headers=_internal_headers(),
        )

    assert finalized.status_code == 200
    payload = finalized.json()
    assert payload["data"]["grant_id"] == "grant-1"
    assert payload["data"]["counted"] is True


def test_precheck_quota_falls_back_to_db_named_lock_when_redis_unavailable(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    class _FakeCursor:
        def __init__(self) -> None:
            self._row = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query: str, params: tuple):
            calls.append((query.strip(), params))
            if "GET_LOCK" in query:
                self._row = {"acquired": 1}
            elif "RELEASE_LOCK" in query:
                self._row = {"released": 1}
            else:
                self._row = {}

        def fetchone(self):
            return dict(self._row)

    class _FakeConnection:
        @contextmanager
        def cursor(self):
            yield _FakeCursor()

        def ping(self, reconnect: bool = False):
            _ = reconnect
            return None

        def close(self):
            return None

    class _FakeDatabase(Database):
        def __init__(self) -> None:
            pass

        def connect(self):
            return _FakeConnection()

    monkeypatch.setattr(quota_service_module.quota_service, "_get_redis_service", lambda: None)
    monkeypatch.setattr(quota_deps, "_allow_unsafe_lock_fallback", lambda: False)
    monkeypatch.setattr(quota_deps, "_quota_database", lambda: _FakeDatabase())
    monkeypatch.setattr(quota_deps.auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(
        quota_service_module.quota_service,
        "check_quota",
        lambda **kwargs: {"success": True, "allowed": True, "config_active": True},
    )
    monkeypatch.setattr(quota_service_module.quota_service, "increment_quota", lambda **kwargs: {"success": True})

    grant = quota_deps.precheck_quota(user_id=7, quota_type="ask_query")
    finalize_quota(grant, result={"success": True})

    assert grant is not None
    assert any("GET_LOCK" in query for query, _params in calls)
    assert any("RELEASE_LOCK" in query for query, _params in calls)


def test_service_get_all_configs_uses_cache(monkeypatch):
    monkeypatch.setenv("QUOTA_ALL_LIST_CACHE_TTL_SECONDS", "120")
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    first = service.get_all_configs()
    second = service.get_all_configs()

    assert first["success"] is True
    assert second["success"] is True
    assert repo.list_all_calls == 1


def test_service_get_all_configs_serializes_datetime_values():
    class _DatetimeQuotaRepo(_FakeQuotaRepo):
        def list_all_configs(self):
            rows = super().list_all_configs()
            rows[0]["created_at"] = datetime(2026, 3, 17, 12, 34, 56)
            rows[0]["updated_at"] = date(2026, 3, 18)
            return rows

    service = QuotaService(repo=_DatetimeQuotaRepo())

    result = service.get_all_configs()

    assert result["success"] is True
    config = result["data"]["configs"][0]
    assert config["created_at"] == "2026-03-17T12:34:56"
    assert config["updated_at"] == "2026-03-18"


@pytest.mark.parametrize(
    ("quota_type", "expected_type"),
    [
        ("kb_qa", "ask_query"),
        ("thinking_qa", "ask_query"),
        ("pdf_qa", "file_qa"),
        ("tabular_qa", "file_qa"),
        ("hybrid_qa", "file_qa"),
        ("pdf_summary", "doc_assist"),
        ("text_translate", "doc_assist"),
        ("reference_preview", "doc_assist"),
        ("literature_content", "doc_assist"),
        ("extract_pdf_text", "doc_assist"),
        ("file_view", "file_view"),
    ],
)
def test_service_normalizes_legacy_aliases_for_check_and_increment(quota_type, expected_type):
    repo = _FakeQuotaRepo()
    repo.configs = {
        expected_type: {
            "quota_type": expected_type,
            "quota_name": expected_type,
            "period": "daily",
            "period_days": None,
            "default_limit": 10,
            "daily_limit": 10,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        }
    }
    service = QuotaService(repo=repo)

    checked = service.check_quota(user_id=3, quota_type=quota_type)
    incremented = service.increment_quota(user_id=3, quota_type=quota_type)

    assert checked["success"] is True
    assert checked["quota_type"] == expected_type
    assert incremented["success"] is True
    assert repo.last_increment_call[-1][1] == expected_type


def test_service_get_all_configs_returns_canonical_admin_view():
    repo = _FakeQuotaRepo()
    repo.configs = {
        "ask_query": {
            "quota_type": "ask_query",
            "quota_name": "Ask Query",
            "period": "daily",
            "period_days": None,
            "default_limit": 10,
            "daily_limit": 10,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
        "pdf_qa": {
            "quota_type": "pdf_qa",
            "quota_name": "PDF QA",
            "period": "daily",
            "period_days": None,
            "default_limit": 5,
            "daily_limit": 5,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
        "text_translate": {
            "quota_type": "text_translate",
            "quota_name": "Translate",
            "period": "weekly",
            "period_days": None,
            "default_limit": 8,
            "daily_limit": None,
            "weekly_limit": 8,
            "monthly_limit": None,
            "is_active": 1,
        },
        "file_view": {
            "quota_type": "file_view",
            "quota_name": "File View",
            "period": "daily",
            "period_days": None,
            "default_limit": 3,
            "daily_limit": 3,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
        "excel_upload": {
            "quota_type": "excel_upload",
            "quota_name": "Excel Upload",
            "period": "daily",
            "period_days": None,
            "default_limit": 99,
            "daily_limit": 99,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
    }
    service = QuotaService(repo=repo)

    result = service.get_all_configs()

    assert result["success"] is True
    assert {item["quota_type"] for item in result["data"]["configs"]} == {
        "ask_query",
        "file_qa",
        "file_view",
        "doc_assist",
    }
    assert all(item["quota_type"] != "excel_upload" for item in result["data"]["configs"])
    assert all(item["quota_type"] not in {"pdf_qa", "text_translate"} for item in result["data"]["configs"])


def test_service_reset_user_quota_resolves_canonical_type():
    repo = _FakeQuotaRepo()
    today_key = period_key("daily")
    repo.configs = {
        "pdf_summary": {
            "quota_type": "pdf_summary",
            "quota_name": "PDF Summary",
            "period": "daily",
            "period_days": None,
            "default_limit": 2,
            "daily_limit": 2,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
        "text_translate": {
            "quota_type": "text_translate",
            "quota_name": "Text Translate",
            "period": "daily",
            "period_days": None,
            "default_limit": 4,
            "daily_limit": 4,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
    }
    repo.usage[(7, "pdf_summary", today_key)] = 2
    repo.usage[(7, "text_translate", today_key)] = 1
    service = QuotaService(repo=repo)

    result = service.reset_user_quota(user_id=7, quota_type="doc_assist")

    assert result["success"] is True
    assert result["data"]["quota_type"] == "doc_assist"
    assert repo.usage[(7, "pdf_summary", today_key)] == 0
    assert repo.usage[(7, "text_translate", today_key)] == 0
    assert [call[1] for call in repo.last_reset_call] == ["pdf_summary", "text_translate"]


def test_service_updates_all_bucket_rows_when_canonical_missing():
    repo = _FakeQuotaRepo()
    repo.configs = {
        "pdf_summary": {
            "quota_type": "pdf_summary",
            "quota_name": "PDF Summary",
            "period": "daily",
            "period_days": None,
            "default_limit": 2,
            "daily_limit": 2,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 0,
        },
        "text_translate": {
            "quota_type": "text_translate",
            "quota_name": "Text Translate",
            "period": "daily",
            "period_days": None,
            "default_limit": 4,
            "daily_limit": 4,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
    }
    service = QuotaService(repo=repo)

    checked = service.check_quota(user_id=7, quota_type="doc_assist")
    updated = service.update_config(
        quota_type="doc_assist",
        default_limit=9,
        daily_limit=9,
        weekly_limit=None,
        monthly_limit=None,
        is_active=True,
        period="daily",
        multi_limits_provided=True,
    )

    assert checked["success"] is True
    assert checked["quota_type"] == "doc_assist"
    assert checked["config_active"] is True
    assert updated["success"] is True
    assert [call[0] for call in repo.update_calls] == ["pdf_summary", "text_translate"]
    assert repo.configs["pdf_summary"]["daily_limit"] == 9
    assert repo.configs["text_translate"]["daily_limit"] == 9


def test_service_get_all_configs_uses_bucket_order_not_repo_order():
    repo = _FakeQuotaRepo()
    repo.configs = {
        "text_translate": {
            "quota_type": "text_translate",
            "quota_name": "Text Translate",
            "period": "daily",
            "period_days": None,
            "default_limit": 4,
            "daily_limit": 4,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
        "pdf_summary": {
            "quota_type": "pdf_summary",
            "quota_name": "PDF Summary",
            "period": "weekly",
            "period_days": None,
            "default_limit": 2,
            "daily_limit": None,
            "weekly_limit": 2,
            "monthly_limit": None,
            "is_active": 1,
        },
    }
    service = QuotaService(repo=repo)

    listed = service.get_all_configs()
    checked = service.check_quota(user_id=7, quota_type="doc_assist")

    assert listed["success"] is True
    assert checked["success"] is True
    doc_assist = next(item for item in listed["data"]["configs"] if item["quota_type"] == "doc_assist")
    assert doc_assist["period"] == "weekly"
    assert doc_assist["weekly_limit"] == 2
    assert checked["period"] == "weekly"
    assert checked["limit"] == 2


def test_service_get_user_quotas_skips_bucket_when_canonical_row_is_inactive():
    repo = _FakeQuotaRepo()
    repo.configs = {
        "doc_assist": {
            "quota_type": "doc_assist",
            "quota_name": "Doc Assist",
            "period": "daily",
            "period_days": None,
            "default_limit": 5,
            "daily_limit": 5,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 0,
        },
        "text_translate": {
            "quota_type": "text_translate",
            "quota_name": "Text Translate",
            "period": "daily",
            "period_days": None,
            "default_limit": 4,
            "daily_limit": 4,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
    }
    service = QuotaService(repo=repo)

    result = service.get_user_quotas(user_id=7)
    checked = service.check_quota(user_id=7, quota_type="doc_assist")

    assert result["success"] is True
    assert all(item["quota_type"] != "doc_assist" for item in result["data"]["quotas"])
    assert checked["success"] is True
    assert checked["config_active"] is False


def test_service_get_user_quotas_returns_only_canonical_buckets_when_legacy_alias_rows_exist():
    repo = _FakeQuotaRepo()
    repo.configs = {
        "ask_query": {
            "quota_type": "ask_query",
            "quota_name": "Ask Query",
            "period": "daily",
            "period_days": None,
            "default_limit": 10,
            "daily_limit": 10,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
        "pdf_qa": {
            "quota_type": "pdf_qa",
            "quota_name": "PDF QA",
            "period": "daily",
            "period_days": None,
            "default_limit": 5,
            "daily_limit": 5,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
        "file_view": {
            "quota_type": "file_view",
            "quota_name": "File View",
            "period": "daily",
            "period_days": None,
            "default_limit": 3,
            "daily_limit": 3,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
        "text_translate": {
            "quota_type": "text_translate",
            "quota_name": "Text Translate",
            "period": "daily",
            "period_days": None,
            "default_limit": 4,
            "daily_limit": 4,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
    }
    service = QuotaService(repo=repo)

    result = service.get_user_quotas(user_id=7)

    assert result["success"] is True
    quota_types = {item["quota_type"] for item in result["data"]["quotas"]}
    assert quota_types == {"ask_query", "file_qa", "file_view", "doc_assist"}
    assert "pdf_qa" not in quota_types
    assert "text_translate" not in quota_types


def test_service_get_all_configs_prefers_inactive_canonical_row_over_active_legacy():
    repo = _FakeQuotaRepo()
    repo.configs = {
        "doc_assist": {
            "quota_type": "doc_assist",
            "quota_name": "Doc Assist",
            "period": "daily",
            "period_days": None,
            "default_limit": 5,
            "daily_limit": 5,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 0,
        },
        "text_translate": {
            "quota_type": "text_translate",
            "quota_name": "Text Translate",
            "period": "weekly",
            "period_days": None,
            "default_limit": 4,
            "daily_limit": None,
            "weekly_limit": 4,
            "monthly_limit": None,
            "is_active": 1,
        },
    }
    service = QuotaService(repo=repo)

    result = service.get_all_configs()

    assert result["success"] is True
    doc_assist = next(item for item in result["data"]["configs"] if item["quota_type"] == "doc_assist")
    assert doc_assist["is_active"] == 0
    assert doc_assist["period"] == "daily"


def test_service_create_inactive_blank_limits_preserves_empty_period_limits():
    repo = _FakeQuotaRepo()
    repo.configs = {}
    service = QuotaService(repo=repo)

    result = service.create_config(
        quota_type="doc_assist",
        quota_name="文档辅助",
        default_limit=0,
        daily_limit=None,
        weekly_limit=None,
        monthly_limit=None,
        is_active=False,
        period="daily",
        multi_limits_provided=False,
    )

    assert result["success"] is True
    assert repo.last_create_call is not None
    assert repo.last_create_call["period"] == "none"
    assert repo.last_create_call["daily_limit"] is None
    assert repo.last_create_call["weekly_limit"] is None
    assert repo.last_create_call["monthly_limit"] is None


def test_service_update_inactive_blank_limits_preserves_empty_period_limits():
    repo = _FakeQuotaRepo()
    repo.configs = {
        "doc_assist": {
            "quota_type": "doc_assist",
            "quota_name": "Doc Assist",
            "period": "daily",
            "period_days": None,
            "default_limit": 5,
            "daily_limit": 5,
            "weekly_limit": None,
            "monthly_limit": None,
            "is_active": 1,
        },
    }
    service = QuotaService(repo=repo)

    result = service.update_config(
        quota_type="doc_assist",
        default_limit=0,
        daily_limit=None,
        weekly_limit=None,
        monthly_limit=None,
        is_active=False,
        period="daily",
        multi_limits_provided=False,
    )

    assert result["success"] is True
    assert [call[0] for call in repo.update_calls] == ["doc_assist"]
    assert repo.configs["doc_assist"]["period"] == "none"
    assert repo.configs["doc_assist"]["daily_limit"] is None
    assert repo.configs["doc_assist"]["weekly_limit"] is None
    assert repo.configs["doc_assist"]["monthly_limit"] is None


def test_service_admin_create_rejects_legacy_quota_type():
    service = QuotaService(repo=_FakeQuotaRepo())

    result = service.create_config(
        quota_type="text_translate",
        quota_name="Text Translate",
        default_limit=10,
        daily_limit=10,
        weekly_limit=None,
        monthly_limit=None,
        is_active=True,
        period="daily",
        multi_limits_provided=True,
    )

    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"


def test_service_admin_update_rejects_legacy_quota_type():
    service = QuotaService(repo=_FakeQuotaRepo())

    result = service.update_config(
        quota_type="ask_stream",
        default_limit=10,
        daily_limit=10,
        weekly_limit=None,
        monthly_limit=None,
        is_active=True,
        period="daily",
        multi_limits_provided=True,
    )

    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"


def test_service_admin_reset_rejects_legacy_quota_type():
    service = QuotaService(repo=_FakeQuotaRepo())

    result = service.reset_user_quota(user_id=7, quota_type="pdf_summary")

    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"


def test_service_create_config_invalidates_metadata_cache(monkeypatch):
    monkeypatch.setenv("QUOTA_CONFIG_CACHE_TTL_SECONDS", "120")
    monkeypatch.setenv("QUOTA_ACTIVE_LIST_CACHE_TTL_SECONDS", "120")
    monkeypatch.setenv("QUOTA_ALL_LIST_CACHE_TTL_SECONDS", "120")
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    before = service.get_all_configs()
    created = service.create_config(
        quota_type="file_qa",
        quota_name="文件问答",
        default_limit=100,
        daily_limit=100,
        weekly_limit=300,
        monthly_limit=1000,
        is_active=True,
        period="daily",
        multi_limits_provided=True,
    )
    after = service.get_all_configs()

    assert before["success"] is True
    assert created["success"] is True
    assert after["success"] is True
    assert any(item["quota_type"] == "file_qa" for item in after["data"]["configs"])
    assert repo.list_all_calls == 2


def test_service_multi_period_increment_updates_all_windows():
    repo = _FakeQuotaRepo()
    service = QuotaService(repo=repo)
    result = service.increment_quota(user_id=3, quota_type="pdf_summary")
    assert result["success"] is True
    assert len(result["data"]["period_usage"]) == 3
    assert len(repo.last_increment_call) == 3


def test_service_inactive_increment_skips():
    service = QuotaService(repo=_FakeQuotaRepo())
    result = service.increment_quota(user_id=1, quota_type="excel_upload")
    assert result["success"] is True
    assert result["skipped"] is True
    assert result["reason"] == "quota_inactive"


def test_service_create_config_supports_multi_period():
    repo = _FakeQuotaRepo()
    service = QuotaService(repo=repo)
    result = service.create_config(
        quota_type="file_qa",
        quota_name="文件问答",
        default_limit=100,
        daily_limit=100,
        weekly_limit=300,
        monthly_limit=1000,
        is_active=True,
        period="daily",
        multi_limits_provided=True,
    )
    assert result["success"] is True
    assert repo.last_create_call is not None
    assert repo.last_create_call["monthly_limit"] == 1000


def test_service_update_unchanged_reports_unchanged():
    repo = _FakeQuotaRepo()
    service = QuotaService(repo=repo)
    result = service.update_config(quota_type="ask_query", default_limit=10, is_active=True, period="daily")
    assert result["success"] is True
    assert result["message"] == "quota_config_unchanged"


def test_get_user_quotas_exposes_partial_failure():
    service = QuotaService(repo=_FakeQuotaRepo())
    original = service.check_quota

    def fake_check(*, user_id: int, quota_type: str):
        if quota_type == "doc_assist":
            return {"success": False, "error": "temporary_failure", "code": "DB_UNAVAILABLE"}
        return original(user_id=user_id, quota_type=quota_type)

    service.check_quota = fake_check  # type: ignore[assignment]
    result = service.get_user_quotas(user_id=1)
    assert result["success"] is True
    assert result["data"]["partial_failure"] is True
    assert result["data"]["warnings"][0]["quota_type"] == "doc_assist"


def test_get_my_quotas_contract(monkeypatch):
    monkeypatch.setattr(quota_service_module.quota_service, "get_user_quotas", lambda **kwargs: {"success": True, "data": {"quotas": [{"quota_type": "ask", "remaining": 3}]}})
    response = quota_api_module.get_my_quotas(AuthContext(user_id=7, role="user", username="alice"))
    assert response.status_code == 200
    assert _decode(response)["data"]["quotas"][0]["quota_type"] == "ask"


def test_get_quota_configs_contract(monkeypatch):
    monkeypatch.setattr(
        quota_service_module.quota_service,
        "get_all_configs",
        lambda: {"success": True, "data": {"configs": [{"quota_type": "ask_query", "default_limit": 10, "is_active": True}]}},
    )
    response = quota_api_module.get_quota_configs(AuthContext(user_id=1, role="admin", username="admin"))
    assert response.status_code == 200
    payload = _decode(response)
    assert payload["data"]["configs"][0]["quota_type"] == "ask_query"


def test_get_user_quotas_contract(monkeypatch):
    monkeypatch.setattr(
        quota_service_module.quota_service,
        "get_user_quotas",
        lambda **kwargs: {
            "success": True,
            "data": {"quotas": [{"quota_type": "doc_assist", "remaining": 1}]},
        },
    )
    response = quota_api_module.get_user_quotas(7, AuthContext(user_id=1, role="admin", username="admin"))
    assert response.status_code == 200
    payload = _decode(response)
    assert payload["data"]["quotas"][0]["quota_type"] == "doc_assist"


def test_create_quota_config_contract(monkeypatch):
    monkeypatch.setattr(quota_service_module.quota_service, "create_config", lambda **kwargs: {"success": True, "message": "quota_config_created"})
    response = quota_api_module.create_quota_config(
        CreateQuotaConfigRequest(
            quota_type="doc_assist",
            quota_name="文档辅助",
            default_limit=10,
            daily_limit=10,
            weekly_limit=30,
            monthly_limit=90,
            is_active=True,
            period="daily",
        ),
        AuthContext(user_id=1, role="admin", username="admin"),
    )
    assert response.status_code == 201
    assert _decode(response)["message"] == "quota_config_created"


def test_update_quota_config_contract(monkeypatch):
    monkeypatch.setattr(quota_service_module.quota_service, "update_config", lambda **kwargs: {"success": True, "message": "quota_config_updated"})
    response = quota_api_module.update_quota_config(
        "ask_query",
        UpdateQuotaConfigRequest(default_limit=10, daily_limit=10, weekly_limit=50, monthly_limit=None, is_active=True, period="weekly", period_days=None),
        AuthContext(user_id=1, role="admin", username="admin"),
    )
    assert response.status_code == 200
    assert _decode(response)["message"] == "quota_config_updated"


def test_reset_user_quota_contract(monkeypatch):
    monkeypatch.setattr(
        quota_service_module.quota_service,
        "reset_user_quota",
        lambda **kwargs: {"success": True, "data": {"quota_type": "doc_assist", "remaining": 3}},
    )
    response = quota_api_module.reset_user_quota(7, "doc_assist", AuthContext(user_id=1, role="admin", username="admin"))
    assert response.status_code == 200
    payload = _decode(response)
    assert payload["data"]["quota_type"] == "doc_assist"


def test_quota_dependency_exceeded(monkeypatch):
    monkeypatch.setattr(quota_deps.auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(quota_deps.quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": True, "allowed": False, "quota_type": "ask_stream", "remaining": 0})
    dependency = quota_deps.require_quota("ask_stream")
    with pytest.raises(QuotaExceededError) as exc_info:
        dependency(AuthContext(user_id=7, role="user", username="alice"))
    assert exc_info.value.code == "QUOTA_EXCEEDED"


def test_quota_dependency_preserves_check_failure_payload(monkeypatch):
    monkeypatch.setattr(quota_deps.auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(quota_deps.quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": False, "error": "quota_check_failed", "code": "QUOTA_CHECK_ERROR"})
    dependency = quota_deps.require_quota("ask_stream")
    with pytest.raises(QuotaCheckFailedError) as exc_info:
        dependency(AuthContext(user_id=7, role="user", username="alice"))
    assert exc_info.value.code == "QUOTA_CHECK_ERROR"
    assert exc_info.value.extra_payload["error"] == "quota_check_failed"


def test_quota_dependency_strict_missing_config(monkeypatch):
    monkeypatch.setattr(quota_deps.auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
    monkeypatch.setattr(quota_deps.quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": True, "allowed": True, "config_missing": True, "config_active": False})
    dependency = quota_deps.require_quota("pdf_summary", strict_config=True)
    with pytest.raises(QuotaConfigMissingError) as exc_info:
        dependency(AuthContext(user_id=7, role="user", username="alice"))
    assert exc_info.value.code == "QUOTA_CONFIG_MISSING"


def test_quota_dependency_admin_bypass(monkeypatch):
    monkeypatch.setattr(quota_deps.auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 1})
    called = {"check": 0}
    monkeypatch.setattr(quota_deps.quota_service_module.quota_service, "check_quota", lambda **kwargs: called.__setitem__("check", called["check"] + 1) or {"success": True, "allowed": True})
    dependency = quota_deps.require_quota("ask_stream")
    assert dependency(AuthContext(user_id=7, role="admin", username="alice")) is None
    assert called["check"] == 0


def test_quota_dependency_auth_lookup_failure_returns_db_unavailable(monkeypatch):
    def raise_db_error(user_id: int):
        raise DatabaseUnavailableError("db_unavailable")

    monkeypatch.setattr(quota_deps.auth_service_module.auth_service, "get_user_by_id", raise_db_error)
    dependency = quota_deps.require_quota("ask_stream")
    with pytest.raises(QuotaCheckFailedError) as exc_info:
        dependency(AuthContext(user_id=7, role="user", username="alice"))
    assert exc_info.value.code == "DB_UNAVAILABLE"


def test_finalize_quota_skips_error_payload(monkeypatch):
    captured = {"count": 0}
    monkeypatch.setattr(quota_deps.quota_service_module.quota_service, "increment_quota", lambda **kwargs: captured.__setitem__("count", captured["count"] + 1) or {"success": True})
    grant = quota_deps.QuotaGrant(user_id=7, quota_type="ask_query", checked={"config_active": True})
    response = JSONResponse(status_code=200, content={"error": "nope"})
    assert finalize_quota(grant, result=response) is None
    assert captured["count"] == 0


def test_finalize_quota_counts_success_response(monkeypatch):
    captured = {"count": 0}
    monkeypatch.setattr(quota_deps.quota_service_module.quota_service, "increment_quota", lambda **kwargs: captured.__setitem__("count", captured["count"] + 1) or {"success": True})
    grant = quota_deps.QuotaGrant(user_id=7, quota_type="ask_query", checked={"config_active": True})
    response = Response(status_code=200)
    finalize_quota(grant, result=response)
    assert captured["count"] == 1


def test_quota_dependency_acquires_and_finalize_releases_redis_lease(monkeypatch):
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    monkeypatch.setattr(
        quota_deps.quota_service_module.quota_service,
        "_get_redis_service",
        lambda: redis_service,
    )
    monkeypatch.setattr(
        quota_deps.auth_service_module.auth_service,
        "get_user_by_id",
        lambda user_id: {"id": user_id, "user_type": 3},
    )
    monkeypatch.setattr(
        quota_deps.quota_service_module.quota_service,
        "check_quota",
        lambda **kwargs: {"success": True, "allowed": True, "config_active": True},
    )
    monkeypatch.setattr(
        quota_deps.quota_service_module.quota_service,
        "increment_quota",
        lambda **kwargs: {"success": True},
    )

    dependency = quota_deps.require_quota("ask_query")
    grant = dependency(AuthContext(user_id=7, role="user", username="alice"))

    assert grant is not None
    assert grant.lease is not None
    assert redis_service.client.get(grant.lease.key) is not None

    finalize_quota(grant, result=Response(status_code=200))

    assert redis_service.client.get(grant.lease.key) is None


def test_protected_quota_route_returns_503_when_auth_repo_unavailable(monkeypatch):
    class FailingRepo:
        def get_by_id(self, user_id):
            raise DatabaseUnavailableError("db_unavailable")

    failing_service = AuthService(repo=FailingRepo(), token_service=TokenService())
    token = failing_service._tokens.issue_access_token(user_id=7, role="user")
    monkeypatch.setattr(auth_service_module, "auth_service", failing_service)

    with TestClient(app) as client:
        response = client.get("/api/v1/quota/my", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert response.json()["code"] == "DB_UNAVAILABLE"


def test_quota_runtime_service_is_bound_to_app_state():
    with TestClient(app) as client:
        assert client.app.state.quota_service is quota_service_module.quota_service
