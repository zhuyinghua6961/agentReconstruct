import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

import config as patent_config
import server_fastapi.app as patent_fastapi_app
from server_fastapi.app import create_app


ROOT_DIR = Path(__file__).resolve().parents[2]
TEST_JWT_SECRET = "patent-test-secret"


@pytest.fixture(autouse=True)
def _fast_degraded_graph_bootstrap(monkeypatch):
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "false")
    monkeypatch.setenv("PATENT_PLANNING_HOT_POOL_ENABLED", "false")
    monkeypatch.setenv("PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED", "false")
    monkeypatch.setenv("PATENT_PLANNING_UPSTREAM_GATE_ENABLED", "false")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "false")
    monkeypatch.setattr(
        patent_fastapi_app,
        "bootstrap_patent_neo4j_client",
        lambda **kwargs: SimpleNamespace(available=False, degraded=True, error="test graph unavailable", close=lambda: None),
    )



def _make_bearer_token(user_id: int, *, secret: str = TEST_JWT_SECRET) -> str:
    serializer = URLSafeTimedSerializer(secret)
    return serializer.dumps({"user_id": user_id, "role": "user"}, salt="highthinking.auth.access")



def test_create_app_exposes_patent_runtime_defaults(monkeypatch):
    monkeypatch.setenv("PATENT_GRAPH_KB_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_V2_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_RAG_INJECTION_ENABLED", "false")
    app = create_app()

    assert app.state.service_name == "patent"
    assert "redis" in app.state.component_status
    assert "authority" in app.state.component_status
    assert "runtime" in app.state.component_status
    assert "shared_llm_pool" in app.state.component_status
    assert "planning_upstream_gate" in app.state.component_status
    assert "patent_graph_kb" in app.state.component_status
    assert app.state.component_status["redis"]["ready"] is False
    assert app.state.component_status["authority"]["ready"] is False
    assert app.state.component_status["runtime"]["ready"] is True
    assert app.state.component_status["shared_llm_pool"]["ready"] is False
    assert app.state.component_status["shared_llm_pool"]["enabled"] is False
    assert app.state.component_status["shared_llm_pool"]["status"] == "disabled"
    assert app.state.component_status["planning_upstream_gate"]["enabled"] is False
    assert app.state.component_status["planning_upstream_gate"]["status"] == "disabled"
    assert app.state.shared_llm_pool is app.state.patent_shared_upstream_provider
    assert app.state.component_status["patent_graph_kb"]["ready"] is False
    assert app.state.component_status["patent_graph_kb"]["enabled"] is False
    assert app.state.component_status["patent_graph_kb"]["v2_enabled"] is False
    assert app.state.component_status["patent_graph_kb"]["rag_injection_enabled"] is False
    assert app.state.component_status["patent_graph_kb"]["status"] == "skipped"


def test_config_shared_env_example_documents_shared_upstream_pool_defaults():
    content = (ROOT_DIR / "config.shared.env.example").read_text(encoding="utf-8")

    assert "PATENT_LLM_HTTP_SHARED_POOL_ENABLED=false" in content
    assert "PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS=120" in content
    assert "PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS=20" in content
    assert "PATENT_LLM_HTTP_MAX_CONNECTIONS=100" in content


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


def test_get_settings_enables_durable_mode_by_default(monkeypatch):
    monkeypatch.delenv("PATENT_DURABLE_MODE_ENABLED", raising=False)

    settings = patent_config.get_settings()

    assert settings.durable_mode_enabled is True


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
    assert "server_fastapi.asgi:app" in script


def test_start_gunicorn_script_bootstraps_patent_durable_runtime_env():
    script = (ROOT_DIR / "scripts" / "start_gunicorn.sh").read_text(encoding="utf-8")

    assert "server_fastapi.asgi:app" in script
    assert "PATENT_DURABLE_MODE_ENABLED" in script
    assert "PATENT_DURABLE_AUTHORITY_ENABLED" in script
    assert "PATENT_AUTHORITY_BASE_URL" in script
    assert "PATENT_REDIS_ENABLED" in script
    assert "PATENT_REDIS_URL" in script
    assert "PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN" in script


