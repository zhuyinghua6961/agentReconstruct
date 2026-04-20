from pathlib import Path

import config as patent_config


ROOT_DIR = Path(__file__).resolve().parents[1]


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

    assert settings.graph_kb.enabled is False
    assert settings.graph_kb.v2_enabled is False
    assert settings.graph_kb.rag_injection_enabled is False
    assert settings.graph_kb.timeout_ms == 3000
    assert settings.graph_kb.max_rows == 20
    assert settings.graph_kb.query_logging is False
    assert settings.graph_kb.neo4j_url == "bolt://127.0.0.1:8687"
    assert settings.graph_kb.neo4j_database == "neo4j"
    assert settings.graph_kb.neo4j_username == "neo4j"
    assert settings.graph_kb.neo4j_password == ""


def test_config_shared_env_example_documents_patent_graph_kb_defaults():
    content = (ROOT_DIR / "config.shared.env.example").read_text(encoding="utf-8")

    assert "PATENT_GRAPH_KB_ENABLED=false" in content
    assert "PATENT_GRAPH_KB_V2_ENABLED=false" in content
    assert "PATENT_GRAPH_KB_RAG_INJECTION_ENABLED=false" in content
    assert "PATENT_GRAPH_KB_TIMEOUT_MS=3000" in content
    assert "PATENT_GRAPH_KB_MAX_ROWS=20" in content
    assert "PATENT_GRAPH_KB_QUERY_LOGGING=false" in content
    assert "PATENT_NEO4J_URL=bolt://127.0.0.1:8687" in content
    assert "PATENT_NEO4J_DATABASE=neo4j" in content
    assert "PATENT_NEO4J_USERNAME=neo4j" in content
    assert "PATENT_NEO4J_PASSWORD=" in content


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
