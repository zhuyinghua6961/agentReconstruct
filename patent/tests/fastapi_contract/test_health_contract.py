import json
import runpy
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

import config as patent_config
import server_fastapi.app as patent_fastapi_app
from server_fastapi.app import create_app


ROOT_DIR = Path(__file__).resolve().parents[2]
TEST_JWT_SECRET = "patent-test-secret"



def _make_bearer_token(user_id: int, *, secret: str = TEST_JWT_SECRET) -> str:
    serializer = URLSafeTimedSerializer(secret)
    return serializer.dumps({"user_id": user_id, "role": "user"}, salt="highthinking.auth.access")



def test_create_app_exposes_patent_runtime_defaults():
    app = create_app()

    assert app.state.service_name == "patent"
    assert "redis" in app.state.component_status
    assert "authority" in app.state.component_status
    assert "runtime" in app.state.component_status
    assert app.state.component_status["redis"]["ready"] is False
    assert app.state.component_status["authority"]["ready"] is False
    assert app.state.component_status["runtime"]["ready"] is True


def test_get_settings_reads_environment_at_call_time(monkeypatch):
    monkeypatch.setenv("PATENT_PORT", "9898")
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("JWT_EXPIRE_SECONDS", "7200")
    monkeypatch.setenv("JWT_COMPATIBLE_ACCESS_SALTS", "agentcode.auth.access,legacy.auth.access")

    settings = patent_config.get_settings()

    assert settings.http.port == 9898
    assert settings.durable_mode_enabled is True
    assert settings.auth.jwt_secret == TEST_JWT_SECRET
    assert settings.auth.jwt_expire_seconds == 7200
    assert settings.auth.jwt_compatible_access_salts == ("agentcode.auth.access", "legacy.auth.access")


def test_get_settings_reads_patent_gunicorn_scaling_env(monkeypatch):
    monkeypatch.setenv("PATENT_GUNICORN_WORKERS", "2")
    monkeypatch.setenv("PATENT_GUNICORN_THREADS", "12")
    monkeypatch.setenv("PATENT_GUNICORN_TIMEOUT", "90")
    monkeypatch.setenv("PATENT_GUNICORN_KEEPALIVE", "20")
    monkeypatch.setenv("PATENT_GUNICORN_MAX_REQUESTS", "1500")
    monkeypatch.setenv("PATENT_GUNICORN_MAX_REQUESTS_JITTER", "250")

    settings = patent_config.get_settings()

    assert settings.gunicorn.workers == 2
    assert settings.gunicorn.threads == 12
    assert settings.gunicorn.timeout == 90
    assert settings.gunicorn.keepalive == 20
    assert settings.gunicorn.max_requests == 1500
    assert settings.gunicorn.max_requests_jitter == 250


def test_gunicorn_conf_reads_extended_patent_worker_settings(monkeypatch):
    monkeypatch.setenv("PATENT_HOST", "127.0.0.1")
    monkeypatch.setenv("PATENT_PORT", "9898")
    monkeypatch.setenv("PATENT_GUNICORN_WORKERS", "3")
    monkeypatch.setenv("PATENT_GUNICORN_THREADS", "10")
    monkeypatch.setenv("PATENT_GUNICORN_TIMEOUT", "95")
    monkeypatch.setenv("PATENT_GUNICORN_KEEPALIVE", "25")
    monkeypatch.setenv("PATENT_GUNICORN_MAX_REQUESTS", "1800")
    monkeypatch.setenv("PATENT_GUNICORN_MAX_REQUESTS_JITTER", "300")
    monkeypatch.setenv("PATENT_GUNICORN_WORKER_CLASS", "uvicorn.workers.UvicornH11Worker")

    values = runpy.run_path(str(ROOT_DIR / "server_fastapi" / "gunicorn.conf.py"))

    assert values["bind"] == "127.0.0.1:9898"
    assert values["worker_class"] == "uvicorn.workers.UvicornH11Worker"
    assert values["workers"] == 3
    assert values["threads"] == 10
    assert values["timeout"] == 95
    assert values["keepalive"] == 25
    assert values["max_requests"] == 1800
    assert values["max_requests_jitter"] == 300


