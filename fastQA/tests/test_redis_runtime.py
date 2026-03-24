from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.core.config import get_settings
from app.core.runtime import (
    bootstrap_generation_runtime,
    bootstrap_redis,
    close_generation_runtime,
    close_redis,
    generation_runtime_is_ready,
)
from app.integrations.redis.client import build_redis_bindings, redact_redis_url


def _reset_settings_cache() -> None:
    get_settings.cache_clear()


def test_settings_resolve_redis_defaults(monkeypatch):
    for name in (
        "REDIS_ENABLED",
        "REDIS_URL",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_USERNAME",
        "REDIS_PASSWORD",
        "REDIS_DB",
        "REDIS_KEY_PREFIX",
        "REDIS_SOCKET_CONNECT_TIMEOUT_SEC",
        "REDIS_SOCKET_TIMEOUT_SEC",
    ):
        monkeypatch.delenv(name, raising=False)

    _reset_settings_cache()
    settings = get_settings()

    assert settings.redis_enabled is False
    assert settings.redis_password == "123456"
    assert settings.redis_key_prefix == "fastqa"
    assert settings.resolved_redis_url == "redis://:123456@127.0.0.1:6379/0"

    _reset_settings_cache()


def test_fastqa_shared_config_enables_redis_by_default():
    repo_root = Path(__file__).resolve().parents[2]
    shared_env = repo_root / "resource/config/services/fastQA/config.shared.env"
    content = shared_env.read_text(encoding="utf-8")

    assert "REDIS_ENABLED=1" in content
    assert "REDIS_KEY_PREFIX=fastqa" in content


def test_redact_redis_url_masks_password():
    assert redact_redis_url("redis://:123456@127.0.0.1:6379/0") == "redis://:***@127.0.0.1:6379/0"


def test_build_redis_bindings_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("REDIS_ENABLED", "0")

    _reset_settings_cache()
    settings = get_settings()
    bindings = build_redis_bindings(settings=settings, redis_lib=None)

    assert bindings.enabled is False
    assert bindings.available is False
    assert bindings.client is None
    assert bindings.detail == "redis disabled by config"

    _reset_settings_cache()


def test_build_redis_bindings_connects_with_fake_library(monkeypatch):
    calls: dict[str, object] = {}

    class FakeClient:
        @classmethod
        def from_url(cls, url, **kwargs):
            calls["url"] = url
            calls["kwargs"] = kwargs
            return cls()

        def ping(self):
            calls["ping"] = True
            return True

    monkeypatch.setenv("REDIS_ENABLED", "1")
    monkeypatch.setenv("REDIS_PASSWORD", "123456")

    _reset_settings_cache()
    settings = get_settings()
    bindings = build_redis_bindings(settings=settings, redis_lib=SimpleNamespace(Redis=FakeClient))

    assert bindings.enabled is True
    assert bindings.available is True
    assert bindings.client is not None
    assert bindings.detail == "redis connected"
    assert bindings.url == "redis://:***@127.0.0.1:6379/0"
    assert calls["url"] == "redis://:123456@127.0.0.1:6379/0"
    assert calls["kwargs"] == {
        "decode_responses": False,
        "socket_connect_timeout": settings.redis_socket_connect_timeout_sec,
        "socket_timeout": settings.redis_socket_timeout_sec,
    }
    assert calls["ping"] is True

    _reset_settings_cache()


def test_bootstrap_redis_updates_runtime_status(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(redis_key_prefix="agentcode"),
        redis_bindings=None,
        redis_client=None,
        redis_service=None,
        component_status={},
        health_flags={},
    )

    monkeypatch.setattr(
        "app.core.runtime.build_redis_bindings",
        lambda settings: SimpleNamespace(
            enabled=True,
            available=True,
            client=object(),
            library_available=True,
            detail="redis connected",
            error="",
            url="redis://:***@127.0.0.1:6379/0",
            key_prefix="agentcode",
        ),
    )

    bootstrap_redis(runtime)

    assert runtime.redis_client is not None
    assert runtime.redis_service is not None
    assert runtime.health_flags["redis"] == "ok"
    assert runtime.component_status["redis"]["status"] == "ok"
    assert runtime.component_status["redis"]["available"] is True
    assert runtime.component_status["redis"]["url"] == "redis://:***@127.0.0.1:6379/0"


def test_bootstrap_generation_runtime_skips_when_disabled():
    runtime = SimpleNamespace(
        settings=SimpleNamespace(generation_runtime_enabled=False),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
    )

    bootstrap_generation_runtime(runtime)

    assert runtime.generation_runtime is None
    assert runtime.generation_runtime_ready is False
    assert runtime.component_status["generation_runtime"]["status"] == "skipped"
    assert generation_runtime_is_ready(runtime) is False


def test_bootstrap_generation_runtime_degrades_when_required_env_missing(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(generation_runtime_enabled=True),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
    )

    monkeypatch.setattr(
        "app.core.runtime.resolve_generation_runtime_inputs",
        lambda **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="", base_url="", model="m"),
    )

    bootstrap_generation_runtime(runtime)

    assert runtime.generation_runtime is None
    assert runtime.generation_runtime_ready is False
    assert runtime.component_status["generation_runtime"]["status"] == "degraded"
    assert generation_runtime_is_ready(runtime) is False


def test_bootstrap_generation_runtime_marks_ready(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(generation_runtime_enabled=True),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
    )

    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="k", base_url="https://example.com/v1", model="m"),
    )
    sentinel = SimpleNamespace(model="m", base_url="https://example.com/v1")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG",
        lambda: sentinel,
    )

    bootstrap_generation_runtime(runtime)

    assert runtime.generation_runtime is sentinel
    assert runtime.generation_runtime_ready is True
    assert runtime.component_status["generation_runtime"]["status"] == "ok"
    assert generation_runtime_is_ready(runtime) is True


def test_close_generation_runtime_closes_client():
    calls = {"closed": False}

    runtime = SimpleNamespace(
        generation_runtime=SimpleNamespace(close=lambda: calls.__setitem__("closed", True)),
        generation_runtime_ready=True,
    )

    close_generation_runtime(runtime)

    assert calls["closed"] is True
    assert runtime.generation_runtime is None
    assert runtime.generation_runtime_ready is False
