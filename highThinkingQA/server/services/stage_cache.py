from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, DefaultDict

import config
from agent_core.llm_client import load_prompt_template
from server.services.redis_client import RedisService, get_redis_service


_COUNTERS: DefaultDict[str, DefaultDict[str, int]] = defaultdict(lambda: defaultdict(int))
_COUNTER_LOCK = Lock()


@dataclass(frozen=True)
class RedisLockHandle:
    key: str
    token: str
    ttl_seconds: int


class RedisLockManager:
    def __init__(self, client: Any | None) -> None:
        self._client = client

    @property
    def available(self) -> bool:
        return self._client is not None

    def acquire(self, key: str, *, ttl_seconds: int) -> RedisLockHandle | None:
        if self._client is None:
            return None
        token = secrets.token_hex(16)
        try:
            acquired = self._client.set(str(key), token, ex=max(1, int(ttl_seconds)), nx=True)
        except Exception:
            return None
        if not acquired:
            return None
        return RedisLockHandle(key=str(key), token=token, ttl_seconds=max(1, int(ttl_seconds)))

    def release(self, handle: RedisLockHandle | None) -> bool:
        if self._client is None or handle is None:
            return False
        try:
            current = self._client.get(handle.key)
        except Exception:
            return False
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        if str(current or "") != handle.token:
            return False
        try:
            return bool(self._client.delete(handle.key))
        except Exception:
            return False


def increment_cache_metric(namespace: str, metric: str, value: int = 1) -> None:
    with _COUNTER_LOCK:
        _COUNTERS[str(namespace or "all")][str(metric or "unknown")] += int(value or 0)


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _cache_epoch() -> str:
    return str(os.getenv("HT_QA_CACHE_EPOCH", "0") or "0").strip() or "0"


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _hash_payload(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prompt_hash(template_name: str) -> str:
    try:
        return hashlib.sha256(str(load_prompt_template(template_name) or "").encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "unknown"


def _direct_answer_ttl() -> int:
    return _env_int("HT_QA_DIRECT_CACHE_TTL_SECONDS", 3600, minimum=60, maximum=86400)


def _decompose_ttl() -> int:
    return _env_int("HT_QA_DECOMPOSE_CACHE_TTL_SECONDS", 3600, minimum=60, maximum=86400)


def _retrieve_ttl() -> int:
    return _env_int("HT_QA_RETRIEVE_CACHE_TTL_SECONDS", 1800, minimum=60, maximum=86400)


def _cache_lock_enabled() -> bool:
    return _env_bool("HT_QA_CACHE_LOCK_ENABLED", True)


def _cache_wait_ms() -> int:
    return _env_int("HT_QA_CACHE_WAIT_MS", 400, minimum=0, maximum=5000)


def _cache_lock_ttl_seconds() -> int:
    return _env_int("HT_QA_CACHE_LOCK_TTL_SECONDS", 30, minimum=1, maximum=600)


def run_singleflight(
    *,
    redis_service: RedisService | None,
    lock_key: str,
    namespace: str,
    read_cached_fn: Callable[[], Any],
    compute_fn: Callable[[], Any],
) -> Any:
    if redis_service is None or not redis_service.available or not _cache_lock_enabled():
        increment_cache_metric(namespace, "lock_skipped")
        return compute_fn()
    lock_manager = RedisLockManager(redis_service.client)
    if not lock_manager.available:
        increment_cache_metric(namespace, "lock_skipped")
        return compute_fn()
    handle = lock_manager.acquire(lock_key, ttl_seconds=_cache_lock_ttl_seconds())
    if handle is not None:
        increment_cache_metric(namespace, "lock_acquired")
        try:
            return compute_fn()
        finally:
            lock_manager.release(handle)
    deadline = time.monotonic() + (_cache_wait_ms() / 1000.0)
    while time.monotonic() < deadline:
        cached = read_cached_fn()
        if cached is not None:
            increment_cache_metric(namespace, "lock_wait_hit")
            return cached
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.05, remaining))
    increment_cache_metric(namespace, "lock_fallback_compute")
    return compute_fn()


