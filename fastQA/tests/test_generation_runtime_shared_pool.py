from __future__ import annotations

from types import SimpleNamespace

from app.core.runtime import bootstrap_generation_runtime, close_generation_runtime
from app.modules.generation_pipeline.generation_driven_rag_facade import GenerationDrivenRAG


def test_bootstrap_generation_runtime_builds_shared_pool_once_per_app_state(monkeypatch):
    monkeypatch.setenv("FASTQA_LLM_HTTP_SHARED_POOL_ENABLED", "1")
    runtime = SimpleNamespace(
        settings=SimpleNamespace(generation_runtime_enabled=True),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )
    calls = {"pool": 0, "rag": []}
    fake_http_client = object()
    fake_pool = SimpleNamespace(client=lambda: fake_http_client, close=lambda: None)

    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    monkeypatch.setattr(
        "app.core.runtime.FastQASharedUpstreamHttpPool.from_env",
        lambda **kwargs: calls.__setitem__("pool", calls["pool"] + 1) or fake_pool,
    )

    def _fake_rag(**kwargs):
        calls["rag"].append(kwargs)
        return SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True))

    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG", _fake_rag)

    bootstrap_generation_runtime(runtime)
    bootstrap_generation_runtime(runtime)

    assert calls["pool"] == 1
    assert runtime.shared_llm_http_pool is fake_pool
    assert calls["rag"][0]["http_client"] is fake_http_client
    assert calls["rag"][1]["http_client"] is fake_http_client


def test_generation_driven_rag_and_query_expander_share_same_underlying_http_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    injected_http_client = object()

    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.ensure_literature_expert_impl",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG._load_vector_db_topics",
        lambda self: setattr(self, "_vector_db_topics", {}),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG._load_prompts",
        lambda self: (setattr(self, "stage1_prompt", "stage1"), setattr(self, "stage2_prompt", "stage2")),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: SimpleNamespace(_client=http_client),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.query_expander.build_chat_completions_client",
        lambda *, api_key, base_url, logger=None, http_client=None, **kwargs: SimpleNamespace(_client=http_client),
    )

    rag = GenerationDrivenRAG(http_client=injected_http_client)
    expander_client = rag._get_query_expander()._get_client()

    assert rag.client._client is injected_http_client
    assert expander_client._client is injected_http_client


