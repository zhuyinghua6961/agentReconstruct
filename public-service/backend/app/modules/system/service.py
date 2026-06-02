from __future__ import annotations

import logging
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

from app.core.timezone import BEIJING_TIMEZONE, now_beijing_iso
from typing import Any, Callable

from app.core.runtime import PublicServiceRuntime
from app.modules.conversation.cache import (
    build_conversation_detail_cache_key,
    build_conversation_list_cache_key,
    build_conversation_list_recent_pages_key,
    get_conversation_detail_cache_version,
    get_conversation_list_cache_version,
    get_recent_conversation_list_pages,
)
from app.modules.qa_cache.metrics import snapshot_cache_metrics
from app.modules.system.upstream_auth_logging import (
    log_upstream_auth_failure,
    log_upstream_auth_success_once,
)


logger = logging.getLogger(__name__)

RequesterFn = Callable[..., dict[str, Any]]
MODEL_STATUS_TEST_TEXT = "hello"
MODEL_STATUS_TEST_TIMEOUT_SECONDS = 30.0


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return str(default or "").strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _normalize_bearer_api_key(api_key: str | None) -> str:
    value = str(api_key or "").strip()
    scheme, separator, token = value.partition(" ")
    if separator and scheme.lower() == "bearer":
        return token.strip()
    return value


def _key_fingerprint(api_key: str | None) -> str:
    value = _normalize_bearer_api_key(api_key)
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _key_input_has_bearer(api_key: str | None) -> bool:
    return str(api_key or "").strip().lower().startswith("bearer ")


