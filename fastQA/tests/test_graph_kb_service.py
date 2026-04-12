from __future__ import annotations

from types import SimpleNamespace

from app.modules.graph_kb.models import GraphKbQueryPlan
import app.modules.graph_kb.service as graph_kb_service
from app.modules.graph_kb.service import render_graph_kb_answer, try_graph_kb_answer


def test_render_lookup_by_doi_answer_is_deterministic():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(template_id="lookup_by_doi", params={"doi": "10.1000/test"}),
        [{"doi": "10.1000/test", "title": "Test Paper", "raw_materials": ["LFP powder", "PVDF"]}],
    )

    assert "Test Paper" in answer
    assert "10.1000/test" in answer
    assert "LFP powder" in answer
    assert references == ("10.1000/test",)


def test_render_expand_doi_context_answer_includes_testing_and_process():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(
            template_id="expand_doi_context_by_doi",
            params={
                "doi": "10.1039/c4ra15767b",
                "include_testing": True,
                "include_process": True,
            },
        ),
        [
            {
                "doi": "10.1039/c4ra15767b",
                "title": "Test Paper",
                "testing_items": ["Rate capability test", "AC impedance measurement"],
                "preparation_methods": ["Composite electrolyte preparation"],
                "process_parameters": ["vacuum drying at 70°C"],
            }
        ],
    )

    assert "Test Paper" in answer
    assert "Rate capability test" in answer
    assert "Composite electrolyte preparation" in answer
    assert "vacuum drying at 70°C" in answer
    assert references == ("10.1039/c4ra15767b",)


def test_render_count_answer_is_explicit():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(template_id="count_by_filter", params={"material_name": "LFP"}),
        [{"count": 12}],
    )

    assert "12" in answer
    assert "LFP" in answer
    assert references == ()


def test_render_list_answer_prefers_titles():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(template_id="list_by_material", params={"material_name": "LFP"}),
        [
            {"doi": "10.1/a", "title": "Paper A"},
            {"doi": "10.1/b", "title": "Paper B"},
        ],
    )

    assert "Paper A" in answer
    assert "Paper B" in answer
    assert references == ("10.1/a", "10.1/b")


def test_render_raw_material_list_answer_mentions_match_reason():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(template_id="list_by_raw_material", params={"material_name": "LiFePO4"}),
        [
            {"doi": "10.1/a", "title": "Paper A", "matched_raw_materials": ["LiFePO4 powder"]},
            {"doi": "10.1/b", "title": "Paper B", "matched_raw_materials": ["commercial LiFePO4"]},
        ],
    )

    assert "LiFePO4" in answer
    assert "Paper A" in answer
    assert "LiFePO4 powder" in answer
    assert references == ("10.1/a", "10.1/b")


def test_try_graph_kb_answer_returns_fallback_for_empty_rows():
    result = try_graph_kb_answer(
        question="有哪些关于LFP的文献？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=SimpleNamespace(query=lambda cypher, params: []), available=True, degraded=False),
        max_rows=5,
    )

    assert result.handled is False
    assert result.fallback_reason == "empty_result"


def test_try_graph_kb_answer_uses_deterministic_path_without_generation_runtime():
    neo4j_client = SimpleNamespace(
        graph=SimpleNamespace(
            query=lambda cypher, params: [
                {"doi": "10.1000/test", "title": "Test Paper", "raw_materials": ["LFP powder"]}
            ]
        ),
        available=True,
        degraded=False,
    )

    result = try_graph_kb_answer(
        question="10.1000/test 这篇文献是什么？",
        conversation_context={},
        neo4j_client=neo4j_client,
        max_rows=5,
        generation_runtime=SimpleNamespace(__getattr__=lambda self, name: (_ for _ in ()).throw(AssertionError("should not touch generation runtime"))),
    )

    assert result.handled is True
    assert result.query_mode == "graph_kb"
    assert result.references == ("10.1000/test",)
    assert "Test Paper" in result.answer


def test_try_graph_kb_answer_falls_back_when_query_times_out(monkeypatch):
    monkeypatch.setattr(
        graph_kb_service,
        "execute_graph_kb_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("graph timeout")),
    )

    result = try_graph_kb_answer(
        question="10.1000/test 这篇文献是什么？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=object(), available=True, degraded=False),
        max_rows=5,
        timeout_ms=1,
    )

    assert result.handled is False
    assert result.fallback_reason == "timeout"
    assert result.template_id == "lookup_by_doi"
