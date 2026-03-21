from __future__ import annotations

import re
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from app.core.config import get_settings
from app.integrations.redis import RedisRenewingLock, RedisService, build_redis_bindings
from app.modules.quota.cache import (
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
    return str(value or "").strip().lower()


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


class QuotaService:
    def __init__(self, *, repo: QuotaRepositoryProtocol | None = None, redis_service: RedisService | None = None):
        self._repo = repo or UnavailableQuotaRepository()
        self._redis_service = redis_service
        self._redis_service_resolved = redis_service is not None

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

    def _repo_get_quota_config(self, quota_type: str) -> dict[str, Any] | None:
        redis_service = self._get_redis_service()
        cached = get_cached_quota_config(redis_service=redis_service, quota_type=quota_type)
        if isinstance(cached, dict):
            return cached
        payload = self._repo.get_quota_config(quota_type)
        cache_quota_config(redis_service=redis_service, quota_type=quota_type, payload=payload)
        return payload

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

    def _invalidate_config_metadata(self, *, quota_type: str) -> None:
        redis_service = self._get_redis_service()
        invalidate_quota_config_cache(redis_service=redis_service, quota_type=quota_type)
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
            config = self._repo_get_quota_config(quota_type)
            if not config:
                return {
                    "success": True,
                    "allowed": True,
                    "quota_type": quota_type,
                    "quota_name": quota_type,
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
                    "quota_type": quota_type,
                    "quota_name": config.get("quota_name", quota_type),
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

            override_limit = self._repo_get_user_override_limit(user_id=user_id, quota_type=quota_type)
            limits = self._resolve_multi_period_limits(config=config, override_limit=override_limit)
            windows = self._build_multi_windows(user_id=user_id, quota_type=quota_type, limits=limits)
            if not windows:
                period = normalize_period(str(config.get("period") or "daily"))
                period_days = to_valid_period_days(config.get("period_days"), default=7) if period == "custom_days" else None
                key = period_key(period, period_days)
                current = self._repo.get_usage(user_id=user_id, quota_type=quota_type, period_key=key)
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
                "quota_type": quota_type,
                "quota_name": config.get("quota_name", quota_type),
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
            config = self._repo_get_quota_config(quota_type)
            if not config:
                return {"success": False, "error": "quota_not_found", "code": "NOT_FOUND"}
            if int(config.get("is_active", 0)) != 1:
                return {"success": True, "skipped": True, "reason": "quota_inactive", "data": {"used_count": 0}}
            limits = self._resolve_multi_period_limits(config=config, override_limit=None)
            windows = self._build_multi_windows(user_id=user_id, quota_type=quota_type, limits=limits)
            period_usage: list[dict[str, Any]] = []
            if windows:
                for item in windows:
                    period = str(item.get("period") or "daily")
                    period_days = item.get("period_days")
                    key = str(item.get("period_key") or period_key(period, period_days))
                    used = self._repo.increment_usage(user_id=user_id, quota_type=quota_type, period_key=key)
                    period_usage.append({"period": period, "period_days": period_days, "period_key": key, "used_count": int(used)})
            else:
                period = normalize_period(str(config.get("period") or "daily"))
                period_days = to_valid_period_days(config.get("period_days"), default=7) if period == "custom_days" else None
                key = period_key(period, period_days)
                used = self._repo.increment_usage(user_id=user_id, quota_type=quota_type, period_key=key)
                period_usage.append({"period": period, "period_days": period_days, "period_key": key, "used_count": int(used)})
            primary = self._select_primary_window(period_usage)
            return {"success": True, "data": {"used_count": int((primary or {}).get("used_count") or 0), "period_usage": period_usage}}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "QUOTA_INCREMENT_ERROR"}

    def get_user_quotas(self, *, user_id: int) -> dict[str, Any]:
        try:
            configs = self._repo_list_active_configs()
            items: list[dict[str, Any]] = []
            warnings: list[dict[str, Any]] = []
            for cfg in configs:
                quota_type = str(cfg.get("quota_type"))
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
            return {"success": True, "data": {"configs": self._repo_list_all_configs()}}
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
        normalized_quota_type = normalize_quota_type(quota_type)
        if not QUOTA_TYPE_RE.fullmatch(normalized_quota_type):
            return {"success": False, "error": "invalid_quota_type", "code": "VALIDATION_ERROR"}
        normalized_quota_name = str(quota_name or "").strip() or normalized_quota_type
        if len(normalized_quota_name) > 128:
            return {"success": False, "error": "invalid_quota_name", "code": "VALIDATION_ERROR"}
        raw_period = str(period).strip().lower() if period is not None else "daily"
        normalized_period = normalize_period(raw_period)
        if normalized_period != raw_period:
            return {"success": False, "error": "invalid_period", "code": "VALIDATION_ERROR"}

        normalized_period_days = to_valid_period_days(period_days, default=7) if normalized_period == "custom_days" else None
        normalized_daily_limit = to_optional_non_negative_int(daily_limit)
        normalized_weekly_limit = to_optional_non_negative_int(weekly_limit)
        normalized_monthly_limit = to_optional_non_negative_int(monthly_limit)
        if multi_limits_provided:
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
            existing = self._repo_get_quota_config(normalized_quota_type)
            if existing:
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
            return {"success": True, "message": "quota_config_created", "data": current}
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
            existing = self._repo_get_quota_config(quota_type)
            if not existing:
                return {"success": False, "error": "quota_not_found", "code": "NOT_FOUND"}
            raw_period = str(period).strip().lower() if period is not None else str(existing.get("period") or "daily")
            normalized_period = normalize_period(raw_period)
            if period is not None and normalized_period != raw_period:
                return {"success": False, "error": "invalid_period", "code": "VALIDATION_ERROR"}
            if multi_limits_provided:
                normalized_daily_limit = to_optional_non_negative_int(daily_limit)
                normalized_weekly_limit = to_optional_non_negative_int(weekly_limit)
                normalized_monthly_limit = to_optional_non_negative_int(monthly_limit)
                if bool(is_active) and all(value is None for value in [normalized_daily_limit, normalized_weekly_limit, normalized_monthly_limit]):
                    return {"success": False, "error": "at_least_one_period_limit_required", "code": "VALIDATION_ERROR"}
                primary_limit = next((item for item in [normalized_daily_limit, normalized_weekly_limit, normalized_monthly_limit] if item is not None), int(default_limit))
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
            affected = self._repo.update_quota_config(
                quota_type=quota_type,
                default_limit=int(primary_limit),
                daily_limit=normalized_daily_limit,
                weekly_limit=normalized_weekly_limit,
                monthly_limit=normalized_monthly_limit,
                is_active=bool(is_active),
                period=normalized_period,
                period_days=normalized_period_days,
            )
            if int(affected) <= 0:
                current = self._repo_get_quota_config(quota_type)
                if not current:
                    return {"success": False, "error": "quota_not_found", "code": "NOT_FOUND"}
                return {"success": True, "message": "quota_config_unchanged"}
            self._invalidate_config_metadata(quota_type=quota_type)
            return {"success": True, "message": "quota_config_updated"}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "QUOTA_CONFIG_UPDATE_ERROR"}

    def reset_user_quota(self, *, user_id: int, quota_type: str) -> dict[str, Any]:
        try:
            cfg = self._repo_get_quota_config(quota_type)
            if not cfg:
                return {"success": False, "error": "quota_not_found", "code": "NOT_FOUND"}
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
                self._repo.reset_user_usage(user_id=user_id, quota_type=quota_type, period_key=key)
            checked = self.check_quota(user_id=user_id, quota_type=quota_type)
            return {"success": True, "data": checked}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "QUOTA_RESET_ERROR"}


def set_quota_service(service: QuotaService) -> QuotaService:
    global quota_service
    quota_service = service
    return quota_service


quota_service = QuotaService()
