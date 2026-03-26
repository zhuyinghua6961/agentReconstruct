from __future__ import annotations

from dataclasses import dataclass



def _normalize(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().strip(":")


@dataclass(frozen=True)
class PatentKeyFactory:
    env: str
    prefix: str = "patent"

    def _join(self, *segments: object) -> str:
        items = [_normalize(self.prefix), _normalize(self.env)]
        for segment in segments:
            normalized = _normalize(segment)
            if normalized:
                items.append(normalized)
        return ":".join(item for item in items if item)

    def conversation_lock(self, conversation_id: int | str) -> str:
        return self._join("exec", "conversation-lock", conversation_id)

    def turn(self, conversation_id: int | str, trace_id: str) -> str:
        return self._join("exec", "turn", conversation_id, trace_id)

    def cache(self, normalized_request_key: object) -> str:
        return self._join("exec", "cache", normalized_request_key)

    def retrieval_cache(self, normalized_query_key: object) -> str:
        return self._join("retrieval", "cache", normalized_query_key)

    def inflight(self, conversation_id: int | str, trace_id: str) -> str:
        return self._join("coord", "inflight", conversation_id, trace_id)

    def pending_turn(self, conversation_id: int | str) -> str:
        return self._join("coord", "pending-turn", conversation_id)

    def overlay_assistant(self, user_id: int | str, conversation_id: int | str) -> str:
        return self._join("overlay", "assistant", user_id, conversation_id)
