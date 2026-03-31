from __future__ import annotations

import fnmatch
import re
import os
import time
import json
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.core.config import get_settings
from app.core.errors import AppError
from app.integrations.redis import RedisLockHandle, RedisLockManager, RedisRenewingLock, RedisService, build_redis_bindings
from app.modules.auth import service as auth_service_module
from app.modules.quota.cache import (
    MISSING_QUOTA_CONFIG_PAYLOAD,
    cache_quota_active_configs,
    cache_quota_all_configs,
    cache_quota_config,
    cache_quota_override,
    get_cached_quota_active_configs,
    get_cached_quota_all_configs,
    get_cached_quota_config,
    get_cached_quota_override,
    invalidate_quota_config_cache,
    invalidate_quota_config_lists_cache,
)

class DatabaseUnavailableError(Exception):
    """Raised when quota persistence has not been wired yet."""


class QuotaRepositoryProtocol(Protocol):
    def get_quota_config(self, quota_type: str) -> dict[str, Any] | None: ...
    def list_active_configs(self) -> list[dict[str, Any]]: ...
    def list_all_configs(self) -> list[dict[str, Any]]: ...
    def get_user_override_limit(self, *, user_id: int, quota_type: str) -> int | None: ...
    def get_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int: ...
    def increment_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int: ...
    def create_quota_config(
        self,
        *,
        quota_type: str,
        quota_name: str,
        period: str,
        period_days: int | None,
        default_limit: int,
        daily_limit: int | None,
        weekly_limit: int | None,
        monthly_limit: int | None,
        is_active: bool,
    ) -> int: ...
    def update_quota_config(
        self,
        *,
        quota_type: str,
        default_limit: int,
        daily_limit: int | None,
        weekly_limit: int | None,
        monthly_limit: int | None,
        is_active: bool,
        period: str,
        period_days: int | None,
    ) -> int: ...
    def reset_user_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int: ...


class UnavailableQuotaRepository:
    def _raise(self):
        raise DatabaseUnavailableError("quota_repository_unavailable")

    def get_quota_config(self, quota_type: str) -> dict[str, Any] | None:
        self._raise()

    def list_active_configs(self) -> list[dict[str, Any]]:
        self._raise()

    def list_all_configs(self) -> list[dict[str, Any]]:
        self._raise()

    def get_user_override_limit(self, *, user_id: int, quota_type: str) -> int | None:
        self._raise()

    def get_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int:
        self._raise()

    def increment_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int:
        self._raise()

    def create_quota_config(self, **kwargs) -> int:
        self._raise()

    def update_quota_config(self, **kwargs) -> int:
        self._raise()

    def reset_user_usage(self, *, user_id: int, quota_type: str, period_key: str) -> int:
        self._raise()


def _is_db_unavailable_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}


ALLOWED_PERIODS = {"daily", "weekly", "monthly", "custom_days", "none"}
MULTI_PERIODS = ("daily", "weekly", "monthly")
QUOTA_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CANONICAL_QUOTA_TYPES = ("ask_query", "file_qa", "file_view", "doc_assist")
CANONICAL_QUOTA_LABELS = {
    "ask_query": "普通问答",
    "file_qa": "文件问答",
    "file_view": "查看原文",
    "doc_assist": "文档辅助",
}
QUOTA_TYPE_BUCKETS = {
    "ask_query": ("ask_query", "kb_qa", "thinking_qa"),
    "file_qa": ("file_qa", "pdf_qa", "tabular_qa", "hybrid_qa"),
    "file_view": ("file_view",),
    "doc_assist": (
        "doc_assist",
        "pdf_summary",
        "text_translate",
        "reference_preview",
        "literature_content",
        "extract_pdf_text",
    ),
}
QUOTA_TYPE_ALIASES = {
    alias: canonical
    for canonical, aliases in QUOTA_TYPE_BUCKETS.items()
    for alias in aliases
}
QUOTA_TYPE_SORT_ORDER = {quota_type: index for index, quota_type in enumerate(CANONICAL_QUOTA_TYPES)}


def to_valid_period_days(value: Any, *, default: int = 7) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(1, min(365, parsed))


def normalize_period(period: str) -> str:
    value = str(period or "").strip().lower()
    if value in ALLOWED_PERIODS:
        return value
    return "daily"


def custom_period_window(period_days: int) -> tuple[date, date]:
    days = to_valid_period_days(period_days, default=7)
    today = date.today()
    anchor = date(1970, 1, 1)
    delta_days = (today - anchor).days
    window_start = anchor + timedelta(days=(delta_days // days) * days)
    window_end = window_start + timedelta(days=days)
    return window_start, window_end


def period_key(period: str, period_days: int | None = None) -> str:
    now = datetime.now()
    normalized = normalize_period(period)
    if normalized == "monthly":
        return now.strftime("%Y-%m")
    if normalized == "weekly":
        iso_year, iso_week, _ = now.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if normalized == "none":
        return "unlimited"
    if normalized == "custom_days":
        start, _ = custom_period_window(to_valid_period_days(period_days, default=7))
        return f"{start:%Y-%m-%d}:{to_valid_period_days(period_days, default=7)}d"
    return now.strftime("%Y-%m-%d")


def period_reset_hint(period: str, period_days: int | None = None) -> str:
    normalized = normalize_period(period)
    if normalized == "monthly":
        return "next_month_start"
    if normalized == "weekly":
        return "next_week_start"
    if normalized == "none":
        return "never"
    if normalized == "custom_days":
        _, window_end = custom_period_window(to_valid_period_days(period_days, default=7))
        return f"next_custom_window_start:{window_end:%Y-%m-%d}"
    return "next_day_start"


def normalize_quota_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return QUOTA_TYPE_ALIASES.get(normalized, normalized)


def normalize_admin_quota_type(value: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in CANONICAL_QUOTA_TYPES:
        return normalized
    return None


def to_optional_non_negative_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    if parsed < 0:
        return None
    return parsed


@dataclass(frozen=True)
class QuotaGrant:
    user_id: int
    quota_type: str
    checked: dict[str, Any]
    lease: RedisRenewingLock | None = None


class _InternalQuotaGrantRenewer:
    def __init__(
        self,
        *,
        service: "QuotaService",
        grant_id: str,
        lease: dict[str, Any],
        ttl_seconds: int,
        refresh_interval_seconds: float | None = None,
    ) -> None:
        self._service = service
        self._grant_id = str(grant_id or "").strip()
        self._lease = dict(lease or {})
        self._ttl_seconds = max(1, int(ttl_seconds))
        default_interval = max(0.5, float(self._ttl_seconds) / 3.0)
        self._refresh_interval_seconds = max(0.5, float(refresh_interval_seconds or default_interval))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lost = False

    @property
    def lost(self) -> bool:
        return bool(self._lost)

    def start(self) -> "_InternalQuotaGrantRenewer":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._run,
            name=f"quota-grant-renew-{self._grant_id[:8] or 'unknown'}",
            daemon=True,
        )
        self._thread.start()
        return self

    def ensure_healthy(self) -> None:
        if self._lost:
            raise AppError(message="quota_grant_lease_lost", code="DB_UNAVAILABLE", status_code=503)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self._refresh_interval_seconds + 1.0))

    def _run(self) -> None:
        while not self._stop_event.wait(self._refresh_interval_seconds):
            if not self._service._internal_quota_grant_exists(grant_id=self._grant_id):
                return
            if self._service._refresh_internal_quota_grant_state(
                grant_id=self._grant_id,
                lease=self._lease,
                ttl_seconds=self._ttl_seconds,
            ):
                continue
            self._lost = True
            return


