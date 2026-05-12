from __future__ import annotations

from types import SimpleNamespace

import app.services.file_route_service as file_route_service_module
import app.services.file_routes as file_routes_module


class _FakeLlm:
    def invoke(self, *_args, **_kwargs):
        return None


def test_get_aux_llm_reuses_app_owned_shared_adapter_when_available():
    sentinel = _FakeLlm()
    app_state = SimpleNamespace(
        shared_llm_adapter=sentinel,
        aux_llm=None,
        generation_runtime=None,
    )

    llm = file_routes_module.get_aux_llm(app_state, logger=None)

    assert llm is sentinel


def test_file_route_service_uses_app_owned_shared_adapter_instead_of_private_cache(monkeypatch):
    shared_http_client = object()
    built: list[dict[str, object]] = []
    sentinel = _FakeLlm()
    app_state = SimpleNamespace(
        shared_llm_adapter=None,
        shared_llm_adapter_ready=False,
        aux_llm=None,
        shared_llm_http_pool=SimpleNamespace(client=lambda: shared_http_client),
    )

    monkeypatch.setattr(
        file_route_service_module,
        "resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    monkeypatch.setattr(
        file_route_service_module,
        "build_chat_adapter",
        lambda **kwargs: built.append(kwargs) or sentinel,
    )

    first = file_route_service_module.file_route_service._resolve_llm(app_state=app_state)
    second = file_route_service_module.file_route_service._resolve_llm(app_state=app_state)

    assert first is sentinel
    assert second is sentinel
    assert app_state.shared_llm_adapter is sentinel
    assert app_state.aux_llm is sentinel
    assert len(built) == 1
    assert built[0]["http_client"] is shared_http_client


def test_file_routes_fallback_path_still_works_when_shared_pool_disabled(monkeypatch):
    built: list[dict[str, object]] = []
    sentinel = _FakeLlm()
    app_state = SimpleNamespace(
        shared_llm_adapter=None,
        shared_llm_adapter_ready=False,
        aux_llm=None,
        shared_llm_http_pool=None,
        generation_runtime=None,
    )

    monkeypatch.setattr(
        file_route_service_module,
        "resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    monkeypatch.setattr(
        file_route_service_module,
        "build_chat_adapter",
        lambda **kwargs: built.append(kwargs) or sentinel,
    )

    llm = file_routes_module.get_aux_llm(app_state, logger=None)

    assert llm is sentinel
    assert app_state.shared_llm_adapter is sentinel
    assert app_state.aux_llm is sentinel
    assert len(built) == 1
    assert built[0]["http_client"] is None


def test_resolve_app_owned_llm_error_mentions_unified_llm_keys(monkeypatch):
    app_state = SimpleNamespace(
        shared_llm_adapter=None,
        aux_llm=None,
        shared_llm_http_pool=None,
    )
    monkeypatch.setattr(
        file_route_service_module,
        "resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="", base_url="", model=""),
    )

    try:
        file_route_service_module.resolve_app_owned_llm(app_state=app_state, logger=None)
    except RuntimeError as exc:
        assert "LLM_API_KEY" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
