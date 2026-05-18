from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from app.core.config import get_settings
from app.core.runtime import bootstrap_graph_kb, close_graph_kb
from app.integrations.neo4j.client import Neo4jBootstrapResult, bootstrap_neo4j


def _reload_config_module():
    import app.core.config as config

    reloaded = importlib.reload(config)
    reloaded.get_settings.cache_clear()
    return reloaded


def test_settings_default_graph_kb_always_on(monkeypatch):
    for name in (
        "FASTQA_GRAPH_KB_ENABLED",
        "FASTQA_GRAPH_KB_V2_ENABLED",
        "FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED",
        "FASTQA_GRAPH_KB_TIMEOUT_MS",
        "FASTQA_GRAPH_KB_MAX_ROWS",
        "FASTQA_GRAPH_KB_QUERY_LOGGING",
    ):
        monkeypatch.delenv(name, raising=False)

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.graph_kb_enabled is True
    assert settings.graph_kb_v2_enabled is True
    assert settings.graph_kb_rag_injection_enabled is True
    assert settings.graph_kb_timeout_ms == 3000
    assert settings.graph_kb_max_rows == 20
    assert settings.graph_kb_query_logging is False

    get_settings.cache_clear()


def test_settings_respects_graph_kb_kill_switch_env(monkeypatch):
    monkeypatch.setenv("FASTQA_GRAPH_KB_ENABLED", "false")
    monkeypatch.setenv("FASTQA_GRAPH_KB_V2_ENABLED", "false")
    monkeypatch.setenv("FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED", "false")

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.graph_kb_enabled is False
    assert settings.graph_kb_v2_enabled is False
    assert settings.graph_kb_rag_injection_enabled is False

    get_settings.cache_clear()


def test_shared_fastqa_config_documents_graph_kb_master_switch():
    repo_root = Path(__file__).resolve().parents[2]
    shared_env = repo_root / "resource/config/services/fastQA/config.shared.env"
    content = shared_env.read_text(encoding="utf-8")

    assert "FASTQA_GRAPH_KB_ENABLED=" in content


def test_settings_prefer_fastqa_namespaced_neo4j(monkeypatch):
    for name in ("NEO4J_URL", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
        monkeypatch.setenv(name, f"legacy-{name.lower()}")
    monkeypatch.setenv("FASTQA_NEO4J_URL", "bolt://fastqa:7688")
    monkeypatch.setenv("FASTQA_NEO4J_USERNAME", "fastqa-user")
    monkeypatch.setenv("FASTQA_NEO4J_PASSWORD", "fastqa-pw")
    monkeypatch.setenv("FASTQA_NEO4J_DATABASE", "fastqa-db")

    config = _reload_config_module()
    settings = config.get_settings()

    assert settings.neo4j_url == "bolt://fastqa:7688"
    assert settings.neo4j_username == "fastqa-user"
    assert settings.neo4j_password == "fastqa-pw"
    assert settings.neo4j_database == "fastqa-db"


def test_settings_keep_legacy_neo4j_fallback(tmp_path, monkeypatch):
    for name in ("FASTQA_NEO4J_URL", "FASTQA_NEO4J_USERNAME", "FASTQA_NEO4J_PASSWORD", "FASTQA_NEO4J_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NEO4J_URL", "bolt://legacy:7688")
    monkeypatch.setenv("NEO4J_USERNAME", "legacy-user")
    monkeypatch.setenv("NEO4J_PASSWORD", "legacy-pw")
    monkeypatch.setenv("NEO4J_DATABASE", "legacy-db")
    monkeypatch.setenv("RESOURCE_ROOT", str(tmp_path / "resource"))

    config = _reload_config_module()
    settings = config.get_settings()

    assert settings.neo4j_url == "bolt://legacy:7688"
    assert settings.neo4j_username == "legacy-user"
    assert settings.neo4j_password == "legacy-pw"
    assert settings.neo4j_database == "legacy-db"


def test_bootstrap_graph_kb_respects_hidden_disabled_flag():
    runtime = SimpleNamespace(
        settings=SimpleNamespace(graph_kb_enabled=False, neo4j_url=""),
        neo4j_client=None,
        graph_kb_ready=False,
        component_status={},
        health_flags={},
    )

    bootstrap_graph_kb(runtime)

    assert runtime.neo4j_client is None
    assert runtime.graph_kb_ready is False
    assert runtime.component_status["graph_kb"]["status"] == "skipped"


def test_bootstrap_graph_kb_degrades_when_enabled_but_url_missing(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(graph_kb_enabled=True, neo4j_url=""),
        neo4j_client=None,
        graph_kb_ready=False,
        component_status={},
        health_flags={},
    )

    bootstrap_graph_kb(runtime)

    assert runtime.neo4j_client is None
    assert runtime.graph_kb_ready is False
    assert runtime.component_status["graph_kb"]["status"] == "degraded"


def test_bootstrap_graph_kb_marks_ready(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            graph_kb_enabled=True,
            neo4j_url="bolt://127.0.0.1:7687",
            neo4j_username="neo4j",
            neo4j_password="secret",
        ),
        neo4j_client=None,
        graph_kb_ready=False,
        component_status={},
        health_flags={},
    )
    monkeypatch.setattr(
        "app.core.runtime.bootstrap_neo4j",
        lambda **kwargs: SimpleNamespace(available=True, degraded=False, graph=object(), error=""),
        raising=False,
    )

    bootstrap_graph_kb(runtime)

    assert runtime.graph_kb_ready is True
    assert runtime.component_status["graph_kb"]["status"] == "ok"
    assert runtime.neo4j_client is not None


