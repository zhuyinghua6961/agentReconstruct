from app.core.config import RedisSettings
from app.integrations.redis import service as redis_service_module
from app.integrations.redis.service import RedisService, bootstrap_redis_runtime


def _settings(**overrides) -> RedisSettings:
    payload = {
        "enabled": False,
        "url": "",
        "host": "127.0.0.1",
        "port": 6379,
        "username": "",
        "password": "",
        "db": 0,
        "key_prefix": "gateway",
        "socket_connect_timeout_seconds": 2,
        "socket_timeout_seconds": 2,
    }
    payload.update(overrides)
    return RedisSettings(**payload)


def test_redis_service_uses_service_local_prefixing():
    service = RedisService.from_prefix(client=None, key_prefix="gateway")

    assert service.prefixed("admission", "queue") == "gateway:admission:queue"
    assert service.key_factory.admission("status", "req_1") == "gateway:admission:status:req_1"
    assert service.key_factory.relay("req_1", "frames") == "gateway:relay:req_1:frames"


def test_bootstrap_redis_runtime_reports_disabled_mode():
    runtime = bootstrap_redis_runtime(_settings(enabled=False))

    assert runtime.client is None
    assert runtime.service.available is False
    assert runtime.status.enabled is False
    assert runtime.status.client_source == "disabled"


def test_bootstrap_redis_runtime_reports_missing_dependency(monkeypatch):
    monkeypatch.setattr(redis_service_module, "_import_redis_module", lambda: None)

    runtime = bootstrap_redis_runtime(_settings(enabled=True))

    assert runtime.client is None
    assert runtime.status.enabled is True
    assert runtime.status.available is False
    assert runtime.status.dependency_available is False
    assert runtime.status.error == "redis_dependency_missing"
