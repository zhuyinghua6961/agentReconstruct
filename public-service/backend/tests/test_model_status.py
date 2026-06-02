from __future__ import annotations

import json

from app.core.deps import AuthContext
from app.main import app
from app.modules.auth.deps import require_admin_context
from app.modules.system import api as system_api_module
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
    monkeypatch.setenv("INTENT_MODEL_ENABLED", "true")
    monkeypatch.setenv("INTENT_MODEL", "qwen3-8b")
    monkeypatch.setenv("QA_EMBEDDING_BASE_URL", "http://host.docker.internal:8001/v1/embeddings")
    monkeypatch.setenv("QA_EMBEDDING_MODEL", "bge-local")
    monkeypatch.setenv("QA_EMBEDDING_API_KEY", "")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_BASE_URL", "")
    monkeypatch.setenv("RERANK_BASE_URL", "http://host.docker.internal:8084")
    monkeypatch.setenv("RERANK_MODEL", "qwen3-vl-rerank")
    monkeypatch.setenv("RERANK_PROVIDER", "local")

    payload, status_code = system_service.build_model_status()

    assert status_code == 200
    assert payload["success"] is True
    assert payload["data"]["probe_method"] == "config_only"
    endpoints = {item["id"]: item for item in payload["data"]["endpoints"]}
    assert endpoints["llm_chat"]["endpoint_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert endpoints["llm_chat"]["api_key_present"] is True
    assert endpoints["llm_chat"]["api_key_input_has_bearer"] is True
    assert endpoints["llm_chat"]["key_fingerprint"]
    assert "api_key" not in endpoints["llm_chat"]
    assert endpoints["fastqa_embedding"]["endpoint_url"] == "http://host.docker.internal:8001/v1/embeddings"
    assert endpoints["fastqa_embedding"]["api_key_present"] is False
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


def test_model_status_test_disables_thinking_for_intent_model(monkeypatch):
    monkeypatch.setenv("INTENT_MODEL_ENABLED", "true")
    monkeypatch.setenv("INTENT_MODEL_BASE_URL", "https://intent.example/v1")
    monkeypatch.setenv("INTENT_MODEL", "qwen3-8b")
    monkeypatch.setenv("INTENT_MODEL_API_KEY", "Bearer intent-token")
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
                "Authorization": "Bearer intent-token",
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
    monkeypatch.setenv("RERANK_PROVIDER", "local")
    monkeypatch.setenv("RERANK_BASE_URL", "http://rerank.example")
    monkeypatch.setenv("RERANK_MODEL", "qwen3-vl-rerank")
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
    assert calls[0]["url"] == "http://embedding.example/v1/embeddings"
    assert calls[0]["headers"]["Authorization"] == "Bearer embedding-token"
    assert calls[0]["payload"] == {"model": "bge-local", "input": ["hello"]}
    assert calls[1]["url"] == "http://rerank.example/v1/rerank"
    assert calls[1]["payload"] == {
        "model": "qwen3-vl-rerank",
        "query": "hello",
        "documents": ["hello", "hello world"],
        "top_n": 1,
    }


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
