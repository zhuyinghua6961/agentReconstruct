"""Queued-request storage primitives for infra-only admission rollout."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import time
from typing import Any

from app.integrations.redis.service import RedisService

_EXPIRED_RECORD_RETENTION_SECONDS = 60


class ExecutionQueueStatusStore:
    def __init__(self, *, redis_service: RedisService) -> None:
        self.redis_service = redis_service
        self._memory_requests: dict[str, dict[str, Any]] = {}
        self._memory_results: dict[str, Any] = {}
        self._memory_request_expiry: dict[str, float] = {}
        self._memory_result_expiry: dict[str, float] = {}
        self._memory_request_ids: set[str] = set()
        self._memory_queued_ids: dict[str, float] = {}
        self._memory_admitted_ids: set[str] = set()
        self._memory_terminal_ids: set[str] = set()
        self._memory_cancellable_ids: set[str] = set()
        self._memory_result_ids: set[str] = set()

    def request_key(self, request_id: str) -> str:
        return self.redis_service.key_factory.admission("request", request_id)

    def result_key(self, request_id: str) -> str:
        return self.redis_service.key_factory.result(request_id)

    def request_index_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "requests")

    def request_expiry_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "request_expiry")

    def queued_index_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "queued")

    def admitted_index_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "admitted")

    def terminal_index_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "terminal")

    def cancellable_index_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "cancellable")

    def result_index_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "results")

    def result_expiry_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "result_expiry")

    def dirty_flag_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "dirty", "queue_status")

    def clean_version_key(self) -> str:
        return self.redis_service.key_factory.admission("index", "dirty_clean", "queue_status")

    def _now(self) -> float:
        return float(time.time())

    def _prune_memory_requests(self) -> None:
        now = self._now()
        expired = [request_id for request_id, deadline in self._memory_request_expiry.items() if deadline <= now]
        for request_id in expired:
            record = self._memory_requests.get(request_id)
            if self._should_terminalize_expired_record(record):
                previous = deepcopy(record) if isinstance(record, dict) else None
                updated = self._terminalize_expired_record(record)
                self._memory_requests[request_id] = updated
                self._memory_request_expiry[request_id] = now + _EXPIRED_RECORD_RETENTION_SECONDS
                self._sync_memory_request_indexes(request_id=request_id, previous=previous, current=updated)
                continue
            self._memory_requests.pop(request_id, None)
            self._memory_request_expiry.pop(request_id, None)
            self._memory_request_ids.discard(request_id)
            self._memory_queued_ids.pop(request_id, None)
            self._memory_admitted_ids.discard(request_id)
            self._memory_terminal_ids.discard(request_id)
            self._memory_cancellable_ids.discard(request_id)

    def _prune_memory_results(self) -> None:
        now = self._now()
        expired = [request_id for request_id, deadline in self._memory_result_expiry.items() if deadline <= now]
        for request_id in expired:
            self._memory_results.pop(request_id, None)
            self._memory_result_expiry.pop(request_id, None)
            self._memory_result_ids.discard(request_id)

    def _queued_age_seconds(self, record: dict[str, Any]) -> int | None:
        enqueued_at = str(record.get("enqueued_at") or "").strip()
        if not enqueued_at:
            return None
        normalized = enqueued_at.replace("Z", "+00:00")
        try:
            queued_dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if queued_dt.tzinfo is None:
            queued_dt = queued_dt.replace(tzinfo=timezone.utc)
        now_dt = datetime.fromtimestamp(self._now(), tz=timezone.utc)
        return max(0, int((now_dt - queued_dt).total_seconds()))

    def _request_records(self) -> list[dict[str, Any]]:
        if self.redis_service.available:
            self._prune_redis_requests()
            output: list[dict[str, Any]] = []
            for request_id in self.redis_service.smembers(self.request_index_key()):
                key = self.request_key(request_id)
                payload = self.redis_service.get_json(key, default=None)
                if isinstance(payload, dict):
                    output.append(deepcopy(payload))
            return output
        self._prune_memory_requests()
        return [deepcopy(item) for item in self._memory_requests.values() if isinstance(item, dict)]

    def _request_sort_key(self, record: dict[str, Any]) -> tuple[float, str]:
        return (
            self._queued_epoch(record) or float("inf"),
            str(record.get("request_id") or ""),
        )

    def _result_count(self) -> int:
        if self.redis_service.available:
            self._prune_redis_results()
            return self.redis_service.scard(self.result_index_key())
        self._prune_memory_results()
        return len(self._memory_result_ids)

    def _queued_epoch(self, record: dict[str, Any]) -> float | None:
        enqueued_at = str(record.get("enqueued_at") or "").strip()
        if not enqueued_at:
            return None
        normalized = enqueued_at.replace("Z", "+00:00")
        try:
            queued_dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if queued_dt.tzinfo is None:
            queued_dt = queued_dt.replace(tzinfo=timezone.utc)
        return float(queued_dt.timestamp())

    def _sync_memory_request_indexes(self, *, request_id: str, previous: dict[str, Any] | None, current: dict[str, Any]) -> None:
        previous_status = str((previous or {}).get("status") or "").strip().lower()
        current_status = str(current.get("status") or "").strip().lower()
        self._memory_request_ids.add(request_id)
        self._memory_queued_ids.pop(request_id, None)
        self._memory_admitted_ids.discard(request_id)
        self._memory_terminal_ids.discard(request_id)
        self._memory_cancellable_ids.discard(request_id)
        if current_status == "queued":
            self._memory_queued_ids[request_id] = self._queued_epoch(current) or self._now()
            if bool(current.get("cancel_allowed")):
                self._memory_cancellable_ids.add(request_id)
        elif current_status == "admitted":
            self._memory_admitted_ids.add(request_id)
        elif current_status:
            self._memory_terminal_ids.add(request_id)
        _ = previous_status

    def _sync_redis_request_indexes(self, *, request_id: str, current: dict[str, Any], ttl_seconds: int) -> None:
        expires_at = self._now() + max(1, int(ttl_seconds))
        self.redis_service.sadd(self.request_index_key(), request_id)
        self.redis_service.zadd(self.request_expiry_key(), {request_id: expires_at})
        self.redis_service.zrem(self.queued_index_key(), request_id)
        self.redis_service.srem(self.admitted_index_key(), request_id)
        self.redis_service.srem(self.terminal_index_key(), request_id)
        self.redis_service.srem(self.cancellable_index_key(), request_id)
        current_status = str(current.get("status") or "").strip().lower()
        if current_status == "queued":
            self.redis_service.zadd(self.queued_index_key(), {request_id: self._queued_epoch(current) or self._now()})
            if bool(current.get("cancel_allowed")):
                self.redis_service.sadd(self.cancellable_index_key(), request_id)
        elif current_status == "admitted":
            self.redis_service.sadd(self.admitted_index_key(), request_id)
        elif current_status:
            self.redis_service.sadd(self.terminal_index_key(), request_id)

    def _redis_request_indexes_consistent(self, *, request_id: str, current: dict[str, Any]) -> bool:
        request_ids = set(self.redis_service.smembers(self.request_index_key()))
        if request_id not in request_ids:
            return False
        request_expiry_ids = set(
            self.redis_service.zrangebyscore(
                self.request_expiry_key(),
                min_score=float("-inf"),
                max_score=float("inf"),
            )
        )
        if request_id not in request_expiry_ids:
            return False
        queued_ids = set(
            self.redis_service.zrangebyscore(
                self.queued_index_key(),
                min_score=float("-inf"),
                max_score=float("inf"),
            )
        )
        admitted_ids = set(self.redis_service.smembers(self.admitted_index_key()))
        terminal_ids = set(self.redis_service.smembers(self.terminal_index_key()))
        cancellable_ids = set(self.redis_service.smembers(self.cancellable_index_key()))
        status = str(current.get("status") or "").strip().lower()
        if status == "queued":
            if request_id not in queued_ids:
                return False
            if bool(current.get("cancel_allowed")) != (request_id in cancellable_ids):
                return False
            return request_id not in admitted_ids and request_id not in terminal_ids
        if status == "admitted":
            return request_id in admitted_ids and request_id not in queued_ids and request_id not in terminal_ids
        if status:
            return request_id in terminal_ids and request_id not in queued_ids and request_id not in admitted_ids
        return False

    def _redis_result_indexes_consistent(self, *, request_id: str) -> bool:
        result_ids = set(self.redis_service.smembers(self.result_index_key()))
        expiry_ids = set(
            self.redis_service.zrangebyscore(
                self.result_expiry_key(),
                min_score=float("-inf"),
                max_score=float("inf"),
            )
        )
        return request_id in result_ids and request_id in expiry_ids

    def _prune_redis_requests(self) -> None:
        expired_ids = self.redis_service.zrangebyscore(
            self.request_expiry_key(),
            min_score=float("-inf"),
            max_score=self._now(),
        )
        if not expired_ids:
            return
        removable_ids: list[str] = []
        for request_id in expired_ids:
            payload = self.redis_service.get_json(self.request_key(request_id), default=None)
            if self._should_terminalize_expired_record(payload):
                updated = self._terminalize_expired_record(payload)
                self.redis_service.set_json(
                    self.request_key(request_id),
                    updated,
                    ttl_seconds=_EXPIRED_RECORD_RETENTION_SECONDS,
                )
                self._sync_redis_request_indexes(
                    request_id=request_id,
                    current=updated,
                    ttl_seconds=_EXPIRED_RECORD_RETENTION_SECONDS,
                )
                continue
            removable_ids.append(request_id)
        if not removable_ids:
            return
        self.redis_service.srem(self.request_index_key(), *removable_ids)
        self.redis_service.zrem(self.request_expiry_key(), *removable_ids)
        self.redis_service.zrem(self.queued_index_key(), *removable_ids)
        self.redis_service.srem(self.admitted_index_key(), *removable_ids)
        self.redis_service.srem(self.terminal_index_key(), *removable_ids)
        self.redis_service.srem(self.cancellable_index_key(), *removable_ids)

    def _should_terminalize_expired_record(self, record: dict[str, Any] | None) -> bool:
        if not isinstance(record, dict):
            return False
        return str(record.get("status") or "").strip().lower() == "queued"

    def _terminalize_expired_record(self, record: dict[str, Any]) -> dict[str, Any]:
        expired_at = datetime.fromtimestamp(self._now(), tz=timezone.utc).isoformat()
        updated = deepcopy(record)
        updated["status"] = "expired"
        updated["cancel_allowed"] = False
        updated["expired_at"] = expired_at
        updated["updated_at"] = expired_at
        updated["terminal_sync_pending"] = True
        return updated

    def _prune_redis_results(self) -> None:
        expired_ids = self.redis_service.zrangebyscore(
            self.result_expiry_key(),
            min_score=float("-inf"),
            max_score=self._now(),
        )
        if not expired_ids:
            return
        self.redis_service.srem(self.result_index_key(), *expired_ids)
        self.redis_service.zrem(self.result_expiry_key(), *expired_ids)

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
        self.redis_service.delete(
            self.request_index_key(),
            self.request_expiry_key(),
            self.queued_index_key(),
            self.admitted_index_key(),
            self.terminal_index_key(),
            self.cancellable_index_key(),
            self.result_index_key(),
            self.result_expiry_key(),
        )
        for key in self.redis_service.scan_keys(self.request_key("*")):
            payload = self.redis_service.get_json(key, default=None)
            ttl_seconds = self.redis_service.ttl(key)
            request_id = key.removeprefix(f"{self.redis_service.key_factory.prefix}:admission:request:")
            if isinstance(payload, dict) and ttl_seconds is not None and ttl_seconds > 0 and request_id:
                self._sync_redis_request_indexes(
                    request_id=request_id,
                    current=payload,
                    ttl_seconds=int(ttl_seconds),
                )
        for key in self.redis_service.scan_keys(self.result_key("*")):
            ttl_seconds = self.redis_service.ttl(key)
            request_id = key.removeprefix(f"{self.redis_service.key_factory.prefix}:result:")
            if ttl_seconds is None or ttl_seconds <= 0 or not request_id:
                continue
            self.redis_service.sadd(self.result_index_key(), request_id)
            self.redis_service.zadd(
                self.result_expiry_key(),
                {request_id: self._now() + max(1, int(ttl_seconds))},
            )
        self._clear_redis_dirty(rebuild_version)

    def put_request(self, record: dict[str, Any], *, ttl_seconds: int) -> bool:
        request_id = str(record.get("request_id") or "").strip()
        if not request_id:
            return False
        normalized = deepcopy(record)
        previous = self.get_request(request_id)
        if self.redis_service.available:
            dirty_version = self._mark_redis_dirty()
            stored = self.redis_service.set_json(self.request_key(request_id), normalized, ttl_seconds=ttl_seconds)
            if not stored:
                self._clear_redis_dirty(dirty_version)
                return False
            self._sync_redis_request_indexes(request_id=request_id, current=normalized, ttl_seconds=ttl_seconds)
            if self._redis_request_indexes_consistent(request_id=request_id, current=normalized):
                self._clear_redis_dirty(dirty_version)
            return True
        self._memory_requests[request_id] = normalized
        self._memory_request_expiry[request_id] = self._now() + max(1, int(ttl_seconds))
        self._sync_memory_request_indexes(request_id=request_id, previous=previous, current=normalized)
        return True

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return None
        if self.redis_service.available:
            payload = self.redis_service.get_json(self.request_key(normalized_id), default=None)
            return deepcopy(payload) if isinstance(payload, dict) else None
        self._prune_memory_requests()
        payload = self._memory_requests.get(normalized_id)
        return deepcopy(payload) if isinstance(payload, dict) else None

    def delete_request(self, request_id: str) -> bool:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return False
        existing = self.get_request(normalized_id)
        if existing is None:
            return False
        if self.redis_service.available:
            dirty_version = self._mark_redis_dirty()
            self.redis_service.delete(self.request_key(normalized_id))
            self.redis_service.srem(self.request_index_key(), normalized_id)
            self.redis_service.zrem(self.request_expiry_key(), normalized_id)
            self.redis_service.zrem(self.queued_index_key(), normalized_id)
            self.redis_service.srem(self.admitted_index_key(), normalized_id)
            self.redis_service.srem(self.terminal_index_key(), normalized_id)
            self.redis_service.srem(self.cancellable_index_key(), normalized_id)
            self._clear_redis_dirty(dirty_version)
            return True
        self._memory_requests.pop(normalized_id, None)
        self._memory_request_expiry.pop(normalized_id, None)
        self._memory_request_ids.discard(normalized_id)
        self._memory_queued_ids.pop(normalized_id, None)
        self._memory_admitted_ids.discard(normalized_id)
        self._memory_terminal_ids.discard(normalized_id)
        self._memory_cancellable_ids.discard(normalized_id)
        return True

    def list_requests(self, *, status: str | None = None) -> list[dict[str, Any]]:
        normalized_status = str(status or "").strip().lower()
        records = self._request_records()
        if normalized_status:
            records = [
                record
                for record in records
                if str(record.get("status") or "").strip().lower() == normalized_status
            ]
        return sorted(records, key=self._request_sort_key)

    def request_ttl_seconds(self, request_id: str) -> int | None:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return None
        if self.redis_service.available:
            ttl_seconds = self.redis_service.ttl(self.request_key(normalized_id))
            return int(ttl_seconds) if ttl_seconds is not None and ttl_seconds > 0 else None
        self._prune_memory_requests()
        expires_at = self._memory_request_expiry.get(normalized_id)
        if expires_at is None:
            return None
        remaining = max(0, int(expires_at - self._now()))
        return remaining or None

    def cancel_request(self, request_id: str, *, cancelled_at: str | None = None) -> dict[str, Any] | None:
        record = self.get_request(request_id)
        if not isinstance(record, dict):
            return None
        status = str(record.get("status") or "").strip().lower()
        if status != "queued" or not bool(record.get("cancel_allowed", True)):
            return None
        updated = dict(record)
        updated["status"] = "cancelled"
        updated["cancel_allowed"] = False
        if cancelled_at:
            updated["cancelled_at"] = str(cancelled_at)
        request_key = self.request_key(str(request_id))
        ttl_seconds = self.redis_service.ttl(request_key) if self.redis_service.available else None
        if ttl_seconds is None or ttl_seconds <= 0:
            ttl_seconds = 60
        if self.redis_service.available:
            dirty_version = self._mark_redis_dirty()
            swapped = self.redis_service.compare_and_swap_json(
                request_key,
                expected_value=record,
                new_value=updated,
                ttl_seconds=int(ttl_seconds),
            )
            if not swapped:
                self._clear_redis_dirty(dirty_version)
                return None
            self._sync_redis_request_indexes(
                request_id=str(request_id or "").strip(),
                current=updated,
                ttl_seconds=int(ttl_seconds),
            )
            if self._redis_request_indexes_consistent(
                request_id=str(request_id or "").strip(),
                current=updated,
            ):
                self._clear_redis_dirty(dirty_version)
        else:
            self.put_request(updated, ttl_seconds=int(ttl_seconds))
        return updated

    def cancel_active_request(self, request_id: str, *, cancelled_at: str | None = None) -> dict[str, Any] | None:
        record = self.get_request(request_id)
        if not isinstance(record, dict):
            return None
        status = str(record.get("status") or "").strip().lower()
        if status in {"completed", "failed", "cancelled", "expired"}:
            return record
        updated = dict(record)
        updated["status"] = "cancelled"
        updated["cancel_allowed"] = False
        updated["updated_at"] = str(cancelled_at or datetime.now(timezone.utc).isoformat())
        if cancelled_at:
            updated["cancelled_at"] = str(cancelled_at)
        request_key = self.request_key(str(request_id))
        ttl_seconds = self.redis_service.ttl(request_key) if self.redis_service.available else None
        if ttl_seconds is None or ttl_seconds <= 0:
            ttl_seconds = 60
        if self.redis_service.available:
            dirty_version = self._mark_redis_dirty()
            swapped = self.redis_service.compare_and_swap_json(
                request_key,
                expected_value=record,
                new_value=updated,
                ttl_seconds=int(ttl_seconds),
            )
            if not swapped:
                self._clear_redis_dirty(dirty_version)
                return None
            self._sync_redis_request_indexes(
                request_id=str(request_id or "").strip(),
                current=updated,
                ttl_seconds=int(ttl_seconds),
            )
            if self._redis_request_indexes_consistent(
                request_id=str(request_id or "").strip(),
                current=updated,
            ):
                self._clear_redis_dirty(dirty_version)
        else:
            self.put_request(updated, ttl_seconds=int(ttl_seconds))
        return updated

    def put_result(self, request_id: str, payload: Any, *, ttl_seconds: int) -> bool:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return False
        if self.redis_service.available:
            dirty_version = self._mark_redis_dirty()
            stored = self.redis_service.set_json(self.result_key(normalized_id), payload, ttl_seconds=ttl_seconds)
            if not stored:
                self._clear_redis_dirty(dirty_version)
                return False
            self.redis_service.sadd(self.result_index_key(), normalized_id)
            self.redis_service.zadd(
                self.result_expiry_key(),
                {normalized_id: self._now() + max(1, int(ttl_seconds))},
            )
            if self._redis_result_indexes_consistent(request_id=normalized_id):
                self._clear_redis_dirty(dirty_version)
            return True
        self._memory_results[normalized_id] = deepcopy(payload)
        self._memory_result_expiry[normalized_id] = self._now() + max(1, int(ttl_seconds))
        self._memory_result_ids.add(normalized_id)
        return True

    def get_result(self, request_id: str) -> Any:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return None
        if self.redis_service.available:
            if normalized_id not in set(self.redis_service.smembers(self.result_index_key())):
                return None
            return self.redis_service.get_json(self.result_key(normalized_id), default=None)
        self._prune_memory_results()
        return deepcopy(self._memory_results.get(normalized_id))

    def delete_result(self, request_id: str) -> bool:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return False
        if self.redis_service.available:
            deleted = self.redis_service.delete(self.result_key(normalized_id)) > 0
            if deleted or self.redis_service.get_json(self.result_key(normalized_id), default=None) is None:
                self.redis_service.srem(self.result_index_key(), normalized_id)
                self.redis_service.zrem(self.result_expiry_key(), normalized_id)
                return True
            return False
        self._prune_memory_results()
        removed = normalized_id in self._memory_results
        self._memory_results.pop(normalized_id, None)
        self._memory_result_expiry.pop(normalized_id, None)
        self._memory_result_ids.discard(normalized_id)
        return removed

    def describe(self) -> dict[str, Any]:
        oldest_queued_age_seconds: int | None = None
        if self.redis_service.available:
            if self._redis_dirty():
                self._rebuild_redis_indexes()
            self._prune_redis_requests()
            self._prune_redis_results()
            oldest_member = self.redis_service.zrange(self.queued_index_key(), start=0, stop=0, withscores=True)
            if oldest_member:
                oldest_epoch = float(oldest_member[0][1])
                oldest_queued_age_seconds = max(0, int(self._now() - oldest_epoch))
            requests_tracked = self.redis_service.scard(self.request_index_key())
            queued_requests = self.redis_service.zcard(self.queued_index_key())
            admitted_requests = self.redis_service.scard(self.admitted_index_key())
            terminal_requests = self.redis_service.scard(self.terminal_index_key())
            cancellable_requests = self.redis_service.scard(self.cancellable_index_key())
            results_tracked = self.redis_service.scard(self.result_index_key())
        else:
            self._prune_memory_requests()
            self._prune_memory_results()
            if self._memory_queued_ids:
                oldest_epoch = min(self._memory_queued_ids.values())
                oldest_queued_age_seconds = max(0, int(self._now() - oldest_epoch))
            requests_tracked = len(self._memory_request_ids)
            queued_requests = len(self._memory_queued_ids)
            admitted_requests = len(self._memory_admitted_ids)
            terminal_requests = len(self._memory_terminal_ids)
            cancellable_requests = len(self._memory_cancellable_ids)
            results_tracked = len(self._memory_result_ids)
        return {
            "available": bool(self.redis_service.available),
            "storage_mode": "redis" if self.redis_service.available else "memory_fallback",
            "request_key_example": self.request_key("req_example"),
            "result_key_example": self.result_key("req_example"),
            "requests_tracked": requests_tracked,
            "queued_requests": queued_requests,
            "admitted_requests": admitted_requests,
            "terminal_requests": terminal_requests,
            "cancellable_requests": cancellable_requests,
            "results_tracked": results_tracked,
            "oldest_queued_age_seconds": oldest_queued_age_seconds,
        }
