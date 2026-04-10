"""Delayed-attach relay storage primitives for infra-only admission rollout."""

from __future__ import annotations

from copy import deepcopy
import time
from typing import Any

from app.integrations.redis.service import RedisService

_TERMINAL_RELAY_EVENT_TYPES = {"done", "error"}
_TERMINAL_RELAY_STATE_STATUSES = {"completed", "failed", "canceled", "cancelled", "expired"}


class ExecutionEventRelayStore:
    def __init__(self, *, redis_service: RedisService) -> None:
        self.redis_service = redis_service
        self._memory_frames: dict[str, list[dict[str, Any]]] = {}
        self._memory_expiry: dict[str, float] = {}
        self._memory_request_ids: set[str] = set()
        self._memory_total_frames = 0
        self._memory_latest_sequence: dict[str, int] = {}
        self._memory_latest_upstream_sequence: dict[str, int] = {}

    def frames_key(self, request_id: str) -> str:
        return self.redis_service.key_factory.relay(request_id, "frames")

    def sequence_key(self, request_id: str) -> str:
        return self.redis_service.key_factory.relay(request_id, "sequence")

    def cursor_key(self, request_id: str) -> str:
        return self.redis_service.key_factory.relay(request_id, "cursor")

    def upstream_sequence_key(self, request_id: str) -> str:
        return self.redis_service.key_factory.relay(request_id, "upstream_sequence")

    def frame_count_key(self, request_id: str) -> str:
        return self.redis_service.key_factory.relay(request_id, "frame_count")

    def request_index_key(self) -> str:
        return self.redis_service.key_factory.relay("index", "requests")

    def expiry_index_key(self) -> str:
        return self.redis_service.key_factory.relay("index", "expiry")

    def total_frames_key(self) -> str:
        return self.redis_service.key_factory.relay("index", "total_frames")

    def dirty_flag_key(self) -> str:
        return self.redis_service.key_factory.relay("index", "dirty")

    def clean_version_key(self) -> str:
        return self.redis_service.key_factory.relay("index", "dirty_clean")

    def _now(self) -> float:
        return float(time.time())

    def _prune_memory_frames(self) -> None:
        now = self._now()
        expired = [request_id for request_id, deadline in self._memory_expiry.items() if deadline <= now]
        for request_id in expired:
            frames = self._memory_frames.pop(request_id, None) or []
            self._memory_expiry.pop(request_id, None)
            self._memory_request_ids.discard(request_id)
            self._memory_total_frames = max(0, self._memory_total_frames - len(frames))
            self._memory_latest_sequence.pop(request_id, None)
            self._memory_latest_upstream_sequence.pop(request_id, None)

    def _prune_redis(self) -> None:
        expired_ids = self.redis_service.zrangebyscore(
            self.expiry_index_key(),
            min_score=float("-inf"),
            max_score=self._now(),
        )
        if not expired_ids:
            return
        total_to_remove = 0
        delete_keys: list[str] = []
        for request_id in expired_ids:
            frame_count = max(0, self.redis_service.get_int(self.frame_count_key(request_id), default=0))
            if frame_count <= 0:
                frame_count = len(self.redis_service.lrange_json(self.frames_key(request_id)))
            total_to_remove += frame_count
            delete_keys.extend(
                [
                    self.frames_key(request_id),
                    self.sequence_key(request_id),
                    self.cursor_key(request_id),
                    self.upstream_sequence_key(request_id),
                    self.frame_count_key(request_id),
                ]
            )
        if total_to_remove:
            self.redis_service.incrby(self.total_frames_key(), -total_to_remove)
        self.redis_service.srem(self.request_index_key(), *expired_ids)
        self.redis_service.zrem(self.expiry_index_key(), *expired_ids)
        if delete_keys:
            self.redis_service.delete(*delete_keys)

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
            self.expiry_index_key(),
            self.total_frames_key(),
        )
        total_frames = 0
        relay_pattern = f"{self.redis_service.key_factory.prefix}:relay:*"
        for key in self.redis_service.scan_keys(relay_pattern):
            if not str(key).endswith(":frames"):
                continue
            request_id = key.removeprefix(f"{self.redis_service.key_factory.prefix}:relay:").removesuffix(":frames")
            if not request_id:
                continue
            frames = [item for item in self.redis_service.lrange_json(key) if isinstance(item, dict)]
            frame_count = len(frames)
            latest_sequence = max([int(item.get("sequence") or 0) for item in frames], default=0)
            highest_upstream_sequence = max(
                [self._payload_upstream_sequence(dict(item.get("payload") or {})) or 0 for item in frames],
                default=0,
            )
            ttl_seconds = self.redis_service.ttl(key)
            if frame_count <= 0 or ttl_seconds is None or ttl_seconds <= 0:
                continue
            total_frames += frame_count
            self.redis_service.set_json(self.sequence_key(request_id), latest_sequence, ttl_seconds=int(ttl_seconds))
            self.redis_service.set_json(self.frame_count_key(request_id), frame_count, ttl_seconds=int(ttl_seconds))
            if highest_upstream_sequence > 0:
                self.redis_service.set_json(
                    self.upstream_sequence_key(request_id),
                    highest_upstream_sequence,
                    ttl_seconds=int(ttl_seconds),
                )
            current_cursor = self.redis_service.get_int(self.cursor_key(request_id), default=0)
            self.redis_service.set_json(
                self.cursor_key(request_id),
                max(current_cursor, latest_sequence),
                ttl_seconds=int(ttl_seconds),
            )
            self.redis_service.sadd(self.request_index_key(), request_id)
            self.redis_service.zadd(
                self.expiry_index_key(),
                {request_id: self._now() + max(1, int(ttl_seconds))},
            )
        if total_frames:
            self.redis_service.incrby(self.total_frames_key(), total_frames)
        self._clear_redis_dirty(rebuild_version)

    def _redis_indexes_consistent_for_request(
        self,
        *,
        request_id: str,
        expected_total_frames: int | None = None,
        expected_latest_sequence: int | None = None,
        expected_latest_upstream_sequence: int | None = None,
        expected_frame_count: int | None = None,
    ) -> bool:
        request_ids = set(self.redis_service.smembers(self.request_index_key()))
        expiry_ids = set(
            self.redis_service.zrangebyscore(
                self.expiry_index_key(),
                min_score=float("-inf"),
                max_score=float("inf"),
            )
        )
        if request_id not in request_ids or request_id not in expiry_ids:
            return False
        if expected_latest_sequence is not None and self.redis_service.get_int(
            self.sequence_key(request_id),
            default=0,
        ) != max(0, int(expected_latest_sequence)):
            return False
        if expected_frame_count is not None and self.redis_service.get_int(
            self.frame_count_key(request_id),
            default=0,
        ) != max(0, int(expected_frame_count)):
            return False
        if expected_latest_upstream_sequence is not None and self.redis_service.get_int(
            self.upstream_sequence_key(request_id),
            default=0,
        ) != max(0, int(expected_latest_upstream_sequence)):
            return False
        if expected_total_frames is None:
            return True
        return self.redis_service.get_int(self.total_frames_key(), default=0) == max(0, int(expected_total_frames))

    def _redis_indexes_cleared_for_request(self, *, request_id: str, expected_total_frames: int) -> bool:
        request_ids = set(self.redis_service.smembers(self.request_index_key()))
        expiry_ids = set(
            self.redis_service.zrangebyscore(
                self.expiry_index_key(),
                min_score=float("-inf"),
                max_score=float("inf"),
            )
        )
        return (
            request_id not in request_ids
            and request_id not in expiry_ids
            and self.redis_service.get_int(self.sequence_key(request_id), default=0) == 0
            and self.redis_service.get_int(self.frame_count_key(request_id), default=0) == 0
            and self.redis_service.get_int(self.total_frames_key(), default=0) == max(0, int(expected_total_frames))
        )

    def _request_frame_items(self) -> list[tuple[str, list[dict[str, Any]]]]:
        if self.redis_service.available:
            self._prune_redis()
            output: list[tuple[str, list[dict[str, Any]]]] = []
            for request_id in self.redis_service.smembers(self.request_index_key()):
                frames = self.redis_service.lrange_json(self.frames_key(request_id))
                output.append((request_id, deepcopy(frames)))
            return output
        self._prune_memory_frames()
        return [
            (request_id, deepcopy(frames))
            for request_id, frames in self._memory_frames.items()
            if isinstance(frames, list)
        ]

    def _frame_payload(self, frame: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(frame, dict):
            return {}
        payload = frame.get("payload")
        return dict(payload) if isinstance(payload, dict) else {}

    def _payload_type(self, payload: dict[str, Any] | None) -> str:
        return str((payload or {}).get("type") or "").strip().lower()

    def _payload_status(self, payload: dict[str, Any] | None) -> str:
        return str((payload or {}).get("status") or "").strip().lower()

    def _payload_upstream_sequence(self, payload: dict[str, Any] | None) -> int | None:
        try:
            sequence = int((payload or {}).get("seq"))
        except Exception:
            return None
        return sequence if sequence > 0 else None

    def _payload_is_terminal(self, payload: dict[str, Any] | None) -> bool:
        event_type = self._payload_type(payload)
        if event_type in _TERMINAL_RELAY_EVENT_TYPES:
            return True
        return event_type == "state" and self._payload_status(payload) in _TERMINAL_RELAY_STATE_STATUSES

    def _ignored_record(
        self,
        last_record: dict[str, Any] | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if last_record is None:
            return {"sequence": 0, "payload": deepcopy(payload), "ignored": True}
        ignored = deepcopy(last_record)
        ignored["ignored"] = True
        return ignored

    def _scan_replay_prefix(self, frames: list[dict[str, Any]]) -> tuple[int, bool]:
        highest_upstream_sequence = 0
        terminal_seen = False
        for item in frames:
            if not isinstance(item, dict):
                continue
            payload = self._frame_payload(item)
            upstream_sequence = self._payload_upstream_sequence(payload)
            if upstream_sequence is not None and upstream_sequence > highest_upstream_sequence:
                highest_upstream_sequence = upstream_sequence
            if self._payload_is_terminal(payload):
                terminal_seen = True
                break
        return highest_upstream_sequence, terminal_seen

    def _get_last_frame_record(self, request_id: str) -> dict[str, Any] | None:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return None
        if self.redis_service.available:
            frames = self.redis_service.lrange_json(self.frames_key(normalized_id), start=-1, stop=-1)
            for item in reversed(frames):
                if isinstance(item, dict):
                    return deepcopy(item)
            return None
        self._prune_memory_frames()
        frames = self._memory_frames.get(normalized_id, [])
        if not frames:
            return None
        last = frames[-1]
        return deepcopy(last) if isinstance(last, dict) else None

    def _get_latest_upstream_sequence(self, request_id: str) -> int:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return 0
        if self.redis_service.available:
            cached = max(0, self.redis_service.get_int(self.upstream_sequence_key(normalized_id), default=0))
            if cached > 0:
                return cached
            frames = self.redis_service.lrange_json(self.frames_key(normalized_id), start=0, stop=-1)
            highest_upstream_sequence, _terminal_seen = self._scan_replay_prefix(
                [item for item in frames if isinstance(item, dict)]
            )
            return highest_upstream_sequence
        self._prune_memory_frames()
        return max(0, int(self._memory_latest_upstream_sequence.get(normalized_id, 0)))

    def append_frame(self, request_id: str, payload: dict[str, Any], *, ttl_seconds: int) -> dict[str, Any]:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            raise ValueError("request_id is required")
        last_record = self._get_last_frame_record(normalized_id)
        last_payload = self._frame_payload(last_record)
        if self._payload_is_terminal(last_payload):
            return self._ignored_record(last_record, payload)
        next_upstream_sequence = self._payload_upstream_sequence(payload)
        if (
            last_record is not None
            and next_upstream_sequence is not None
            and next_upstream_sequence <= self._get_latest_upstream_sequence(normalized_id)
        ):
            return self._ignored_record(last_record, payload)

        if self.redis_service.available:
            dirty_version = self._mark_redis_dirty()
            next_sequence = self.redis_service.incr(self.cursor_key(normalized_id))
            if next_sequence is None:
                self._clear_redis_dirty(dirty_version)
                raise RuntimeError("relay_sequence_unavailable")
            record = {"sequence": next_sequence, "payload": deepcopy(payload)}
            pushed = self.redis_service.rpush_json(self.frames_key(normalized_id), record)
            if pushed is None:
                raise RuntimeError("relay_append_unavailable")
            self.redis_service.set_json(self.sequence_key(normalized_id), next_sequence, ttl_seconds=int(ttl_seconds))
            self.redis_service.set_json(self.frame_count_key(normalized_id), pushed, ttl_seconds=int(ttl_seconds))
            self.redis_service.expire(self.cursor_key(normalized_id), ttl_seconds)
            self.redis_service.expire(self.frames_key(normalized_id), ttl_seconds)
            if next_upstream_sequence is not None and next_upstream_sequence > 0:
                self.redis_service.set_json(
                    self.upstream_sequence_key(normalized_id),
                    next_upstream_sequence,
                    ttl_seconds=int(ttl_seconds),
                )
            self.redis_service.sadd(self.request_index_key(), normalized_id)
            self.redis_service.zadd(
                self.expiry_index_key(),
                {normalized_id: self._now() + max(1, int(ttl_seconds))},
            )
            total_frames = self.redis_service.incrby(self.total_frames_key(), 1)
            if total_frames is not None and self._redis_indexes_consistent_for_request(
                request_id=normalized_id,
                expected_latest_sequence=next_sequence,
                expected_latest_upstream_sequence=next_upstream_sequence if next_upstream_sequence is not None and next_upstream_sequence > 0 else None,
                expected_frame_count=pushed,
            ):
                self._clear_redis_dirty(dirty_version)
            return record
        self._prune_memory_frames()
        frames = self._memory_frames.get(normalized_id, [])
        next_sequence = (frames[-1]["sequence"] if frames else 0) + 1
        record = {"sequence": next_sequence, "payload": deepcopy(payload)}
        frames.append(record)
        self._memory_frames[normalized_id] = deepcopy(frames)
        self._memory_expiry[normalized_id] = self._now() + max(1, int(ttl_seconds))
        self._memory_request_ids.add(normalized_id)
        self._memory_total_frames += 1
        self._memory_latest_sequence[normalized_id] = next_sequence
        if next_upstream_sequence is not None and next_upstream_sequence > 0:
            self._memory_latest_upstream_sequence[normalized_id] = max(
                int(self._memory_latest_upstream_sequence.get(normalized_id, 0)),
                next_upstream_sequence,
            )
        return record

    def get_frames(self, request_id: str, *, after_sequence: int) -> list[dict[str, Any]]:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return []
        start_index = max(0, int(after_sequence))
        highest_upstream_sequence = 0
        if self.redis_service.available:
            latest_sequence = max(0, self.redis_service.get_int(self.sequence_key(normalized_id), default=0))
            frame_count = max(0, self.redis_service.get_int(self.frame_count_key(normalized_id), default=0))
            if frame_count <= 0:
                frames = self.redis_service.lrange_json(self.frames_key(normalized_id), start=0, stop=-1)
                frame_count = len(frames)
            else:
                frames = []
            if latest_sequence > frame_count:
                if not frames:
                    frames = self.redis_service.lrange_json(self.frames_key(normalized_id), start=0, stop=-1)
            else:
                if start_index > 0:
                    prefix_frames = self.redis_service.lrange_json(self.frames_key(normalized_id), start=0, stop=start_index - 1)
                    highest_upstream_sequence, terminal_seen = self._scan_replay_prefix(prefix_frames)
                    if terminal_seen:
                        return []
                frames = self.redis_service.lrange_json(self.frames_key(normalized_id), start=start_index, stop=-1)
        else:
            self._prune_memory_frames()
            all_frames = list(self._memory_frames.get(normalized_id, []))
            latest_sequence = max(0, int(self._memory_latest_sequence.get(normalized_id, 0)))
            if latest_sequence > len(all_frames):
                frames = all_frames
            else:
                if start_index > 0:
                    highest_upstream_sequence, terminal_seen = self._scan_replay_prefix(all_frames[:start_index])
                    if terminal_seen:
                        return []
                frames = all_frames[start_index:]
        output: list[dict[str, Any]] = []
        for item in frames:
            if not isinstance(item, dict):
                continue
            payload = self._frame_payload(item)
            upstream_sequence = self._payload_upstream_sequence(payload)
            is_duplicate_upstream_sequence = (
                upstream_sequence is not None and upstream_sequence <= highest_upstream_sequence
            )
            if upstream_sequence is not None and upstream_sequence > highest_upstream_sequence:
                highest_upstream_sequence = upstream_sequence
            sequence = int(item.get("sequence") or 0)
            if sequence <= int(after_sequence) or is_duplicate_upstream_sequence:
                if self._payload_is_terminal(payload):
                    break
                continue
            output.append(deepcopy(item))
            if self._payload_is_terminal(payload):
                break
        return output

    def clear(self, request_id: str) -> int:
        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            return 0
        if self.redis_service.available:
            dirty_version = self._mark_redis_dirty()
            previous_total_frames = self.redis_service.get_int(self.total_frames_key(), default=0)
            frame_count = max(0, self.redis_service.get_int(self.frame_count_key(normalized_id), default=0))
            if frame_count <= 0:
                frame_count = len(self.redis_service.lrange_json(self.frames_key(normalized_id)))
            deleted = self.redis_service.delete(
                self.frames_key(normalized_id),
                self.sequence_key(normalized_id),
                self.cursor_key(normalized_id),
                self.upstream_sequence_key(normalized_id),
                self.frame_count_key(normalized_id),
            )
            if frame_count:
                self.redis_service.incrby(self.total_frames_key(), -frame_count)
            self.redis_service.srem(self.request_index_key(), normalized_id)
            self.redis_service.zrem(self.expiry_index_key(), normalized_id)
            if self._redis_indexes_cleared_for_request(
                request_id=normalized_id,
                expected_total_frames=previous_total_frames - frame_count,
            ):
                self._clear_redis_dirty(dirty_version)
            return deleted
        self._memory_expiry.pop(normalized_id, None)
        frames = self._memory_frames.pop(normalized_id, None) or []
        had_value = bool(frames)
        self._memory_request_ids.discard(normalized_id)
        self._memory_total_frames = max(0, self._memory_total_frames - len(frames))
        self._memory_latest_sequence.pop(normalized_id, None)
        self._memory_latest_upstream_sequence.pop(normalized_id, None)
        return 1 if had_value else 0

    def describe(self) -> dict[str, Any]:
        if self.redis_service.available:
            if self._redis_dirty():
                self._rebuild_redis_indexes()
            self._prune_redis()
            requests_tracked = self.redis_service.scard(self.request_index_key())
            frames_tracked = max(0, self.redis_service.get_int(self.total_frames_key(), default=0))
        else:
            self._prune_memory_frames()
            requests_tracked = len(self._memory_request_ids)
            frames_tracked = self._memory_total_frames
        return {
            "available": bool(self.redis_service.available),
            "storage_mode": "redis" if self.redis_service.available else "memory_fallback",
            "frames_key_example": self.frames_key("req_example"),
            "requests_tracked": requests_tracked,
            "frames_tracked": frames_tracked,
        }

    def describe_request(self, request_id: str) -> dict[str, Any]:
        normalized_id = str(request_id or "").strip()
        if self.redis_service.available:
            if self._redis_dirty():
                self._rebuild_redis_indexes()
            self._prune_redis()
            latest_sequence = max(0, self.redis_service.get_int(self.sequence_key(normalized_id), default=0))
            frames_tracked = max(0, self.redis_service.get_int(self.frame_count_key(normalized_id), default=0))
        else:
            self._prune_memory_frames()
            latest_sequence = max(0, int(self._memory_latest_sequence.get(normalized_id, 0)))
            frames_tracked = len(self._memory_frames.get(normalized_id, []))
        return {
            "request_id": normalized_id,
            "frames_tracked": frames_tracked,
            "latest_sequence": latest_sequence,
            "frames_key": self.frames_key(request_id),
            "sequence_key": self.sequence_key(request_id),
        }
