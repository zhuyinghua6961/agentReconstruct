from __future__ import annotations

from types import SimpleNamespace

from app.core.config import get_settings
from app.core.runtime import bootstrap_generation_runtime


def test_settings_expose_stage2_hot_pool_flags():
    settings = get_settings()

    assert hasattr(settings, "stage2_chat_hot_pool_enabled")
    assert hasattr(settings, "stage2_rerank_hot_pool_enabled")
    assert hasattr(settings, "stage2_chat_hot_lane_count")
    assert hasattr(settings, "stage2_rerank_hot_lane_count")
    assert hasattr(settings, "stage2_chat_warm_timeout_seconds")
    assert hasattr(settings, "stage2_rerank_warm_timeout_seconds")
    assert hasattr(settings, "stage2_warm_active_start_hour")
    assert hasattr(settings, "stage2_warm_active_end_hour")


def test_bootstrap_generation_runtime_exposes_stage2_hot_pool_status_when_disabled():
    runtime = SimpleNamespace(
        settings=SimpleNamespace(generation_runtime_enabled=False),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )

    bootstrap_generation_runtime(runtime)

    assert "stage2_chat_hot_pool" in runtime.component_status
    assert "stage2_rerank_hot_pool" in runtime.component_status
    assert "ready_lanes" in runtime.component_status["stage2_chat_hot_pool"]
    assert "total_lanes" in runtime.component_status["stage2_chat_hot_pool"]
    assert "ready_lanes" in runtime.component_status["stage2_rerank_hot_pool"]
    assert "total_lanes" in runtime.component_status["stage2_rerank_hot_pool"]


def test_bootstrap_generation_runtime_marks_disabled_hot_pools_skipped(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            generation_runtime_enabled=True,
            llm_http_shared_pool_enabled=False,
            stage2_chat_hot_pool_enabled=False,
            stage2_chat_hot_lane_count=0,
            stage2_rerank_hot_pool_enabled=False,
            stage2_rerank_hot_lane_count=0,
        ),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )

    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG",
        lambda **kwargs: SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True)),
    )

    bootstrap_generation_runtime(runtime)

    assert runtime.component_status["stage2_chat_hot_pool"]["status"] == "skipped"
    assert runtime.component_status["stage2_rerank_hot_pool"]["status"] == "skipped"


def test_bootstrap_generation_runtime_uses_dedicated_rerank_api_key_for_warmup(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            generation_runtime_enabled=True,
            llm_http_shared_pool_enabled=False,
            stage2_rerank_hot_pool_enabled=True,
            stage2_rerank_hot_lane_count=1,
            stage2_rerank_warmup_enabled=True,
            stage2_rerank_warm_interval_seconds=300,
            stage2_rerank_warm_timeout_seconds=420.0,
            stage2_bootstrap_warm_max_parallel=1,
            stage2_bootstrap_warm_jitter_seconds=0,
            stage2_warm_jitter_seconds=0,
            stage2_lane_degraded_after_seconds=900,
        ),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )
    calls: dict[str, object] = {}

    monkeypatch.setenv("QA_RETRIEVAL_RERANK_API_KEY", "rerank-key")
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_BASE_URL", "https://rerank.example.com")
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_MODEL", "rerank-model")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="chat-key", base_url="https://example.com/v1", model="m"),
    )

    def _fake_rerank_pool(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(snapshot=lambda: {})

    monkeypatch.setattr("app.core.runtime.RerankSessionPool", _fake_rerank_pool)
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG",
        lambda **kwargs: SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True)),
    )

    bootstrap_generation_runtime(runtime)

    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})
    fake_session = SimpleNamespace(
        post=lambda endpoint, headers, json, timeout: (
            calls.update({"endpoint": endpoint, "headers": headers, "payload": json, "timeout": timeout}) or response
        )
    )
    calls["warm_lane_fn"](lane=SimpleNamespace(session=fake_session), timeout_seconds=12.0, reason="bootstrap")

    assert calls["headers"]["Authorization"] == "Bearer rerank-key"
    assert calls["endpoint"] == "https://rerank.example.com/api/v1/services/rerank/text-rerank/text-rerank"


def test_bootstrap_generation_runtime_marks_hot_pool_status_degraded_when_runtime_bootstrap_fails(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            generation_runtime_enabled=True,
            llm_http_shared_pool_enabled=False,
            stage2_chat_hot_pool_enabled=True,
            stage2_chat_hot_lane_count=1,
            stage2_chat_hot_keepalive_expiry_seconds=1800.0,
            stage2_rerank_hot_pool_enabled=True,
            stage2_rerank_hot_lane_count=1,
        ),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )

    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG",
        lambda **kwargs: SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=False, availability_detail="boom")),
    )

    bootstrap_generation_runtime(runtime)

    assert runtime.component_status["stage2_chat_hot_pool"]["status"] == "degraded"
    assert runtime.component_status["stage2_rerank_hot_pool"]["status"] == "degraded"


