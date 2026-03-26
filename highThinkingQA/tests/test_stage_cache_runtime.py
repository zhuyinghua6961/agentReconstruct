from __future__ import annotations

from types import SimpleNamespace

from server.services.redis_client import (
    RedisService,
    bootstrap_redis_state,
    build_redis_bindings,
    build_key_factory,
    get_redis_settings,
    redact_redis_url,
)


def test_redact_redis_url_masks_password():
    assert redact_redis_url("redis://:123456@127.0.0.1:6379/0") == "redis://:***@127.0.0.1:6379/0"


def test_get_redis_settings_uses_service_namespace_by_default(monkeypatch):
    for name in (
        "REDIS_ENABLED",
        "REDIS_URL",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "REDIS_DB",
        "REDIS_KEY_PREFIX",
        "REDIS_SOCKET_CONNECT_TIMEOUT_SEC",
        "REDIS_SOCKET_TIMEOUT_SEC",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = get_redis_settings()

    assert settings.enabled is False
    assert settings.key_prefix == "highthinkingqa"
    assert settings.resolved_url == "redis://:123456@127.0.0.1:6379/0"


def test_build_redis_bindings_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "server.services.redis_client.get_redis_settings",
        lambda: SimpleNamespace(
            enabled=False,
            resolved_url="redis://:123456@127.0.0.1:6379/0",
            key_prefix="highthinking",
            socket_connect_timeout_sec=2,
            socket_timeout_sec=2,
        ),
    )

    bindings = build_redis_bindings(redis_lib=None)

    assert bindings.enabled is False
    assert bindings.available is False
    assert bindings.client is None
    assert bindings.detail == "redis disabled by config"


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

    monkeypatch.setattr(
        "server.services.redis_client.get_redis_settings",
        lambda: SimpleNamespace(
            enabled=True,
            resolved_url="redis://:123456@127.0.0.1:6379/0",
            key_prefix="highthinking",
            socket_connect_timeout_sec=2,
            socket_timeout_sec=2,
        ),
    )

    bindings = build_redis_bindings(redis_lib=SimpleNamespace(Redis=FakeClient))

    assert bindings.enabled is True
    assert bindings.available is True
    assert bindings.client is not None
    assert bindings.detail == "redis connected"
    assert bindings.url == "redis://:***@127.0.0.1:6379/0"
    assert calls["url"] == "redis://:123456@127.0.0.1:6379/0"
    assert calls["ping"] is True


def test_bootstrap_redis_state_updates_app_state(monkeypatch):
    app_state = SimpleNamespace(component_status={}, redis_bindings=None, redis_service=None)
    service = RedisService.from_prefix(client=object(), key_prefix="highthinking")
    bindings = SimpleNamespace(
        enabled=True,
        available=True,
        client=object(),
        library_available=True,
        detail="redis connected",
        error="",
        url="redis://:***@127.0.0.1:6379/0",
        key_prefix="highthinking",
    )

    monkeypatch.setattr("server.services.redis_client.build_redis_bindings", lambda redis_lib=None: bindings)
    monkeypatch.setattr("server.services.redis_client.get_redis_service", lambda: service)

    bootstrap_redis_state(app_state)

    assert app_state.redis_bindings is bindings
    assert app_state.redis_service is service
    assert app_state.component_status["redis"]["status"] == "ok"
    assert app_state.component_status["redis"]["available"] is True


def test_build_key_factory_namespaces_segments():
    factory = build_key_factory("highthinking")

    assert factory.cache("direct_answer", "abc") == "highthinking:cache:direct_answer:abc"
    assert factory.lock("retrieve", "abc") == "highthinking:lock:retrieve:abc"