def test_asgi_module_exposes_patent_app():
    namespace = runpy.run_path(str(ROOT_DIR / "server_fastapi" / "asgi.py"))

    app = namespace["app"]
    assert app.state.service_name == "patent"


def test_pyproject_includes_server_package_discovery_for_future_tasks():
    pyproject = (ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8")

    assert '"server_fastapi*"' in pyproject
    assert '"server*"' in pyproject


def test_versioned_health_route_returns_ok_by_default(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
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
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_V2_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_RAG_INJECTION_ENABLED", "false")
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health", headers={"X-Trace-ID": "req_contract"})

    assert response.status_code == 200
    payload = response.json()
    runtime = payload["components"]["runtime"]
    assert runtime["stream_slots_capacity"] == 2
    assert runtime["stream_slots_available"] == 2
    assert runtime["ask_executor_max_workers"] == 3
    assert payload["patent_graph_kb_enabled"] is False
    assert payload["patent_graph_kb_ready"] is False
    assert payload["patent_graph_kb_v2_enabled"] is False
    assert payload["patent_graph_kb_rag_injection_enabled"] is False
    assert payload["components"]["shared_llm_pool"]["status"] == "disabled"
    assert payload["components"]["patent_graph_kb"]["status"] == "skipped"
    assert response.headers["X-Trace-ID"] == "req_contract"


def test_health_exposes_shared_llm_pool_snapshot(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "true")

    class _SharedProvider:
        enabled = True

        def client(self):
            return object()

        def close(self):
            return None

        def snapshot(self) -> dict[str, object]:
            return {
                "pool_owner": "app",
                "client_owner": "shared",
                "shared_client_id": "shared-123",
                "pid": 321,
                "bootstrap_source": "startup",
                "pool_timeout_count": 2,
                "pool_wait_ms": 18.5,
                "max_connections": 100,
                "max_keepalive_connections": 20,
                "keepalive_expiry_seconds": 120.0,
            }

    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentSharedUpstreamHttpProvider",
        type("_ProviderFactory", (), {"from_env": staticmethod(lambda: _SharedProvider())}),
        raising=False,
    )

    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    shared_llm_pool = payload["components"]["shared_llm_pool"]
    assert shared_llm_pool["enabled"] is True
    assert shared_llm_pool["ready"] is True
    assert shared_llm_pool["status"] == "ok"
    assert shared_llm_pool["client_owner"] == "shared"
    assert shared_llm_pool["shared_client_id"] == "shared-123"
    assert shared_llm_pool["pool_timeout_count"] == 2
    assert shared_llm_pool["pool_wait_ms"] == 18.5


def test_health_refreshes_shared_llm_pool_snapshot_at_request_time(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "true")

    class _SharedProvider:
        enabled = True

        def __init__(self) -> None:
            self.pool_timeout_count = 1
            self.pool_wait_ms = 12.5

        def client(self):
            return object()

        def close(self):
            return None

        def snapshot(self) -> dict[str, object]:
            return {
                "pool_owner": "app",
                "client_owner": "shared",
                "shared_client_id": "shared-live",
                "pid": 321,
                "bootstrap_source": "startup",
                "pool_timeout_count": self.pool_timeout_count,
                "pool_wait_ms": self.pool_wait_ms,
                "max_connections": 100,
                "max_keepalive_connections": 20,
                "keepalive_expiry_seconds": 120.0,
            }

    provider = _SharedProvider()
    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentSharedUpstreamHttpProvider",
        type("_ProviderFactory", (), {"from_env": staticmethod(lambda: provider)}),
        raising=False,
    )

    app = create_app()
    provider.pool_timeout_count = 4
    provider.pool_wait_ms = 21.0

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    shared_llm_pool = payload["components"]["shared_llm_pool"]
    assert shared_llm_pool["shared_client_id"] == "shared-live"
    assert shared_llm_pool["pool_timeout_count"] == 4
    assert shared_llm_pool["pool_wait_ms"] == 21.0


