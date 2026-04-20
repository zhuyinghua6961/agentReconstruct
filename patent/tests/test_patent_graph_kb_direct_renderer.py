from __future__ import annotations

from server.patent.graph_kb.canonicalizer import canonicalize_patent_graph_rows
from server.patent.graph_kb.client import build_patent_parametric_query_candidates
from server.patent.graph_kb.direct_renderer import render_patent_direct_answer
from server.patent.graph_kb.models import PatentGraphKbQueryPlan, PatentGraphQueryPlanV2, PatentGraphSemanticDecision


def test_direct_renderer_returns_handled_result_for_template_bundle():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="template",
        intent="lookup_patent_by_id",
        legacy_template_id="lookup_patent_by_id",
        legacy_template_plan=PatentGraphKbQueryPlan("lookup_patent_by_id", {"patent_id": "CN100355122C"}),
    )
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {
                "patent_id": "CN100355122C",
                "title": "一种提高磷酸铁锂大电流放电性能的方法",
                "abstract": "通过材料体系和工艺协同优化改善放电性能。",
                "ipc_codes": ["H01M10/0525"],
                "applicants": ["宁德时代新能源科技股份有限公司"],
                "inventors": ["张三"],
                "stub": None,
            }
        ],
    )

    result = render_patent_direct_answer(
        decision=decision,
        plan=plan,
        bundle=bundle,
    )

    assert result.handled is True
    assert "CN100355122C" in result.answer
    assert result.references == ("CN100355122C",)
    assert result.reference_objects[0]["patent_id"] == result.references[0]


def test_direct_renderer_returns_handled_result_for_parametric_listing_bundle():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="inventor_listing",
        question="发明人张三有哪些专利？",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("发明人张三有哪些专利？"),
        },
    )
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {
                "patent_id": "CN100355122C",
                "title": "一种提高磷酸铁锂大电流放电性能的方法",
                "inventor_name": "张三",
                "stub": None,
            }
        ],
    )

    result = render_patent_direct_answer(
        decision=decision,
        plan=plan,
        bundle=bundle,
    )

    assert result.handled is True
    assert "张三" in result.answer
    assert result.references == ("CN100355122C",)
    assert result.reference_objects[0]["patent_id"] == result.references[0]


def test_direct_renderer_returns_unhandled_for_non_direct_bundle():
    decision = PatentGraphSemanticDecision(mode="graph_for_rag", route_family="hybrid")
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="multi_patent_compare",
        question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("比较 CN100355122C 和 CN100371239C 的工艺步骤差异"),
        },
    )
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {"patent_id": "CN100355122C", "step_name": "配料混合", "stub": None},
            {"patent_id": "CN100371239C", "step_name": "前驱体合成", "stub": None},
        ],
    )

    result = render_patent_direct_answer(
        decision=decision,
        plan=plan,
        bundle=bundle,
    )

    assert result.handled is False
    assert result.metadata["reason"] == "not_direct_answerable"


def test_direct_renderer_avoids_stub_only_direct_answers():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="template",
        intent="lookup_patent_by_id",
        legacy_template_id="lookup_patent_by_id",
        legacy_template_plan=PatentGraphKbQueryPlan("lookup_patent_by_id", {"patent_id": "CN100355122C"}),
    )
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {
                "patent_id": "CN100355122C",
                "title": "stub patent",
                "stub": True,
            }
        ],
    )

    result = render_patent_direct_answer(
        decision=decision,
        plan=plan,
        bundle=bundle,
    )

    assert result.handled is False
