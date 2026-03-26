from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any

from server.patent.cache_keys import PatentKeyFactory


def _encode_pending_turn(trace_id: str, *, user_written: bool) -> str:
    suffix = "written" if user_written else "claimed"
    return f"{str(trace_id).strip()}|{suffix}"


def _decode_pending_turn(value: str) -> dict[str, Any]:
    raw = str(value or "").strip()
    if not raw:
        return {"trace_id": "", "user_written": False}
    trace_id, sep, suffix = raw.partition("|")
    if not sep:
        return {"trace_id": raw, "user_written": False}
    return {"trace_id": trace_id.strip(), "user_written": suffix.strip() == "written"}


class ExecutionCache:
    def __init__(self, client: Any | None, key_factory: PatentKeyFactory) -> None:
        self._client = client
        self._keys = key_factory
        self.last_error = ""

    @property
    def available(self) -> bool:
        return self._client is not None

    def _set_json_value(self, key: str, *, payload: dict[str, Any], ttl_seconds: int, nx: bool = False) -> bool:
        if self._client is None:
            return False
        encoded = json.dumps(payload, ensure_ascii=True)
        return bool(self._client.set(key, encoded, ex=max(1, int(ttl_seconds)), nx=nx))

    def _get_json_value(self, key: str) -> dict[str, Any] | None:
        if self._client is None:
            return None
        value = self._client.get(key)
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return None
        try:
            decoded = json.loads(str(value))
        except (JSONDecodeError, TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None

    def _read_text_value(self, key: str) -> str:
        if self._client is None:
            return ""
        value = self._client.get(key)
        if value is None:
            return ""
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return ""
        return str(value).strip()

    def claim_turn_identity(self, *, conversation_id: int, trace_id: str, ttl_seconds: int) -> bool:
        if self._client is None:
            return False
        key = self._keys.turn(conversation_id, trace_id)
        setter = getattr(self._client, "set", None)
        if not callable(setter):
            return False
        return bool(setter(key, "1", ex=max(1, int(ttl_seconds)), nx=True))

    def clear_turn_identity(self, *, conversation_id: int, trace_id: str) -> bool:
        if self._client is None:
            return False
        key = self._keys.turn(conversation_id, trace_id)
        return bool(self._client.delete(key))

    def has_turn_identity(self, *, conversation_id: int, trace_id: str) -> bool:
        key = self._keys.turn(conversation_id, trace_id)
        return bool(self._read_text_value(key))

    def mark_turn_inflight(self, *, conversation_id: int, trace_id: str, ttl_seconds: int) -> bool:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return False
        key = self._keys.inflight(conversation_id, trace_id)
        setter = getattr(self._client, "set", None)
        if not callable(setter):
            self.last_error = "redis set helper unavailable"
            return False
        marked = bool(setter(key, "1", ex=max(1, int(ttl_seconds)), nx=True))
        self.last_error = "" if marked else "inflight marker already present"
        return marked

    def clear_turn_inflight(self, *, conversation_id: int, trace_id: str) -> bool:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return False
        key = self._keys.inflight(conversation_id, trace_id)
        cleared = bool(self._client.delete(key))
        self.last_error = "" if cleared else "inflight marker missing"
        return cleared

    def is_turn_inflight(self, *, conversation_id: int, trace_id: str) -> bool:
        key = self._keys.inflight(conversation_id, trace_id)
        return bool(self._read_text_value(key))

    def renew_turn_inflight(self, *, conversation_id: int, trace_id: str, ttl_seconds: int) -> bool:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return False
        compare_expire = getattr(self._client, "compare_expire", None)
        if not callable(compare_expire):
            self.last_error = "atomic compare_expire helper unavailable"
            return False
        key = self._keys.inflight(conversation_id, trace_id)
        try:
            renewed = bool(compare_expire(key, "1", max(1, int(ttl_seconds))))
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self.last_error = "" if renewed else "inflight renew rejected"
        return renewed

    def claim_pending_turn(self, *, conversation_id: int, trace_id: str, ttl_seconds: int, user_written: bool = False) -> bool:
        if self._client is None:
            return False
        key = self._keys.pending_turn(conversation_id)
        setter = getattr(self._client, "set", None)
        if not callable(setter):
            return False
        payload = _encode_pending_turn(trace_id, user_written=user_written)
        return bool(setter(key, payload, ex=max(1, int(ttl_seconds)), nx=True))

    def get_pending_turn_state(self, *, conversation_id: int) -> dict[str, Any]:
        key = self._keys.pending_turn(conversation_id)
        return _decode_pending_turn(self._read_text_value(key))

    def get_pending_turn(self, *, conversation_id: int) -> str:
        return str(self.get_pending_turn_state(conversation_id=conversation_id).get("trace_id") or "")

    def mark_pending_turn_user_written(self, *, conversation_id: int, trace_id: str, ttl_seconds: int) -> bool:
        if self._client is None:
            return False
        state = self.get_pending_turn_state(conversation_id=conversation_id)
        if str(state.get("trace_id") or "") != str(trace_id).strip():
            return False
        key = self._keys.pending_turn(conversation_id)
        setter = getattr(self._client, "set", None)
        if not callable(setter):
            return False
        payload = _encode_pending_turn(trace_id, user_written=True)
        return bool(setter(key, payload, ex=max(1, int(ttl_seconds)), nx=False))

    def clear_pending_turn(self, *, conversation_id: int, trace_id: str) -> bool:
        if self._client is None:
            return False
        key = self._keys.pending_turn(conversation_id)
        raw_value = self._read_text_value(key)
        state = _decode_pending_turn(raw_value)
        if str(state.get("trace_id") or "") != str(trace_id).strip():
            return False
        compare_delete = getattr(self._client, "compare_delete", None)
        if callable(compare_delete):
            return bool(compare_delete(key, raw_value))
        return bool(self._client.delete(key))

    def set_json_cache(self, *, key: str, payload: dict[str, Any], ttl_seconds: int) -> bool:
        return self._set_json_value(key, payload=payload, ttl_seconds=ttl_seconds)

    def get_json_cache(self, *, key: str) -> dict[str, Any] | None:
        return self._get_json_value(key)

    def set_execution_cache(self, *, normalized_request_key: object, payload: dict[str, Any], ttl_seconds: int) -> bool:
        key = self._keys.cache(normalized_request_key)
        return self.set_json_cache(key=key, payload=payload, ttl_seconds=ttl_seconds)

    def get_execution_cache(self, *, normalized_request_key: object) -> dict[str, Any] | None:
        key = self._keys.cache(normalized_request_key)
        return self.get_json_cache(key=key)

    def set_retrieval_cache(self, *, normalized_query_key: object, payload: dict[str, Any], ttl_seconds: int) -> bool:
        key = self._keys.retrieval_cache(normalized_query_key)
        return self.set_json_cache(key=key, payload=payload, ttl_seconds=ttl_seconds)

    def get_retrieval_cache(self, *, normalized_query_key: object) -> dict[str, Any] | None:
        key = self._keys.retrieval_cache(normalized_query_key)
        return self.get_json_cache(key=key)

    def set_turn_result(self, *, conversation_id: int, trace_id: str, payload: dict[str, Any], ttl_seconds: int) -> bool:
        return self.set_execution_cache(
            normalized_request_key=f"turn-result:{int(conversation_id)}:{str(trace_id).strip()}",
            payload=payload,
            ttl_seconds=ttl_seconds,
        )

    def get_turn_result(self, *, conversation_id: int, trace_id: str) -> dict[str, Any] | None:
        return self.get_execution_cache(
            normalized_request_key=f"turn-result:{int(conversation_id)}:{str(trace_id).strip()}",
        )

    def set_overlay_assistant(self, *, user_id: int, conversation_id: int, payload: dict[str, Any], ttl_seconds: int) -> bool:
        key = self._keys.overlay_assistant(user_id, conversation_id)
        return self._set_json_value(key, payload=payload, ttl_seconds=ttl_seconds)

    def get_overlay_assistant(self, *, user_id: int, conversation_id: int) -> dict[str, Any] | None:
        key = self._keys.overlay_assistant(user_id, conversation_id)
        return self._get_json_value(key)

    def clear_overlay_if_converged(self, *, user_id: int, conversation_id: int, assistant_trace_id: str) -> bool:
        overlay = self.get_overlay_assistant(user_id=user_id, conversation_id=conversation_id)
        if not overlay:
            return False
        if str(overlay.get("trace_id") or "") != str(assistant_trace_id):
            return False
        return self.delete_overlay_assistant(user_id=user_id, conversation_id=conversation_id)

    def delete_overlay_assistant(self, *, user_id: int, conversation_id: int) -> bool:
        if self._client is None:
            return False
        key = self._keys.overlay_assistant(user_id, conversation_id)
        return bool(self._client.delete(key))