def test_health_refresh_sets_non_empty_detail_when_live_llm_components_degrade(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "true")
    monkeypatch.setenv("PATENT_PLANNING_HOT_POOL_ENABLED", "true")

    class _SharedProvider:
        enabled = True

        def __init__(self) -> None:
            self._client = object()

        def client(self):
            return self._client

        def close(self):
            return None

        def snapshot(self) -> dict[str, object]:
            return {
                "pool_owner": "app",
                "client_owner": "shared",
                "shared_client_id": "shared-live",
                "pid": 321,
                "bootstrap_source": "startup",
                "pool_timeout_count": 0,
                "pool_wait_ms": 0.0,
                "max_connections": 100,
                "max_keepalive_connections": 20,
                "keepalive_expiry_seconds": 120.0,
            }

    class _PlanningHotPool:
        enabled = True

        def __init__(self) -> None:
            self.ready_lanes = 1

        def close(self):
            return None

        def snapshot(self) -> dict[str, object]:
            return {
                "enabled": True,
                "total_lanes": 1,
                "ready_lanes": self.ready_lanes,
                "warming_lanes": 0,
                "degraded_lanes": 0,
                "busy_lanes": 0,
            }

    provider = _SharedProvider()
    hot_pool = _PlanningHotPool()
    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentSharedUpstreamHttpProvider",
        type("_ProviderFactory", (), {"from_settings": staticmethod(lambda settings: provider)}),
        raising=False,
    )
    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentPlanningHotPool",
        type("_PlanningHotPoolFactory", (), {"from_settings": staticmethod(lambda *args, **kwargs: hot_pool)}),
        raising=False,
    )

    class _Runtime:
        def close(self):
            return None

    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda **kwargs: _Runtime())

    app = create_app()
    provider._client = None
    hot_pool.ready_lanes = 0

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["components"]["shared_llm_pool"]["status"] == "degraded"
    assert payload["components"]["shared_llm_pool"]["detail"] == "shared llm pool client unavailable"
    assert payload["components"]["planning_hot_pool"]["status"] == "degraded"
    assert payload["components"]["planning_hot_pool"]["detail"] == "planning hot pool has no ready lanes"


def test_health_exposes_planning_hot_pool_snapshot(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_PLANNING_HOT_POOL_ENABLED", "true")

    class _PlanningHotPool:
        def client(self):
            return object()

        def close(self):
            return None

        def snapshot(self) -> dict[str, object]:
            return {
                "enabled": True,
                "total_lanes": 2,
                "ready_lanes": 2,
                "warming_lanes": 0,
                "degraded_lanes": 0,
                "busy_lanes": 0,
            }

    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentPlanningHotPool",
        type("_PlanningHotPoolFactory", (), {"from_env": staticmethod(lambda **kwargs: _PlanningHotPool())}),
        raising=False,
    )

    class _Runtime:
        def close(self):
            return None

    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda **kwargs: _Runtime())

    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    planning_hot_pool = payload["components"]["planning_hot_pool"]
    assert planning_hot_pool["enabled"] is True
    assert planning_hot_pool["ready"] is True
    assert planning_hot_pool["status"] == "ok"
    assert planning_hot_pool["total_lanes"] == 2
    assert planning_hot_pool["ready_lanes"] == 2
    assert planning_hot_pool["warming_lanes"] == 0
    assert planning_hot_pool["degraded_lanes"] == 0


def test_create_app_passes_planning_warm_model_to_hot_pool(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_PLANNING_HOT_POOL_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_MODEL", "planner-model")

    captured: dict[str, object] = {}

    class _PlanningHotPool:
        def close(self):
            return None

        def snapshot(self) -> dict[str, object]:
            return {
                "enabled": True,
                "total_lanes": 2,
                "ready_lanes": 2,
                "warming_lanes": 0,
                "degraded_lanes": 0,
                "busy_lanes": 0,
            }

    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentPlanningHotPool",
        type(
            "_PlanningHotPoolFactory",
            (),
            {
                "from_settings": staticmethod(
                    lambda *args, **kwargs: captured.update(kwargs) or _PlanningHotPool()
                )
            },
        ),
        raising=False,
    )

    class _Runtime:
        def close(self):
            return None

    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda **kwargs: _Runtime())

    app = create_app()

    assert app.state.planning_hot_pool is not None
    assert captured["warm_model"] == "planner-model"


