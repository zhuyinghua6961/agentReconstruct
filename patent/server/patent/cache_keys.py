from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from urllib.parse import quote


_VOLATILE_CACHE_KEYS = {"cache_hit", "negative_cache_hit", "timings"}
_VOLATILE_METADATA_KEYS = {
    "candidate_patent_ids",
    "retrieval_plan_queries",
    "localization_fallback",
    "cache_hit",
    "cache_namespace",
    "cache_fingerprint",
    "cache_stage",
    "cache_key",
    "stage_cache_hits",
}
_TABLE_SCOPED_FILE_ROUTE_SIGNATURE_KEYS = {
    "tabular_service_type",
    "tabular_answer_backend",
    "tabular_prompt_version",
    "tabular_runtime_signature",
    "tabular_max_context_chars",
    "hybrid_table_context_chars",
    "tabular_compare_tables_version",
    "tabular_compare_status_version",
    "table_parity_signature",
}
_GRAPH_KB_VOLATILE_KEYS = {"diagnostics"}


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


def _normalize_payload_for_cache(value: object) -> object:
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in sorted(value.items(), key=lambda entry: str(entry[0])):
            normalized_key = str(key)
            if normalized_key in _VOLATILE_CACHE_KEYS:
                continue
            if normalized_key == "metadata" and isinstance(item, dict):
                normalized[normalized_key] = {
                    str(meta_key): _normalize_payload_for_cache(meta_value)
                    for meta_key, meta_value in sorted(item.items(), key=lambda entry: str(entry[0]))
                    if str(meta_key) not in _VOLATILE_METADATA_KEYS
                }
                continue
            normalized[normalized_key] = _normalize_payload_for_cache(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_payload_for_cache(item) for item in value]
    return _jsonable(value)


def _normalize_retrieval_results_for_cache(value: object) -> object:
    return _normalize_payload_for_cache(value)


def _is_table_scoped_file_route(*, route: str, source_scope: str) -> bool:
    normalized_route = str(route or "").strip().lower()
    if normalized_route == "tabular_qa":
        return True
    scope_tokens = {item.strip().lower() for item in str(source_scope or "").split("+") if item.strip()}
    return "table" in scope_tokens


def _normalize_file_route_runtime_signature(
    *,
    route: str,
    source_scope: str,
    runtime_signature: dict[str, object] | None,
) -> dict[str, object]:
    normalized = dict(runtime_signature or {})
    if _is_table_scoped_file_route(route=route, source_scope=source_scope):
        return normalized
    return {
        str(key): value
        for key, value in normalized.items()
        if str(key) not in _TABLE_SCOPED_FILE_ROUTE_SIGNATURE_KEYS
    }


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(_jsonable(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_graph_kb_context_for_cache(value: object) -> object:
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, object] = {}
    for key, item in sorted(value.items(), key=lambda entry: str(entry[0])):
        normalized_key = str(key)
        if normalized_key in _GRAPH_KB_VOLATILE_KEYS:
            continue
        normalized[normalized_key] = _normalize_payload_for_cache(item)
    return normalized


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
            "graph_kb": _normalize_graph_kb_context_for_cache(context.get("graph_kb")),
            "runtime_signature": runtime_signature or {},
        }
    )


def build_stage2_cache_fingerprint(
    *,
    question: str,
    retrieval_claims: object | None = None,
    retrieval_plan: object,
    conversation_context: dict[str, object] | None = None,
    runtime_signature: dict[str, object] | None = None,
) -> str:
    context = dict(conversation_context or {})
    return _fingerprint(
        {
            "question": " ".join(str(question or "").split()),
            "retrieval_claims": retrieval_claims or [],
            "retrieval_plan": retrieval_plan,
            "graph_kb": _normalize_graph_kb_context_for_cache(context.get("graph_kb")),
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
            "retrieval_results": _normalize_payload_for_cache(retrieval_results or {}),
            "source_ids": list(source_ids or []),
            "force_pdf": bool(force_pdf),
            "runtime_signature": runtime_signature or {},
        }
    )


def build_stage4_cache_fingerprint(
    *,
    question: str,
    deep_answer: str | None = None,
    retrieval_results: dict[str, object] | None,
    patent_evidence_bundle: dict[str, object] | None,
    conversation_context: dict[str, object] | None = None,
    runtime_signature: dict[str, object] | None = None,
) -> str:
    return _fingerprint(
        {
            "question": " ".join(str(question or "").split()),
            "deep_answer": " ".join(str(deep_answer or "").split()),
            "retrieval_results": _normalize_payload_for_cache(retrieval_results or {}),
            "patent_evidence_bundle": _normalize_payload_for_cache(patent_evidence_bundle or {}),
            "conversation_context": _normalize_payload_for_cache(conversation_context or {}),
            "runtime_signature": runtime_signature or {},
        }
    )


def build_file_route_cache_fingerprint(
    *,
    question: str,
    route: str,
    source_scope: str,
    selected_file_ids: list[int] | None,
    primary_file_id: int | None,
    selected_execution_files: list[object] | None,
    file_selection: dict[str, object] | None,
    runtime_signature: dict[str, object] | None = None,
) -> str:
    return _fingerprint(
        {
            "question": " ".join(str(question or "").split()),
            "route": str(route or "").strip(),
            "source_scope": str(source_scope or "").strip(),
            "selected_file_ids": list(selected_file_ids or []),
            "primary_file_id": primary_file_id,
            "selected_execution_files": _jsonable(list(selected_execution_files or [])),
            "file_selection": _jsonable(dict(file_selection or {})),
            "runtime_signature": _normalize_file_route_runtime_signature(
                route=str(route or ""),
                source_scope=str(source_scope or ""),
                runtime_signature=runtime_signature,
            ),
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

    def file_route_cache(self, fingerprint: object) -> str:
        return self._join("qa-core", "cache", "file-route", fingerprint)

    def file_route_singleflight(self, fingerprint: object) -> str:
        return self._join("qa-core", "lock", "file-route", fingerprint)

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