def _direct_answer_key(*, redis_service: RedisService, question: str, model: str, enable_thinking: bool | None) -> str:
    return redis_service.key_factory.cache(
        "direct_answer",
        _cache_epoch(),
        str(model or ""),
        int(bool(enable_thinking)),
        _prompt_hash("direct_answer.txt"),
        _hash_payload(_normalized_text(question)),
    )


def _decompose_key(*, redis_service: RedisService, question: str, model: str, enable_thinking: bool | None, num_sub_questions: int) -> str:
    return redis_service.key_factory.cache(
        "decompose",
        _cache_epoch(),
        str(model or ""),
        int(bool(enable_thinking)),
        int(num_sub_questions),
        _prompt_hash("decompose.txt"),
        _hash_payload(_normalized_text(question)),
    )


def _retrieve_query_key(*, redis_service: RedisService, query: str, top_k: int | None) -> str:
    return redis_service.key_factory.cache(
        "retrieve",
        _cache_epoch(),
        str(config.CHROMA_PERSIST_DIR or ""),
        str(config.CHROMA_COLLECTION_NAME or ""),
        int(top_k or config.RETRIEVAL_TOP_K),
        _hash_payload(_normalized_text(query)),
    )


def _retrieve_query_lock_key(*, redis_service: RedisService, query: str, top_k: int | None) -> str:
    return redis_service.key_factory.lock(
        "retrieve",
        _cache_epoch(),
        str(config.CHROMA_PERSIST_DIR or ""),
        str(config.CHROMA_COLLECTION_NAME or ""),
        int(top_k or config.RETRIEVAL_TOP_K),
        _hash_payload(_normalized_text(query)),
    )


def get_cached_direct_answer(*, redis_service: RedisService | None, question: str, model: str, enable_thinking: bool | None) -> str | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(_direct_answer_key(redis_service=redis_service, question=question, model=model, enable_thinking=enable_thinking), default=None)
    if not isinstance(payload, dict):
        return None
    answer = str(payload.get("answer") or "")
    return answer or None


def cache_direct_answer(*, redis_service: RedisService | None, question: str, model: str, enable_thinking: bool | None, answer: str) -> bool:
    if redis_service is None or not redis_service.available or not str(answer or "").strip():
        return False
    return bool(
        redis_service.set_json(
            _direct_answer_key(redis_service=redis_service, question=question, model=model, enable_thinking=enable_thinking),
            {"answer": str(answer or "")},
            ttl_seconds=_direct_answer_ttl(),
        )
    )


def get_cached_decompose(*, redis_service: RedisService | None, question: str, model: str, enable_thinking: bool | None, num_sub_questions: int) -> list[str] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(
        _decompose_key(
            redis_service=redis_service,
            question=question,
            model=model,
            enable_thinking=enable_thinking,
            num_sub_questions=num_sub_questions,
        ),
        default=None,
    )
    if not isinstance(payload, dict):
        return None
    questions = payload.get("sub_questions") if isinstance(payload.get("sub_questions"), list) else None
    if not questions:
        return None
    return [str(item) for item in questions if str(item or "").strip()]


def cache_decompose(*, redis_service: RedisService | None, question: str, model: str, enable_thinking: bool | None, num_sub_questions: int, sub_questions: list[str]) -> bool:
    normalized = [str(item) for item in list(sub_questions or []) if str(item or "").strip()]
    if redis_service is None or not redis_service.available or not normalized:
        return False
    return bool(
        redis_service.set_json(
            _decompose_key(
                redis_service=redis_service,
                question=question,
                model=model,
                enable_thinking=enable_thinking,
                num_sub_questions=num_sub_questions,
            ),
            {"sub_questions": normalized},
            ttl_seconds=_decompose_ttl(),
        )
    )


def get_cached_retrieve_query(*, redis_service: RedisService | None, query: str, top_k: int | None) -> list[dict[str, Any]] | None:
    if redis_service is None or not redis_service.available or not str(query or "").strip():
        return None
    payload = redis_service.get_json(_retrieve_query_key(redis_service=redis_service, query=query, top_k=top_k), default=None)
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, list):
        return None
    return [dict(chunk) for chunk in results if isinstance(chunk, dict)]