def test_health_exposes_planning_upstream_gate_snapshot(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_PLANNING_UPSTREAM_GATE_ENABLED", "true")
    monkeypatch.setenv("PATENT_PLANNING_UPSTREAM_GATE_LIMIT", "3")

    class _Gate:
        def snapshot(self) -> dict[str, object]:
            return {
                "name": "planning",
                "limit": 3,
                "effective_limit": 2,
                "in_flight": 1,
            }

    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentPlanningUpstreamGate",
        type("_GateFactory", (), {"from_settings": staticmethod(lambda *args, **kwargs: _Gate())}),
        raising=False,
    )

    class _Runtime:
        def close(self):
            return None

    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda **kwargs: _Runtime())

    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    gate = payload["components"]["planning_upstream_gate"]
    assert gate["enabled"] is True
    assert gate["status"] == "ok"
    assert gate["limit"] == 3
    assert gate["effective_limit"] == 2
    assert gate["in_flight"] == 1


def test_planning_hot_pool_bootstrap_failure_closes_pool_resources(monkeypatch):
    monkeypatch.setenv("PATENT_PLANNING_HOT_POOL_ENABLED", "true")
    hot_pools = []

    class _PlanningHotPool:
        def __init__(self) -> None:
            self.closed = False
            hot_pools.append(self)

        def close(self) -> None:
            self.closed = True

        def snapshot(self) -> dict[str, object]:
            return {
                "enabled": True,
                "total_lanes": 2,
                "ready_lanes": 2,
                "warming_lanes": 0,
                "degraded_lanes": 0,
                "busy_lanes": 0,
            }

    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentPlanningHotPool",
        type("_PlanningHotPoolFactory", (), {"from_env": staticmethod(lambda **kwargs: _PlanningHotPool())}),
        raising=False,
    )
    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda **kwargs: None)
    monkeypatch.setattr(
        patent_fastapi_app,
        "OriginalViewService",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        create_app()

    assert len(hot_pools) == 1
    assert hot_pools[0].closed is True


def test_planning_hot_pool_shutdown_stops_scheduler_cleanly(monkeypatch):
    monkeypatch.setenv("PATENT_PLANNING_HOT_POOL_ENABLED", "true")
    hot_pools = []

    class _PlanningHotPool:
        def __init__(self) -> None:
            self.closed = False
            hot_pools.append(self)

        def close(self) -> None:
            self.closed = True

        def snapshot(self) -> dict[str, object]:
            return {
                "enabled": True,
                "total_lanes": 2,
                "ready_lanes": 2,
                "warming_lanes": 0,
                "degraded_lanes": 0,
                "busy_lanes": 0,
            }

    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentPlanningHotPool",
        type("_PlanningHotPoolFactory", (), {"from_env": staticmethod(lambda **kwargs: _PlanningHotPool())}),
        raising=False,
    )
    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda **kwargs: None)

    app = create_app()

    with TestClient(app):
        pass

    assert len(hot_pools) == 1
    assert hot_pools[0].closed is True


