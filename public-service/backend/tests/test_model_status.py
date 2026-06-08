from __future__ import annotations

import json
import logging

from app.core.deps import AuthContext
from app.main import app
from app.modules.auth.deps import require_admin_context
from app.modules.system import api as system_api_module
from app.modules.system import service as system_service_module
from app.modules.system.service import system_service


def _decode(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _route_for(path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"route not found: {method} {path}")


def test_admin_model_status_route_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/admin/model-status" in paths
    assert "/api/admin/model-status/test" in paths

    route = _route_for("/api/admin/model-status", "GET")
    assert require_admin_context in {dep.call for dep in route.dependant.dependencies}

    test_route = _route_for("/api/admin/model-status/test", "POST")
    assert require_admin_context in {dep.call for dep in test_route.dependant.dependencies}


def test_model_status_lists_configured_models_without_network_probe(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LLM_API_KEY", "Bearer llm-key")
    monkeypatch.setenv("LLM_AUTH_MODE", "authorization")
    monkeypatch.setenv("INTENT_MODEL_ENABLED", "true")
    monkeypatch.setenv("INTENT_MODEL", "qwen3-8b")
    monkeypatch.setenv("QA_EMBEDDING_BASE_URL", "http://host.docker.internal:8001/v1")
    monkeypatch.setenv("QA_EMBEDDING_MODEL", "bge-local")
    monkeypatch.setenv("QA_EMBEDDING_API_KEY", "")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_BASE_URL", "")
    monkeypatch.setenv("RERANK_BASE_URL", "http://host.docker.internal:8084/v1")
    monkeypatch.setenv("RERANK_MODEL", "qwen3-vl-rerank")

    payload, status_code = system_service.build_model_status()

    assert status_code == 200
    assert payload["success"] is True
    assert payload["data"]["probe_method"] == "config_only"
    endpoints = {item["id"]: item for item in payload["data"]["endpoints"]}
    assert endpoints["llm_chat"]["endpoint_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert endpoints["llm_chat"]["api_key_present"] is True
    assert endpoints["llm_chat"]["api_key_input_has_bearer"] is True
    assert endpoints["llm_chat"]["auth_mode"] == "authorization"
    assert endpoints["llm_chat"]["key_fingerprint"]
    assert "api_key" not in endpoints["llm_chat"]
    assert endpoints["fastqa_embedding"]["endpoint_url"] == "http://host.docker.internal:8001/v1/embeddings"
    assert endpoints["fastqa_embedding"]["auth_mode"] == "bearer"
    assert endpoints["fastqa_embedding"]["api_key_present"] is False
    assert endpoints["rerank"]["endpoint_url"] == "http://host.docker.internal:8084/v1/rerank"
    assert "provider" not in endpoints["rerank"]


def test_model_status_list_logs_configuration_summary(monkeypatch, caplog):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "chat-model")
    monkeypatch.setenv("LLM_API_KEY", "Bearer secret-token")
    monkeypatch.setenv("QA_EMBEDDING_BASE_URL", "http://embedding.example/v1")
    monkeypatch.setenv("QA_EMBEDDING_MODEL", "bge-local")

    with caplog.at_level(logging.INFO, logger="app.modules.system.service"):
        payload, status_code = system_service.build_model_status()

    assert status_code == 200
    assert payload["success"] is True
    messages = [record.message for record in caplog.records]
    assert any(
        "admin_model_status config_summary" in message
        and "total=" in message
        and "configured=" in message
        and "probe_method=config_only" in message
        for message in messages
    )
    assert any(
        "admin_model_status config_endpoint" in message
        and "id=llm_chat" in message
        and "kind=chat" in message
        and "model=chat-model" in message
        and "key_present=True" in message
        for message in messages
    )
    assert all("secret-token" not in message for message in messages)


def test_model_status_normalizes_rerank_base_url_without_duplicate_v1(monkeypatch):
    monkeypatch.setenv("RERANK_BASE_URL", "http://host.docker.internal:8084/v1/v1")
    monkeypatch.setenv("RERANK_MODEL", "qwen3-vl-rerank")

    payload, status_code = system_service.build_model_status()

    assert status_code == 200
    endpoints = {item["id"]: item for item in payload["data"]["endpoints"]}
    assert endpoints["rerank"]["base_url"] == "http://host.docker.internal:8084/v1/v1"
    assert endpoints["rerank"]["endpoint_url"] == "http://host.docker.internal:8084/v1/rerank"


def test_model_status_test_sends_chat_hello_with_normalized_bearer(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "chat-model")
    monkeypatch.setenv("LLM_API_KEY", "Bearer chat-token")
    calls: list[dict] = []

    def fake_requester(*, url: str, headers: dict, payload: dict, timeout_seconds: float) -> dict:
        calls.append({"url": url, "headers": headers, "payload": payload, "timeout_seconds": timeout_seconds})
        return {
            "status_code": 200,
            "json": {"choices": [{"message": {"content": "hello back"}}]},
            "text": "",
        }

    payload, status_code = system_service.test_model_status_endpoint(
        "llm_chat",
        requester=fake_requester,
    )

    assert status_code == 200
    assert payload["success"] is True
    assert payload["data"]["ok"] is True
    assert calls == [
        {
            "url": "https://llm.example/v1/chat/completions",
            "headers": {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer chat-token",
            },
            "payload": {
                "model": "chat-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
                "max_tokens": 16,
            },
            "timeout_seconds": 30.0,
        }
    ]


def test_model_status_test_logs_probe_success_without_sensitive_values(monkeypatch, caplog):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "chat-model")
    monkeypatch.setenv("LLM_API_KEY", "Bearer secret-token")

    def fake_requester(*, url: str, headers: dict, payload: dict, timeout_seconds: float) -> dict:
        return {
            "status_code": 200,
            "json": {"choices": [{"message": {"content": "hello back"}}]},
            "text": "",
        }

    with caplog.at_level(logging.INFO, logger="app.modules.system.service"):
        payload, status_code = system_service.test_model_status_endpoint(
            "llm_chat",
            requester=fake_requester,
        )

    assert status_code == 200
    assert payload["data"]["ok"] is True
    messages = [record.message for record in caplog.records]
    assert any(
        "admin_model_status probe_start" in message
        and "id=llm_chat" in message
        and "kind=chat" in message
        and "model=chat-model" in message
        and "endpoint=https://llm.example/v1/chat/completions" in message
        and "auth_mode=bearer" in message
        and "key_present=True" in message
        and "message_count=1" in message
        and "message_chars=5" in message
        for message in messages
    )
    assert any(
        "admin_model_status probe_success" in message
        and "id=llm_chat" in message
        and "status_code=200" in message
        and "ok=True" in message
        and "answer_present=True" in message
        for message in messages
    )
    assert all("secret-token" not in message for message in messages)
    assert all("hello" not in message.lower() for message in messages)


def test_model_status_test_logs_probe_failure_without_response_body(monkeypatch, caplog):
    monkeypatch.setenv("QA_EMBEDDING_BASE_URL", "http://embedding.example/v1")
    monkeypatch.setenv("QA_EMBEDDING_MODEL", "bge-local")
    monkeypatch.setenv("QA_EMBEDDING_API_KEY", "Bearer embedding-token")

    def fake_requester(*, url: str, headers: dict, payload: dict, timeout_seconds: float) -> dict:
        return {
            "status_code": 502,
            "json": {"error": {"message": "sensitive upstream body"}},
            "text": "sensitive upstream body",
            "error": "sensitive transport detail",
        }

    with caplog.at_level(logging.INFO, logger="app.modules.system.service"):
        payload, status_code = system_service.test_model_status_endpoint(
            "fastqa_embedding",
            requester=fake_requester,
        )

    assert status_code == 200
    assert payload["data"]["ok"] is False
    messages = [record.message for record in caplog.records]
    assert any(
        "admin_model_status probe_start" in message
        and "id=fastqa_embedding" in message
        and "kind=embedding" in message
        and "input_count=1" in message
        and "input_chars=5" in message
        and "dimensions_param_present=False" in message
        for message in messages
    )
    assert any(
        "admin_model_status probe_failed" in message
        and "id=fastqa_embedding" in message
        and "status_code=502" in message
        and "reason=http_error" in message
        and "error_type=upstream_http_error" in message
        for message in messages
    )
    assert all("embedding-token" not in message for message in messages)
    assert all("sensitive upstream body" not in message for message in messages)
    assert all("sensitive transport detail" not in message for message in messages)


def test_model_status_test_supports_non_bearer_auth_mode(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "chat-model")
    monkeypatch.setenv("LLM_API_KEY", "Bearer chat-token")
    monkeypatch.setenv("LLM_AUTH_MODE", "authorization")
    calls: list[dict] = []

    def fake_requester(*, url: str, headers: dict, payload: dict, timeout_seconds: float) -> dict:
        calls.append({"url": url, "headers": headers, "payload": payload, "timeout_seconds": timeout_seconds})
        return {
            "status_code": 200,
            "json": {"choices": [{"message": {"content": "hello back"}}]},
            "text": "",
        }

    payload, status_code = system_service.test_model_status_endpoint(
        "llm_chat",
        requester=fake_requester,
    )

    assert status_code == 200
    assert payload["data"]["auth_mode"] == "authorization"
    assert calls[0]["headers"]["Authorization"] == "chat-token"


def test_model_status_test_disables_thinking_for_intent_model(monkeypatch):
    monkeypatch.setenv("INTENT_MODEL_ENABLED", "true")
    monkeypatch.setenv("INTENT_MODEL_BASE_URL", "https://intent.example/v1")
    monkeypatch.setenv("INTENT_MODEL", "qwen3-8b")
    monkeypatch.setenv("INTENT_MODEL_API_KEY", "Bearer intent-token")
    monkeypatch.setenv("LLM_AUTH_MODE", "authorization")
    monkeypatch.setenv("INTENT_MODEL_AUTH_MODE", "x-api-key")
    calls: list[dict] = []

    def fake_requester(*, url: str, headers: dict, payload: dict, timeout_seconds: float) -> dict:
        calls.append({"url": url, "headers": headers, "payload": payload, "timeout_seconds": timeout_seconds})
        return {
            "status_code": 200,
            "json": {"choices": [{"message": {"content": "hello back"}}]},
            "text": "",
        }

    payload, status_code = system_service.test_model_status_endpoint(
        "intent_chat",
        requester=fake_requester,
    )

    assert status_code == 200
    assert payload["success"] is True
    assert payload["data"]["ok"] is True
    assert calls == [
        {
            "url": "https://intent.example/v1/chat/completions",
            "headers": {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-API-Key": "intent-token",
            },
            "payload": {
                "model": "qwen3-8b",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
                "max_tokens": 64,
                "temperature": 0.0,
                "enable_thinking": False,
            },
            "timeout_seconds": 30.0,
        }
    ]


def test_model_status_test_uses_embedding_and_local_rerank_protocols(monkeypatch):
    monkeypatch.setenv("QA_EMBEDDING_BASE_URL", "http://embedding.example/v1")
    monkeypatch.setenv("QA_EMBEDDING_MODEL", "bge-local")
    monkeypatch.setenv("QA_EMBEDDING_API_KEY", "Bearer embedding-token")
    monkeypatch.setenv("QA_EMBEDDING_AUTH_MODE", "x-api-key")
    monkeypatch.setenv("RERANK_BASE_URL", "http://rerank.example/v1")
    monkeypatch.setenv("RERANK_MODEL", "qwen3-vl-rerank")
    monkeypatch.setenv("RERANK_API_KEY", "Bearer rerank-token")
    monkeypatch.setenv("RERANK_AUTH_MODE", "authorization")
    calls: list[dict] = []

    def fake_requester(*, url: str, headers: dict, payload: dict, timeout_seconds: float) -> dict:
        calls.append({"url": url, "headers": headers, "payload": payload, "timeout_seconds": timeout_seconds})
        if "embeddings" in url:
            return {"status_code": 200, "json": {"data": [{"embedding": [0.1, 0.2]}]}, "text": ""}
        return {"status_code": 200, "json": {"results": [{"index": 0, "relevance_score": 0.9}]}, "text": ""}

    embedding_payload, _ = system_service.test_model_status_endpoint("fastqa_embedding", requester=fake_requester)
    rerank_payload, _ = system_service.test_model_status_endpoint("rerank", requester=fake_requester)

    assert embedding_payload["data"]["ok"] is True
    assert rerank_payload["data"]["ok"] is True
    assert embedding_payload["data"]["auth_mode"] == "x-api-key"
    assert rerank_payload["data"]["auth_mode"] == "authorization"
    assert calls[0]["url"] == "http://embedding.example/v1/embeddings"
    assert calls[0]["headers"]["X-API-Key"] == "embedding-token"
    assert "Authorization" not in calls[0]["headers"]
    assert calls[0]["payload"] == {"model": "bge-local", "input": ["hello"]}
    assert calls[1]["url"] == "http://rerank.example/v1/rerank"
    assert calls[1]["headers"]["Authorization"] == "rerank-token"
    assert calls[1]["payload"] == {
        "model": "qwen3-vl-rerank",
        "query": "hello",
        "documents": ["hello", "hello world"],
        "top_n": 1,
    }


def test_model_status_embedding_probe_handles_4096_dimension_response(monkeypatch):
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_BASE_URL", "http://embedding.example/v1")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_MODEL", "qwen3-embedding-8b")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_AUTH_MODE", "none")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_DIMENSIONS", "4096")
    calls: list[dict] = []

    def fake_requester(*, url: str, headers: dict, payload: dict, timeout_seconds: float) -> dict:
        calls.append({"url": url, "headers": headers, "payload": payload, "timeout_seconds": timeout_seconds})
        return {
            "status_code": 200,
            "json": {"data": [{"embedding": [0.1] * 4096}]},
            "text": "",
        }

    payload, status_code = system_service.test_model_status_endpoint(
        "highthinkingqa_embedding",
        requester=fake_requester,
    )

    assert status_code == 200
    assert payload["data"]["ok"] is True
    assert payload["data"]["auth_mode"] == "none"
    assert payload["data"]["detected_dimension"] == 4096
    assert payload["data"]["expected_dimension"] == 4096
    assert calls == [
        {
            "url": "http://embedding.example/v1/embeddings",
            "headers": {
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "payload": {
                "model": "qwen3-embedding-8b",
                "input": ["hello"],
            },
            "timeout_seconds": 30.0,
        }
    ]


def test_model_status_embedding_probe_reports_dimension_mismatch(monkeypatch):
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_BASE_URL", "http://embedding.example/v1")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_MODEL", "qwen3-embedding-8b")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_DIMENSIONS", "4096")

    def fake_requester(*, url: str, headers: dict, payload: dict, timeout_seconds: float) -> dict:
        return {
            "status_code": 200,
            "json": {"data": [{"embedding": [0.1] * 2048}]},
            "text": "",
        }

    payload, status_code = system_service.test_model_status_endpoint(
        "highthinkingqa_embedding",
        requester=fake_requester,
    )

    assert status_code == 200
    assert payload["data"]["ok"] is False
    assert payload["data"]["test_status"] == "failed"
    assert payload["data"]["detected_dimension"] == 2048
    assert payload["data"]["expected_dimension"] == 4096
    assert "维度不匹配" in payload["data"]["message"]


def test_http_post_json_reads_large_embedding_response_without_truncation(monkeypatch):
    body = json.dumps({"data": [{"embedding": [0.1] * 4096}]}).encode("utf-8")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size: int = -1):
            return body if size == -1 else body[:size]

        def getcode(self):
            return 200

    def fake_urlopen(request, timeout):
        return FakeResponse()

    monkeypatch.setattr(system_service_module.urllib.request, "urlopen", fake_urlopen)

    result = system_service_module._http_post_json(
        url="http://embedding.example/v1/embeddings",
        headers={"Content-Type": "application/json"},
        payload={"model": "m", "input": ["hello"]},
        timeout_seconds=1,
    )

    assert result["status_code"] == 200
    assert len(result["json"]["data"][0]["embedding"]) == 4096


def test_admin_model_status_endpoint_returns_payload(monkeypatch):
    monkeypatch.setattr(
        system_service,
        "build_model_status",
        lambda: (
            {
                "success": True,
                "data": {
                    "probe_method": "config_only",
                    "endpoints": [{"id": "llm", "status": "configured"}],
                },
            },
            200,
        ),
    )

    response = system_api_module.admin_model_status(
        _context=AuthContext(user_id=1, role="admin", username="admin")
    )

    assert response.status_code == 200
    assert _decode(response)["data"]["endpoints"][0]["status"] == "configured"