def test_bootstrap_graph_kb_does_not_change_generation_runtime_state(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            graph_kb_enabled=True,
            neo4j_url="bolt://127.0.0.1:7687",
            neo4j_username="neo4j",
            neo4j_password="secret",
        ),
        neo4j_client=None,
        graph_kb_ready=False,
        generation_runtime=object(),
        generation_runtime_ready=True,
        component_status={"generation_runtime": {"status": "ok"}},
        health_flags={},
    )
    monkeypatch.setattr(
        "app.core.runtime.bootstrap_neo4j",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        raising=False,
    )

    bootstrap_graph_kb(runtime)

    assert runtime.generation_runtime_ready is True
    assert runtime.component_status["generation_runtime"]["status"] == "ok"
    assert runtime.component_status["graph_kb"]["status"] == "degraded"


def test_close_graph_kb_closes_underlying_driver():
    class _Driver:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    driver = _Driver()
    runtime = SimpleNamespace(
        neo4j_client=Neo4jBootstrapResult(
            graph=SimpleNamespace(_driver=driver),
            available=True,
            degraded=False,
            connectivity_verified=False,
            attempted_modes=("basic",),
        ),
        graph_kb_ready=True,
    )

    close_graph_kb(runtime)

    assert driver.closed is True
    assert runtime.neo4j_client is None
    assert runtime.graph_kb_ready is False


def test_local_bootstrap_neo4j_succeeds_with_first_supported_mode():
    graph = object()

    result = bootstrap_neo4j(
        url="bolt://127.0.0.1:7687",
        username="neo4j",
        password="secret",
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        graph_factory=lambda **kwargs: graph,
    )

    assert result.graph is graph
    assert result.available is True
    assert result.degraded is False
    assert result.connectivity_verified is False
    assert result.attempted_modes == ("refresh_schema_false_sanitize",)


def test_local_bootstrap_neo4j_falls_back_after_type_error_modes():
    calls: list[dict[str, object]] = []

    def _factory(**kwargs):
        calls.append(kwargs)
        if "refresh_schema" in kwargs or "sanitize" in kwargs:
            raise TypeError("unsupported kwargs")
        return object()

    result = bootstrap_neo4j(
        url="bolt://127.0.0.1:7687",
        username="neo4j",
        password="secret",
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        graph_factory=_factory,
    )

    assert result.available is True
    assert result.degraded is False
    assert result.attempted_modes == ("refresh_schema_false_sanitize", "sanitize", "basic")
    assert len(calls) == 3


def test_local_bootstrap_neo4j_marks_degraded_for_regular_failure():
    result = bootstrap_neo4j(
        url="bolt://127.0.0.1:7687",
        username="neo4j",
        password="secret",
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        graph_factory=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("connect failed")),
    )

    assert result.graph is None
    assert result.available is False
    assert result.degraded is True
    assert result.connectivity_verified is False
    assert "connect failed" in str(result.error or "")


def test_local_bootstrap_neo4j_apoc_fallback_verifies_connectivity():
    class _Driver:
        def __init__(self):
            self.closed = False

        def verify_connectivity(self):
            return True

        def close(self):
            self.closed = True

    driver = _Driver()

    result = bootstrap_neo4j(
        url="bolt://127.0.0.1:7687",
        username="neo4j",
        password="secret",
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        graph_factory=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("APOC unavailable")),
        base_driver_factory=lambda url, auth: driver,
    )

    assert result.graph is None
    assert result.available is True
    assert result.degraded is True
    assert result.connectivity_verified is True
    assert driver.closed is True
