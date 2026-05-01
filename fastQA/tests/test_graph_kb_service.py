from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.modules.graph_kb.models import GraphKbQueryPlan
import app.modules.graph_kb.service as graph_kb_service
from app.modules.graph_kb.service import render_graph_kb_answer, route_graph_kb_v2, try_graph_kb_answer


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


def test_render_expand_doi_context_keeps_clean_method_prose_intact():
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
                "title": "Clean Process Paper",
                "preparation_methods": ["sol-gel method assisted coating"],
            }
        ],
    )

    assert "### Sol-gel method assisted coating" in answer
    assert "- sol-gel" not in answer
    assert references == ("10.1039/c4ra15767b",)


def test_render_raw_material_list_answer_preserves_title_punctuation():
    answer, references = render_graph_kb_answer(
        GraphKbQueryPlan(template_id="list_by_raw_material", params={"material_name": "LiFePO4"}),
        [
            {
                "doi": "10.1/a",
                "title": "Effects of A, B, and C",
                "matched_raw_materials": ["LiFePO4 powder"],
            }
        ],
    )

    assert "Effects of A, B, and C" in answer
    assert "Effects of A；B；and C" not in answer
    assert references == ("10.1/a",)


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


def test_route_graph_kb_v2_renders_direct_answer_for_legacy_template():
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [{"doi": "10.1000/test", "title": "Direct Paper", "raw_materials": ["LFP powder"]}]

    routing_result = route_graph_kb_v2(
        question="10.1000/test 这篇文献是什么？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.mode == "direct_answer"
    assert routing_result.direct_result is not None
    assert routing_result.direct_result.handled is True
    assert "Direct Paper" in routing_result.direct_result.answer
    assert routing_result.direct_result.references == ("10.1000/test",)


def test_route_graph_kb_v2_doi_content_question_uses_graph_for_rag():
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [
                {
                    "doi": "10.1039/c4ra15767b",
                    "title": "Context Paper",
                    "testing_items": ["Rate capability test"],
                    "preparation_methods": ["Composite electrolyte preparation"],
                    "process_parameters": ["vacuum drying at 70°C"],
                }
            ]

    routing_result = route_graph_kb_v2(
        question="10.1039/c4ra15767b 这篇文献的测试和工艺是什么？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.mode == "graph_for_rag"
    assert routing_result.rag_payload is not None
    assert "Context Paper" in routing_result.rag_payload.stage4_fact_block
    assert routing_result.diagnostics["graph_route_family"] == "hybrid"


def test_route_graph_kb_v2_preserves_raw_material_list_direct_rendering():
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [
                {"doi": "10.1/a", "title": "Paper A", "matched_raw_materials": ["LiFePO4 powder"]},
                {"doi": "10.1/b", "title": "Paper B", "matched_raw_materials": ["commercial LiFePO4"]},
            ]

    routing_result = route_graph_kb_v2(
        question="有哪些使用LiFePO4作为原料的文献？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.mode == "direct_answer"
    assert routing_result.direct_result is not None
    assert "## 📚 文献概览" in routing_result.direct_result.answer
    assert "### [1] Paper A" in routing_result.direct_result.answer
    assert "### [2] Paper B" in routing_result.direct_result.answer


def test_route_graph_kb_v2_direct_answer_returns_enriched_profile_without_rag_payload():
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [
                {
                    "doi": "10.1021/jp1005692",
                    "title": "Carbon Paper",
                    "carbon_sources": ["sucrose"],
                    "preparation_methods": ["solid-state synthesis"],
                    "testing_items": ["Rate capability"],
                }
            ]

    routing_result = route_graph_kb_v2(
        question="列出使用蔗糖作为碳源的文献",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.mode == "direct_answer"
    assert routing_result.direct_result is not None
    assert routing_result.direct_result.handled is True
    assert routing_result.rag_payload is None
    assert "solid-state synthesis" in routing_result.direct_result.answer
    assert "Rate capability" in routing_result.direct_result.answer


def test_route_graph_kb_v2_executes_planner_generated_parametric_query_without_guardrail_reject():
    calls: list[tuple[str, dict]] = []

    class _Graph:
        def query(self, cypher, params):
            calls.append((str(cypher), dict(params)))
            return [{"doi": "10.1/a", "title": "Paper A", "raw_materials": ["LFP powder"]}]

    routing_result = route_graph_kb_v2(
        question="压实密度最高的LFP材料有哪些？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.mode == "graph_for_rag"
    assert routing_result.rag_payload is not None
    assert calls
    assert calls[0][1]["query_terms"]


def test_route_graph_kb_v2_exposes_doi_filtering_metadata():
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [
                {"doi": "10.1021/jp1005692", "title": "Valid", "carbon_source": "sucrose"},
                {"doi": "10.1007/s12598-", "title": "Suspicious", "carbon_source": "sucrose"},
            ]

    routing_result = route_graph_kb_v2(
        question="列出使用蔗糖作为碳源的文献",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.diagnostics["graph_doi_candidates_count"] == 1
    assert routing_result.diagnostics["graph_filtered_doi_count"] == 1
    assert routing_result.diagnostics["graph_suspicious_doi_count"] == 1


def test_route_graph_kb_v2_skips_when_graph_unavailable():
    routing_result = route_graph_kb_v2(
        question="列出使用蔗糖作为碳源的文献",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=None, available=False, degraded=True),
        max_rows=5,
    )

    assert routing_result.mode == "skip_graph"
    assert routing_result.diagnostics["graph_fallback_reason"] == "neo4j_unavailable"
    assert routing_result.diagnostics["graph_ready"] is False
    assert routing_result.diagnostics["graph_execution_mode"] == "skip_graph"
    assert routing_result.diagnostics["tri_state_mode"] == "skip_graph"


def test_route_graph_kb_v2_direct_decline_downgrades_to_graph_for_rag():
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [{"doi": "10.1007/s12598-", "title": "Broken", "carbon_sources": ["sucrose"]}]

    routing_result = route_graph_kb_v2(
        question="列出使用蔗糖作为碳源的文献",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.mode == "graph_for_rag"
    assert routing_result.rag_payload is not None
    assert routing_result.diagnostics["direct_fallback_reason"] == "suspicious_doi"
    assert routing_result.diagnostics["graph_execution_mode"] == "graph_for_rag"
    assert routing_result.diagnostics["tri_state_mode"] == "graph_for_rag"


def test_route_graph_kb_v2_community_without_id_records_fallback_reason():
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return []

    routing_result = route_graph_kb_v2(
        question="LFP 的关系网络和机制关联是什么？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.mode == "graph_for_rag"
    assert routing_result.rag_payload is not None
    assert routing_result.diagnostics["graph_route_family"] == "community"
    assert routing_result.diagnostics["graph_fallback_reason"] == "community_id_unavailable"


@pytest.mark.parametrize(
    ("question", "rows", "expected_route", "expected_mode"),
    [
        (
            "10.1021/jp1005692 这篇文献是什么？",
            [{"doi": "10.1021/jp1005692", "title": "Lookup Paper"}],
            "precise",
            "direct_answer",
        ),
        (
            "列出使用蔗糖作为碳源的文献",
            [{"doi": "10.1021/jp1005692", "title": "Carbon Paper", "carbon_sources": ["sucrose"]}],
            "precise",
            "direct_answer",
        ),
        (
            "放电容量超过150 mAh/g的LFP有哪些特点？",
            [{"doi": "10.1021/jp1005692", "title": "Capacity Paper", "sample_name": "LFP/C", "value": "155 mAh/g"}],
            "hybrid",
            "graph_for_rag",
        ),
        (
            "LFP 的关系网络和机制关联是什么？",
            [
                {
                    "community_id": 7,
                    "dois": ["10.1021/jp1005692"],
                    "titles": ["Community Paper"],
                    "materials": ["LFP/C"],
                    "preparation_methods": ["solid-state synthesis"],
                }
            ],
            "community",
            "graph_for_rag",
        ),
    ],
)
def test_route_graph_kb_v2_acceptance_fixture_matrix(question, rows, expected_route, expected_mode):
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [dict(row) for row in rows]

    routing_result = route_graph_kb_v2(
        question=question,
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.mode == expected_mode
    assert routing_result.diagnostics["graph_route_family"] == expected_route
    if expected_mode == "direct_answer":
        assert routing_result.direct_result is not None
        assert routing_result.direct_result.handled is True
    else:
        assert routing_result.rag_payload is not None


def test_route_graph_kb_v2_hybrid_payload_includes_candidate_and_expansion_facts():
    class _Graph:
        def query(self, cypher, params):
            _ = params
            if "preparation_methods" in str(cypher) or "carbon_sources" in str(cypher):
                return [{"doi": "10.1021/jp1005692", "preparation_methods": ["solid-state"], "carbon_sources": ["sucrose"]}]
            return [{"doi": "10.1021/jp1005692", "title": "Capacity Paper", "sample_name": "LFP/C", "value": "155 mAh/g"}]

    routing_result = route_graph_kb_v2(
        question="放电容量超过150 mAh/g的LFP有哪些特点？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.mode == "graph_for_rag"
    assert routing_result.rag_payload is not None
    fact_block = routing_result.rag_payload.stage4_fact_block
    assert "155 mAh/g" in fact_block
    assert "solid-state" in fact_block
    assert "sucrose" in fact_block


def test_route_graph_kb_v2_hybrid_filters_expansion_to_numeric_passing_dois():
    class _Graph:
        def query(self, cypher, params):
            if "preparation_methods" in str(cypher) or "carbon_sources" in str(cypher):
                return [
                    {"doi": "10.1021/high", "preparation_methods": ["solid-state"]},
                    {"doi": "10.1021/low", "preparation_methods": ["low-temp"]},
                    {"doi": "10.1021/unrelated", "preparation_methods": ["unrelated"]},
                ]
            return [
                {"doi": "10.1021/high", "title": "High Capacity", "sample_name": "LFP/C", "value": "155 mAh/g"},
                {"doi": "10.1021/low", "title": "Low Capacity", "sample_name": "LFP", "value": "145 mAh/g"},
            ]

    routing_result = route_graph_kb_v2(
        question="放电容量超过150 mAh/g的LFP有哪些特点？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=10,
    )

    assert routing_result.mode == "graph_for_rag"
    assert routing_result.rag_payload is not None
    assert routing_result.rag_payload.stage2_doi_candidates == ("10.1021/high",)
    fact_block = routing_result.rag_payload.stage4_fact_block
    assert "solid-state" in fact_block
    assert "low-temp" not in fact_block
    assert "unrelated" not in fact_block


def test_route_graph_kb_v2_hybrid_top_limit_orders_promoted_dois():
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [
                {"doi": "10.1021/mid", "title": "Mid Density", "sample_name": "LFP-2", "value": "2.4 g/cm3"},
                {"doi": "10.1021/high", "title": "High Density", "sample_name": "LFP-1", "value": "2.6 g/cm3"},
                {"doi": "10.1021/low", "title": "Low Density", "sample_name": "LFP-3", "value": "2.1 g/cm3"},
            ]

    routing_result = route_graph_kb_v2(
        question="请分析压实密度最高的前2个LiFePO4样品有哪些特点？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=10,
    )

    assert routing_result.mode == "graph_for_rag"
    assert routing_result.rag_payload is not None
    assert routing_result.rag_payload.stage2_doi_candidates == ("10.1021/high", "10.1021/mid")


def test_route_graph_kb_v2_hybrid_top_limit_fetches_broader_candidate_pool_before_ranking():
    captured_limits: list[int] = []

    class _Graph:
        def query(self, cypher, params):
            captured_limits.append(int(params.get("limit") or 0))
            rows = [
                {"doi": "10.1021/low", "title": "Low Density", "sample_name": "LFP-3", "value": "2.1 g/cm3"},
                {"doi": "10.1021/mid", "title": "Mid Density", "sample_name": "LFP-2", "value": "2.4 g/cm3"},
            ]
            if int(params.get("limit") or 0) > 2:
                rows.append({"doi": "10.1021/high", "title": "High Density", "sample_name": "LFP-1", "value": "2.6 g/cm3"})
            return rows

    routing_result = route_graph_kb_v2(
        question="请分析压实密度最高的前2个LiFePO4样品有哪些特点？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=10,
    )

    assert captured_limits
    assert captured_limits[0] > 2
    assert routing_result.mode == "graph_for_rag"
    assert routing_result.rag_payload is not None
    assert routing_result.rag_payload.stage2_doi_candidates == ("10.1021/high", "10.1021/mid")


def test_route_graph_kb_v2_hybrid_ranking_uses_rows_beyond_service_max_rows():
    class _Graph:
        def query(self, cypher, params):
            if "candidate_dois" in params and params.get("candidate_dois"):
                return []
            rows = [
                {
                    "doi": f"10.1021/low-{index}",
                    "title": f"Low Density {index}",
                    "sample_name": f"LFP-{index}",
                    "value": "2.1 g/cm3",
                }
                for index in range(20)
            ]
            rows.append({"doi": "10.1021/high", "title": "High Density", "sample_name": "LFP-high", "value": "2.8 g/cm3"})
            return rows

    routing_result = route_graph_kb_v2(
        question="请分析压实密度最高的前2个LiFePO4样品有哪些特点？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=10,
    )

    assert routing_result.mode == "graph_for_rag"
    assert routing_result.rag_payload is not None
    assert routing_result.rag_payload.stage2_doi_candidates[0] == "10.1021/high"


def test_route_graph_kb_v2_process_method_uses_material_target_terms():
    captured_params: list[dict] = []

    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            captured_params.append(dict(params))
            return [
                {
                    "doi": "10.1021/lfp",
                    "title": "LiFePO4 process paper",
                    "preparation_methods": ["solid-state synthesis"],
                }
            ]

    routing_result = route_graph_kb_v2(
        question="LiFePO4 的制备方法有哪些？",
        conversation_context={},
        neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
        max_rows=5,
    )

    assert routing_result.mode == "graph_for_rag"
    assert routing_result.rag_payload is not None
    assert captured_params[0]["target_terms"]
    assert "lifepo4" in captured_params[0]["target_terms"]
    assert "solid-state synthesis" in routing_result.rag_payload.stage4_fact_block


def test_route_graph_kb_v2_logs_each_graph_stage_for_process_question(caplog):
    class _Graph:
        def query(self, cypher, params):
            _ = cypher
            _ = params
            return [
                {
                    "doi": "10.1021/lfp",
                    "title": "LiFePO4 process paper",
                    "preparation_methods": ["solid-state synthesis"],
                }
            ]

    with caplog.at_level(logging.INFO, logger="app.modules.graph_kb"):
        routing_result = route_graph_kb_v2(
            question="LiFePO4 的制备方法有哪些？",
            conversation_context={},
            neo4j_client=SimpleNamespace(graph=_Graph(), available=True, degraded=False),
            max_rows=5,
        )

    assert routing_result.mode == "graph_for_rag"
    assert "graph_kb_v2 route_start" in caplog.text
    assert "graph_kb_v2 classify_done" in caplog.text
    assert "matched_rule=process_slot_signal" in caplog.text
    assert "graph_kb_v2 plan_done" in caplog.text
    assert "intent=list_by_process_method" in caplog.text
    assert "graph_kb_v2 executor_start" in caplog.text
    assert "graph_kb_v2 candidate_execute_start" in caplog.text
    assert "path_id=process.method" in caplog.text
    assert "graph_kb_v2 candidate_execute_done" in caplog.text
    assert "graph_kb_v2 executor_done" in caplog.text
    assert "graph_kb_v2 canonicalize_done" in caplog.text
    assert "graph_kb_v2 rag_payload_done" in caplog.text
    assert "graph_kb_v2 route_end mode=graph_for_rag" in caplog.text


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