def test_health_remains_200_when_patent_graph_kb_is_degraded_but_runtime_is_ready(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_ENABLED", "true")
    app = create_app()
    app.state.component_status["patent_graph_kb"] = {
        "ready": False,
        "enabled": True,
        "status": "degraded",
        "detail": "graph unavailable",
    }

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["patent_graph_kb_enabled"] is True
    assert payload["patent_graph_kb_ready"] is False
    assert payload["components"]["patent_graph_kb"]["status"] == "degraded"


def test_health_exposes_patent_graph_v2_and_rag_flags_when_enabled(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_ENABLED", "true")
    monkeypatch.setenv("PATENT_GRAPH_KB_V2_ENABLED", "true")
    monkeypatch.setenv("PATENT_GRAPH_KB_RAG_INJECTION_ENABLED", "true")
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["patent_graph_kb_enabled"] is True
    assert payload["patent_graph_kb_v2_enabled"] is True
    assert payload["patent_graph_kb_rag_injection_enabled"] is True
    assert payload["components"]["patent_graph_kb"]["v2_enabled"] is True
    assert payload["components"]["patent_graph_kb"]["rag_injection_enabled"] is True


def test_durable_health_remains_200_when_patent_graph_kb_is_degraded_but_other_dependencies_are_ready(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_GRAPH_KB_ENABLED", "true")
    monkeypatch.setenv("PATENT_GRAPH_KB_V2_ENABLED", "true")
    monkeypatch.setenv("PATENT_GRAPH_KB_RAG_INJECTION_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    app.state.component_status["patent_graph_kb"] = {
        "ready": False,
        "enabled": True,
        "v2_enabled": True,
        "rag_injection_enabled": True,
        "status": "degraded",
        "detail": "graph unavailable",
    }
    token = _make_bearer_token(42)

    with TestClient(app) as client:
        response = client.get(
            "/api/health",
            params={"durable": "true"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["patent_graph_kb_ready"] is False
    assert payload["patent_graph_kb_v2_enabled"] is True
    assert payload["patent_graph_kb_rag_injection_enabled"] is True
    assert payload["components"]["patent_graph_kb"]["status"] == "degraded"


def test_create_app_bootstraps_patent_graph_kb_client_when_enabled(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_ENABLED", "true")
    monkeypatch.setenv("PATENT_GRAPH_KB_V2_ENABLED", "true")
    monkeypatch.setenv("PATENT_GRAPH_KB_RAG_INJECTION_ENABLED", "true")
    monkeypatch.setenv("PATENT_NEO4J_PASSWORD", "")
    captured = {}

    class _GraphClient:
        def __init__(self):
            self.closed = False
            self.available = True
            self.degraded = False
            self.error = ""
            self.database = "neo4j"

        def close(self):
            self.closed = True

    graph_client = _GraphClient()

    def _bootstrap_patent_neo4j_client(*, url, username, password, database, logger=None):
        captured.update(
            {
                "url": url,
                "username": username,
                "password": password,
                "database": database,
                "logger_present": logger is not None,
            }
        )
        return graph_client

    monkeypatch.setattr(
        patent_fastapi_app,
        "bootstrap_patent_neo4j_client",
        _bootstrap_patent_neo4j_client,
    )

    app = create_app()

    assert app.state.patent_graph_kb_client is graph_client
    assert app.state.ask_service._patent_executor._kb_service._graph_kb_client is graph_client
    assert captured["url"] == "bolt://127.0.0.1:8687"
    assert captured["username"] == "neo4j"
    assert captured["password"] == ""
    assert captured["database"] == "neo4j"
    assert app.state.component_status["patent_graph_kb"]["ready"] is True
    assert app.state.component_status["patent_graph_kb"]["v2_enabled"] is True
    assert app.state.component_status["patent_graph_kb"]["rag_injection_enabled"] is True
    assert app.state.ask_service._patent_executor._kb_service._graph_kb_service_v2 is patent_fastapi_app.route_patent_graph_kb_v2
    assert app.state.ask_service._patent_executor._kb_service._graph_kb_v2_enabled is True
    assert app.state.ask_service._patent_executor._kb_service._graph_kb_rag_injection_enabled is True
    assert app.state.component_status["patent_graph_kb"]["status"] == "ok"


def test_create_app_closes_patent_graph_kb_client_on_shutdown(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_ENABLED", "true")
    instances = []

    class _GraphClient:
        def __init__(self):
            self.closed = False
            self.available = True
            self.degraded = False
            self.error = ""
            self.database = "neo4j"
            instances.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        patent_fastapi_app,
        "bootstrap_patent_neo4j_client",
        lambda **kwargs: _GraphClient(),
    )
    app = create_app()

    with TestClient(app):
        pass

    assert len(instances) == 1
    assert instances[0].closed is True

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
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
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


def test_reused_app_recovers_runtime_readiness_on_successful_rebootstrap(monkeypatch):
    runtimes = [None, object()]

    monkeypatch.setattr(
        patent_fastapi_app,
        "build_default_patent_runtime",
        lambda **kwargs: runtimes.pop(0),
    )

    app = create_app()
    assert app.state.component_status["runtime"]["ready"] is False
    assert app.state.component_status["runtime"]["detail"] == "patent runtime bootstrap unavailable"

    with TestClient(app):
        pass

    with TestClient(app):
        assert app.state.component_status["runtime"]["ready"] is True
        assert "detail" not in app.state.component_status["runtime"]


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


def test_create_app_closes_runtime_when_service_bootstrap_fails_after_runtime_creation(monkeypatch):
    runtime_instances = []

    class _Runtime:
        def __init__(self):
            self.closed = False
            runtime_instances.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(patent_fastapi_app, "build_default_patent_runtime", lambda **kwargs: _Runtime())
    monkeypatch.setattr(patent_fastapi_app, "OriginalViewService", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        create_app()

    assert len(runtime_instances) == 1
    assert runtime_instances[0].closed is True


def test_create_app_closes_shared_provider_and_pdf_service_when_service_bootstrap_fails(monkeypatch):
    runtime_instances = []
    pdf_services = []
    tabular_services = []

    class _Runtime:
        def __init__(self):
            self.closed = False
            runtime_instances.append(self)

        def close(self):
            self.closed = True

    class _SharedProvider:
        def __init__(self):
            self.closed = False

        def client(self):
            return object()

        def close(self):
            self.closed = True

    class _AnswerClient:
        def close(self):
            return None

    class _PdfService:
        def __init__(self, *, answer_client=None, **kwargs):
            self.answer_client = answer_client
            self.closed = False
            pdf_services.append(self)

        def close(self):
            self.closed = True

    class _TabularService:
        def __init__(self, *, answer_client=None, **kwargs):
            self.answer_client = answer_client
            self.closed = False
            tabular_services.append(self)

        def close(self):
            self.closed = True

    provider = _SharedProvider()

    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentSharedUpstreamHttpProvider",
        type("_ProviderFactory", (), {"from_env": staticmethod(lambda: provider)}),
        raising=False,
    )
    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentPdfAnswerClient",
        type("_PdfAnswerClientFactory", (), {"from_env": staticmethod(lambda http_client=None: _AnswerClient())}),
        raising=False,
    )
    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentTabularAnswerClient",
        type("_TabularAnswerClientFactory", (), {"from_env": staticmethod(lambda http_client=None: _AnswerClient())}),
        raising=False,
    )
    monkeypatch.setattr(patent_fastapi_app, "PatentPdfService", _PdfService, raising=False)
    monkeypatch.setattr(patent_fastapi_app, "PatentTabularService", _TabularService, raising=False)
    monkeypatch.setattr(patent_fastapi_app, "build_default_patent_runtime", lambda **kwargs: _Runtime())
    monkeypatch.setattr(patent_fastapi_app, "OriginalViewService", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        create_app()

    assert provider.closed is True
    assert len(pdf_services) == 1
    assert pdf_services[0].closed is True
    assert len(tabular_services) == 1
    assert tabular_services[0].closed is True
    assert len(runtime_instances) == 1
    assert runtime_instances[0].closed is True


def test_lifespan_shutdown_closes_app_owned_shared_provider_and_pdf_service(monkeypatch):
    pdf_services = []
    tabular_services = []
    hybrid_clients = []

    class _SharedProvider:
        def __init__(self):
            self.closed = False

        def client(self):
            return object()

        def close(self):
            self.closed = True

    class _AnswerClient:
        def close(self):
            return None

    class _PdfService:
        def __init__(self, *, answer_client=None, **kwargs):
            self.answer_client = answer_client
            self.closed = False
            pdf_services.append(self)

        def close(self):
            self.closed = True

    class _TabularService:
        def __init__(self, *, answer_client=None, **kwargs):
            self.answer_client = answer_client
            self.closed = False
            tabular_services.append(self)

        def close(self):
            self.closed = True

    class _HybridClient:
        def __init__(self, *, http_client=None):
            self.http_client = http_client
            self.closed = False
            hybrid_clients.append(self)

        def close(self):
            self.closed = True

    provider = _SharedProvider()

    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentSharedUpstreamHttpProvider",
        type("_ProviderFactory", (), {"from_env": staticmethod(lambda: provider)}),
        raising=False,
    )
    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentPdfAnswerClient",
        type("_PdfAnswerClientFactory", (), {"from_env": staticmethod(lambda http_client=None: _AnswerClient())}),
        raising=False,
    )
    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentTabularAnswerClient",
        type("_TabularAnswerClientFactory", (), {"from_env": staticmethod(lambda http_client=None: _AnswerClient())}),
        raising=False,
    )
    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentHybridSynthesisClient",
        type("_HybridClientFactory", (), {"from_env": staticmethod(lambda http_client=None: _HybridClient(http_client=http_client))}),
        raising=False,
    )
    monkeypatch.setattr(patent_fastapi_app, "PatentPdfService", _PdfService, raising=False)
    monkeypatch.setattr(patent_fastapi_app, "PatentTabularService", _TabularService, raising=False)
    monkeypatch.setattr(patent_fastapi_app, "build_default_patent_runtime", lambda **kwargs: None)

    app = create_app()

    with TestClient(app):
        pass

    assert provider.closed is True
    assert len(pdf_services) == 1
    assert pdf_services[0].closed is True
    assert len(tabular_services) == 1
    assert tabular_services[0].closed is True
    assert len(hybrid_clients) == 1
    assert hybrid_clients[0].closed is True


def test_create_app_degrades_when_tabular_client_bootstrap_fails(monkeypatch):
    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda **kwargs: None)
    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentTabularAnswerClient",
        type(
            "_FailingTabularAnswerClientFactory",
            (),
            {
                "from_env": staticmethod(
                    lambda http_client=None: (_ for _ in ()).throw(RuntimeError("tabular client boom"))
                )
            },
        ),
        raising=False,
    )

    app = create_app()

    assert app.state.component_status["patent_tabular_answer_client"]["ready"] is False
    assert app.state.component_status["patent_tabular_answer_client"]["status"] == "degraded"