def _auth_headers(api_key: str | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    key = _normalize_bearer_api_key(api_key)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _normalize_chat_endpoint(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    if not value.endswith("/v1"):
        value = value.rstrip("/") + "/v1"
    return value.rstrip("/") + "/chat/completions"


def _normalize_embedding_endpoint(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    for suffix in ("/v1/embeddings", "/embeddings"):
        if value.endswith(suffix):
            return value
    if value.endswith("/v1"):
        return value + "/embeddings"
    return value + "/v1/embeddings"


def _normalize_rerank_endpoint(base_url: str, provider: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    provider_norm = str(provider or "").strip().lower()
    if provider_norm == "dashscope":
        suffix = "/api/v1/services/rerank/text-rerank/text-rerank"
        if value.endswith(suffix):
            return value
        return value + suffix
    for suffix in ("/v1/rerank", "/rerank"):
        if value.endswith(suffix):
            return value
    return value + "/v1/rerank"


def _endpoint_url_for_spec(spec: dict[str, Any]) -> str:
    base_url = str(spec.get("base_url") or "").strip()
    if not base_url:
        return ""
    kind = str(spec.get("kind") or "").strip().lower()
    if kind == "chat":
        return _normalize_chat_endpoint(base_url)
    if kind == "embedding":
        return _normalize_embedding_endpoint(base_url)
    if kind == "rerank":
        return _normalize_rerank_endpoint(base_url, str(spec.get("provider") or "local"))
    return base_url


def _model_status_specs() -> list[dict[str, Any]]:
    return [
        {
            "id": "llm_chat",
            "label": "主大模型",
            "kind": "chat",
            "base_url": _first_env("LLM_BASE_URL", "OPENAI_BASE_URL", "DASHSCOPE_BASE_URL"),
            "model": _first_env("LLM_MODEL", "OPENAI_MODEL"),
            "api_key": _first_env("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"),
            "enabled": True,
            "provider": "openai_compatible",
        },
        {
            "id": "intent_chat",
            "label": "意图模型",
            "kind": "chat",
            "base_url": _first_env(
                "INTENT_MODEL_BASE_URL",
                "LLM_BASE_URL",
                "OPENAI_BASE_URL",
                "DASHSCOPE_BASE_URL",
                default="https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            "model": _first_env("INTENT_MODEL", "QA_INTENT_DETECT_MODEL", default="qwen3-8b"),
            "api_key": _first_env("INTENT_MODEL_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"),
            "enabled": _env_bool("INTENT_MODEL_ENABLED", False),
            "provider": "openai_compatible",
            "test_payload_overrides": {
                "temperature": 0.0,
                "max_tokens": 64,
                "enable_thinking": False,
            },
        },
        {
            "id": "fastqa_embedding",
            "label": "FastQA 向量模型",
            "kind": "embedding",
            "base_url": _first_env("QA_EMBEDDING_BASE_URL", "EMBEDDING_API_URL"),
            "model": _first_env("QA_EMBEDDING_MODEL", "EMBEDDING_API_MODEL", "EMBEDDING_MODEL_NAME"),
            "api_key": _first_env("QA_EMBEDDING_API_KEY", "EMBEDDING_API_KEY"),
            "enabled": True,
            "provider": "openai_compatible",
        },
        {
            "id": "highthinkingqa_embedding",
            "label": "HighThinkingQA 向量模型",
            "kind": "embedding",
            "base_url": _first_env("HIGHTHINKINGQA_EMBEDDING_BASE_URL"),
            "model": _first_env("HIGHTHINKINGQA_EMBEDDING_MODEL"),
            "api_key": _first_env("HIGHTHINKINGQA_EMBEDDING_API_KEY"),
            "enabled": True,
            "provider": "openai_compatible",
        },
        {
            "id": "rerank",
            "label": "重排模型",
            "kind": "rerank",
            "base_url": _first_env("RERANK_BASE_URL"),
            "model": _first_env("RERANK_MODEL"),
            "api_key": _first_env("RERANK_API_KEY"),
            "enabled": _first_env("RERANK_PROVIDER", default="none").lower() not in {"", "none", "disabled", "off"},
            "provider": _first_env("RERANK_PROVIDER", default="none").lower(),
        },
    ]


def _public_endpoint_spec(spec: dict[str, Any]) -> dict[str, Any]:
    base_url = str(spec.get("base_url") or "").strip()
    model = str(spec.get("model") or "").strip()
    enabled = bool(spec.get("enabled"))
    endpoint_url = _endpoint_url_for_spec(spec)
    configured = bool(enabled and base_url and model and endpoint_url)
    api_key = str(spec.get("api_key") or "")
    status = "configured" if configured else "disabled" if not enabled else "unconfigured"
    return {
        "id": str(spec.get("id") or ""),
        "label": str(spec.get("label") or ""),
        "kind": str(spec.get("kind") or ""),
        "provider": str(spec.get("provider") or ""),
        "model": model,
        "base_url": base_url,
        "endpoint_url": endpoint_url,
        "configured": configured,
        "enabled": enabled,
        "status": status,
        "test_supported": configured,
        "api_key_present": bool(_normalize_bearer_api_key(api_key)),
        "api_key_input_has_bearer": _key_input_has_bearer(api_key),
        "key_fingerprint": _key_fingerprint(api_key),
    }


def _http_post_json(*, url: str, headers: dict[str, str], payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=float(timeout_seconds)) as response:
            raw_body = response.read(65536)
            status_code = int(response.getcode() or 0)
    except urllib.error.HTTPError as exc:
        raw_body = exc.read(65536)
        return {
            "status_code": int(exc.code or 0),
            "json": _safe_json_loads(raw_body),
            "text": _decode_body(raw_body),
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "status_code": 0,
            "json": None,
            "text": "",
            "error": str(exc),
        }
    return {
        "status_code": status_code,
        "json": _safe_json_loads(raw_body),
        "text": _decode_body(raw_body),
        "error": "",
    }


def _decode_body(raw_body: bytes | str | None) -> str:
    if raw_body is None:
        return ""
    if isinstance(raw_body, str):
        return raw_body
    try:
        return raw_body.decode("utf-8", errors="replace")
    except Exception:
        return str(raw_body)


def _safe_json_loads(raw_body: bytes | str | None) -> Any:
    text = _decode_body(raw_body).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _response_preview(data: Any, text: str) -> str:
    if data is not None:
        try:
            text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(data)
    value = str(text or "").strip()
    if len(value) > 500:
        return value[:500] + "..."
    return value


def _chat_test_payload(model: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": MODEL_STATUS_TEST_TEXT}],
        "stream": False,
        "max_tokens": 16,
    }
    if overrides:
        payload.update(overrides)
    return payload


def _embedding_test_payload(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "input": [MODEL_STATUS_TEST_TEXT],
    }


def _rerank_test_payload(model: str, provider: str) -> dict[str, Any]:
    provider_norm = str(provider or "").strip().lower()
    if provider_norm == "dashscope":
        return {
            "model": model,
            "input": {
                "query": MODEL_STATUS_TEST_TEXT,
                "documents": [MODEL_STATUS_TEST_TEXT, "hello world"],
            },
            "parameters": {
                "return_documents": False,
                "top_n": 1,
            },
        }
    return {
        "model": model,
        "query": MODEL_STATUS_TEST_TEXT,
        "documents": [MODEL_STATUS_TEST_TEXT, "hello world"],
        "top_n": 1,
    }


def _test_payload_for_spec(spec: dict[str, Any]) -> dict[str, Any]:
    model = str(spec.get("model") or "").strip()
    kind = str(spec.get("kind") or "").strip().lower()
    if kind == "chat":
        overrides = spec.get("test_payload_overrides")
        return _chat_test_payload(model, overrides if isinstance(overrides, dict) else None)
    if kind == "embedding":
        return _embedding_test_payload(model)
    if kind == "rerank":
        return _rerank_test_payload(model, str(spec.get("provider") or "local"))
    return {"model": model, "input": MODEL_STATUS_TEST_TEXT}


def _response_has_model_result(kind: str, data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    kind_norm = str(kind or "").strip().lower()
    if kind_norm == "chat":
        choices = data.get("choices")
        return isinstance(choices, list) and len(choices) > 0
    if kind_norm == "embedding":
        if isinstance(data.get("data"), list) and len(data["data"]) > 0:
            return True
        return isinstance(data.get("embeddings"), list) or isinstance(data.get("embedding"), list)
    if kind_norm == "rerank":
        if isinstance(data.get("results"), list):
            return True
        output = data.get("output")
        return isinstance(output, dict) and isinstance(output.get("results"), list)
    return True


class SystemService:
    @staticmethod
    def _ttl_or_none(runtime: PublicServiceRuntime, key: str) -> int | None:
        redis_service = runtime.redis_service
        if redis_service is None:
            return None
        ttl = redis_service.ttl(key)
        return ttl if isinstance(ttl, int) else None

    @staticmethod
    def _cache_status() -> dict[str, Any]:
        return {
            "metrics": snapshot_cache_metrics(),
            "config": {
                "lock_enabled": str(os.getenv("QA_CACHE_LOCK_ENABLED", "1") or "1").strip(),
                "wait_ms": str(os.getenv("QA_CACHE_WAIT_MS", "400") or "400").strip(),
                "lock_ttl_seconds": str(os.getenv("QA_CACHE_LOCK_TTL_SECONDS", "30") or "30").strip(),
                "stage1_ttl_seconds": str(os.getenv("QA_STAGE1_CACHE_TTL_SECONDS", "3600") or "3600").strip(),
                "stage2_ttl_seconds": str(os.getenv("QA_STAGE2_CACHE_TTL_SECONDS", "1800") or "1800").strip(),
                "pdf_text_ttl_seconds": str(os.getenv("PDF_TEXT_CACHE_TTL_SECONDS", "86400") or "86400").strip(),
                "conversation_list_ttl_seconds": str(os.getenv("CONVERSATION_LIST_CACHE_TTL_SECONDS", "60") or "60").strip(),
                "conversation_detail_ttl_seconds": str(os.getenv("CONVERSATION_DETAIL_CACHE_TTL_SECONDS", "30") or "30").strip(),
                "conversation_detail_touch_on_hit": str(os.getenv("CONVERSATION_DETAIL_CACHE_TOUCH_ON_HIT", "1") or "1").strip(),
                "conversation_list_recent_pages_ttl_seconds": str(os.getenv("CONVERSATION_LIST_RECENT_PAGES_TTL_SECONDS", "900") or "900").strip(),
                "conversation_list_recent_pages_limit": str(os.getenv("CONVERSATION_LIST_RECENT_PAGES_LIMIT", "8") or "8").strip(),
            },
        }

    def build_health(self, runtime: PublicServiceRuntime) -> dict[str, Any]:
        component_status = dict(runtime.component_status or {})
        component_states = [str((item or {}).get("status") or "").strip().lower() for item in component_status.values()]
        overall_status = "healthy"
        if any(state == "degraded" for state in component_states):
            overall_status = "degraded"
        elif any(state in {"pending", "skeleton"} for state in component_states):
            overall_status = "starting"
        return {
            "status": overall_status,
            "agent_initialized": (
                runtime.agent is not None
                or runtime.vector_collection is not None
                or bool(getattr(runtime.neo4j_client, "available", False))
            ),
            "generation_runtime_initialized": runtime.generation_runtime is not None,
            "vector_db_initialized": runtime.vector_db_client is not None,
            "storage_backend": str(((runtime.component_status or {}).get("storage") or {}).get("backend") or ""),
            "components": component_status,
            "qa_cache": self._cache_status(),
            "timestamp": now_beijing_iso(),
        }

    def build_background_status(self, runtime: PublicServiceRuntime) -> tuple[dict[str, Any], int]:
        try:
            outbox_thread = runtime.conversation_outbox_thread
            outbox_status = dict(runtime.conversation_outbox_status or {})
            outbox_status["thread_alive"] = bool(outbox_thread.is_alive()) if outbox_thread is not None and hasattr(outbox_thread, "is_alive") else bool(outbox_status.get("thread_alive"))
            if not outbox_status:
                outbox_status = {
                    "state": "uninitialized",
                    "thread_alive": False,
                    "loops": 0,
                    "last_summary": None,
                    "last_error": "",
                    "last_run_at": None,
                }

            upload_status = dict(((runtime.component_status or {}).get("upload_processing") or {}))
            upload_worker = getattr(runtime, "upload_processing_worker", None)
            if upload_worker is not None:
                upload_status.setdefault("enabled", bool(getattr(upload_worker, "enabled", True)))
                active_keys = getattr(upload_worker, "_active_keys", None)
                if isinstance(active_keys, set):
                    upload_status["active_tasks"] = len(active_keys)

            assistant_inbox_status = dict(getattr(runtime, "authority_assistant_inbox_status", {}) or {})
            assistant_inbox_thread = getattr(runtime, "authority_assistant_inbox_thread", None)
            assistant_inbox_status["thread_alive"] = bool(assistant_inbox_thread.is_alive()) if assistant_inbox_thread is not None and hasattr(assistant_inbox_thread, "is_alive") else bool(assistant_inbox_status.get("thread_alive"))
            if not assistant_inbox_status:
                assistant_inbox_status = {
                    "state": "uninitialized",
                    "thread_alive": False,
                    "loops": 0,
                    "last_summary": None,
                    "last_error": "",
                    "last_run_at": None,
                    "backlog": 0,
                    "processing": 0,
                    "failed": 0,
                    "enabled": True,
                }

            status = {
                "has_current_answer_context": bool(runtime.current_answer_context and runtime.current_answer_context.strip()),
                "current_answer_preview": (runtime.current_answer_context[:500] + "...") if runtime.current_answer_context else "",
                "latest_background_file": None,
                "latest_background_file_mtime": None,
                "conversation_outbox": outbox_status,
                "authority_assistant_inbox": assistant_inbox_status,
                "upload_processing": upload_status,
                "qa_cache": self._cache_status(),
            }

            logs_dir = runtime.logs_dir
            if logs_dir.exists() and logs_dir.is_dir():
                files = sorted(
                    logs_dir.glob("background_programmatic_insert_*.json"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                if files:
                    latest = files[0]
                    status["latest_background_file"] = str(latest)
                    status["latest_background_file_mtime"] = datetime.fromtimestamp(latest.stat().st_mtime, tz=BEIJING_TIMEZONE).isoformat(timespec="seconds")

            return {"success": True, "status": status}, 200
        except Exception as exc:
            logger.warning("Failed to read background status: %s", exc)
            return {"success": False, "error": str(exc)}, 500

    def build_model_status(self) -> tuple[dict[str, Any], int]:
        endpoints = [_public_endpoint_spec(spec) for spec in _model_status_specs()]
        summary = {
            "total": len(endpoints),
            "configured": sum(1 for item in endpoints if item["status"] == "configured"),
            "unconfigured": sum(1 for item in endpoints if item["status"] == "unconfigured"),
            "disabled": sum(1 for item in endpoints if item["status"] == "disabled"),
            "test_supported": sum(1 for item in endpoints if item["test_supported"]),
        }
        return {
            "success": True,
            "data": {
                "checked_at": now_beijing_iso(),
                "probe_method": "config_only",
                "test_method": "click_to_send_minimal_request",
                "summary": summary,
                "endpoints": endpoints,
            },
        }, 200

    def test_model_status_endpoint(
        self,
        endpoint_id: str,
        *,
        requester: RequesterFn | None = None,
    ) -> tuple[dict[str, Any], int]:
        endpoint_id = str(endpoint_id or "").strip()
        specs = {str(spec.get("id") or ""): spec for spec in _model_status_specs()}
        spec = specs.get(endpoint_id)
        if spec is None:
            return {"success": False, "error": "model_endpoint_not_found", "message": "模型配置不存在"}, 404

        public_spec = _public_endpoint_spec(spec)
        if not public_spec["test_supported"]:
            return {
                "success": True,
                "data": {
                    **public_spec,
                    "ok": False,
                    "test_status": "unconfigured",
                    "status_code": None,
                    "elapsed_ms": None,
                    "message": "模型未启用或 base_url/model 未配置，无法测试",
                    "response_preview": "",
                },
            }, 200

        endpoint_url = str(public_spec["endpoint_url"] or "")
        api_key = str(spec.get("api_key") or "")
        headers = _auth_headers(api_key)
        payload = _test_payload_for_spec(spec)
        post_json = requester or _http_post_json
        started_at = time.monotonic()
        response = post_json(
            url=endpoint_url,
            headers=headers,
            payload=payload,
            timeout_seconds=MODEL_STATUS_TEST_TIMEOUT_SECONDS,
        )
        elapsed_ms = round((time.monotonic() - started_at) * 1000.0, 2)
        status_code = response.get("status_code")
        try:
            status_code_int = int(status_code or 0)
        except Exception:
            status_code_int = 0
        data = response.get("json")
        text = str(response.get("text") or "")
        transport_error = str(response.get("error") or "")
        status_ok = 200 <= status_code_int < 300
        result_ok = bool(status_ok and _response_has_model_result(str(spec.get("kind") or ""), data))

        if status_ok:
            log_upstream_auth_success_once(
                logger=logger,
                service="public-service-admin-model-status",
                endpoint=str(spec.get("kind") or ""),
                model=str(spec.get("model") or ""),
                base_url=str(spec.get("base_url") or ""),
                api_key=api_key,
                status_code=status_code_int,
            )
        else:
            log_upstream_auth_failure(
                logger=logger,
                service="public-service-admin-model-status",
                endpoint=str(spec.get("kind") or ""),
                model=str(spec.get("model") or ""),
                base_url=str(spec.get("base_url") or ""),
                api_key=api_key,
                status_code=status_code_int,
            )

        if result_ok:
            message = "模型响应正常"
            test_status = "ok"
        elif status_code_int:
            message = f"模型测试失败，HTTP {status_code_int}"
            test_status = "failed"
        elif transport_error:
            message = f"模型测试失败：{transport_error}"
            test_status = "failed"
        else:
            message = "模型测试失败：未获得可识别响应"
            test_status = "failed"

        return {
            "success": True,
            "data": {
                **public_spec,
                "ok": result_ok,
                "test_status": test_status,
                "status_code": status_code_int or None,
                "elapsed_ms": elapsed_ms,
                "message": message,
                "response_preview": _response_preview(data, text or transport_error),
            },
        }, 200

    def build_kb_info(self, runtime: PublicServiceRuntime) -> tuple[dict[str, Any], int]:
        try:
            chromadb_count = self._chromadb_count(runtime)
            graph = self._graph(runtime)
            kb_ready = (
                graph is not None
                or runtime.vector_collection is not None
                or chromadb_count > 0
            )
            if not kb_ready:
                return {
                    "success": False,
                    "message": "知识库运行时未初始化",
                    "kb_size": 0,
                    "chromadb_size": chromadb_count,
                    "source_stats": {
                        "neo4j": 0,
                        "neo4j_connected": False,
                        "chromadb": chromadb_count,
                    },
                }, 200

            neo4j_connected = True
            try:
                if graph is None:
                    raise RuntimeError("neo4j_graph_unavailable")
                query_result = graph.query("MATCH (n) RETURN count(n) as count")
                node_count = int(query_result[0]["count"] or 0) if query_result else 0
            except Exception as exc:
                logger.warning("Failed to query Neo4j node count: %s", exc)
                node_count = 0
                neo4j_connected = False

            return {
                "success": True,
                "kb_size": node_count,
                "chromadb_size": chromadb_count,
                "source_stats": {
                    "neo4j": node_count,
                    "neo4j_connected": neo4j_connected,
                    "chromadb": chromadb_count,
                },
            }, 200
        except Exception as exc:
            logger.error("Failed to get KB info: %s", exc)
            return {
                "success": False,
                "message": str(exc),
                "kb_size": 0,
                "chromadb_size": 0,
                "source_stats": {
                    "neo4j": 0,
                    "neo4j_connected": False,
                    "chromadb": 0,
                },
            }, 200

    def refresh_kb(self, runtime: PublicServiceRuntime) -> tuple[dict[str, Any], int]:
        try:
            payload_base = {"scope": "instance_local", "cluster_consistency": "not_coordinated"}
            if runtime.init_agent is None:
                return {**payload_base, "success": False, "message": "知识库运行时未配置"}, 200
            if runtime.init_agent():
                return {**payload_base, "success": True, "message": "当前实例知识库已刷新"}, 200
            return {**payload_base, "success": False, "message": "当前实例知识库刷新失败"}, 200
        except Exception as exc:
            logger.error("Failed to refresh KB: %s", exc)
            return {"success": False, "message": str(exc), "scope": "instance_local", "cluster_consistency": "not_coordinated"}, 200

    def clear_cache(self, runtime: PublicServiceRuntime) -> tuple[dict[str, Any], int]:
        try:
            runtime.answer_cache.clear()
            logger.info("Answer cache cleared")
            return {
                "success": True,
                "message": "当前实例答案缓存已清空",
                "scope": "instance_local",
                "cluster_consistency": "not_coordinated",
            }, 200
        except Exception as exc:
            logger.error("Failed to clear answer cache: %s", exc)
            return {"success": False, "message": str(exc), "scope": "instance_local", "cluster_consistency": "not_coordinated"}, 200

    def build_conversation_cache_debug(
        self,
        runtime: PublicServiceRuntime,
        *,
        user_id: int,
        conversation_id: int | None = None,
    ) -> tuple[dict[str, Any], int]:
        try:
            redis_service = runtime.redis_service
            if redis_service is None:
                return {
                    "success": True,
                    "data": {
                        "redis_available": False,
                        "key_prefix": str(getattr(runtime.settings, "redis_key_prefix", "agentcode") or "agentcode"),
                        "conversation_cache": {
                            "user_id": int(user_id),
                            "list": {"version": "0", "recent_pages_key": "", "recent_pages_ttl_seconds": None, "recent_pages": [], "pages": []},
                            "detail": {},
                        },
                    },
                }, 200

            recent_pages = get_recent_conversation_list_pages(redis_service=redis_service, user_id=user_id)
            pages_to_check: list[tuple[int, int]] = [(1, 20)]
            for item in recent_pages:
                candidate = (int(item.get("page") or 0), int(item.get("page_size") or 0))
                if candidate[0] <= 0 or candidate[1] <= 0 or candidate in pages_to_check:
                    continue
                pages_to_check.append(candidate)

            list_version = get_conversation_list_cache_version(redis_service=redis_service, user_id=user_id)
            list_pages: list[dict[str, Any]] = []
            for page, page_size in pages_to_check:
                key = build_conversation_list_cache_key(redis_service=redis_service, user_id=user_id, page=page, page_size=page_size)
                payload = redis_service.get_json(key, default=None)
                data = payload.get("data") if isinstance(payload, dict) else {}
                conversations = data.get("conversations") if isinstance(data, dict) else []
                preview: list[dict[str, Any]] = []
                if isinstance(conversations, list):
                    for item in conversations[:5]:
                        if not isinstance(item, dict):
                            continue
                        preview.append(
                            {
                                "conversation_id": int(item.get("conversation_id") or 0),
                                "title": str(item.get("title") or ""),
                                "message_count": int(item.get("message_count") or 0),
                            }
                        )
                list_pages.append(
                    {
                        "page": page,
                        "page_size": page_size,
                        "key": key,
                        "present": isinstance(payload, dict) and payload.get("success") is True,
                        "ttl_seconds": self._ttl_or_none(runtime, key),
                        "conversation_count": len(conversations) if isinstance(conversations, list) else 0,
                        "total_count": int((data or {}).get("total_count") or 0) if isinstance(data, dict) else 0,
                        "preview": preview,
                    }
                )

            detail_section: dict[str, Any] = {}
            if conversation_id is not None and int(conversation_id) > 0:
                detail_key = build_conversation_detail_cache_key(
                    redis_service=redis_service,
                    user_id=user_id,
                    conversation_id=int(conversation_id),
                )
                detail_payload = redis_service.get_json(detail_key, default=None)
                detail_data = detail_payload.get("data") if isinstance(detail_payload, dict) else {}
                messages = detail_data.get("messages") if isinstance(detail_data, dict) else []
                uploaded_files = detail_data.get("uploaded_files") if isinstance(detail_data, dict) else []
                last_message = messages[-1] if isinstance(messages, list) and messages else {}
                detail_section = {
                    "conversation_id": int(conversation_id),
                    "version": get_conversation_detail_cache_version(
                        redis_service=redis_service,
                        user_id=user_id,
                        conversation_id=int(conversation_id),
                    ),
                    "key": detail_key,
                    "present": isinstance(detail_payload, dict) and detail_payload.get("success") is True,
                    "ttl_seconds": self._ttl_or_none(runtime, detail_key),
                    "message_count": len(messages) if isinstance(messages, list) else 0,
                    "uploaded_files_count": len(uploaded_files) if isinstance(uploaded_files, list) else 0,
                    "title": str((detail_data or {}).get("title") or "") if isinstance(detail_data, dict) else "",
                    "updated_at": (detail_data or {}).get("updated_at") if isinstance(detail_data, dict) else None,
                    "last_message_preview": {
                        "role": str((last_message or {}).get("role") or "") if isinstance(last_message, dict) else "",
                        "content": str((last_message or {}).get("content") or "")[:120] if isinstance(last_message, dict) else "",
                    },
                }

            recent_pages_key = build_conversation_list_recent_pages_key(redis_service=redis_service, user_id=user_id)
            return {
                "success": True,
                "data": {
                    "redis_available": bool(redis_service.available),
                    "key_prefix": str(getattr(runtime.settings, "redis_key_prefix", "agentcode") or "agentcode"),
                    "conversation_cache": {
                        "user_id": int(user_id),
                        "list": {
                            "version": list_version,
                            "recent_pages_key": recent_pages_key,
                            "recent_pages_ttl_seconds": self._ttl_or_none(runtime, recent_pages_key),
                            "recent_pages": recent_pages,
                            "pages": list_pages,
                        },
                        "detail": detail_section,
                    },
                },
            }, 200
        except Exception as exc:
            logger.warning("Failed to read conversation cache debug: %s", exc)
            return {"success": False, "error": str(exc)}, 500

    @staticmethod
    def _chromadb_count(runtime: PublicServiceRuntime) -> int:
        vector_client = runtime.vector_db_client
        collection = runtime.vector_collection or SystemService._get_semantic_collection(runtime.agent)
        if vector_client is not None and hasattr(vector_client, "count"):
            try:
                result = vector_client.count(collection=collection)
                return int(getattr(result, "count", 0) or 0)
            except Exception as exc:
                logger.warning("Failed to query runtime vector DB client: %s", exc)
        if collection is not None and hasattr(collection, "count"):
            try:
                return int(collection.count() or 0)
            except Exception as exc:
                logger.warning("Failed to query semantic collection: %s", exc)
        return 0

    @staticmethod
    def _get_semantic_collection(agent: Any) -> Any | None:
        semantic_expert = getattr(agent, "semantic_expert", None)
        if semantic_expert is None:
            return None
        return getattr(semantic_expert, "collection", None)

    @staticmethod
    def _graph(runtime: PublicServiceRuntime) -> Any | None:
        agent_graph = getattr(getattr(runtime, "agent", None), "graph", None)
        if agent_graph is not None:
            return agent_graph
        neo4j_client = getattr(runtime, "neo4j_client", None)
        if neo4j_client is not None and bool(getattr(neo4j_client, "available", False)):
            return getattr(neo4j_client, "graph", None)
        return None


system_service = SystemService()
