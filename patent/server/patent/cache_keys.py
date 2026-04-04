from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from urllib.parse import quote



def _normalize(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().strip(":")


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalize_retrieval_results_for_cache(value: object) -> object:
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in sorted(value.items(), key=lambda entry: str(entry[0])):
            normalized_key = str(key)
            if normalized_key in {"cache_hit", "negative_cache_hit", "timings"}:
                continue
            if normalized_key == "metadata" and isinstance(item, dict):
                normalized[normalized_key] = {
                    str(meta_key): _normalize_retrieval_results_for_cache(meta_value)
                    for meta_key, meta_value in sorted(item.items(), key=lambda entry: str(entry[0]))
                    if str(meta_key) not in {"candidate_patent_ids", "retrieval_plan_queries", "localization_fallback"}
                }
                continue
            normalized[normalized_key] = _normalize_retrieval_results_for_cache(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_retrieval_results_for_cache(item) for item in value]
    return _jsonable(value)


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(_jsonable(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_stage1_cache_fingerprint(
    *,
    question: str,
    conversation_context: dict[str, object] | None,
    runtime_signature: dict[str, object] | None,
) -> str:
    context = dict(conversation_context or {})
    return _fingerprint(
        {
            "question": " ".join(str(question or "").split()),
            "recent_turns_for_llm": context.get("recent_turns_for_llm") or [],
            "summary_for_llm": context.get("summary_for_llm") or {},
            "runtime_signature": runtime_signature or {},
        }
    )


def build_stage2_cache_fingerprint(
    *,
    question: str,
    retrieval_claims: object | None = None,
    retrieval_plan: object,
    runtime_signature: dict[str, object] | None = None,
) -> str:
    return _fingerprint(
        {
            "question": " ".join(str(question or "").split()),
            "retrieval_claims": retrieval_claims or [],
            "retrieval_plan": retrieval_plan,
            "runtime_signature": runtime_signature or {},
        }
    )


def build_stage25_cache_fingerprint(
    *,
    question: str,
    retrieval_results: dict[str, object] | None,
    source_ids: list[str] | None,
    skipped: bool = False,
    skip_reason: str = "",
    runtime_signature: dict[str, object] | None = None,
) -> str:
    return _fingerprint(
        {
            "question": " ".join(str(question or "").split()),
            "retrieval_results": _normalize_retrieval_results_for_cache(retrieval_results or {}),
            "source_ids": list(source_ids or []),
            "skipped": bool(skipped),
            "skip_reason": str(skip_reason or "").strip(),
            "runtime_signature": runtime_signature or {},
        }
    )


def build_stage3_cache_fingerprint(
    *,
    retrieval_results: dict[str, object] | None,
    source_ids: list[str] | None,
    force_pdf: bool,
    runtime_signature: dict[str, object] | None = None,
) -> str:
    return _fingerprint(
        {
            "retrieval_results": _normalize_retrieval_results_for_cache(retrieval_results or {}),
            "source_ids": list(source_ids or []),
            "force_pdf": bool(force_pdf),
            "runtime_signature": runtime_signature or {},
        }
    )


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

    def stage_cache(self, stage: object, fingerprint: object) -> str:
        return self._join("qa-core", "cache", stage, fingerprint)

    def stage_singleflight(self, stage: object, fingerprint: object) -> str:
        return self._join("qa-core", "lock", stage, fingerprint)

    def retrieval_cache(self, normalized_query_key: object) -> str:
        return self._join("retrieval", "cache", normalized_query_key)

    def negative_patent_resolve(self, raw_identifier: object) -> str:
        return self._join("negative", "patent-resolve", raw_identifier)

    def negative_retrieval(self, normalized_query_key: object) -> str:
        return self._join("negative", "retrieval", normalized_query_key)

    def original_cache(
        self,
        canonical_patent_id: object,
        section: object,
        anchor: object,
        response_format: object,
        original_version: object,
    ) -> str:
        encoded_anchor = quote(str(anchor or "").strip(), safe="")
        return self._join("original", "cache", canonical_patent_id, section, encoded_anchor, response_format, original_version)

    def inflight(self, conversation_id: int | str, trace_id: str) -> str:
        return self._join("coord", "inflight", conversation_id, trace_id)

    def pending_turn(self, conversation_id: int | str) -> str:
        return self._join("coord", "pending-turn", conversation_id)

    def overlay_assistant(self, user_id: int | str, conversation_id: int | str) -> str:
        return self._join("overlay", "assistant", user_id, conversation_id)
