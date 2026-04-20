from __future__ import annotations

from server.patent.graph_kb.client import build_patent_parametric_query_candidates
from server.patent.graph_kb.models import PatentGraphKbQueryPlan, PatentGraphQueryPlanV2
from server.patent.graph_kb.canonicalizer import canonicalize_patent_graph_rows


def test_canonicalizer_deduplicates_candidates_and_preserves_diagnostics():
    plan = PatentGraphQueryPlanV2(
        strategy="template",
        intent="lookup_patent_by_id",
        legacy_template_id="lookup_patent_by_id",
        legacy_template_plan=PatentGraphKbQueryPlan("lookup_patent_by_id", {"patent_id": "CN100355122C"}),
        diagnostics={"matched_rule": "legacy_template"},
    )

    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {
                "patent_id": "CN100355122C",
                "title": "一种提高磷酸铁锂大电流放电性能的方法",
                "ipc_codes": ["H01M10/0525", "H01M10/0525"],
                "applicants": ["宁德时代新能源科技股份有限公司"],
                "inventors": ["张三", "张三"],
                "stub": None,
            },
            {
                "patent_id": "CN100355122C",
                "title": "一种提高磷酸铁锂大电流放电性能的方法",
                "ipc_codes": ["H01M10/0525"],
                "applicants": ["宁德时代新能源科技股份有限公司"],
                "inventors": ["张三"],
                "stub": None,
            },
        ],
    )

    assert bundle.patent_candidates == ("CN100355122C",)
    assert bundle.ipc_candidates == ("H01M10/0525",)
    assert bundle.organization_candidates == ("宁德时代新能源科技股份有限公司",)
    assert bundle.inventor_candidates == ("张三",)
    assert bundle.direct_answerable is True
    assert bundle.diagnostics["matched_rule"] == "legacy_template"
    assert bundle.diagnostics["row_count"] == 2


def test_canonicalizer_marks_compare_plan_as_not_direct_answerable_and_stable():
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="multi_patent_compare",
        question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("比较 CN100355122C 和 CN100371239C 的工艺步骤差异"),
        },
        diagnostics={"matched_rule": "multi_patent_compare"},
    )

    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {"patent_id": "CN100355122C", "step_order": 1, "step_name": "配料混合", "stub": None},
            {"patent_id": "CN100371239C", "step_order": 1, "step_name": "前驱体合成", "stub": None},
        ],
    )

    assert bundle.patent_candidates == ("CN100355122C", "CN100371239C")
    assert bundle.direct_answerable is False
    assert bundle.facts == (
        "patent_id=CN100355122C; step_name=配料混合; step_order=1",
        "patent_id=CN100371239C; step_name=前驱体合成; step_order=1",
    )


def test_canonicalizer_extracts_constraints_for_parametric_listing():
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
        rows=[{"patent_id": "CN100355122C", "inventor_name": "张三", "title": "示例专利", "stub": None}],
    )

    assert bundle.constraints_for_rag[0].field == "person.inventor"
    assert bundle.constraints_for_rag[0].operator == "eq"
    assert bundle.constraints_for_rag[0].value == "张三"
