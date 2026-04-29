import importlib
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config as patent_config  # noqa: E402




def test_get_settings_exposes_patent_graph_kb_defaults(monkeypatch):
    for name in (
        "PATENT_GRAPH_KB_ENABLED",
        "PATENT_GRAPH_KB_V2_ENABLED",
        "PATENT_GRAPH_KB_RAG_INJECTION_ENABLED",
        "PATENT_GRAPH_KB_TIMEOUT_MS",
        "PATENT_GRAPH_KB_MAX_ROWS",
        "PATENT_GRAPH_KB_QUERY_LOGGING",
        "PATENT_NEO4J_URL",
        "PATENT_NEO4J_DATABASE",
        "PATENT_NEO4J_USERNAME",
        "PATENT_NEO4J_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = patent_config.get_settings()

    assert settings.graph_kb.enabled is True
    assert settings.graph_kb.v2_enabled is True
    assert settings.graph_kb.rag_injection_enabled is True
    assert settings.graph_kb.timeout_ms == 3000
    assert settings.graph_kb.max_rows == 20
    assert settings.graph_kb.query_logging is False
    assert settings.graph_kb.neo4j_url == "bolt://127.0.0.1:8687"
    assert settings.graph_kb.neo4j_database == "neo4j"
    assert settings.graph_kb.neo4j_username == "neo4j"
    assert settings.graph_kb.neo4j_password == ""


def test_config_shared_env_example_documents_patent_graph_kb_defaults():
    content = (ROOT_DIR / "config.shared.env.example").read_text(encoding="utf-8")

    assert "PATENT_GRAPH_KB_ENABLED" not in content
    assert "PATENT_GRAPH_KB_V2_ENABLED" not in content
    assert "PATENT_GRAPH_KB_RAG_INJECTION_ENABLED" not in content
    assert "PATENT_GRAPH_KB_TIMEOUT_MS=3000" in content
    assert "PATENT_GRAPH_KB_MAX_ROWS=20" in content
    assert "PATENT_GRAPH_KB_QUERY_LOGGING=false" in content
    assert "PATENT_NEO4J_URL=bolt://127.0.0.1:8687" in content
    assert "PATENT_NEO4J_DATABASE=neo4j" in content
    assert "PATENT_NEO4J_USERNAME=neo4j" in content
    assert "PATENT_NEO4J_PASSWORD=" not in content
    secret_template = (ROOT_DIR.parent / "resource" / "config" / "shared" / "graph.secret.env.example").read_text(
        encoding="utf-8"
    )
    assert "PATENT_NEO4J_PASSWORD=" in secret_template


def test_get_settings_exposes_patent_graph_kb_v2_overrides(monkeypatch):
    monkeypatch.setenv("PATENT_GRAPH_KB_ENABLED", "true")
    monkeypatch.setenv("PATENT_GRAPH_KB_V2_ENABLED", "true")
    monkeypatch.setenv("PATENT_GRAPH_KB_RAG_INJECTION_ENABLED", "true")
    monkeypatch.setenv("PATENT_GRAPH_KB_TIMEOUT_MS", "4500")
    monkeypatch.setenv("PATENT_GRAPH_KB_MAX_ROWS", "33")
    monkeypatch.setenv("PATENT_GRAPH_KB_QUERY_LOGGING", "true")

    settings = patent_config.get_settings()

    assert settings.graph_kb.enabled is True
    assert settings.graph_kb.v2_enabled is True
    assert settings.graph_kb.rag_injection_enabled is True
    assert settings.graph_kb.timeout_ms == 4500
    assert settings.graph_kb.max_rows == 33
    assert settings.graph_kb.query_logging is True


def test_get_settings_allows_hidden_graph_kb_rollback_env(monkeypatch):
    monkeypatch.setenv("PATENT_GRAPH_KB_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_V2_ENABLED", "false")
    monkeypatch.setenv("PATENT_GRAPH_KB_RAG_INJECTION_ENABLED", "false")

    settings = patent_config.get_settings()

    assert settings.graph_kb.enabled is False
    assert settings.graph_kb.v2_enabled is False
    assert settings.graph_kb.rag_injection_enabled is False


def test_get_settings_loads_patent_namespaced_graph_from_shared_resource(tmp_path, monkeypatch):
    resource_root = tmp_path / "resource"
    shared_root = resource_root / "config" / "shared"
    service_root = resource_root / "config" / "services" / "patent"
    shared_root.mkdir(parents=True)
    service_root.mkdir(parents=True)
    (shared_root / "graph.shared.env").write_text(
        "PATENT_NEO4J_URL=bolt://patent-shared:8687\n"
        "PATENT_NEO4J_USERNAME=patent-user\n"
        "PATENT_NEO4J_DATABASE=patent-db\n",
        encoding="utf-8",
    )
    (shared_root / "graph.secret.env").write_text("PATENT_NEO4J_PASSWORD=patent-pw\n", encoding="utf-8")
    for name in (
        "PATENT_NEO4J_URL",
        "PATENT_NEO4J_USERNAME",
        "PATENT_NEO4J_PASSWORD",
        "PATENT_NEO4J_DATABASE",
        "NEO4J_URL",
        "NEO4J_USERNAME",
        "NEO4J_PASSWORD",
        "NEO4J_DATABASE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))

    reloaded = importlib.reload(patent_config)
    settings = reloaded.get_settings()

    assert settings.graph_kb.neo4j_url == "bolt://patent-shared:8687"
    assert settings.graph_kb.neo4j_username == "patent-user"
    assert settings.graph_kb.neo4j_password == "patent-pw"
    assert settings.graph_kb.neo4j_database == "patent-db"