class QuotaService:
    def __init__(self, *, repo: QuotaRepositoryProtocol | None = None, redis_service: RedisService | None = None):
        self._repo = repo or UnavailableQuotaRepository()
        self._redis_service = redis_service
        self._redis_service_resolved = redis_service is not None
        self._internal_quota_grant_renewers: dict[str, _InternalQuotaGrantRenewer] = {}
        self._internal_quota_grant_renewers_lock = threading.Lock()

    @staticmethod
    def _json_safe_value(value: Any) -> Any:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value

    @classmethod
    def _serialize_config_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {str(key): cls._json_safe_value(value) for key, value in dict(row or {}).items()}

    def _get_redis_service(self) -> RedisService | None:
        if self._redis_service_resolved:
            return self._redis_service
        self._redis_service_resolved = True
        try:
            settings = get_settings()
            bindings = build_redis_bindings(settings=settings)
            self._redis_service = RedisService.from_prefix(
                client=bindings.client,
                key_prefix=str(settings.redis_key_prefix or "agentcode"),
            )
        except Exception:
            self._redis_service = None
        return self._redis_service

    @staticmethod
    def quota_lock_ttl_seconds() -> int:
        try:
            return max(5, min(300, int(str(os.getenv("QUOTA_LOCK_TTL_SECONDS", "30") or "30").strip())))
        except Exception:
            return 30

    @staticmethod
    def quota_lock_wait_seconds() -> int:
        try:
            return max(1, min(120, int(str(os.getenv("QUOTA_LOCK_WAIT_SECONDS", "10") or "10").strip())))
        except Exception:
            return 10

    @staticmethod
    def quota_lock_retry_interval_ms() -> int:
        try:
            return max(10, min(1000, int(str(os.getenv("QUOTA_LOCK_RETRY_INTERVAL_MS", "100") or "100").strip())))
        except Exception:
            return 100

    @staticmethod
    def quota_grant_ttl_seconds() -> int:
        try:
            return max(30, min(3600, int(str(os.getenv("QUOTA_GRANT_TTL_SECONDS", "60") or "60").strip())))
        except Exception:
            return 60

    def _repo_get_quota_config(self, quota_type: str) -> dict[str, Any] | None:
        redis_service = self._get_redis_service()
        cached = get_cached_quota_config(redis_service=redis_service, quota_type=quota_type)
        if isinstance(cached, dict):
            if cached == MISSING_QUOTA_CONFIG_PAYLOAD:
                return None
            return cached
        payload = self._repo.get_quota_config(quota_type)
        cache_quota_config(redis_service=redis_service, quota_type=quota_type, payload=payload)
        return payload

    def _quota_grant_pending_key(self, grant_id: str) -> str:
        redis_service = self._get_redis_service()
        if redis_service is not None:
            return redis_service.key_factory.cache("quota", "grant", "pending", str(grant_id))
        return f"quota:grant:pending:{grant_id}"

    def _quota_grant_finalized_key(self, grant_id: str) -> str:
        redis_service = self._get_redis_service()
        if redis_service is not None:
            return redis_service.key_factory.cache("quota", "grant", "finalized", str(grant_id))
        return f"quota:grant:finalized:{grant_id}"

    def _quota_grants_root_dir(self) -> Path:
        return get_settings().data_root / "quota_grants"

    def _quota_grant_file_path(self, *, grant_id: str, finalized: bool) -> Path:
        bucket = "finalized" if finalized else "pending"
        return self._quota_grants_root_dir() / bucket / f"{str(grant_id).strip()}.json"

    def _quota_grant_lock_file_path(self, *, user_id: int, quota_type: str) -> Path:
        safe_type = re.sub(r"[^a-z0-9_]+", "_", str(quota_type or "").strip().lower())
        return self._quota_grants_root_dir() / "locks" / f"{int(user_id)}__{safe_type}.lock.json"

    def _iter_redis_keys(self, pattern: str) -> list[str]:
        redis_service = self._get_redis_service()
        client = getattr(redis_service, "client", None)
        if client is None:
            return []
        scan_iter = getattr(client, "scan_iter", None)
        if callable(scan_iter):
            try:
                return [str(item.decode("utf-8") if isinstance(item, bytes) else item) for item in scan_iter(match=pattern)]
            except TypeError:
                return [str(item.decode("utf-8") if isinstance(item, bytes) else item) for item in scan_iter(pattern)]
            except Exception:
                return []
        keys_fn = getattr(client, "keys", None)
        if callable(keys_fn):
            try:
                return [str(item.decode("utf-8") if isinstance(item, bytes) else item) for item in keys_fn(pattern)]
            except Exception:
                return []
        values = getattr(client, "values", None)
        if isinstance(values, dict):
            return [str(key) for key in values.keys() if fnmatch.fnmatch(str(key), pattern)]
        return []

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temp_path.replace(path)

    def _touch_json_file_ttl(self, path: Path, *, ttl_seconds: int) -> bool:
        payload = self._read_json_file(path)
        if not payload:
            return False
        payload["expires_at_ts"] = float(time.time() + max(1, int(ttl_seconds)))
        self._write_json_file(path, payload)
        return True

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _cleanup_file_grant(self, *, path: Path) -> None:
        payload = self._read_json_file(path)
        if not payload:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                return
            return
        if float(payload.get("expires_at_ts") or 0) > time.time():
            return
        try:
            path.unlink(missing_ok=True)
        except Exception:
            return

    def _store_internal_quota_grant(self, *, grant_id: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        redis_service = self._get_redis_service()
        if redis_service is not None and redis_service.available:
            stored = redis_service.set_json(
                self._quota_grant_pending_key(grant_id),
                payload,
                ttl_seconds=ttl_seconds,
            )
            if stored:
                return
        file_payload = dict(payload)
        file_payload["expires_at_ts"] = float(time.time() + ttl_seconds)
        self._write_json_file(self._quota_grant_file_path(grant_id=grant_id, finalized=False), file_payload)

    def _get_internal_quota_grant(self, *, grant_id: str, finalized: bool = False) -> dict[str, Any] | None:
        redis_service = self._get_redis_service()
        key = self._quota_grant_finalized_key(grant_id) if finalized else self._quota_grant_pending_key(grant_id)
        if redis_service is not None and redis_service.available:
            payload = redis_service.get_json(key, default=None)
            if isinstance(payload, dict):
                return payload
        path = self._quota_grant_file_path(grant_id=grant_id, finalized=finalized)
        self._cleanup_file_grant(path=path)
        payload = self._read_json_file(path)
        return payload if isinstance(payload, dict) else None

    def _iter_pending_internal_quota_grants(self) -> list[tuple[str, dict[str, Any]]]:
        results: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        redis_service = self._get_redis_service()
        if redis_service is not None and redis_service.available:
            for key in self._iter_redis_keys(self._quota_grant_pending_key("*")):
                payload = redis_service.get_json(key, default=None)
                if not isinstance(payload, dict):
                    continue
                grant_id = str(payload.get("grant_id") or "").strip()
                if not grant_id or grant_id in seen:
                    continue
                seen.add(grant_id)
                results.append((grant_id, payload))

        pending_dir = self._quota_grants_root_dir() / "pending"
        try:
            paths = sorted(pending_dir.glob("*.json"))
        except Exception:
            paths = []
        for path in paths:
            self._cleanup_file_grant(path=path)
            payload = self._read_json_file(path)
            if not isinstance(payload, dict):
                continue
            grant_id = str(payload.get("grant_id") or path.stem).strip()
            if not grant_id or grant_id in seen:
                continue
            seen.add(grant_id)
            results.append((grant_id, payload))
        return results

    def _delete_internal_quota_grant(self, *, grant_id: str, finalized: bool = False) -> None:
        redis_service = self._get_redis_service()
        key = self._quota_grant_finalized_key(grant_id) if finalized else self._quota_grant_pending_key(grant_id)
        if redis_service is not None and redis_service.available:
            redis_service.delete(key)
        try:
            self._quota_grant_file_path(grant_id=grant_id, finalized=finalized).unlink(missing_ok=True)
        except Exception:
            return

    def _persist_internal_quota_grant_result(self, *, grant_id: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        redis_service = self._get_redis_service()
        if redis_service is not None and redis_service.available:
            stored = redis_service.set_json(
                self._quota_grant_finalized_key(grant_id),
                payload,
                ttl_seconds=ttl_seconds,
            )
            if stored:
                return
        file_payload = dict(payload)
        file_payload["expires_at_ts"] = float(time.time() + ttl_seconds)
        self._write_json_file(self._quota_grant_file_path(grant_id=grant_id, finalized=True), file_payload)

    def _acquire_internal_quota_grant_lease(self, *, grant_id: str, user_id: int, quota_type: str) -> dict[str, Any] | None:
        redis_service = self._get_redis_service()
        ttl_seconds = self.quota_grant_ttl_seconds()
        deadline = time.monotonic() + float(self.quota_lock_wait_seconds())
        retry_interval_seconds = max(0.01, float(self.quota_lock_retry_interval_ms()) / 1000.0)
        normalized_quota_type = normalize_quota_type(quota_type)
        if redis_service is not None and redis_service.available:
            key = redis_service.key_factory.lock("quota", int(user_id), normalized_quota_type)
            lock_manager = RedisLockManager(redis_service.client)
            while True:
                handle = lock_manager.acquire(key, ttl_seconds=ttl_seconds)
                if handle is not None:
                    return {
                        "backend": "redis",
                        "key": handle.key,
                        "token": handle.token,
                        "ttl_seconds": int(handle.ttl_seconds),
                    }
                if time.monotonic() >= deadline:
                    return None
                time.sleep(retry_interval_seconds)

        path = self._quota_grant_lock_file_path(user_id=user_id, quota_type=normalized_quota_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            self._cleanup_file_grant(path=path)
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    return None
                time.sleep(retry_interval_seconds)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "grant_id": grant_id,
                        "user_id": int(user_id),
                        "quota_type": normalized_quota_type,
                        "expires_at_ts": float(time.time() + ttl_seconds),
                    },
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            return {"backend": "file", "path": str(path)}

    def _release_internal_quota_grant_lease(self, lease: dict[str, Any] | None) -> None:
        if not isinstance(lease, dict):
            return
        backend = str(lease.get("backend") or "").strip().lower()
        if backend == "redis":
            redis_service = self._get_redis_service()
            if redis_service is None or not redis_service.available:
                return
            RedisLockManager(redis_service.client).release(
                RedisLockHandle(
                    key=str(lease.get("key") or ""),
                    token=str(lease.get("token") or ""),
                    ttl_seconds=max(1, int(lease.get("ttl_seconds") or self.quota_grant_ttl_seconds())),
                )
            )
            return
        if backend == "file":
            path = Path(str(lease.get("path") or "")).expanduser()
            try:
                path.unlink(missing_ok=True)
            except Exception:
                return

    def _internal_quota_grant_exists(self, *, grant_id: str) -> bool:
        return self._get_internal_quota_grant(grant_id=grant_id, finalized=False) is not None

    def _refresh_internal_quota_grant_state(self, *, grant_id: str, lease: dict[str, Any], ttl_seconds: int) -> bool:
        normalized_grant_id = str(grant_id or "").strip()
        if not normalized_grant_id:
            return False
        normalized_ttl = max(1, int(ttl_seconds))
        backend = str(lease.get("backend") or "").strip().lower()
        if backend == "redis":
            redis_service = self._get_redis_service()
            if redis_service is None or not redis_service.available:
                return False
            lock_handle = RedisLockHandle(
                key=str(lease.get("key") or ""),
                token=str(lease.get("token") or ""),
                ttl_seconds=max(1, int(lease.get("ttl_seconds") or normalized_ttl)),
            )
            if not RedisLockManager(redis_service.client).extend(lock_handle, ttl_seconds=normalized_ttl):
                return False
            if redis_service.expire(self._quota_grant_pending_key(normalized_grant_id), normalized_ttl):
                return True
        elif backend == "file":
            lock_path = Path(str(lease.get("path") or "")).expanduser()
            if not self._touch_json_file_ttl(lock_path, ttl_seconds=normalized_ttl):
                return False

        pending_path = self._quota_grant_file_path(grant_id=normalized_grant_id, finalized=False)
        if self._touch_json_file_ttl(pending_path, ttl_seconds=normalized_ttl):
            return True

        redis_service = self._get_redis_service()
        if redis_service is not None and redis_service.available:
            return bool(redis_service.expire(self._quota_grant_pending_key(normalized_grant_id), normalized_ttl))
        return False

    def _register_internal_quota_grant_renewer(
        self,
        *,
        grant_id: str,
        lease: dict[str, Any] | None,
        ttl_seconds: int,
    ) -> None:
        if not isinstance(lease, dict):
            return
        renewer = _InternalQuotaGrantRenewer(
            service=self,
            grant_id=grant_id,
            lease=lease,
            ttl_seconds=ttl_seconds,
        ).start()
        with self._internal_quota_grant_renewers_lock:
            previous = self._internal_quota_grant_renewers.get(grant_id)
            self._internal_quota_grant_renewers[grant_id] = renewer
        if previous is not None:
            previous.stop()

    def _get_internal_quota_grant_renewer(self, *, grant_id: str) -> _InternalQuotaGrantRenewer | None:
        with self._internal_quota_grant_renewers_lock:
            return self._internal_quota_grant_renewers.get(grant_id)

    def _unregister_internal_quota_grant_renewer(self, *, grant_id: str) -> None:
        with self._internal_quota_grant_renewers_lock:
            renewer = self._internal_quota_grant_renewers.pop(grant_id, None)
        if renewer is not None:
            renewer.stop()

    def cleanup_pending_internal_quota_grants(self) -> dict[str, Any]:
        cleaned = 0
        failed = 0
        errors: list[str] = []
        for grant_id, payload in self._iter_pending_internal_quota_grants():
            lease = payload.get("lease") if isinstance(payload.get("lease"), dict) else None
            try:
                self._release_internal_quota_grant_lease(lease)
                self._delete_internal_quota_grant(grant_id=grant_id, finalized=False)
                self._unregister_internal_quota_grant_renewer(grant_id=grant_id)
                cleaned += 1
            except Exception as exc:
                failed += 1
                errors.append(f"{grant_id}:{exc}")
        return {
            "success": failed == 0,
            "data": {
                "cleaned": cleaned,
                "failed": failed,
                "errors": errors,
            },
        }

    def _repo_list_active_configs(self) -> list[dict[str, Any]]:
        redis_service = self._get_redis_service()
        cached = get_cached_quota_active_configs(redis_service=redis_service)
        if isinstance(cached, list):
            return cached
        payload = self._repo.list_active_configs()
        cache_quota_active_configs(redis_service=redis_service, payload=payload)
        return payload

    def _repo_list_all_configs(self) -> list[dict[str, Any]]:
        redis_service = self._get_redis_service()
        cached = get_cached_quota_all_configs(redis_service=redis_service)
        if isinstance(cached, list):
            return cached
        payload = [self._serialize_config_row(row) for row in self._repo.list_all_configs()]
        cache_quota_all_configs(redis_service=redis_service, payload=payload)
        return payload

    def _repo_get_user_override_limit(self, *, user_id: int, quota_type: str) -> int | None:
        redis_service = self._get_redis_service()
        cached = get_cached_quota_override(redis_service=redis_service, user_id=user_id, quota_type=quota_type)
        if isinstance(cached, dict) and "custom_limit" in cached:
            return to_optional_non_negative_int(cached.get("custom_limit"))
        payload = self._repo.get_user_override_limit(user_id=user_id, quota_type=quota_type)
        cache_quota_override(redis_service=redis_service, user_id=user_id, quota_type=quota_type, custom_limit=payload)
        return payload

    @staticmethod
    def _quota_bucket_types(quota_type: str) -> tuple[str, ...]:
        normalized = normalize_quota_type(quota_type)
        return QUOTA_TYPE_BUCKETS.get(normalized, (normalized,))

    @staticmethod
    def _quota_display_name(quota_type: str, fallback: Any = None) -> str:
        label = CANONICAL_QUOTA_LABELS.get(quota_type)
        if label:
            return label
        text = str(fallback or "").strip()
        return text or quota_type

    def _resolve_quota_configs(
        self,
        quota_type: str,
        *,
        include_all_bucket_rows: bool = False,
    ) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
        canonical_type = normalize_quota_type(quota_type)
        canonical_config = self._repo_get_quota_config(canonical_type)
        if canonical_config and not include_all_bucket_rows:
            return canonical_type, [(canonical_type, canonical_config)]
        configs: list[tuple[str, dict[str, Any]]] = []
        if canonical_config:
            configs.append((canonical_type, canonical_config))
        for raw_type in self._quota_bucket_types(canonical_type):
            if raw_type == canonical_type:
                continue
            config = self._repo_get_quota_config(raw_type)
            if config:
                configs.append((raw_type, config))
        return canonical_type, configs

    def _resolve_primary_quota_config(self, quota_type: str) -> tuple[str, str | None, dict[str, Any] | None]:
        canonical_type, configs = self._resolve_quota_configs(quota_type)
        storage_quota_type, preferred = self._pick_preferred_config(canonical_type, configs)
        return canonical_type, storage_quota_type, preferred

    @staticmethod
    def _pick_preferred_config(
        canonical_type: str, configs: list[tuple[str, dict[str, Any]]]
    ) -> tuple[str, dict[str, Any]] | tuple[None, None]:
        if not configs:
            return None, None
        for raw_type, config in configs:
            if raw_type == canonical_type:
                return raw_type, config
        for raw_type, config in configs:
            if int(config.get("is_active", 0)) == 1:
                return raw_type, config
        return configs[0]

    def _canonicalize_config_row(self, canonical_type: str, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._serialize_config_row(row)
        payload["quota_type"] = canonical_type
        payload["quota_name"] = self._quota_display_name(canonical_type, payload.get("quota_name"))
        return payload

    def _canonical_config_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            raw_type = str(row.get("quota_type") or "").strip().lower()
            if not raw_type:
                continue
            canonical_type = normalize_quota_type(raw_type)
            if canonical_type not in CANONICAL_QUOTA_TYPES:
                continue
            grouped.setdefault(canonical_type, {})[raw_type] = row
        items: list[dict[str, Any]] = []
        for canonical_type in CANONICAL_QUOTA_TYPES:
            raw_rows = grouped.get(canonical_type) or {}
            bucket_rows = [(raw_type, raw_rows[raw_type]) for raw_type in self._quota_bucket_types(canonical_type) if raw_type in raw_rows]
            _raw_type, selected = self._pick_preferred_config(canonical_type, bucket_rows)
            if selected:
                items.append(self._canonicalize_config_row(canonical_type, selected))
        return items

    def _invalidate_config_metadata(self, *, quota_type: str) -> None:
        redis_service = self._get_redis_service()
        for raw_type in self._quota_bucket_types(quota_type):
            invalidate_quota_config_cache(redis_service=redis_service, quota_type=raw_type)
        invalidate_quota_config_lists_cache(redis_service=redis_service)

    @staticmethod
    def _select_primary_window(windows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not windows:
            return None
        for period in MULTI_PERIODS:
            for item in windows:
                if str(item.get("period")) == period:
                    return item
        return windows[0]

    def _resolve_multi_period_limits(self, *, config: dict[str, Any], override_limit: int | None) -> dict[str, int | None]:
        base_limits = {
            "daily": to_optional_non_negative_int(config.get("daily_limit")),
            "weekly": to_optional_non_negative_int(config.get("weekly_limit")),
            "monthly": to_optional_non_negative_int(config.get("monthly_limit")),
        }
        if override_limit is None:
            return base_limits
        has_multi_limits = any(value is not None for value in base_limits.values())
        if not has_multi_limits:
            return base_limits
        normalized_override = int(override_limit)
        return {name: (normalized_override if base_limits.get(name) is not None else None) for name in MULTI_PERIODS}

    def _build_multi_windows(self, *, user_id: int, quota_type: str, limits: dict[str, int | None]) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        for period in MULTI_PERIODS:
            limit = limits.get(period)
            if limit is None:
                continue
            key = period_key(period, None)
            current = self._repo.get_usage(user_id=user_id, quota_type=quota_type, period_key=key)
            remaining = max(0, int(limit) - int(current))
            windows.append(
                {
                    "period": period,
                    "period_days": None,
                    "period_key": key,
                    "current": int(current),
                    "limit": int(limit),
                    "remaining": int(remaining),
                    "allowed": bool(int(current) < int(limit)),
                    "reset_hint": period_reset_hint(period, None),
                }
            )
        return windows

    def check_quota(self, *, user_id: int, quota_type: str) -> dict[str, Any]:
        try:
            normalized_quota_type, storage_quota_type, config = self._resolve_primary_quota_config(quota_type)
            if not config:
                return {
                    "success": True,
                    "allowed": True,
                    "quota_type": normalized_quota_type,
                    "quota_name": self._quota_display_name(normalized_quota_type),
                    "current": 0,
                    "limit": 0,
                    "remaining": 0,
                    "period": "none",
                    "period_days": None,
                    "reset_hint": "never",
                    "config_missing": True,
                    "config_active": False,
                    "windows": [],
                    "multi_period_enabled": False,
                }
            if int(config.get("is_active", 0)) != 1:
                limits = self._resolve_multi_period_limits(config=config, override_limit=None)
                windows = [
                    {
                        "period": period,
                        "period_days": None,
                        "period_key": period_key(period, None),
                        "current": 0,
                        "limit": int(limit),
                        "remaining": int(limit),
                        "allowed": True,
                        "reset_hint": period_reset_hint(period, None),
                    }
                    for period, limit in limits.items()
                    if limit is not None
                ]
                normalized_period = normalize_period(str(config.get("period", "none")))
                return {
                    "success": True,
                    "allowed": True,
                    "quota_type": normalized_quota_type,
                    "quota_name": self._quota_display_name(normalized_quota_type, config.get("quota_name")),
                    "current": 0,
                    "limit": 0,
                    "remaining": 0,
                    "period": normalized_period,
                    "period_days": to_valid_period_days(config.get("period_days", 7), default=7) if normalized_period == "custom_days" else None,
                    "reset_hint": "never",
                    "config_missing": False,
                    "config_active": False,
                    "windows": windows,
                    "multi_period_enabled": len(windows) > 1,
                }

            override_limit = self._repo_get_user_override_limit(user_id=user_id, quota_type=storage_quota_type)
            limits = self._resolve_multi_period_limits(config=config, override_limit=override_limit)
            windows = self._build_multi_windows(user_id=user_id, quota_type=storage_quota_type, limits=limits)
            if not windows:
                period = normalize_period(str(config.get("period") or "daily"))
                period_days = to_valid_period_days(config.get("period_days"), default=7) if period == "custom_days" else None
                key = period_key(period, period_days)
                current = self._repo.get_usage(user_id=user_id, quota_type=storage_quota_type, period_key=key)
                limit = int(override_limit if override_limit is not None else int(config.get("default_limit") or 0))
                remaining = max(0, limit - current)
                windows = [{
                    "period": period,
                    "period_days": period_days,
                    "period_key": key,
                    "current": int(current),
                    "limit": int(limit),
                    "remaining": int(remaining),
                    "allowed": bool(current < limit),
                    "reset_hint": period_reset_hint(period, period_days),
                }]

            allowed = all(bool(item.get("allowed", False)) for item in windows) if windows else True
            primary = self._select_primary_window(windows)
            return {
                "success": True,
                "allowed": allowed,
                "quota_type": normalized_quota_type,
                "quota_name": self._quota_display_name(normalized_quota_type, config.get("quota_name")),
                "current": int((primary or {}).get("current") or 0),
                "limit": int((primary or {}).get("limit") or 0),
                "remaining": int((primary or {}).get("remaining") or 0),
                "period": str((primary or {}).get("period") or "none"),
                "period_days": (primary or {}).get("period_days"),
                "reset_hint": str((primary or {}).get("reset_hint") or "never"),
                "config_missing": False,
                "config_active": True,
                "windows": windows,
                "multi_period_enabled": len(windows) > 1,
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "QUOTA_CHECK_ERROR"}

    def increment_quota(self, *, user_id: int, quota_type: str) -> dict[str, Any]:
        try:
            normalized_quota_type, storage_quota_type, config = self._resolve_primary_quota_config(quota_type)
            if not config:
                return {"success": False, "error": "quota_not_found", "code": "NOT_FOUND"}
            if int(config.get("is_active", 0)) != 1:
                return {"success": True, "skipped": True, "reason": "quota_inactive", "data": {"used_count": 0}}
            limits = self._resolve_multi_period_limits(config=config, override_limit=None)
            windows = self._build_multi_windows(user_id=user_id, quota_type=storage_quota_type, limits=limits)
            period_usage: list[dict[str, Any]] = []
            if windows:
                for item in windows:
                    period = str(item.get("period") or "daily")
                    period_days = item.get("period_days")
                    key = str(item.get("period_key") or period_key(period, period_days))
                    used = self._repo.increment_usage(user_id=user_id, quota_type=storage_quota_type, period_key=key)
                    period_usage.append({"period": period, "period_days": period_days, "period_key": key, "used_count": int(used)})
            else:
                period = normalize_period(str(config.get("period") or "daily"))
                period_days = to_valid_period_days(config.get("period_days"), default=7) if period == "custom_days" else None
                key = period_key(period, period_days)
                used = self._repo.increment_usage(user_id=user_id, quota_type=storage_quota_type, period_key=key)
                period_usage.append({"period": period, "period_days": period_days, "period_key": key, "used_count": int(used)})
            primary = self._select_primary_window(period_usage)
            return {"success": True, "data": {"used_count": int((primary or {}).get("used_count") or 0), "period_usage": period_usage}}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "QUOTA_INCREMENT_ERROR"}

    def get_user_quotas(self, *, user_id: int) -> dict[str, Any]:
        try:
            items: list[dict[str, Any]] = []
            warnings: list[dict[str, Any]] = []
            for quota_type in CANONICAL_QUOTA_TYPES:
                _canonical_type, bucket_rows = self._resolve_quota_configs(quota_type, include_all_bucket_rows=True)
                _storage_quota_type, cfg = self._pick_preferred_config(quota_type, bucket_rows)
                if not cfg or int(cfg.get("is_active", 0)) != 1:
                    continue
                checked = self.check_quota(user_id=user_id, quota_type=quota_type)
                if checked.get("success"):
                    items.append(
                        {
                            "quota_type": checked.get("quota_type"),
                            "quota_name": checked.get("quota_name"),
                            "period": checked.get("period"),
                            "period_days": checked.get("period_days"),
                            "current": checked.get("current"),
                            "limit": checked.get("limit"),
                            "remaining": checked.get("remaining"),
                            "reset_hint": checked.get("reset_hint"),
                            "windows": checked.get("windows") or [],
                            "multi_period_enabled": bool(checked.get("multi_period_enabled")),
                        }
                    )
                else:
                    warnings.append({"quota_type": quota_type, "code": checked.get("code"), "error": checked.get("error")})
            return {"success": True, "data": {"quotas": items, "warnings": warnings, "partial_failure": bool(warnings)}}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "QUOTA_FETCH_ERROR"}

    def get_all_configs(self) -> dict[str, Any]:
        try:
            return {"success": True, "data": {"configs": self._canonical_config_rows(self._repo_list_all_configs())}}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "QUOTA_CONFIG_FETCH_ERROR"}

    def create_config(
        self,
        *,
        quota_type: str,
        quota_name: str,
        default_limit: int,
        daily_limit: int | None = None,
        weekly_limit: int | None = None,
        monthly_limit: int | None = None,
        is_active: bool,
        period: str | None = None,
        period_days: int | None = None,
        multi_limits_provided: bool = False,
    ) -> dict[str, Any]:
        if default_limit < 0:
            return {"success": False, "error": "invalid_default_limit", "code": "VALIDATION_ERROR"}
        normalized_quota_type = normalize_admin_quota_type(quota_type)
        if normalized_quota_type is None:
            return {"success": False, "error": "invalid_quota_type", "code": "VALIDATION_ERROR"}
        if not QUOTA_TYPE_RE.fullmatch(normalized_quota_type):
            return {"success": False, "error": "invalid_quota_type", "code": "VALIDATION_ERROR"}
        normalized_quota_name = self._quota_display_name(normalized_quota_type, quota_name)
        if len(normalized_quota_name) > 128:
            return {"success": False, "error": "invalid_quota_name", "code": "VALIDATION_ERROR"}
        raw_period = str(period).strip().lower() if period is not None else "daily"
        normalized_period = normalize_period(raw_period)
        if normalized_period != raw_period:
            return {"success": False, "error": "invalid_period", "code": "VALIDATION_ERROR"}

        persist_empty_inactive = (
            not bool(is_active)
            and not bool(multi_limits_provided)
            and daily_limit is None
            and weekly_limit is None
            and monthly_limit is None
            and int(default_limit) == 0
        )
        normalized_period_days = to_valid_period_days(period_days, default=7) if normalized_period == "custom_days" else None
        normalized_daily_limit = to_optional_non_negative_int(daily_limit)
        normalized_weekly_limit = to_optional_non_negative_int(weekly_limit)
        normalized_monthly_limit = to_optional_non_negative_int(monthly_limit)
        if persist_empty_inactive:
            normalized_period = "none"
            normalized_period_days = None
            normalized_daily_limit = None
            normalized_weekly_limit = None
            normalized_monthly_limit = None
            primary_limit = 0
        elif multi_limits_provided:
            if bool(is_active) and all(value is None for value in [normalized_daily_limit, normalized_weekly_limit, normalized_monthly_limit]):
                return {"success": False, "error": "at_least_one_period_limit_required", "code": "VALIDATION_ERROR"}
            if normalized_period in {"daily", "weekly", "monthly", "none"}:
                normalized_period_days = None
            primary_limit = next((item for item in [normalized_daily_limit, normalized_weekly_limit, normalized_monthly_limit] if item is not None), int(default_limit))
        else:
            primary_limit = int(default_limit)
            if normalized_period == "daily":
                normalized_daily_limit = int(default_limit)
            elif normalized_period == "weekly":
                normalized_weekly_limit = int(default_limit)
            elif normalized_period == "monthly":
                normalized_monthly_limit = int(default_limit)
        try:
            _normalized_type, bucket_rows = self._resolve_quota_configs(normalized_quota_type, include_all_bucket_rows=True)
            if bucket_rows:
                return {"success": False, "error": "quota_already_exists", "code": "ALREADY_EXISTS"}
            created = self._repo.create_quota_config(
                quota_type=normalized_quota_type,
                quota_name=normalized_quota_name,
                period=normalized_period,
                period_days=normalized_period_days,
                default_limit=int(primary_limit),
                daily_limit=normalized_daily_limit,
                weekly_limit=normalized_weekly_limit,
                monthly_limit=normalized_monthly_limit,
                is_active=bool(is_active),
            )
            if int(created) <= 0:
                return {"success": False, "error": "quota_create_failed", "code": "QUOTA_CONFIG_CREATE_ERROR"}
            self._invalidate_config_metadata(quota_type=normalized_quota_type)
            current = self._repo_get_quota_config(normalized_quota_type) or {
                "quota_type": normalized_quota_type,
                "quota_name": normalized_quota_name,
                "period": normalized_period,
                "period_days": normalized_period_days,
                "default_limit": int(primary_limit),
                "daily_limit": normalized_daily_limit,
                "weekly_limit": normalized_weekly_limit,
                "monthly_limit": normalized_monthly_limit,
                "is_active": 1 if bool(is_active) else 0,
            }
            return {"success": True, "message": "quota_config_created", "data": self._canonicalize_config_row(normalized_quota_type, current)}
        except Exception as exc:
            text = str(exc or "")
            if "duplicate" in text.lower():
                return {"success": False, "error": "quota_already_exists", "code": "ALREADY_EXISTS"}
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": text, "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": text, "code": "QUOTA_CONFIG_CREATE_ERROR"}

    def update_config(
        self,
        *,
        quota_type: str,
        default_limit: int,
        daily_limit: int | None = None,
        weekly_limit: int | None = None,
        monthly_limit: int | None = None,
        is_active: bool,
        period: str | None = None,
        period_days: int | None = None,
        multi_limits_provided: bool = False,
    ) -> dict[str, Any]:
        if default_limit < 0:
            return {"success": False, "error": "invalid_default_limit", "code": "VALIDATION_ERROR"}
        try:
            normalized_quota_type = normalize_admin_quota_type(quota_type)
            if normalized_quota_type is None:
                return {"success": False, "error": "invalid_quota_type", "code": "VALIDATION_ERROR"}
            _normalized_type, bucket_rows = self._resolve_quota_configs(normalized_quota_type, include_all_bucket_rows=True)
            storage_quota_type, existing = self._pick_preferred_config(normalized_quota_type, bucket_rows)
            if not existing:
                return {"success": False, "error": "quota_not_found", "code": "NOT_FOUND"}
            raw_period = str(period).strip().lower() if period is not None else str(existing.get("period") or "daily")
            normalized_period = normalize_period(raw_period)
            if period is not None and normalized_period != raw_period:
                return {"success": False, "error": "invalid_period", "code": "VALIDATION_ERROR"}
            persist_empty_inactive = (
                not bool(is_active)
                and not bool(multi_limits_provided)
                and daily_limit is None
                and weekly_limit is None
                and monthly_limit is None
                and int(default_limit) == 0
            )
            if persist_empty_inactive:
                normalized_period = "none"
                normalized_period_days = None
                normalized_daily_limit = None
                normalized_weekly_limit = None
                normalized_monthly_limit = None
                primary_limit = 0
            elif multi_limits_provided:
                normalized_daily_limit = to_optional_non_negative_int(daily_limit)
                normalized_weekly_limit = to_optional_non_negative_int(weekly_limit)
                normalized_monthly_limit = to_optional_non_negative_int(monthly_limit)
                if bool(is_active) and all(value is None for value in [normalized_daily_limit, normalized_weekly_limit, normalized_monthly_limit]):
                    return {"success": False, "error": "at_least_one_period_limit_required", "code": "VALIDATION_ERROR"}
                primary_limit = next((item for item in [normalized_daily_limit, normalized_weekly_limit, normalized_monthly_limit] if item is not None), int(default_limit))
                normalized_period_days = to_valid_period_days(period_days if period_days is not None else existing.get("period_days"), default=7) if normalized_period == "custom_days" else None
            else:
                normalized_daily_limit = None
                normalized_weekly_limit = None
                normalized_monthly_limit = None
                if normalized_period == "daily":
                    normalized_daily_limit = int(default_limit)
                elif normalized_period == "weekly":
                    normalized_weekly_limit = int(default_limit)
                elif normalized_period == "monthly":
                    normalized_monthly_limit = int(default_limit)
                primary_limit = int(default_limit)
                normalized_period_days = to_valid_period_days(period_days if period_days is not None else existing.get("period_days"), default=7) if normalized_period == "custom_days" else None
            affected_total = 0
            for raw_quota_type, _cfg in bucket_rows:
                affected_total += int(
                    self._repo.update_quota_config(
                        quota_type=raw_quota_type,
                        default_limit=int(primary_limit),
                        daily_limit=normalized_daily_limit,
                        weekly_limit=normalized_weekly_limit,
                        monthly_limit=normalized_monthly_limit,
                        is_active=bool(is_active),
                        period=normalized_period,
                        period_days=normalized_period_days,
                    )
                )
            if int(affected_total) <= 0:
                current = self._repo_get_quota_config(storage_quota_type)
                if not current:
                    return {"success": False, "error": "quota_not_found", "code": "NOT_FOUND"}
                return {"success": True, "message": "quota_config_unchanged"}
            self._invalidate_config_metadata(quota_type=normalized_quota_type)
            return {"success": True, "message": "quota_config_updated"}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "QUOTA_CONFIG_UPDATE_ERROR"}

    def reset_user_quota(self, *, user_id: int, quota_type: str) -> dict[str, Any]:
        try:
            normalized_quota_type = normalize_admin_quota_type(quota_type)
            if normalized_quota_type is None:
                return {"success": False, "error": "invalid_quota_type", "code": "VALIDATION_ERROR"}
            _normalized_type, bucket_rows = self._resolve_quota_configs(normalized_quota_type, include_all_bucket_rows=True)
            if not bucket_rows:
                return {"success": False, "error": "quota_not_found", "code": "NOT_FOUND"}
            for raw_quota_type, cfg in bucket_rows:
                limits = self._resolve_multi_period_limits(config=cfg, override_limit=None)
                keys: list[str] = []
                for period in MULTI_PERIODS:
                    limit = limits.get(period)
                    if limit is None:
                        continue
                    keys.append(period_key(period, None))
                if not keys:
                    period = normalize_period(str(cfg.get("period") or "daily"))
                    period_days = to_valid_period_days(cfg.get("period_days"), default=7) if period == "custom_days" else None
                    keys.append(period_key(period, period_days))
                for key in keys:
                    self._repo.reset_user_usage(user_id=user_id, quota_type=raw_quota_type, period_key=key)
            checked = self.check_quota(user_id=user_id, quota_type=normalized_quota_type)
            return {"success": True, "data": checked}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "QUOTA_RESET_ERROR"}

    def create_internal_quota_grant(
        self,
        *,
        user_id: int,
        quota_type: str,
        strict_config: bool = False,
    ) -> dict[str, Any]:
        try:
            user = auth_service_module.auth_service.get_user_by_id(int(user_id))
        except Exception as exc:
            if exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}:
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "QUOTA_GRANT_ERROR"}

        normalized_quota_type = normalize_quota_type(quota_type)
        noop = _is_quota_exempt_user(user)
        checked: dict[str, Any]
        if noop:
            checked = {
                "success": True,
                "allowed": True,
                "quota_type": normalized_quota_type,
                "config_active": False,
                "config_missing": False,
            }
        else:
            checked = self.check_quota(user_id=int(user_id), quota_type=normalized_quota_type)
            if not checked.get("success"):
                return checked
            if bool(checked.get("config_missing")) and bool(strict_config):
                return {
                    "success": False,
                    "error": "quota_config_missing",
                    "code": "QUOTA_CONFIG_MISSING",
                    "data": checked,
                }
            if not bool(checked.get("allowed")):
                return {
                    "success": False,
                    "error": "quota_exceeded",
                    "code": "QUOTA_EXCEEDED",
                    "data": checked,
                }

        grant_id = uuid4().hex
        ttl_seconds = self.quota_grant_ttl_seconds()
        lease = self._acquire_internal_quota_grant_lease(
            grant_id=grant_id,
            user_id=int(user_id),
            quota_type=normalized_quota_type,
        )
        if lease is None:
            return {
                "success": False,
                "error": "grant_already_active",
                "code": "GRANT_ALREADY_ACTIVE",
            }
        payload = {
            "grant_id": grant_id,
            "user_id": int(user_id),
            "quota_type": normalized_quota_type,
            "noop": bool(noop),
            "checked": checked,
            "config_active": bool(checked.get("config_active", True)),
            "lease": lease,
        }
        try:
            self._store_internal_quota_grant(grant_id=grant_id, payload=payload, ttl_seconds=ttl_seconds)
        except Exception:
            self._release_internal_quota_grant_lease(lease)
            raise
        self._register_internal_quota_grant_renewer(
            grant_id=grant_id,
            lease=lease,
            ttl_seconds=ttl_seconds,
        )
        return {
            "success": True,
            "data": {
                "grant_id": grant_id,
                "quota_type": normalized_quota_type,
                "noop": bool(noop),
                "checked": checked,
                "ttl_seconds": ttl_seconds,
            },
        }

    def finalize_internal_quota_grant(self, *, grant_id: str, success: bool) -> dict[str, Any]:
        normalized_grant_id = str(grant_id or "").strip()
        if not normalized_grant_id:
            return {"success": False, "error": "invalid_grant_id", "code": "VALIDATION_ERROR"}

        prior = self._get_internal_quota_grant(grant_id=normalized_grant_id, finalized=True)
        if prior:
            payload = dict(prior)
            payload["idempotent"] = True
            return {"success": True, "data": payload}

        pending = self._get_internal_quota_grant(grant_id=normalized_grant_id, finalized=False)
        if not pending:
            return {"success": False, "error": "grant_not_found", "code": "NOT_FOUND"}

        lease = pending.get("lease") if isinstance(pending.get("lease"), dict) else None
        renewer = self._get_internal_quota_grant_renewer(grant_id=normalized_grant_id)
        if renewer is not None:
            try:
                renewer.ensure_healthy()
            except AppError as exc:
                return {"success": False, "error": str(exc.message or exc), "code": str(exc.code or "DB_UNAVAILABLE")}
        result_payload = {
            "grant_id": normalized_grant_id,
            "quota_type": str(pending.get("quota_type") or ""),
            "noop": bool(pending.get("noop")),
            "counted": False,
            "idempotent": False,
        }
        if bool(success) and not bool(pending.get("noop")) and bool(pending.get("config_active", True)):
            incremented = self.increment_quota(
                user_id=int(pending.get("user_id") or 0),
                quota_type=str(pending.get("quota_type") or ""),
            )
            if not incremented.get("success"):
                return incremented
            result_payload["counted"] = True
            result_payload["increment"] = incremented.get("data")

        self._persist_internal_quota_grant_result(
            grant_id=normalized_grant_id,
            payload=result_payload,
            ttl_seconds=self.quota_grant_ttl_seconds(),
        )
        self._delete_internal_quota_grant(grant_id=normalized_grant_id, finalized=False)
        self._release_internal_quota_grant_lease(lease)
        self._unregister_internal_quota_grant_renewer(grant_id=normalized_grant_id)
        return {"success": True, "data": result_payload}


def _is_quota_exempt_user(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    try:
        return int(user.get("user_type") or 0) in {1, 2}
    except Exception:
        return False


def set_quota_service(service: QuotaService) -> QuotaService:
    global quota_service
    quota_service = service
    return quota_service


quota_service = QuotaService()