def test_bootstrap_generation_runtime_closes_partial_runtime_when_literature_expert_unavailable(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            generation_runtime_enabled=True,
            llm_http_shared_pool_enabled=False,
            stage2_chat_hot_pool_enabled=True,
            stage2_chat_hot_lane_count=1,
            stage2_chat_hot_keepalive_expiry_seconds=1800.0,
            stage2_rerank_hot_pool_enabled=True,
            stage2_rerank_hot_lane_count=1,
        ),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )
    calls = {"runtime": 0, "chat_pool": 0, "rerank_pool": 0}
    fake_chat_pool = SimpleNamespace(
        close=lambda: calls.__setitem__("chat_pool", calls["chat_pool"] + 1),
        snapshot=lambda: {},
    )
    fake_rerank_pool = SimpleNamespace(
        close=lambda: calls.__setitem__("rerank_pool", calls["rerank_pool"] + 1),
        snapshot=lambda: {},
    )

    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    monkeypatch.setattr("app.core.runtime.ChatHotLanePool", lambda **kwargs: fake_chat_pool)
    monkeypatch.setattr("app.core.runtime.RerankSessionPool", lambda **kwargs: fake_rerank_pool)
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG",
        lambda **kwargs: SimpleNamespace(
            close=lambda: calls.__setitem__("runtime", calls["runtime"] + 1),
            model="m",
            base_url="https://example.com/v1",
            literature_expert=SimpleNamespace(available=False, availability_detail="boom"),
        ),
    )

    bootstrap_generation_runtime(runtime)

    assert calls == {"runtime": 1, "chat_pool": 1, "rerank_pool": 1}
    assert runtime.generation_runtime is None
    assert runtime.generation_runtime_ready is False
    assert runtime.stage2_chat_hot_pool is None
    assert runtime.stage2_rerank_hot_pool is None
    assert runtime.component_status["generation_runtime"]["status"] == "degraded"
    assert runtime.component_status["stage2_chat_hot_pool"]["status"] == "degraded"
    assert runtime.component_status["stage2_rerank_hot_pool"]["status"] == "degraded"


def test_bootstrap_generation_runtime_initializes_rerank_hot_pool_when_enabled(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            generation_runtime_enabled=True,
            llm_http_shared_pool_enabled=False,
            stage2_rerank_hot_pool_enabled=True,
            stage2_rerank_hot_lane_count=3,
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

    def _fake_rerank_pool(**kwargs):
        calls["rerank_pool_kwargs"] = kwargs
        return fake_pool

    monkeypatch.setattr("app.core.runtime.RerankSessionPool", _fake_rerank_pool)
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG",
        lambda **kwargs: SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True)),
    )

    bootstrap_generation_runtime(runtime)

    assert runtime.stage2_rerank_hot_pool is fake_pool
    assert runtime.component_status["stage2_rerank_hot_pool"]["enabled"] is True


def test_bootstrap_generation_runtime_exposes_stage2_hot_pool_aggregate_fields(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            generation_runtime_enabled=True,
            llm_http_shared_pool_enabled=False,
            stage2_chat_hot_pool_enabled=True,
            stage2_chat_hot_lane_count=2,
            stage2_chat_hot_keepalive_expiry_seconds=1800.0,
            stage2_chat_warmup_enabled=True,
            stage2_chat_warm_interval_seconds=300,
            stage2_chat_warm_timeout_seconds=420.0,
            stage2_bootstrap_warm_max_parallel=1,
            stage2_bootstrap_warm_jitter_seconds=0,
            stage2_warm_jitter_seconds=0,
            stage2_lane_degraded_after_seconds=900,
        ),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )
    fake_pool = SimpleNamespace(
        snapshot=lambda: {
            "total_lanes": 2,
            "ready_lanes": 1,
            "warming_lanes": 1,
            "degraded_lanes": 0,
            "last_any_warm_success_at": "2026-04-22T12:00:00+08:00",
            "last_any_error_at": "",
            "last_error_summary": "",
            "next_keepalive_at": "2026-04-22T12:05:00+08:00",
        }
    )

    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    monkeypatch.setattr("app.core.runtime.ChatHotLanePool", lambda **kwargs: fake_pool)
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG",
        lambda **kwargs: SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True)),
    )

    bootstrap_generation_runtime(runtime)

    status = runtime.component_status["stage2_chat_hot_pool"]
    assert status["enabled"] is True
    assert status["total_lanes"] == 2
    assert status["ready_lanes"] == 1
    assert status["last_any_warm_success_at"] == "2026-04-22T12:00:00+08:00"
    assert status["next_keepalive_at"] == "2026-04-22T12:05:00+08:00"


def test_bootstrap_generation_runtime_passes_warm_active_window_to_hot_pools(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            generation_runtime_enabled=True,
            llm_http_shared_pool_enabled=False,
            stage2_chat_hot_pool_enabled=True,
            stage2_chat_hot_lane_count=1,
            stage2_chat_hot_keepalive_expiry_seconds=1800.0,
            stage2_chat_warmup_enabled=True,
            stage2_chat_warm_interval_seconds=7200,
            stage2_chat_warm_timeout_seconds=420.0,
            stage2_rerank_hot_pool_enabled=True,
            stage2_rerank_hot_lane_count=1,
            stage2_rerank_warmup_enabled=True,
            stage2_rerank_warm_interval_seconds=7200,
            stage2_rerank_warm_timeout_seconds=420.0,
            stage2_bootstrap_warm_max_parallel=1,
            stage2_bootstrap_warm_jitter_seconds=0,
            stage2_warm_jitter_seconds=0,
            stage2_lane_degraded_after_seconds=900,
            stage2_warm_active_start_hour=8,
            stage2_warm_active_end_hour=18,
        ),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )
    calls: dict[str, dict[str, object]] = {}

    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    monkeypatch.setattr(
        "app.core.runtime.ChatHotLanePool",
        lambda **kwargs: calls.__setitem__("chat", kwargs) or SimpleNamespace(snapshot=lambda: {}),
    )
    monkeypatch.setattr(
        "app.core.runtime.RerankSessionPool",
        lambda **kwargs: calls.__setitem__("rerank", kwargs) or SimpleNamespace(snapshot=lambda: {}),
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG",
        lambda **kwargs: SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True)),
    )

    bootstrap_generation_runtime(runtime)

    assert calls["chat"]["warm_active_start_hour"] == 8
    assert calls["chat"]["warm_active_end_hour"] == 18
    assert calls["rerank"]["warm_active_start_hour"] == 8
    assert calls["rerank"]["warm_active_end_hour"] == 18