def test_create_app_degrades_when_hybrid_client_bootstrap_fails(monkeypatch):
    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda **kwargs: None)
    monkeypatch.setattr(
        patent_fastapi_app,
        "PatentHybridSynthesisClient",
        type(
            "_FailingHybridClientFactory",
            (),
            {
                "from_env": staticmethod(
                    lambda http_client=None: (_ for _ in ()).throw(RuntimeError("hybrid client boom"))
                )
            },
        ),
        raising=False,
    )

    app = create_app()

    assert app.state.component_status["patent_hybrid_synthesis_client"]["ready"] is False
    assert app.state.component_status["patent_hybrid_synthesis_client"]["status"] == "degraded"


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


def test_health_route_allows_durable_file_only_probe_without_runtime(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setattr(patent_fastapi_app, "build_default_patent_runtime", lambda **kwargs: None)
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    token = _make_bearer_token(42)

    with TestClient(app) as client:
        response = client.get(
            "/api/health",
            params={"durable": "true", "route": "pdf_qa", "source_scope": "pdf"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["components"]["runtime"]["ready"] is False


def test_health_route_reports_not_ready_when_file_route_gate_is_disabled(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "false")
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    token = _make_bearer_token(42)

    with TestClient(app) as client:
        response = client.get(
            "/api/health",
            params={"durable": "true", "route": "pdf_qa", "source_scope": "pdf"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "SERVICE_NOT_READY"