def test_gunicorn_conf_loads_patent_local_env_files_when_process_env_is_absent(monkeypatch, tmp_path):
    temp_root = tmp_path / "patent_config_case"
    temp_root.mkdir()
    (temp_root / "server_fastapi").mkdir()
    (temp_root / "config.py").write_text((ROOT_DIR / "config.py").read_text(encoding="utf-8"), encoding="utf-8")
    (temp_root / "server_fastapi" / "gunicorn.conf.py").write_text(
        (ROOT_DIR / "server_fastapi" / "gunicorn.conf.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (temp_root / "config.shared.env").write_text(
        "PATENT_HOST=127.0.0.1\n"
        "PATENT_PORT=9797\n"
        "PATENT_GUNICORN_WORKERS=4\n"
        "PATENT_GUNICORN_THREADS=11\n"
        "PATENT_GUNICORN_TIMEOUT=88\n"
        "PATENT_GUNICORN_KEEPALIVE=22\n"
        "PATENT_GUNICORN_MAX_REQUESTS=1700\n"
        "PATENT_GUNICORN_MAX_REQUESTS_JITTER=275\n"
        "PATENT_GUNICORN_WORKER_CLASS=uvicorn.workers.UvicornH11Worker\n",
        encoding="utf-8",
    )
    for name in (
        "PATENT_HOST",
        "PATENT_PORT",
        "PATENT_GUNICORN_WORKERS",
        "PATENT_GUNICORN_THREADS",
        "PATENT_GUNICORN_TIMEOUT",
        "PATENT_GUNICORN_KEEPALIVE",
        "PATENT_GUNICORN_MAX_REQUESTS",
        "PATENT_GUNICORN_MAX_REQUESTS_JITTER",
        "PATENT_GUNICORN_WORKER_CLASS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.syspath_prepend(str(temp_root))
    monkeypatch.delitem(sys.modules, "config", raising=False)

    values = runpy.run_path(str(temp_root / "server_fastapi" / "gunicorn.conf.py"))

    assert values["bind"] == "127.0.0.1:9797"
    assert values["worker_class"] == "uvicorn.workers.UvicornH11Worker"
    assert values["workers"] == 4
    assert values["threads"] == 11
    assert values["timeout"] == 88
    assert values["keepalive"] == 22
    assert values["max_requests"] == 1700
    assert values["max_requests_jitter"] == 275


def test_patent_local_env_precedence_is_process_then_dotenv_then_secret_then_shared(monkeypatch, tmp_path):
    temp_root = tmp_path / "patent_secret_case"
    temp_root.mkdir()
    (temp_root / "config.py").write_text((ROOT_DIR / "config.py").read_text(encoding="utf-8"), encoding="utf-8")
    (temp_root / "config.shared.env").write_text(
        "PATENT_PORT=7777\n"
        "PATENT_GUNICORN_THREADS=11\n"
        "PATENT_AUTHORITY_INTERNAL_TOKEN=\n"
        "JWT_SECRET=\n",
        encoding="utf-8",
    )
    (temp_root / "config.secret.env").write_text(
        "PATENT_PORT=8888\n"
        "PATENT_GUNICORN_THREADS=12\n"
        "PATENT_AUTHORITY_INTERNAL_TOKEN=secret-token\n"
        "JWT_SECRET=secret-jwt\n",
        encoding="utf-8",
    )
    (temp_root / ".env").write_text(
        "PATENT_AUTHORITY_INTERNAL_TOKEN=dotenv-token\n"
        "JWT_SECRET=dotenv-jwt\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATENT_GUNICORN_THREADS", "9")
    for name in ("PATENT_HOST", "PATENT_PORT", "PATENT_AUTHORITY_INTERNAL_TOKEN", "JWT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.syspath_prepend(str(temp_root))
    monkeypatch.delitem(sys.modules, "config", raising=False)

    loaded = runpy.run_path(str(temp_root / "config.py"))
    settings = loaded["get_settings"]()

    assert settings.http.port == 8888
    assert settings.gunicorn.threads == 9
    assert settings.authority.internal_token == "dotenv-token"
    assert settings.auth.jwt_secret == "dotenv-jwt"


def test_get_settings_rejects_invalid_boolean_env(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "flase")

    with pytest.raises(ValueError, match="PATENT_DURABLE_MODE_ENABLED"):
        patent_config.get_settings()


def test_start_script_uses_conda_agent_and_gunicorn():
    script = (ROOT_DIR / "scripts" / "start.sh").read_text(encoding="utf-8")

    assert "conda run -n agent" in script
    assert "gunicorn" in script
    assert "server_fastapi/gunicorn.conf.py" in script


def test_pyproject_includes_server_package_discovery_for_future_tasks():
    pyproject = (ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8")

    assert '"server_fastapi*"' in pyproject
    assert '"server*"' in pyproject


def test_versioned_health_route_returns_ok_by_default():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["service"] == "patent"



def test_health_contract_exposes_runtime_concurrency_and_trace(monkeypatch):
    monkeypatch.setenv("PATENT_ASK_STREAM_MAX_CONCURRENT", "2")
    monkeypatch.setenv("PATENT_ASK_EXECUTOR_MAX_WORKERS", "3")
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health", headers={"X-Trace-ID": "req_contract"})

    assert response.status_code == 200
    payload = response.json()
    runtime = payload["components"]["runtime"]
    assert runtime["stream_slots_capacity"] == 2
    assert runtime["stream_slots_available"] == 2
    assert runtime["ask_executor_max_workers"] == 3
    assert response.headers["X-Trace-ID"] == "req_contract"

def test_health_returns_503_when_runtime_not_ready():
    app = create_app()
    app.state.component_status["runtime"]["ready"] = False

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["components"]["runtime"]["ready"] is False


def test_health_returns_503_when_dispatcher_runtime_state_degrades_after_start():
    app = create_app()
    runtime_state = dict(app.state.runtime_dispatcher.runtime_state())
    app.state.runtime_dispatcher.runtime_state = lambda: {**runtime_state, "ready": False}

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["components"]["runtime"]["ready"] is False


def test_durable_patent_auth_requires_authorization_header():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health", params={"durable": "true"})

    assert response.status_code == 401
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == "TOKEN_MISSING"


def test_durable_patent_auth_requires_explicit_jwt_secret(monkeypatch):
    app = create_app()
    monkeypatch.delenv("JWT_SECRET", raising=False)

    with TestClient(app) as client:
        response = client.get(
            "/api/health",
            params={"durable": "true"},
            headers={"Authorization": "Bearer demo-token"},
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "SERVICE_NOT_READY"


def test_durable_patent_auth_requires_user_id_derivation_context(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/api/health",
            params={"durable": "true"},
            headers={"Authorization": "Bearer demo-token"},
        )

    assert response.status_code == 401
    payload = response.json()
    assert payload["code"] == "TOKEN_INVALID"


def test_durable_patent_auth_maps_bad_serializer_data_to_401(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/api/health",
            params={"durable": "true"},
            headers={"Authorization": "Bearer not.a.valid.serializer.token"},
        )

    assert response.status_code == 401
    payload = response.json()
    assert payload["code"] == "TOKEN_INVALID"


def test_durable_mode_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    token = _make_bearer_token(42)

    with TestClient(app) as client:
        response = client.get(
            "/api/health",
            params={"durable": "true"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "DURABLE_MODE_DISABLED"


def test_plain_health_returns_503_when_durable_mode_is_enabled_without_ready_dependencies(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["durable_mode_enabled"] is True
    assert payload["components"]["redis"]["ready"] is False
    assert payload["components"]["authority"]["ready"] is False


def test_create_app_closes_authority_client_on_shutdown(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    instances = []

    class _ClosableAuthorityClient:
        def __init__(self):
            self.closed = False
            instances.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(patent_fastapi_app, "ConversationAuthorityClient", _ClosableAuthorityClient)
    app = create_app()

    with TestClient(app):
        pass

    assert len(instances) == 1
    assert instances[0].closed is True


def test_create_app_closes_redis_client_on_shutdown(monkeypatch):
    instances = []

    class _ClosableRedisClient:
        def __init__(self):
            self.closed = False
            instances.append(self)

        def close(self):
            self.closed = True

    def _bootstrap_redis_state(app_state, *, redis_lib=None):
        app_state.redis_bindings = type("_Bindings", (), {"client": _ClosableRedisClient()})()
        app_state.redis_key_factory = object()
        app_state.component_status["redis"] = {"ready": True}

    monkeypatch.setattr(patent_fastapi_app, "bootstrap_redis_state", _bootstrap_redis_state)
    app = create_app()

    with TestClient(app):
        pass

    assert len(instances) == 1
    assert instances[0].closed is True


def test_create_app_shutdown_tolerates_preclosed_resources(monkeypatch):
    authority_instances = []
    redis_instances = []
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")

    class _RaisingOnSecondClose:
        def __init__(self, bucket):
            self.closed = False
            bucket.append(self)

        def close(self):
            if self.closed:
                raise RuntimeError("already closed")
            self.closed = True

    class _AuthorityClient(_RaisingOnSecondClose):
        def __init__(self):
            super().__init__(authority_instances)

    def _bootstrap_redis_state(app_state, *, redis_lib=None):
        app_state.redis_bindings = type("_Bindings", (), {"client": _RaisingOnSecondClose(redis_instances)})()
        app_state.redis_key_factory = object()
        app_state.component_status["redis"] = {"ready": True}

    monkeypatch.setattr(patent_fastapi_app, "ConversationAuthorityClient", _AuthorityClient)
    monkeypatch.setattr(patent_fastapi_app, "bootstrap_redis_state", _bootstrap_redis_state)
    app = create_app()

    with TestClient(app):
        app.state.authority_client.close()
        app.state.redis_bindings.client.close()

    assert authority_instances[0].closed is True
    assert redis_instances[0].closed is True
    assert app.state.authority_client is None
    assert app.state.redis_bindings.client is None


def test_reused_app_rebootstraps_resources_on_next_startup(monkeypatch):
    authority_instances = []
    redis_instances = []
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")

    class _AuthorityClient:
        def __init__(self):
            self.closed = False
            authority_instances.append(self)

        def close(self):
            self.closed = True

    class _RedisClient:
        def __init__(self):
            self.closed = False
            redis_instances.append(self)

        def close(self):
            self.closed = True

    def _bootstrap_redis_state(app_state, *, redis_lib=None):
        app_state.redis_bindings = type("_Bindings", (), {"client": _RedisClient()})()
        app_state.redis_key_factory = object()
        app_state.component_status["redis"] = {"ready": True}

    monkeypatch.setattr(patent_fastapi_app, "ConversationAuthorityClient", _AuthorityClient)
    monkeypatch.setattr(patent_fastapi_app, "bootstrap_redis_state", _bootstrap_redis_state)
    app = create_app()

    assert len(authority_instances) == 1
    assert len(redis_instances) == 1

    with TestClient(app):
        assert app.state.authority_client is authority_instances[0]
        assert app.state.redis_bindings.client is redis_instances[0]

    assert app.state.authority_client is None
    assert app.state.redis_bindings.client is None

    with TestClient(app):
        assert len(authority_instances) == 2
        assert len(redis_instances) == 2
        assert app.state.authority_client is authority_instances[1]
        assert app.state.redis_bindings.client is redis_instances[1]


def test_create_app_cleans_up_open_resources_when_bootstrap_fails(monkeypatch):
    authority_instances = []
    redis_instances = []
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")

    class _AuthorityClient:
        def __init__(self):
            self.closed = False
            authority_instances.append(self)

        def close(self):
            self.closed = True

    class _RedisClient:
        def __init__(self):
            self.closed = False
            redis_instances.append(self)

        def close(self):
            self.closed = True

    def _bootstrap_redis_state(app_state, *, redis_lib=None):
        app_state.redis_bindings = type("_Bindings", (), {"client": _RedisClient()})()
        app_state.redis_key_factory = object()
        app_state.component_status["redis"] = {"ready": True}

    def _failing_bootstrap_service_state(app):
        raise RuntimeError("bootstrap boom")

    monkeypatch.setattr(patent_fastapi_app, "ConversationAuthorityClient", _AuthorityClient)
    monkeypatch.setattr(patent_fastapi_app, "bootstrap_redis_state", _bootstrap_redis_state)
    monkeypatch.setattr(patent_fastapi_app, "_bootstrap_service_state", _failing_bootstrap_service_state)

    with pytest.raises(RuntimeError, match="bootstrap boom"):
        create_app()

    assert len(authority_instances) == 1
    assert len(redis_instances) == 1
    assert authority_instances[0].closed is True
    assert redis_instances[0].closed is True


def test_rebootstrap_failure_closes_reopened_resources(monkeypatch):
    authority_instances = []
    redis_instances = []
    bootstrap_calls = []
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")

    class _AuthorityClient:
        def __init__(self):
            self.closed = False
            authority_instances.append(self)

        def close(self):
            self.closed = True

    class _RedisClient:
        def __init__(self):
            self.closed = False
            redis_instances.append(self)

        def close(self):
            self.closed = True

    def _bootstrap_redis_state(app_state, *, redis_lib=None):
        app_state.redis_bindings = type("_Bindings", (), {"client": _RedisClient()})()
        app_state.redis_key_factory = object()
        app_state.component_status["redis"] = {"ready": True}

    def _bootstrap_service_state_once_then_fail(app):
        bootstrap_calls.append(len(bootstrap_calls) + 1)
        if len(bootstrap_calls) == 1:
            app.state.ask_service = object()
            return
        raise RuntimeError("rebootstrap boom")

    monkeypatch.setattr(patent_fastapi_app, "ConversationAuthorityClient", _AuthorityClient)
    monkeypatch.setattr(patent_fastapi_app, "bootstrap_redis_state", _bootstrap_redis_state)
    monkeypatch.setattr(patent_fastapi_app, "_bootstrap_service_state", _bootstrap_service_state_once_then_fail)
    app = create_app()

    with TestClient(app):
        pass

    assert app.state.authority_client is None
    assert app.state.redis_bindings.client is None

    with pytest.raises(RuntimeError, match="rebootstrap boom"):
        with TestClient(app):
            pass

    assert len(authority_instances) == 2
    assert len(redis_instances) == 2
    assert authority_instances[1].closed is True
    assert redis_instances[1].closed is True


def test_health_returns_503_when_durable_mode_is_enabled_without_ready_dependencies(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    token = _make_bearer_token(42)

    with TestClient(app) as client:
        response = client.get(
            "/api/health",
            params={"durable": "true"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "SERVICE_NOT_READY"
    assert payload["components"]["redis"]["ready"] is False
    assert payload["components"]["authority"]["ready"] is False
