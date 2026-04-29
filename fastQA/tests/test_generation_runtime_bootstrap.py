from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.modules.generation_pipeline.runtime_bootstrap import (
    apply_default_doi_runtime_settings,
    build_openai_client,
    ensure_literature_expert,
    resolve_generation_runtime_inputs,
)


def test_resolve_generation_runtime_inputs_uses_service_roots(monkeypatch, tmp_path):
    state_root = (tmp_path / "state").resolve()
    asset_root = (tmp_path / "assets").resolve()
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("EMBEDDING_MODEL_TYPE", "local")
    monkeypatch.setenv("EMBEDDING_MODEL_PATH", "models/bge")
    monkeypatch.setenv("VECTOR_DB_PATH", "vectordb")

    resolved = resolve_generation_runtime_inputs(
        api_key=None,
        base_url=None,
        model=None,
        config=None,
        state_root=state_root,
        asset_root=asset_root,
    )

    assert resolved.api_key == "openai-key"
    assert resolved.base_url == "https://example.com/v1"
    assert resolved.model == "gpt-test"
    assert resolved.embedding_model_path == str((asset_root / "models/bge").resolve())
    assert resolved.chroma_db_path == str((state_root / "vectordb").resolve())


def test_resolve_generation_runtime_inputs_accepts_dashscope_aliases(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("DASHSCOPE_MODEL", "qwen-plus")

    resolved = resolve_generation_runtime_inputs(
        api_key=None,
        base_url=None,
        model=None,
        config=None,
        state_root=tmp_path / "state",
        asset_root=tmp_path / "assets",
    )

    assert resolved.api_key == "dash-key"
    assert resolved.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert resolved.model == "qwen-plus"


def test_build_openai_client_uses_local_factory(monkeypatch):
    sentinel = object()
    calls: dict[str, object] = {}

    def _fake_builder(
        *,
        api_key: str,
        base_url: str,
        logger=None,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        stream_read_timeout_seconds: float,
        write_timeout_seconds: float,
        pool_timeout_seconds: float,
        keepalive_expiry_seconds: float,
        max_connections: int,
        max_keepalive_connections: int,
        http_client=None,
    ):
        calls["api_key"] = api_key
        calls["base_url"] = base_url
        calls["logger"] = logger
        calls["connect_timeout_seconds"] = connect_timeout_seconds
        calls["read_timeout_seconds"] = read_timeout_seconds
        calls["stream_read_timeout_seconds"] = stream_read_timeout_seconds
        calls["write_timeout_seconds"] = write_timeout_seconds
        calls["pool_timeout_seconds"] = pool_timeout_seconds
        calls["keepalive_expiry_seconds"] = keepalive_expiry_seconds
        calls["max_connections"] = max_connections
        calls["max_keepalive_connections"] = max_keepalive_connections
        calls["http_client"] = http_client
        return sentinel

    for name in (
        "FASTQA_LLM_HTTP_CONNECT_TIMEOUT_SECONDS",
        "FASTQA_LLM_HTTP_READ_TIMEOUT_SECONDS",
        "FASTQA_LLM_HTTP_WRITE_TIMEOUT_SECONDS",
        "FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS",
        "FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS",
        "FASTQA_LLM_HTTP_MAX_CONNECTIONS",
        "FASTQA_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS",
        "LLM_CONNECT_TIMEOUT_SECONDS",
        "LLM_READ_TIMEOUT_SECONDS",
        "LLM_WRITE_TIMEOUT_SECONDS",
        "LLM_POOL_TIMEOUT_SECONDS",
        "LLM_KEEPALIVE_EXPIRY_SECONDS",
        "LLM_MAX_CONNECTIONS",
        "LLM_MAX_KEEPALIVE_CONNECTIONS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_CONNECT_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("OPENAI_READ_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("OPENAI_WRITE_TIMEOUT_SECONDS", "181")
    monkeypatch.setenv("OPENAI_POOL_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("FASTQA_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr("app.modules.generation_pipeline.runtime_bootstrap.build_chat_completions_client", _fake_builder)
    logger = SimpleNamespace(info=lambda *args, **kwargs: None)

    client = build_openai_client(api_key="key", base_url="https://example.com/v1", logger=logger)

    assert client is sentinel
    assert calls["api_key"] == "key"
    assert calls["base_url"] == "https://example.com/v1"
    assert calls["connect_timeout_seconds"] == 12.0
    assert calls["read_timeout_seconds"] == 180.0
    assert calls["stream_read_timeout_seconds"] == 5.0
    assert calls["write_timeout_seconds"] == 181.0
    assert calls["pool_timeout_seconds"] == 9.0
    assert calls["keepalive_expiry_seconds"] == 90.0
    assert calls["max_connections"] == 160
    assert calls["max_keepalive_connections"] == 64
    assert calls["http_client"] is None


def test_ensure_literature_expert_uses_resolved_paths():
    calls: dict[str, object] = {}

    class _Expert:
        def __init__(self, **kwargs):
            calls.update(kwargs)

    logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    runtime_inputs = resolve_generation_runtime_inputs(
        api_key="k",
        base_url="u",
        model="m",
        config={
            "embedding_model_type": "local",
            "embedding_model_path": "/abs/model",
            "chroma_db_path": "/abs/vdb",
        },
        state_root=Path("/tmp/state"),
        asset_root=Path("/tmp/assets"),
    )

    expert = ensure_literature_expert(
        existing_expert=None,
        expert_cls=_Expert,
        runtime_inputs=runtime_inputs,
        logger=logger,
    )

    assert isinstance(expert, _Expert)
    assert calls["model_path"] == "/abs/model"
    assert calls["db_path"] == "/abs/vdb"


def test_apply_default_doi_runtime_settings_populates_flags():
    target = SimpleNamespace()
    apply_default_doi_runtime_settings(target)

    assert target.enable_programmatic_doi_insertion is True
    assert target.strict_mode is True
    assert target.strict_action == "remove"


def test_create_app_registers_authority_hooks_when_enabled(monkeypatch):
    monkeypatch.setenv("CHAT_PERSIST_ENABLED", "1")
    monkeypatch.setenv("CHAT_PERSIST_ASYNC", "1")

    from app.main import create_app

    app = create_app()

    assert app.state.persist_user_message_hook is not None
    assert app.state.load_conversation_context_hook is not None
    assert app.state.persist_assistant_summary_hook is not None
    assert app.state.persist_assistant_terminal_hook is not None
    assert app.state.persist_user_message_hook.keywords["async_enabled"] is False
    assert app.state.persist_assistant_summary_hook.keywords["async_enabled"] is True
    assert app.state.persist_assistant_terminal_hook.keywords["async_enabled"] is True


def test_runtime_bootstrap_reports_shared_pool_degraded_when_provider_init_fails(monkeypatch):
    from app.core.runtime import bootstrap_generation_runtime

    runtime = SimpleNamespace(
        settings=SimpleNamespace(generation_runtime_enabled=True),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )
    monkeypatch.setenv("FASTQA_LLM_HTTP_SHARED_POOL_ENABLED", "1")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    monkeypatch.setattr(
        "app.core.runtime.FastQASharedUpstreamHttpPool.from_env",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("pool bootstrap failed")),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG",
        lambda **kwargs: SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True)),
    )

    bootstrap_generation_runtime(runtime)

    shared_status = runtime.component_status["shared_llm_pool"]
    assert shared_status["status"] == "degraded"
    assert shared_status["ready"] is False
    assert shared_status["client_owner"] == "private"
    assert shared_status["pool_owner"] == "app"
    assert shared_status["bootstrap_source"] == "startup"
    assert shared_status["max_connections"] == 160
    assert shared_status["max_keepalive_connections"] == 64
    assert shared_status["keepalive_expiry_seconds"] == 120.0
