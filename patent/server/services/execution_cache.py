from __future__ import annotations

import json
import uuid
from json import JSONDecodeError
from dataclasses import asdict, is_dataclass
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


def _normalize_overlay_items(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("items") if isinstance(payload.get("items"), list) else [payload]
    normalized: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        trace_id = str(item.get("trace_id") or "").strip()
        assistant_content = str(item.get("assistant_content") or "").strip()
        route = str(item.get("route") or "").strip()
        if not trace_id or not assistant_content:
            continue
        normalized.append(
            {
                "trace_id": trace_id,
                "assistant_content": assistant_content,
                "route": route,
            }
        )
    return normalized


def _json_compatible(value: Any) -> Any:
    if is_dataclass(value):
        return _json_compatible(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class ExecutionCache:
    def __init__(self, client: Any | None, key_factory: PatentKeyFactory) -> None:
        self._client = client
        self._keys = key_factory
        self.last_error = ""

    @property
    def available(self) -> bool:
        return self._client is not None

    def coordination_ready(self) -> bool:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return False
        if not callable(getattr(self._client, "set", None)):
            self.last_error = "redis set helper unavailable"
            return False
        if not callable(getattr(self._client, "compare_delete", None)):
            self.last_error = "atomic compare_delete helper unavailable"
            return False
        if not callable(getattr(self._client, "compare_expire", None)):
            self.last_error = "atomic compare_expire helper unavailable"
            return False
        if not callable(getattr(self._client, "compare_set", None)):
            self.last_error = "atomic compare_set helper unavailable"
            return False
        self.last_error = ""
        return True

    def _set_json_value(self, key: str, *, payload: dict[str, Any], ttl_seconds: int, nx: bool = False) -> bool:
        if self._client is None:
            return False
        encoded = json.dumps(_json_compatible(payload), ensure_ascii=True)
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
            self.last_error = "redis client unavailable"
            return False
        key = self._keys.pending_turn(conversation_id)
        raw_value = self._read_text_value(key)
        state = _decode_pending_turn(raw_value)
        if str(state.get("trace_id") or "") != str(trace_id).strip():
            self.last_error = "pending turn marker mismatch"
            return False
        compare_delete = getattr(self._client, "compare_delete", None)
        if not callable(compare_delete):
            self.last_error = "atomic compare_delete helper unavailable"
            return False
        cleared = bool(compare_delete(key, raw_value))
        self.last_error = "" if cleared else "pending turn clear rejected"
        return cleared

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

    def set_stage_cache(self, *, stage: str, fingerprint: str, payload: dict[str, Any], ttl_seconds: int) -> bool:
        key = self._keys.stage_cache(stage, fingerprint)
        return self.set_json_cache(key=key, payload=payload, ttl_seconds=ttl_seconds)

    def get_stage_cache(self, *, stage: str, fingerprint: str) -> dict[str, Any] | None:
        key = self._keys.stage_cache(stage, fingerprint)
        return self.get_json_cache(key=key)

    def _claim_singleflight(self, *, key: str, ttl_seconds: int, already_held_error: str) -> str:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return ""
        setter = getattr(self._client, "set", None)
        if not callable(setter):
            self.last_error = "redis set helper unavailable"
            return ""
        token = uuid.uuid4().hex
        claimed = bool(setter(key, token, ex=max(1, int(ttl_seconds)), nx=True))
        self.last_error = "" if claimed else already_held_error
        return token if claimed else ""

    def _renew_singleflight(self, *, key: str, token: str, ttl_seconds: int, renew_rejected_error: str) -> bool:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return False
        compare_expire = getattr(self._client, "compare_expire", None)
        if not callable(compare_expire):
            self.last_error = "atomic compare_expire helper unavailable"
            return False
        try:
            renewed = bool(compare_expire(key, str(token or ""), max(1, int(ttl_seconds))))
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self.last_error = "" if renewed else renew_rejected_error
        return renewed

    def _clear_singleflight(self, *, key: str, token: str, clear_rejected_error: str) -> bool:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return False
        compare_delete = getattr(self._client, "compare_delete", None)
        if not callable(compare_delete):
            self.last_error = "atomic compare_delete helper unavailable"
            return False
        try:
            cleared = bool(compare_delete(key, str(token or "")))
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self.last_error = "" if cleared else clear_rejected_error
        return cleared

    def claim_stage_singleflight(self, *, stage: str, fingerprint: str, ttl_seconds: int) -> str:
        key = self._keys.stage_singleflight(stage, fingerprint)
        return self._claim_singleflight(
            key=key,
            ttl_seconds=ttl_seconds,
            already_held_error="stage singleflight already held",
        )

    def get_stage_singleflight_owner(self, *, stage: str, fingerprint: str) -> str:
        key = self._keys.stage_singleflight(stage, fingerprint)
        return self._read_text_value(key)

    def renew_stage_singleflight(self, *, stage: str, fingerprint: str, token: str, ttl_seconds: int) -> bool:
        key = self._keys.stage_singleflight(stage, fingerprint)
        return self._renew_singleflight(
            key=key,
            token=token,
            ttl_seconds=ttl_seconds,
            renew_rejected_error="stage singleflight renew rejected",
        )

    def clear_stage_singleflight(self, *, stage: str, fingerprint: str, token: str) -> bool:
        key = self._keys.stage_singleflight(stage, fingerprint)
        return self._clear_singleflight(
            key=key,
            token=token,
            clear_rejected_error="stage singleflight clear rejected",
        )

    def set_file_route_cache(self, *, fingerprint: str, payload: dict[str, Any], ttl_seconds: int) -> bool:
        key = self._keys.file_route_cache(fingerprint)
        return self.set_json_cache(key=key, payload=payload, ttl_seconds=ttl_seconds)

    def get_file_route_cache(self, *, fingerprint: str) -> dict[str, Any] | None:
        key = self._keys.file_route_cache(fingerprint)
        return self.get_json_cache(key=key)

    def claim_file_route_singleflight(self, *, fingerprint: str, ttl_seconds: int) -> str:
        key = self._keys.file_route_singleflight(fingerprint)
        return self._claim_singleflight(
            key=key,
            ttl_seconds=ttl_seconds,
            already_held_error="file-route singleflight already held",
        )

    def get_file_route_singleflight_owner(self, *, fingerprint: str) -> str:
        key = self._keys.file_route_singleflight(fingerprint)
        return self._read_text_value(key)

    def renew_file_route_singleflight(self, *, fingerprint: str, token: str, ttl_seconds: int) -> bool:
        key = self._keys.file_route_singleflight(fingerprint)
        return self._renew_singleflight(
            key=key,
            token=token,
            ttl_seconds=ttl_seconds,
            renew_rejected_error="file-route singleflight renew rejected",
        )

    def clear_file_route_singleflight(self, *, fingerprint: str, token: str) -> bool:
        key = self._keys.file_route_singleflight(fingerprint)
        return self._clear_singleflight(
            key=key,
            token=token,
            clear_rejected_error="file-route singleflight clear rejected",
        )

    def set_retrieval_cache(self, *, normalized_query_key: object, payload: dict[str, Any], ttl_seconds: int) -> bool:
        key = self._keys.retrieval_cache(normalized_query_key)
        return self.set_json_cache(key=key, payload=payload, ttl_seconds=ttl_seconds)

    def get_retrieval_cache(self, *, normalized_query_key: object) -> dict[str, Any] | None:
        key = self._keys.retrieval_cache(normalized_query_key)
        return self.get_json_cache(key=key)

    def set_negative_patent_resolve(self, *, raw_identifier: object, payload: dict[str, Any], ttl_seconds: int) -> bool:
        key = self._keys.negative_patent_resolve(raw_identifier)
        return self.set_json_cache(key=key, payload=payload, ttl_seconds=ttl_seconds)

    def get_negative_patent_resolve(self, *, raw_identifier: object) -> dict[str, Any] | None:
        key = self._keys.negative_patent_resolve(raw_identifier)
        return self.get_json_cache(key=key)

    def set_negative_retrieval(self, *, normalized_query_key: object, payload: dict[str, Any], ttl_seconds: int) -> bool:
        key = self._keys.negative_retrieval(normalized_query_key)
        return self.set_json_cache(key=key, payload=payload, ttl_seconds=ttl_seconds)

    def get_negative_retrieval(self, *, normalized_query_key: object) -> dict[str, Any] | None:
        key = self._keys.negative_retrieval(normalized_query_key)
        return self.get_json_cache(key=key)

    def set_original_cache(
        self,
        *,
        canonical_patent_id: str,
        section: str,
        anchor: str,
        response_format: str,
        original_version: str,
        payload: dict[str, Any],
        ttl_seconds: int,
    ) -> bool:
        key = self._keys.original_cache(canonical_patent_id, section, anchor, response_format, original_version)
        return self.set_json_cache(key=key, payload=payload, ttl_seconds=ttl_seconds)

    def get_original_cache(
        self,
        *,
        canonical_patent_id: str,
        section: str,
        anchor: str,
        response_format: str,
        original_version: str,
    ) -> dict[str, Any] | None:
        key = self._keys.original_cache(canonical_patent_id, section, anchor, response_format, original_version)
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
        if self._client is None:
            self.last_error = "redis client unavailable"
            return False
        compare_set = getattr(self._client, "compare_set", None)
        if not callable(compare_set):
            self.last_error = "atomic compare_set helper unavailable"
            return False
        key = self._keys.overlay_assistant(user_id, conversation_id)
        normalized_payload = {
            "trace_id": str(payload.get("trace_id") or "").strip(),
            "assistant_content": str(payload.get("assistant_content") or "").strip(),
            "route": str(payload.get("route") or "").strip(),
        }
        if not normalized_payload["trace_id"] or not normalized_payload["assistant_content"]:
            self.last_error = "overlay assistant payload incomplete"
            return False
        ttl = max(1, int(ttl_seconds))
        for _ in range(8):
            raw_value = self._read_text_value(key)
            existing_items: list[dict[str, Any]] = []
            if raw_value:
                try:
                    decoded = json.loads(raw_value)
                except (JSONDecodeError, TypeError, ValueError):
                    decoded = None
                existing_items = _normalize_overlay_items(decoded if isinstance(decoded, dict) else None)
            merged_items = [
                dict(item)
                for item in existing_items
                if str(item.get("trace_id") or "").strip() != normalized_payload["trace_id"]
            ]
            merged_items.append(dict(normalized_payload))
            encoded = json.dumps({"items": merged_items}, ensure_ascii=True)
            try:
                updated = bool(compare_set(key, raw_value, encoded, ttl))
            except Exception as exc:
                self.last_error = str(exc)
                return False
            if updated:
                self.last_error = ""
                return True
        self.last_error = "overlay compare_set retries exhausted"
        return False

    def get_overlay_assistant(self, *, user_id: int, conversation_id: int) -> dict[str, Any] | None:
        items = self.get_overlay_assistants(user_id=user_id, conversation_id=conversation_id)
        return dict(items[-1]) if items else None

    def get_overlay_assistants(self, *, user_id: int, conversation_id: int) -> list[dict[str, Any]]:
        key = self._keys.overlay_assistant(user_id, conversation_id)
        payload = self._get_json_value(key)
        return _normalize_overlay_items(payload)

    def get_overlay_assistant_state(self, *, user_id: int, conversation_id: int) -> tuple[list[dict[str, Any]], str]:
        key = self._keys.overlay_assistant(user_id, conversation_id)
        raw_value = self._read_text_value(key)
        if not raw_value:
            return [], ""
        try:
            payload = json.loads(raw_value)
        except (JSONDecodeError, TypeError, ValueError):
            return [], raw_value
        return _normalize_overlay_items(payload if isinstance(payload, dict) else None), raw_value

    def clear_overlay_if_converged(self, *, user_id: int, conversation_id: int, assistant_trace_id: str) -> bool:
        overlays, raw_value = self.get_overlay_assistant_state(user_id=user_id, conversation_id=conversation_id)
        if len(overlays) != 1:
            return False
        overlay = overlays[0]
        if str(overlay.get("trace_id") or "") != str(assistant_trace_id):
            return False
        return self.delete_overlay_assistant_if_unchanged(
            user_id=user_id,
            conversation_id=conversation_id,
            raw_value=raw_value,
        )

    def delete_overlay_assistant(self, *, user_id: int, conversation_id: int) -> bool:
        if self._client is None:
            return False
        key = self._keys.overlay_assistant(user_id, conversation_id)
        return bool(self._client.delete(key))

    def delete_overlay_assistant_if_unchanged(self, *, user_id: int, conversation_id: int, raw_value: str) -> bool:
        if self._client is None:
            self.last_error = "redis client unavailable"
            return False
        compare_delete = getattr(self._client, "compare_delete", None)
        if not callable(compare_delete):
            self.last_error = "atomic compare_delete helper unavailable"
            return False
        key = self._keys.overlay_assistant(user_id, conversation_id)
        deleted = bool(compare_delete(key, str(raw_value or "")))
        self.last_error = "" if deleted else "overlay compare_delete rejected"
        return deleted
