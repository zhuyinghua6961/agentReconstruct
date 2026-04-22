from __future__ import annotations

from pathlib import Path

import config as patent_config


ROOT_DIR = Path(__file__).resolve().parents[1]


def test_get_settings_reads_patent_shared_http_timeout_fields(monkeypatch):
    monkeypatch.setenv("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", "true")
    monkeypatch.setenv("PATENT_LLM_HTTP_CONNECT_TIMEOUT_SECONDS", "11")
    monkeypatch.setenv("PATENT_LLM_HTTP_READ_TIMEOUT_SECONDS", "121")
    monkeypatch.setenv("PATENT_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS", "601")
    monkeypatch.setenv("PATENT_LLM_HTTP_WRITE_TIMEOUT_SECONDS", "122")
    monkeypatch.setenv("PATENT_LLM_HTTP_POOL_TIMEOUT_SECONDS", "17")
    monkeypatch.setenv("PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", "123")
    monkeypatch.setenv("PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", "11")
    monkeypatch.setenv("PATENT_LLM_HTTP_MAX_CONNECTIONS", "22")

    settings = patent_config.get_settings()

    llm_http = getattr(settings, "llm_http", None)
    assert llm_http is not None
    assert llm_http.shared_pool_enabled is True
    assert llm_http.connect_timeout_seconds == 11.0
    assert llm_http.read_timeout_seconds == 121.0
    assert llm_http.stream_read_timeout_seconds == 601.0
    assert llm_http.write_timeout_seconds == 122.0
    assert llm_http.pool_timeout_seconds == 17.0
    assert llm_http.keepalive_expiry_seconds == 123.0
    assert llm_http.max_keepalive_connections == 11
    assert llm_http.max_connections == 22


def test_config_shared_env_example_documents_patent_shared_http_timeout_defaults():
    content = (ROOT_DIR / "config.shared.env.example").read_text(encoding="utf-8")

    assert "PATENT_LLM_HTTP_SHARED_POOL_ENABLED=false" in content
    assert "PATENT_LLM_HTTP_CONNECT_TIMEOUT_SECONDS=15" in content
    assert "PATENT_LLM_HTTP_READ_TIMEOUT_SECONDS=180" in content
    assert "PATENT_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS=600" in content
    assert "PATENT_LLM_HTTP_WRITE_TIMEOUT_SECONDS=180" in content
    assert "PATENT_LLM_HTTP_POOL_TIMEOUT_SECONDS=30" in content
    assert "PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS=120" in content
    assert "PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS=20" in content
    assert "PATENT_LLM_HTTP_MAX_CONNECTIONS=100" in content


def test_get_settings_reads_patent_planning_gate_fields(monkeypatch):
    monkeypatch.setenv("PATENT_PLANNING_UPSTREAM_GATE_ENABLED", "true")
    monkeypatch.setenv("PATENT_PLANNING_UPSTREAM_GATE_LIMIT", "3")

    settings = patent_config.get_settings()

    gate = getattr(settings, "planning_upstream_gate", None)
    assert gate is not None
    assert gate.enabled is True
    assert gate.limit == 3


def test_config_shared_env_example_documents_patent_planning_gate_defaults():
    content = (ROOT_DIR / "config.shared.env.example").read_text(encoding="utf-8")

    assert "PATENT_PLANNING_UPSTREAM_GATE_ENABLED=false" in content
    assert "PATENT_PLANNING_UPSTREAM_GATE_LIMIT=1" in content
