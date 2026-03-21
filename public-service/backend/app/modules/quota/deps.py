from __future__ import annotations

from collections.abc import Callable
import os
import time
from typing import Any, Protocol

from fastapi import Depends
from fastapi.responses import JSONResponse, Response

from app.core.db import Database
from app.core.db_locks import MySQLNamedLockLease
from app.core.deps import AuthContext
from app.core.errors import AppError
from app.integrations.redis import RedisLockManager, RedisRenewingLock
from app.modules.auth.deps import require_auth_context
from app.modules.auth import service as auth_service_module
from app.modules.quota import service as quota_service_module
from app.modules.quota.service import QuotaGrant


class LeaseProtocol(Protocol):
    def ensure_healthy(self) -> None: ...
    def release(self) -> bool: ...


class QuotaExceededError(AppError):
    def __init__(self, checked: dict):
        super().__init__(message="quota_exceeded", code="QUOTA_EXCEEDED", status_code=429)
        self.extra_payload = {"data": checked}


class QuotaConfigMissingError(AppError):
    def __init__(self, checked: dict):
        super().__init__(message="quota_config_missing", code="QUOTA_CONFIG_MISSING", status_code=503)
        self.extra_payload = {"data": checked}


class QuotaCheckFailedError(AppError):
    def __init__(self, checked: dict):
        payload = dict(checked or {})
        super().__init__(
            message=str(payload.get("error") or "quota_check_failed"),
            code=str(payload.get("code") or "DB_UNAVAILABLE"),
            status_code=503,
        )
        self.extra_payload = payload


def _is_quota_exempt(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    try:
        return int(user.get("user_type") or 0) in {1, 2}
    except Exception:
        return False


def _quota_lock_key(*, user_id: int, quota_type: str) -> str:
    redis_service = quota_service_module.quota_service._get_redis_service()
    if redis_service is not None:
        return redis_service.key_factory.lock("quota", int(user_id), str(quota_type or "").strip().lower())
    return f"quota:{int(user_id)}:{str(quota_type or '').strip().lower()}"


def _allow_unsafe_lock_fallback() -> bool:
    return str(os.getenv("APP_ENV", "development") or "development").strip().lower() == "test"


def _quota_database() -> Database | None:
    repo = getattr(quota_service_module.quota_service, "_repo", None)
    database = getattr(repo, "_db", None)
    return database if isinstance(database, Database) else None


def _acquire_quota_lease(*, user_id: int, quota_type: str) -> LeaseProtocol | None:
    redis_service = quota_service_module.quota_service._get_redis_service()
    lock_manager = RedisLockManager(getattr(redis_service, "client", None))
    key = _quota_lock_key(user_id=user_id, quota_type=quota_type)
    if lock_manager.available:
        deadline = time.monotonic() + float(quota_service_module.quota_service.quota_lock_wait_seconds())
        while True:
            handle = lock_manager.acquire(
                key,
                ttl_seconds=quota_service_module.quota_service.quota_lock_ttl_seconds(),
            )
            if handle is not None:
                return RedisRenewingLock(
                    lock_manager=lock_manager,
                    handle=handle,
                    label="quota_lock",
                ).start()
            if time.monotonic() >= deadline:
                raise QuotaCheckFailedError(
                    {"success": False, "error": "quota_lock_timeout", "code": "QUOTA_LOCK_TIMEOUT"}
                )
            time.sleep(quota_service_module.quota_service.quota_lock_retry_interval_ms() / 1000.0)
    if _allow_unsafe_lock_fallback():
        return None
    database = _quota_database()
    if database is None:
        raise QuotaCheckFailedError(
            {"success": False, "error": "quota_lock_backend_unavailable", "code": "QUOTA_LOCK_UNAVAILABLE"}
        )
    try:
        lease = MySQLNamedLockLease.acquire(
            database=database,
            key=_quota_lock_key(user_id=user_id, quota_type=quota_type),
            wait_seconds=quota_service_module.quota_service.quota_lock_wait_seconds(),
            label="quota_lock",
        )
    except Exception as exc:
        raise QuotaCheckFailedError({"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}) from exc
    if lease is None:
        raise QuotaCheckFailedError(
            {"success": False, "error": "quota_lock_timeout", "code": "QUOTA_LOCK_TIMEOUT"}
        )
    return lease


def _release_quota_lease(lease: LeaseProtocol | None) -> None:
    if lease is None:
        return
    lease.release()


def precheck_quota(*, user_id: int, quota_type: str, strict_config: bool = False) -> QuotaGrant | None:
    lease = _acquire_quota_lease(user_id=user_id, quota_type=quota_type)
    try:
        user = auth_service_module.auth_service.get_user_by_id(user_id)
        if _is_quota_exempt(user):
            _release_quota_lease(lease)
            return None
    except Exception as exc:
        _release_quota_lease(lease)
        if exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}:
            raise QuotaCheckFailedError({"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}) from exc
        raise
    checked = quota_service_module.quota_service.check_quota(user_id=user_id, quota_type=quota_type)
    if not checked.get("success"):
        _release_quota_lease(lease)
        raise QuotaCheckFailedError(checked)
    if bool(checked.get("config_missing")) and bool(strict_config):
        _release_quota_lease(lease)
        raise QuotaConfigMissingError(checked)
    if not checked.get("allowed"):
        _release_quota_lease(lease)
        raise QuotaExceededError(checked)
    return QuotaGrant(user_id=int(user_id), quota_type=str(quota_type), checked=dict(checked), lease=lease)


def _should_count_json_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    if payload.get("success") is False:
        return False
    if payload.get("error"):
        return False
    return True


def should_count_result(*, result: Any, status_code: int | None = None) -> bool:
    if isinstance(result, Response):
        if int(result.status_code or 500) >= 400:
            return False
        if isinstance(result, JSONResponse):
            try:
                import json
                payload = json.loads(result.body.decode("utf-8"))
            except Exception:
                return True
            return _should_count_json_payload(payload)
        return True
    if status_code is not None and int(status_code) >= 400:
        return False
    return _should_count_json_payload(result)


def finalize_quota(grant: QuotaGrant | None, *, result: Any, status_code: int | None = None) -> dict[str, Any] | None:
    if grant is None:
        return None
    try:
        lease = getattr(grant, "lease", None)
        if lease is not None:
            try:
                lease.ensure_healthy()
            except Exception as exc:
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
        if not bool((grant.checked or {}).get("config_active", True)):
            return None
        if not should_count_result(result=result, status_code=status_code):
            return None
        incremented = quota_service_module.quota_service.increment_quota(user_id=grant.user_id, quota_type=grant.quota_type)
        return incremented
    finally:
        _release_quota_lease(getattr(grant, "lease", None))


def require_quota(quota_type: str, *, strict_config: bool = False) -> Callable:
    def _dependency(context: AuthContext = Depends(require_auth_context)) -> QuotaGrant | None:
        return precheck_quota(user_id=context.user_id, quota_type=quota_type, strict_config=strict_config)
    return _dependency
