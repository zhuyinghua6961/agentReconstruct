"""Shared slot lease primitives for admission infra-only rollout."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import time
from typing import Any

from app.integrations.redis.service import RedisService


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


class ExecutionSlotLeaseStore:
    def __init__(self, *, redis_service: RedisService) -> None:
        self.redis_service = redis_service
        self._memory_leases: dict[str, dict[str, Any]] = {}
        self._memory_expiry: dict[str, float] = {}
        self._memory_active_ids: set[str] = set()
        self._memory_capacity_ids: dict[str, set[str]] = {}
        self._memory_acquired_at: dict[str, float] = {}

    def lease_key(self, request_id: str) -> str:
        return self.redis_service.key_factory.admission("slot_lease", request_id)

    def lease_expiry_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "slot_lease_expiry")

    def lease_active_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "slot_leases")

    def lease_capacity_names_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "slot_capacity_names")

    def lease_capacity_key(self, capacity_key: str) -> str:
        return self.redis_service.key_factory.admission("index", "slot_capacity", capacity_key)

    def lease_acquired_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "slot_acquired")

    def dirty_flag_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "dirty", "slot_leases")

    def clean_version_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "dirty_clean", "slot_leases")

    def _now(self) -> float:
        return float(time.time())

    def _prune_memory(self) -> None:
        now = self._now()
        expired = [request_id for request_id, deadline in self._memory_expiry.items() if deadline <= now]
        for request_id in expired:
            lease = self._memory_leases.pop(request_id, None) or {}
            self._memory_expiry.pop(request_id, None)
            self._memory_active_ids.discard(request_id)
            self._memory_acquired_at.pop(request_id, None)
            capacity_key = str(lease.get("capacity_key") or "").strip()
            if capacity_key:
                self._memory_capacity_ids.setdefault(capacity_key, set()).discard(request_id)

    def _all_leases(self) -> list[dict[str, Any]]:
        if self.redis_service.available:
            self._prune_redis()
            output: list[dict[str, Any]] = []
            for request_id in self.redis_service.smembers(self.lease_active_key()):
                key = self.lease_key(request_id)
                payload = self.redis_service.get_json(key, default=None)
                if isinstance(payload, dict):
                    output.append(deepcopy(payload))
            return output
        self._prune_memory()
        return [deepcopy(item) for item in self._memory_leases.values() if isinstance(item, dict)]

    def _acquired_epoch(self, value: object) -> float | None:
        acquired_at = _parse_timestamp(value)
        if acquired_at is None:
            return None
        if acquired_at.tzinfo is None:
            acquired_at = acquired_at.replace(tzinfo=timezone.utc)
        return float(acquired_at.timestamp())

    def _prune_redis(self) -> None:
        expired_ids = self.redis_service.zrangebyscore(
            self.lease_expiry_key(),
            min_score=float("-inf"),
            max_score=self._now(),
        )
        if not expired_ids:
            return
        self.redis_service.srem(self.lease_active_key(), *expired_ids)
        self.redis_service.zrem(self.lease_expiry_key(), *expired_ids)
        self.redis_service.zrem(self.lease_acquired_key(), *expired_ids)
        for capacity_key in self.redis_service.smembers(self.lease_capacity_names_key()):
            self.redis_service.srem(self.lease_capacity_key(capacity_key), *expired_ids)

    def _mark_redis_dirty(self) -> int:
        if not self.redis_service.available:
            return 0
        return max(0, int(self.redis_service.incr(self.dirty_flag_key()) or 0))

    def _clear_redis_dirty(self, version: int) -> None:
        if (
            self.redis_service.available
            and int(version) > 0
            and self.redis_service.get_int(self.dirty_flag_key(), default=0) == int(version)
        ):
            self.redis_service.set_json(self.clean_version_key(), int(version))

    def _redis_dirty(self) -> bool:
        if not self.redis_service.available:
            return False
        return self.redis_service.get_int(self.dirty_flag_key(), default=0) > self.redis_service.get_int(
            self.clean_version_key(),
            default=0,
        )

    def _rebuild_redis_indexes(self) -> None:
        if not self.redis_service.available:
            return
        rebuild_version = self.redis_service.get_int(self.dirty_flag_key(), default=0)
        capacity_names = self.redis_service.smembers(self.lease_capacity_names_key())
        delete_keys = [
            self.lease_expiry_key(),
            self.lease_active_key(),
            self.lease_capacity_names_key(),
            self.lease_acquired_key(),
        ]
        delete_keys.extend(self.lease_capacity_key(capacity_key) for capacity_key in capacity_names)
        self.redis_service.delete(*delete_keys)
        for key in self.redis_service.scan_keys(self.lease_key("*")):
            payload = self.redis_service.get_json(key, default=None)
            ttl_seconds = self.redis_service.ttl(key)
            request_id = key.removeprefix(f"{self.redis_service.key_factory.prefix}:admission:slot_lease:")
            if not isinstance(payload, dict) or ttl_seconds is None or ttl_seconds <= 0 or not request_id:
                continue
            capacity_key = str(payload.get("capacity_key") or "").strip()
            self.redis_service.sadd(self.lease_active_key(), request_id)
            if capacity_key:
                self.redis_service.sadd(self.lease_capacity_names_key(), capacity_key)
                self.redis_service.sadd(self.lease_capacity_key(capacity_key), request_id)
            self.redis_service.zadd(
                self.lease_expiry_key(),
                {request_id: self._now() + max(1, int(ttl_seconds))},
            )
            self.redis_service.zadd(
                self.lease_acquired_key(),
                {request_id: self._acquired_epoch(payload.get("acquired_at")) or self._now()},
            )
        self._clear_redis_dirty(rebuild_version)

    def _redis_indexes_consistent(self, *, request_id: str, capacity_key: str) -> bool:
        active_ids = set(self.redis_service.smembers(self.lease_active_key()))
        expiry_ids = set(
            self.redis_service.zrangebyscore(
                self.lease_expiry_key(),
                min_score=float("-inf"),
                max_score=float("inf"),
            )
        )
        acquired_ids = set(
            self.redis_service.zrangebyscore(
                self.lease_acquired_key(),
                min_score=float("-inf"),
                max_score=float("inf"),
            )
        )
        capacity_ids = set(self.redis_service.smembers(self.lease_capacity_key(capacity_key)))
        return (
            request_id in active_ids
            and request_id in expiry_ids
            and request_id in acquired_ids
            and request_id in capacity_ids
        )

    def _redis_indexes_cleared(self, *, request_id: str, capacity_key: str) -> bool:
        active_ids = set(self.redis_service.smembers(self.lease_active_key()))
        expiry_ids = set(
            self.redis_service.zrangebyscore(
                self.lease_expiry_key(),
                min_score=float("-inf"),
                max_score=float("inf"),
            )
        )
        acquired_ids = set(
            self.redis_service.zrangebyscore(
                self.lease_acquired_key(),
                min_score=float("-inf"),
                max_score=float("inf"),
            )
        )
        capacity_ids = set(self.redis_service.smembers(self.lease_capacity_key(capacity_key)))
        return (
            request_id not in active_ids
            and request_id not in expiry_ids
            and request_id not in acquired_ids
            and request_id not in capacity_ids
        )

    def acquire(
        self,
        *,
        request_id: str,
        capacity_key: str,
        owner_id: str,
        ttl_seconds: int,
        acquired_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        normalized_request_id = str(request_id or "").strip()
        normalized_capacity_key = str(capacity_key or "").strip()
        normalized_owner_id = str(owner_id or "").strip()
        if not normalized_request_id or not normalized_capacity_key or not normalized_owner_id:
            return None

        record = {
            "request_id": normalized_request_id,
            "capacity_key": normalized_capacity_key,
            "owner_id": normalized_owner_id,
            "acquired_at": str(acquired_at or ""),
            "last_renewed_at": str(acquired_at or ""),
            "lease_ttl_seconds": max(1, int(ttl_seconds)),
            "metadata": deepcopy(metadata or {}),
        }
        if self.redis_service.available:
            dirty_version = self._mark_redis_dirty()
            created = self.redis_service.set_json_if_absent(
                self.lease_key(normalized_request_id),
                record,
                ttl_seconds=int(ttl_seconds),
            )
            if not created:
                self._clear_redis_dirty(dirty_version)
                return None
            self.redis_service.sadd(self.lease_active_key(), normalized_request_id)
            self.redis_service.sadd(self.lease_capacity_names_key(), normalized_capacity_key)
            self.redis_service.sadd(self.lease_capacity_key(normalized_capacity_key), normalized_request_id)
            self.redis_service.zadd(
                self.lease_expiry_key(),
                {normalized_request_id: self._now() + max(1, int(ttl_seconds))},
            )
            self.redis_service.zadd(
                self.lease_acquired_key(),
                {normalized_request_id: self._acquired_epoch(acquired_at) or self._now()},
            )
            if self._redis_indexes_consistent(
                request_id=normalized_request_id,
                capacity_key=normalized_capacity_key,
            ):
                self._clear_redis_dirty(dirty_version)
            return deepcopy(record)

        self._prune_memory()
        if normalized_request_id in self._memory_leases:
            return None
        self._memory_leases[normalized_request_id] = deepcopy(record)
        self._memory_expiry[normalized_request_id] = self._now() + max(1, int(ttl_seconds))
        self._memory_active_ids.add(normalized_request_id)
        self._memory_capacity_ids.setdefault(normalized_capacity_key, set()).add(normalized_request_id)
        self._memory_acquired_at[normalized_request_id] = self._acquired_epoch(acquired_at) or self._now()
        return deepcopy(record)

    def get(self, request_id: str) -> dict[str, Any] | None:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return None
        if self.redis_service.available:
            payload = self.redis_service.get_json(self.lease_key(normalized_request_id), default=None)
            return deepcopy(payload) if isinstance(payload, dict) else None
        self._prune_memory()
        payload = self._memory_leases.get(normalized_request_id)
        return deepcopy(payload) if isinstance(payload, dict) else None

    def renew(
        self,
        *,
        request_id: str,
        owner_id: str,
        ttl_seconds: int,
        renewed_at: str | None = None,
    ) -> dict[str, Any] | None:
        record = self.get(request_id)
        normalized_owner_id = str(owner_id or "").strip()
        if not isinstance(record, dict) or not normalized_owner_id:
            return None
        if str(record.get("owner_id") or "").strip() != normalized_owner_id:
            return None
        updated = dict(record)
        updated["last_renewed_at"] = str(renewed_at or record.get("last_renewed_at") or "")
        updated["lease_ttl_seconds"] = max(1, int(ttl_seconds))
        capacity_key = str(record.get("capacity_key") or "").strip()
        if self.redis_service.available:
            dirty_version = self._mark_redis_dirty()
            swapped = self.redis_service.compare_and_swap_json(
                self.lease_key(str(request_id)),
                expected_value=record,
                new_value=updated,
                ttl_seconds=int(ttl_seconds),
            )
            if not swapped:
                self._clear_redis_dirty(dirty_version)
                return None
            self.redis_service.zadd(
                self.lease_expiry_key(),
                {str(request_id or "").strip(): self._now() + max(1, int(ttl_seconds))},
            )
            if self._redis_indexes_consistent(
                request_id=str(request_id or "").strip(),
                capacity_key=capacity_key,
            ):
                self._clear_redis_dirty(dirty_version)
            return deepcopy(updated)

        normalized_request_id = str(request_id or "").strip()
        self._memory_leases[normalized_request_id] = deepcopy(updated)
        self._memory_expiry[normalized_request_id] = self._now() + max(1, int(ttl_seconds))
        return deepcopy(updated)

    def release(self, request_id: str, *, owner_id: str | None = None) -> bool:
        record = self.get(request_id)
        if not isinstance(record, dict):
            return False
        normalized_owner_id = str(owner_id or "").strip()
        if normalized_owner_id and str(record.get("owner_id") or "").strip() != normalized_owner_id:
            return False
        normalized_request_id = str(request_id or "").strip()
        if self.redis_service.available:
            dirty_version = self._mark_redis_dirty()
            capacity_key = str(record.get("capacity_key") or "").strip()
            removed = self.redis_service.delete(self.lease_key(normalized_request_id)) > 0
            if removed:
                self.redis_service.srem(self.lease_active_key(), normalized_request_id)
                self.redis_service.zrem(self.lease_expiry_key(), normalized_request_id)
                self.redis_service.zrem(self.lease_acquired_key(), normalized_request_id)
                for capacity_key in self.redis_service.smembers(self.lease_capacity_names_key()):
                    self.redis_service.srem(self.lease_capacity_key(capacity_key), normalized_request_id)
            if self._redis_indexes_cleared(request_id=normalized_request_id, capacity_key=capacity_key):
                self._clear_redis_dirty(dirty_version)
            return removed
        self._memory_expiry.pop(normalized_request_id, None)
        lease = self._memory_leases.pop(normalized_request_id, None) or {}
        self._memory_active_ids.discard(normalized_request_id)
        self._memory_acquired_at.pop(normalized_request_id, None)
        capacity_key = str(lease.get("capacity_key") or "").strip()
        if capacity_key:
            self._memory_capacity_ids.setdefault(capacity_key, set()).discard(normalized_request_id)
        return bool(lease)

    def describe(self) -> dict[str, Any]:
        oldest_age_seconds: int | None = None
        now_dt = datetime.fromtimestamp(self._now(), tz=timezone.utc)
        if self.redis_service.available:
            if self._redis_dirty():
                self._rebuild_redis_indexes()
            self._prune_redis()
            capacity_counts = {
                capacity_key: self.redis_service.scard(self.lease_capacity_key(capacity_key))
                for capacity_key in self.redis_service.smembers(self.lease_capacity_names_key())
                if self.redis_service.scard(self.lease_capacity_key(capacity_key)) > 0
            }
            oldest_member = self.redis_service.zrange(self.lease_acquired_key(), start=0, stop=0, withscores=True)
            if oldest_member:
                oldest_epoch = float(oldest_member[0][1])
                oldest_age_seconds = max(0, int(self._now() - oldest_epoch))
            active_leases = self.redis_service.scard(self.lease_active_key())
        else:
            self._prune_memory()
            capacity_counts = {
                capacity_key: len(request_ids)
                for capacity_key, request_ids in self._memory_capacity_ids.items()
                if request_ids
            }
            if self._memory_acquired_at:
                oldest_epoch = min(self._memory_acquired_at.values())
                oldest_age_seconds = max(0, int(self._now() - oldest_epoch))
            active_leases = len(self._memory_active_ids)
        return {
            "available": bool(self.redis_service.available),
            "storage_mode": "redis" if self.redis_service.available else "memory_fallback",
            "lease_key_example": self.lease_key("req_example"),
            "active_leases": active_leases,
            "capacity_counts": capacity_counts,
            "oldest_lease_age_seconds": oldest_age_seconds,
        }