def test_generation_driven_rag_query_expander_private_client_uses_shared_transport_config(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("FASTQA_LLM_HTTP_CONNECT_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("FASTQA_LLM_HTTP_READ_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("FASTQA_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS", "601")
    monkeypatch.setenv("FASTQA_LLM_HTTP_WRITE_TIMEOUT_SECONDS", "181")
    monkeypatch.setenv("FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("FASTQA_LLM_HTTP_MAX_CONNECTIONS", "160")
    monkeypatch.setenv("FASTQA_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", "64")
    monkeypatch.setenv("FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", "90")
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.ensure_literature_expert_impl",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG._load_vector_db_topics",
        lambda self: setattr(self, "_vector_db_topics", {}),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG._load_prompts",
        lambda self: (setattr(self, "stage1_prompt", "stage1"), setattr(self, "stage2_prompt", "stage2")),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: SimpleNamespace(_client=http_client),
    )

    def _fake_expander_builder(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(_client=kwargs.get("http_client"))

    monkeypatch.setattr(
        "app.modules.generation_pipeline.query_expander.build_chat_completions_client",
        _fake_expander_builder,
    )

    rag = GenerationDrivenRAG(http_client=None)
    rag._get_query_expander()._get_client()

    assert calls["http_client"] is None
    assert calls["connect_timeout_seconds"] == 15.0
    assert calls["read_timeout_seconds"] == 180.0
    assert calls["stream_read_timeout_seconds"] == 601.0
    assert calls["write_timeout_seconds"] == 181.0
    assert calls["pool_timeout_seconds"] == 30.0
    assert calls["max_connections"] == 160
    assert calls["max_keepalive_connections"] == 64
    assert calls["keepalive_expiry_seconds"] == 90.0


def test_generation_runtime_degrades_to_private_path_when_shared_pool_bootstrap_fails(monkeypatch):
    monkeypatch.setenv("FASTQA_LLM_HTTP_SHARED_POOL_ENABLED", "1")
    runtime = SimpleNamespace(
        settings=SimpleNamespace(generation_runtime_enabled=True),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    monkeypatch.setattr(
        "app.core.runtime.FastQASharedUpstreamHttpPool.from_env",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("pool bootstrap failed")),
    )

    def _fake_rag(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True))

    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG", _fake_rag)

    bootstrap_generation_runtime(runtime)

    assert runtime.generation_runtime_ready is True
    assert runtime.generation_runtime is not None
    assert calls[0]["http_client"] is None


def test_close_generation_runtime_closes_shared_pool_once():
    calls = {"runtime": 0, "pool": 0, "chat_pool": 0, "rerank_pool": 0}
    runtime = SimpleNamespace(
        generation_runtime=SimpleNamespace(close=lambda: calls.__setitem__("runtime", calls["runtime"] + 1)),
        generation_runtime_ready=True,
        stage2_chat_hot_pool=SimpleNamespace(close=lambda: calls.__setitem__("chat_pool", calls["chat_pool"] + 1)),
        stage2_rerank_hot_pool=SimpleNamespace(close=lambda: calls.__setitem__("rerank_pool", calls["rerank_pool"] + 1)),
        shared_llm_http_pool=SimpleNamespace(close=lambda: calls.__setitem__("pool", calls["pool"] + 1)),
    )

    close_generation_runtime(runtime)
    close_generation_runtime(runtime)

    assert calls == {"runtime": 1, "pool": 1, "chat_pool": 1, "rerank_pool": 1}
    assert runtime.generation_runtime is None
    assert runtime.generation_runtime_ready is False
    assert runtime.stage2_chat_hot_pool is None
    assert runtime.stage2_rerank_hot_pool is None


def test_bootstrap_generation_runtime_closes_existing_runtime_and_hot_pools_before_rebootstrap(monkeypatch):
    calls = {"runtime": 0, "chat_pool": 0, "rerank_pool": 0, "rag": 0}
    runtime = SimpleNamespace(
        settings=SimpleNamespace(generation_runtime_enabled=True, llm_http_shared_pool_enabled=False),
        generation_runtime=SimpleNamespace(close=lambda: calls.__setitem__("runtime", calls["runtime"] + 1)),
        generation_runtime_ready=True,
        stage2_chat_hot_pool=SimpleNamespace(close=lambda: calls.__setitem__("chat_pool", calls["chat_pool"] + 1)),
        stage2_rerank_hot_pool=SimpleNamespace(close=lambda: calls.__setitem__("rerank_pool", calls["rerank_pool"] + 1)),
        shared_llm_http_pool=SimpleNamespace(client=lambda: object(), close=lambda: None),
        component_status={},
        health_flags={},
    )

    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )

    def _fake_rag(**kwargs):
        calls["rag"] += 1
        return SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True))

    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG", _fake_rag)

    bootstrap_generation_runtime(runtime)

    assert calls["runtime"] == 1
    assert calls["chat_pool"] == 1
    assert calls["rerank_pool"] == 1
    assert calls["rag"] == 1


def test_bootstrap_generation_runtime_initializes_chat_hot_pool_when_enabled(monkeypatch):
    monkeypatch.setenv("FASTQA_LLM_HTTP_SHARED_POOL_ENABLED", "0")
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            generation_runtime_enabled=True,
            llm_http_shared_pool_enabled=False,
            stage2_chat_hot_pool_enabled=True,
            stage2_chat_hot_lane_count=3,
            stage2_chat_hot_keepalive_expiry_seconds=1800.0,
        ),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )
    calls: dict[str, object] = {}
    fake_pool = object()

    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )

    def _fake_chat_pool(**kwargs):
        calls["chat_pool_kwargs"] = kwargs
        return fake_pool

    monkeypatch.setattr("app.core.runtime.ChatHotLanePool", _fake_chat_pool)

    def _fake_rag(**kwargs):
        calls["rag_kwargs"] = kwargs
        return SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True))

    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG", _fake_rag)

    bootstrap_generation_runtime(runtime)

    assert runtime.stage2_chat_hot_pool is fake_pool
    assert calls["rag_kwargs"]["stage2_chat_hot_pool"] is fake_pool