def cache_retrieve_query(*, redis_service: RedisService | None, query: str, top_k: int | None, results: list[dict[str, Any]]) -> bool:
    if redis_service is None or not redis_service.available or not str(query or "").strip():
        return False
    normalized = [dict(chunk) for chunk in results if isinstance(chunk, dict)]
    return bool(
        redis_service.set_json(
            _retrieve_query_key(redis_service=redis_service, query=query, top_k=top_k),
            {"results": normalized},
            ttl_seconds=_retrieve_ttl(),
        )
    )


def get_or_compute_direct_answer(*, question: str, model: str, enable_thinking: bool | None, compute_fn: Callable[[], str]) -> str:
    redis_service = get_redis_service()
    cached = get_cached_direct_answer(redis_service=redis_service, question=question, model=model, enable_thinking=enable_thinking)
    if cached is not None:
        increment_cache_metric("direct_answer", "cache_hit")
        return cached
    increment_cache_metric("direct_answer", "cache_miss")

    def _compute() -> str:
        answer = str(compute_fn() or "")
        cache_direct_answer(redis_service=redis_service, question=question, model=model, enable_thinking=enable_thinking, answer=answer)
        return answer

    return run_singleflight(
        redis_service=redis_service,
        lock_key=redis_service.key_factory.lock("direct_answer", _cache_epoch(), str(model or ""), _hash_payload(_normalized_text(question))) if redis_service and redis_service.available else "",
        namespace="direct_answer",
        read_cached_fn=lambda: get_cached_direct_answer(redis_service=redis_service, question=question, model=model, enable_thinking=enable_thinking),
        compute_fn=_compute,
    )


def get_or_compute_decompose(*, question: str, model: str, enable_thinking: bool | None, num_sub_questions: int, compute_fn: Callable[[], list[str]]) -> list[str]:
    redis_service = get_redis_service()
    cached = get_cached_decompose(
        redis_service=redis_service,
        question=question,
        model=model,
        enable_thinking=enable_thinking,
        num_sub_questions=num_sub_questions,
    )
    if cached is not None:
        increment_cache_metric("decompose", "cache_hit")
        return cached
    increment_cache_metric("decompose", "cache_miss")

    def _compute() -> list[str]:
        sub_questions = [str(item) for item in list(compute_fn() or []) if str(item or "").strip()]
        cache_decompose(
            redis_service=redis_service,
            question=question,
            model=model,
            enable_thinking=enable_thinking,
            num_sub_questions=num_sub_questions,
            sub_questions=sub_questions,
        )
        return sub_questions

    return run_singleflight(
        redis_service=redis_service,
        lock_key=redis_service.key_factory.lock("decompose", _cache_epoch(), str(model or ""), int(num_sub_questions), _hash_payload(_normalized_text(question))) if redis_service and redis_service.available else "",
        namespace="decompose",
        read_cached_fn=lambda: get_cached_decompose(
            redis_service=redis_service,
            question=question,
            model=model,
            enable_thinking=enable_thinking,
            num_sub_questions=num_sub_questions,
        ),
        compute_fn=_compute,
    )


def get_or_compute_retrieve_query(*, query: str, top_k: int | None, compute_fn: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    redis_service = get_redis_service()
    cached = get_cached_retrieve_query(redis_service=redis_service, query=query, top_k=top_k)
    if cached is not None:
        increment_cache_metric("retrieve", "cache_hit")
        return cached
    increment_cache_metric("retrieve", "cache_miss")

    def _compute() -> list[dict[str, Any]]:
        results = [dict(item) for item in list(compute_fn() or []) if isinstance(item, dict)]
        cache_retrieve_query(redis_service=redis_service, query=query, top_k=top_k, results=results)
        return results

    return run_singleflight(
        redis_service=redis_service,
        lock_key=_retrieve_query_lock_key(redis_service=redis_service, query=query, top_k=top_k) if redis_service and redis_service.available else "",
        namespace="retrieve",
        read_cached_fn=lambda: get_cached_retrieve_query(redis_service=redis_service, query=query, top_k=top_k),
        compute_fn=_compute,
    )
