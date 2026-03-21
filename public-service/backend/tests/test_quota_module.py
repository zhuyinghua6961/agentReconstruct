import json
from contextlib import contextmanager
from datetime import date, datetime

import pytest
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

from app.core.deps import AuthContext
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


def _decode(response):
    return json.loads(response.body.decode("utf-8"))


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
        self.last_increment_call: list[tuple[int, str, str]] = []
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


def test_service_create_config_invalidates_metadata_cache(monkeypatch):
    monkeypatch.setenv("QUOTA_CONFIG_CACHE_TTL_SECONDS", "120")
    monkeypatch.setenv("QUOTA_ACTIVE_LIST_CACHE_TTL_SECONDS", "120")
    monkeypatch.setenv("QUOTA_ALL_LIST_CACHE_TTL_SECONDS", "120")
    repo = _FakeQuotaRepo()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    service = QuotaService(repo=repo, redis_service=redis_service)

    before = service.get_all_configs()
    created = service.create_config(
        quota_type="text_translate",
        quota_name="Text Translate",
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
    assert any(item["quota_type"] == "text_translate" for item in after["data"]["configs"])
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
        quota_type="text_translate",
        quota_name="Text Translate",
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
        if quota_type == "pdf_summary":
            return {"success": False, "error": "temporary_failure", "code": "DB_UNAVAILABLE"}
        return original(user_id=user_id, quota_type=quota_type)

    service.check_quota = fake_check  # type: ignore[assignment]
    result = service.get_user_quotas(user_id=1)
    assert result["success"] is True
    assert result["data"]["partial_failure"] is True
    assert result["data"]["warnings"][0]["quota_type"] == "pdf_summary"


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
            "data": {"quotas": [{"quota_type": "pdf_summary", "remaining": 1}]},
        },
    )
    response = quota_api_module.get_user_quotas(7, AuthContext(user_id=1, role="admin", username="admin"))
    assert response.status_code == 200
    payload = _decode(response)
    assert payload["data"]["quotas"][0]["quota_type"] == "pdf_summary"


def test_create_quota_config_contract(monkeypatch):
    monkeypatch.setattr(quota_service_module.quota_service, "create_config", lambda **kwargs: {"success": True, "message": "quota_config_created"})
    response = quota_api_module.create_quota_config(
        CreateQuotaConfigRequest(
            quota_type="text_translate",
            quota_name="Text Translate",
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
        "ask_stream",
        UpdateQuotaConfigRequest(default_limit=10, daily_limit=10, weekly_limit=50, monthly_limit=None, is_active=True, period="weekly", period_days=None),
        AuthContext(user_id=1, role="admin", username="admin"),
    )
    assert response.status_code == 200
    assert _decode(response)["message"] == "quota_config_updated"


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
