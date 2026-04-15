from __future__ import annotations

import sys
import types

import pytest

from server.patent.graph_kb.neo4j_client import PatentNeo4jClient, bootstrap_patent_neo4j_client


def _install_fake_neo4j(monkeypatch, module: types.ModuleType) -> None:
    monkeypatch.setitem(sys.modules, "neo4j", module)


def test_bootstrap_patent_neo4j_client_degrades_when_driver_module_is_missing(monkeypatch):
    original_import = __import__

    def _raising_import(name, *args, **kwargs):
        if name == "neo4j":
            raise ModuleNotFoundError("neo4j missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _raising_import)

    result = bootstrap_patent_neo4j_client(
        url="bolt://127.0.0.1:8687",
        username="neo4j",
        password="secret",
        database="neo4j",
        logger=None,
    )

    assert result.available is False
    assert result.degraded is True
    assert "neo4j missing" in result.error
    assert result.database == "neo4j"


def test_bootstrap_patent_neo4j_client_marks_available_when_connectivity_succeeds(monkeypatch):
    captured: dict[str, object] = {}

    class _Driver:
        def __init__(self) -> None:
            self.closed = False

        def verify_connectivity(self) -> None:
            captured["verified"] = True

        def execute_query(self, query, database_, parameters_=None):
            captured["database"] = database_
            return [{"ok": 1}], None, None

        def close(self) -> None:
            self.closed = True

    driver = _Driver()

    class _GraphDatabase:
        @staticmethod
        def driver(url, auth):
            captured["url"] = url
            captured["auth"] = auth
            return driver

    module = types.ModuleType("neo4j")
    module.GraphDatabase = _GraphDatabase
    _install_fake_neo4j(monkeypatch, module)

    result = bootstrap_patent_neo4j_client(
        url="bolt://127.0.0.1:8687",
        username="neo4j",
        password="secret",
        database="neo4j",
        logger=None,
    )

    assert result.available is True
    assert result.degraded is False
    assert result.driver is driver
    assert result.database == "neo4j"
    assert captured == {
        "url": "bolt://127.0.0.1:8687",
        "auth": ("neo4j", "secret"),
        "verified": True,
        "database": "neo4j",
    }


def test_bootstrap_patent_neo4j_client_degrades_when_connectivity_fails(monkeypatch):
    class _Driver:
        def verify_connectivity(self) -> None:
            raise RuntimeError("connectivity failed")

        def close(self) -> None:
            self.closed = True

    class _GraphDatabase:
        @staticmethod
        def driver(url, auth):
            return _Driver()

    module = types.ModuleType("neo4j")
    module.GraphDatabase = _GraphDatabase
    _install_fake_neo4j(monkeypatch, module)

    result = bootstrap_patent_neo4j_client(
        url="bolt://127.0.0.1:8687",
        username="neo4j",
        password="secret",
        database="neo4j",
        logger=None,
    )

    assert result.available is False
    assert result.degraded is True
    assert "connectivity failed" in result.error


def test_bootstrap_patent_neo4j_client_degrades_when_database_probe_fails(monkeypatch):
    class _Driver:
        def verify_connectivity(self) -> None:
            return None

        def execute_query(self, query, database_, parameters_=None):
            raise RuntimeError(f"database probe failed for {database_}")

        def close(self) -> None:
            self.closed = True

    class _GraphDatabase:
        @staticmethod
        def driver(url, auth):
            return _Driver()

    module = types.ModuleType("neo4j")
    module.GraphDatabase = _GraphDatabase
    _install_fake_neo4j(monkeypatch, module)

    result = bootstrap_patent_neo4j_client(
        url="bolt://127.0.0.1:8687",
        username="neo4j",
        password="secret",
        database="missing_db",
        logger=None,
    )

    assert result.available is False
    assert result.degraded is True
    assert "database probe failed for missing_db" in result.error


def test_patent_neo4j_client_close_is_idempotent():
    class _Driver:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    driver = _Driver()
    client = PatentNeo4jClient(
        driver=driver,
        available=True,
        degraded=False,
        error="",
        database="neo4j",
    )

    client.close()
    client.close()

    assert driver.close_calls == 1
    assert client.driver is None


def test_patent_neo4j_client_query_uses_timeout_and_database_when_available(monkeypatch):
    captured: dict[str, object] = {}

    class _Row:
        def __init__(self, payload):
            self._payload = payload

        def data(self):
            return dict(self._payload)

    class _Query:
        def __init__(self, text, timeout):
            self.text = text
            self.timeout = timeout

    class _Driver:
        def execute_query(self, query, database_, parameters_):
            captured["text"] = query.text
            captured["timeout"] = query.timeout
            captured["database"] = database_
            captured["parameters"] = dict(parameters_)
            return [_Row({"patent_id": "CN100355122C"})], None, None

        def close(self) -> None:
            pass

    module = types.ModuleType("neo4j")
    module.Query = _Query
    _install_fake_neo4j(monkeypatch, module)

    client = PatentNeo4jClient(
        driver=_Driver(),
        available=True,
        degraded=False,
        error="",
        database="neo4j",
    )

    rows = client.query(
        "MATCH (p:Patent {patent_id: $patent_id}) RETURN p.patent_id AS patent_id",
        {"patent_id": "CN100355122C"},
        timeout_ms=2500,
    )

    assert rows == [{"patent_id": "CN100355122C"}]
    assert captured == {
        "text": "MATCH (p:Patent {patent_id: $patent_id}) RETURN p.patent_id AS patent_id",
        "timeout": 2.5,
        "database": "neo4j",
        "parameters": {"patent_id": "CN100355122C"},
    }


def test_patent_neo4j_client_query_converts_timeout_error(monkeypatch):
    class _Query:
        def __init__(self, text, timeout):
            self.text = text
            self.timeout = timeout

    class _TimeoutError(RuntimeError):
        code = "Neo.ClientError.Transaction.TransactionTimedOut"

    class _Driver:
        def execute_query(self, query, database_, parameters_):
            raise _TimeoutError("timed out waiting for lock")

        def close(self) -> None:
            pass

    module = types.ModuleType("neo4j")
    module.Query = _Query
    _install_fake_neo4j(monkeypatch, module)

    client = PatentNeo4jClient(
        driver=_Driver(),
        available=True,
        degraded=False,
        error="",
        database="neo4j",
    )

    with pytest.raises(TimeoutError, match="timed out waiting for lock"):
        client.query("RETURN 1 AS ok", {}, timeout_ms=1000)


def test_patent_neo4j_client_query_returns_empty_when_unavailable():
    client = PatentNeo4jClient(
        driver=None,
        available=False,
        degraded=True,
        error="offline",
        database="neo4j",
    )

    assert client.query("RETURN 1 AS ok", {}, timeout_ms=1000) == []
