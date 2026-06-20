from __future__ import annotations

from types import SimpleNamespace

from app.integrations.neo4j.client import run_graph_query


def test_run_graph_query_uses_langchain_query_api():
    class _Graph:
        def query(self, query, params):
            assert "$doi" in query
            assert params == {"doi": "10.1000/test"}
            return [{"title": "Paper"}]

    rows = run_graph_query(_Graph(), "MATCH (n) WHERE n.doi = $doi RETURN n.title AS title", {"doi": "10.1000/test"})
    assert rows == [{"title": "Paper"}]


def test_run_graph_query_falls_back_to_py2neo_run():
    class _Graph:
        def run(self, query, **kwargs):
            assert kwargs == {"doi": "10.1000/test"}
            return SimpleNamespace(data=lambda: [{"title": "Legacy"}])

    rows = run_graph_query(_Graph(), "MATCH (n) WHERE n.doi = $doi RETURN n.title AS title", {"doi": "10.1000/test"})
    assert rows == [{"title": "Legacy"}]
