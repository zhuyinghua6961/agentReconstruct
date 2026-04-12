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


def test_render_expand_doi_context_answer_uses_structured_markdown_sections():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(
            template_id="expand_doi_context_by_doi",
            params={
                "doi": "10.1039/c4ra15767b",
                "include_testing": True,
                "include_process": True,
                "include_raw_materials": False,
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

    assert answer.startswith("## 📄 文献信息")
    assert "- 标题：Test Paper" in answer
    assert "- DOI：10.1039/c4ra15767b" in answer
    assert "## 🔬 测试/表征" in answer
    assert "- Rate capability test" in answer
    assert "- AC impedance measurement" in answer
    assert "## ⚙️ 制备/工艺" in answer
    assert "### Composite electrolyte preparation" in answer
    assert "## 📌 关键参数" in answer
    assert "- vacuum drying at 70°C" in answer
    assert references == ("10.1039/c4ra15767b",)


def test_render_expand_doi_context_keeps_legal_journal_doi():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(
            template_id="expand_doi_context_by_doi",
            params={
                "doi": "10.1016/j.orgel.2015.09.050",
                "include_testing": True,
            },
        ),
        [
            {
                "doi": "10.1016/j.orgel.2015.09.050",
                "title": "Orgel Context Paper",
                "testing_items": ["EIS"],
            }
        ],
    )

    assert "10.1016/j.orgel.2015.09.050" in answer
    assert "EIS" in answer
    assert references == ("10.1016/j.orgel.2015.09.050",)


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


def test_render_raw_material_list_answer_uses_structured_markdown_sections():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(template_id="list_by_raw_material", params={"material_name": "LiFePO4"}),
        [
            {"doi": "10.1/a", "title": "Paper A", "matched_raw_materials": ["LiFePO4 powder"]},
            {"doi": "10.1/b", "title": "Paper B", "matched_raw_materials": ["commercial LiFePO4"]},
        ],
    )

    assert answer.startswith("## 📚 文献概览")
    assert "- 当前展示 2 篇相关文献" in answer
    assert "## 📖 相关文献" in answer
    assert "### [1] Paper A" in answer
    assert "### [2] Paper B" in answer
    assert "- DOI：10.1/a" in answer
    assert "- DOI：10.1/b" in answer
    assert "- 命中条件：原料 = LiFePO4 powder" in answer
    assert "(原料命中：" not in answer
    assert references == ("10.1/a", "10.1/b")


def test_render_expand_doi_context_normalizes_dirty_process_fields():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(
            template_id="expand_doi_context_by_doi",
            params={
                "doi": "10.1039/c4ra15767b",
                "include_testing": False,
                "include_process": True,
            },
        ),
        [
            {
                "doi": "10.1039/c4ra15767b",
                "title": "Dirty Process Paper",
                "preparation_methods": [
                    "method_ball milling_time_12 h_speed_350 rpm_null",
                    "method_vacuum drying_temperature_110 C_time_12 h_null",
                ],
                "process_parameters": [
                    "ball_powder_ratio_10:1_null",
                    "atmosphere_argon__null_",
                ],
            }
        ],
    )

    assert "Dirty Process Paper" in answer
    assert "## ⚙️ 制备/工艺" in answer
    assert "_null_" not in answer
    assert "null_" not in answer
    assert "### Ball milling" in answer
    assert "- 时间：12 h" in answer
    assert "- 转速：350 rpm" in answer
    assert "### Vacuum drying" in answer
    assert "- 温度：110 C" in answer
    assert "- 球粉比：10:1" in answer
    assert "- 气氛：argon" in answer
    assert references == ("10.1039/c4ra15767b",)


def test_render_raw_material_list_answer_filters_truncated_doi_rows():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(template_id="list_by_raw_material", params={"material_name": "LiFePO4"}),
        [
            {"doi": "10.1007/s12598-", "title": "Broken Paper", "matched_raw_materials": ["LiFePO4 powder"]},
            {"doi": "10.1038/s44359-024-00018-w", "title": "Good Paper", "matched_raw_materials": ["lithium iron phosphate"]},
        ],
    )

    assert "Good Paper" in answer
    assert "Broken Paper" not in answer
    assert references == ("10.1038/s44359-024-00018-w",)


def test_render_lookup_by_doi_answer_keeps_fixable_doi():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(template_id="lookup_by_doi", params={"doi": "10.1039/D5GC01367D"}),
        [{"doi": "10.1039/D5GC01367D.", "title": "Fixed Paper", "raw_materials": []}],
    )

    assert "Fixed Paper" in answer
    assert "10.1039/D5GC01367D" in answer
    assert references == ("10.1039/D5GC01367D",)


def test_render_lookup_by_doi_answer_keeps_journal_segment_with_dot_org():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(template_id="lookup_by_doi", params={"doi": "10.1016/j.orgel.2015.09.050"}),
        [{"doi": "10.1016/j.orgel.2015.09.050", "title": "Orgel Paper", "raw_materials": []}],
    )

    assert "Orgel Paper" in answer
    assert "10.1016/j.orgel.2015.09.050" in answer
    assert references == ("10.1016/j.orgel.2015.09.050",)


def test_render_lookup_by_doi_answer_keeps_journal_segment_with_dot_com():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(template_id="lookup_by_doi", params={"doi": "10.1016/j.comcom.2020.102078"}),
        [{"doi": "10.1016/j.comcom.2020.102078", "title": "Comcom Paper", "raw_materials": []}],
    )

    assert "Comcom Paper" in answer
    assert "10.1016/j.comcom.2020.102078" in answer
    assert references == ("10.1016/j.comcom.2020.102078",)


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


def test_try_graph_kb_answer_falls_back_when_rows_only_have_invalid_doi(monkeypatch):
    monkeypatch.setattr(
        graph_kb_service,
        "execute_graph_kb_plan",
        lambda *args, **kwargs: [
            {
                "doi": "10.1007/s12598-",
                "title": "Broken Paper",
                "matched_raw_materials": ["LiFePO4 powder"],
            }
        ],
    )

    result = try_graph_kb_answer(
        question="有哪些使用LiFePO4作为原料的文献？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=object(), available=True, degraded=False),
        max_rows=5,
    )

    assert result.handled is False
    assert result.fallback_reason == "render_empty"
    assert result.template_id == "list_by_raw_material"
    assert result.result_count == 0
