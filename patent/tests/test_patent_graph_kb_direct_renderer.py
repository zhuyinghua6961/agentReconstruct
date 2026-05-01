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


def test_direct_renderer_renders_rich_material_listing_profiles():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="material_listing",
        question="涉及磷酸铁锂的专利有哪些？",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("涉及磷酸铁锂的专利有哪些？"),
        },
    )
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {
                "patent_id": "CN100355122C",
                "title": "一种提高磷酸铁锂大电流放电性能的方法",
                "abstract": "通过材料体系和工艺协同优化改善放电性能。",
                "application_date": "2005-01-01",
                "publication_date": "2007-12-12",
                "legal_status": "授权",
                "applicants": ["宁德时代新能源科技股份有限公司"],
                "inventors": ["张三"],
                "ipc_codes": ["H01M10/0525"],
                "material_name": "磷酸铁锂",
                "material_roles": ["main: 磷酸铁锂"],
                "process_steps": ["喷雾干燥", "热处理"],
                "problems": ["倍率性能不足"],
                "solutions": ["通过碳包覆改善导电网络"],
                "inventive_points": ["优化粒径和包覆层"],
                "performance_facts": ["大电流放电性能提升"],
                "measurements": ["0.5C: 155 mAh/g"],
                "stub": None,
            }
        ],
    )

    result = render_patent_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert result.handled is True
    assert "CN100355122C" in result.answer
    assert "宁德时代新能源科技股份有限公司" in result.answer
    assert "授权" in result.answer
    assert "2007-12-12" in result.answer
    assert "磷酸铁锂" in result.answer
    assert "喷雾干燥" in result.answer
    assert "倍率性能不足" in result.answer
    assert "通过碳包覆改善导电网络" in result.answer
    assert "大电流放电性能提升" in result.answer
    assert "0.5C: 155 mAh/g" in result.answer
    assert result.references == ("CN100355122C",)
    assert result.reference_objects[0]["patent_id"] == "CN100355122C"


def test_direct_renderer_listing_header_handles_template_matched_lists():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="material_listing",
        question="涉及磷酸铁锂的专利有哪些？",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("涉及磷酸铁锂的专利有哪些？"),
        },
    )
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {
                "patent_id": "CN100355122C",
                "title": "示例专利",
                "material_name": ["磷酸铁锂", "LFP"],
                "stub": None,
            }
        ],
    )

    result = render_patent_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert result.handled is True
    assert "涉及材料 `磷酸铁锂；LFP` 的专利包括：" in result.answer
    assert "['磷酸铁锂'" not in result.answer


def test_direct_renderer_caps_patent_profiles_but_preserves_references():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="material_listing",
        question="涉及磷酸铁锂的专利有哪些？",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("涉及磷酸铁锂的专利有哪些？"),
        },
    )
    rows = [
        {
            "patent_id": f"CN{index:09d}A",
            "title": f"示例专利 {index}",
            "material_name": "磷酸铁锂",
            "stub": None,
        }
        for index in range(1, 8)
    ]
    bundle = canonicalize_patent_graph_rows(plan=plan, rows=rows)

    result = render_patent_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert result.handled is True
    assert "CN000000005A" in result.answer
    assert "CN000000006A" not in result.answer
    assert "另有 2 件" in result.answer
    assert result.references == tuple(row["patent_id"] for row in rows)
    assert len(result.reference_objects) == 7


def test_direct_renderer_cleans_and_truncates_patent_profile_values():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="process_listing",
        question="涉及热处理的专利有哪些？",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("涉及热处理的专利有哪些？"),
        },
    )
    long_fact = "性能提升" * 100 + "_null"
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {
                "patent_id": "CN100355122C",
                "title": "脏值专利",
                "step_name": "热处理",
                "process_steps": ["heat__treatment_null", "heat__treatment_null", "null"],
                "performance_facts": [long_fact],
                "stub": None,
            }
        ],
    )

    result = render_patent_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert result.handled is True
    assert "_null" not in result.answer
    assert "null_null" not in result.answer
    assert "heat treatment" in result.answer
    assert result.answer.count("heat treatment") == 1
    assert "..." in result.answer


def test_direct_renderer_lookup_appends_supplemental_graph_fields():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="lookup_patent_by_id",
        question="CN100355122C 这件专利是什么？",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("CN100355122C 这件专利是什么？"),
        },
    )
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {
                "patent_id": "CN100355122C",
                "title": "一种提高磷酸铁锂大电流放电性能的方法",
                "abstract": "通过材料体系和工艺协同优化改善放电性能。",
                "problems": ["倍率性能不足"],
                "solutions": ["碳包覆"],
                "inventive_points": ["优化包覆层"],
                "performance_facts": ["容量保持率提升"],
                "measurements": ["1C: 150 mAh/g"],
                "stub": None,
            }
        ],
    )

    result = render_patent_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert result.handled is True
    assert "图谱补充信息" in result.answer
    assert "倍率性能不足" in result.answer
    assert "碳包覆" in result.answer
    assert "容量保持率提升" in result.answer
    assert "1C: 150 mAh/g" in result.answer


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


def test_direct_renderer_handles_stub_true_process_facet_rows():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="list_patent_process_steps",
        question="CN100355122C 的工艺步骤是什么？",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("CN100355122C 的工艺步骤是什么？"),
        },
    )
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[
            {"patent_id": "CN100355122C", "title": "示例专利", "step_order": 1, "step_name": "干燥", "stub": True}
        ],
    )

    result = render_patent_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert result.handled is True
    assert "干燥" in result.answer
    assert result.references == ("CN100355122C",)


def test_direct_renderer_uses_matched_path_and_refuses_primary_facet_fallback_rows():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="list_patent_process_steps",
        question="CN100355122C 的工艺步骤是什么？",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("CN100355122C 的工艺步骤是什么？"),
        },
    )
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[{"patent_id": "CN100355122C", "title": "示例专利", "abstract": "基础信息", "stub": None}],
        matched_path="lookup_patent_by_id",
    )

    result = render_patent_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert result.handled is False
    assert result.metadata["reason"] == "not_direct_answerable"


def test_direct_renderer_handles_applicant_and_ipc_count_parametric_paths():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    applicant_plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="applicant_listing",
        question="宁德时代有哪些专利？",
        parametric_slots={"candidate_queries": build_patent_parametric_query_candidates("宁德时代有哪些专利？")},
    )
    applicant_bundle = canonicalize_patent_graph_rows(
        plan=applicant_plan,
        rows=[{"patent_id": "CN100355122C", "title": "示例专利", "applicant_name": "宁德时代", "stub": True}],
    )

    applicant_result = render_patent_direct_answer(decision=decision, plan=applicant_plan, bundle=applicant_bundle)

    assert applicant_result.handled is True
    assert "申请人 `宁德时代`" in applicant_result.answer
    assert applicant_result.references == ("CN100355122C",)

    ipc_plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="ipc_count",
        question="H01M10 有多少专利？",
        parametric_slots={"candidate_queries": build_patent_parametric_query_candidates("H01M10 有多少专利？")},
    )
    ipc_bundle = canonicalize_patent_graph_rows(
        plan=ipc_plan,
        rows=[{"ipc_code_prefix": "H01M10", "patent_count": 7}],
    )

    ipc_result = render_patent_direct_answer(decision=decision, plan=ipc_plan, bundle=ipc_bundle)

    assert ipc_result.handled is True
    assert "`H01M10` 对应的专利数量为 7" in ipc_result.answer


def test_direct_renderer_allows_stub_true_rows_when_requested_facet_exists():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")
    plan = PatentGraphQueryPlanV2(
        strategy="parametric",
        intent="list_patent_atmospheres",
        question="CN100355122C 的气氛条件是什么？",
        parametric_slots={
            "candidate_queries": build_patent_parametric_query_candidates("CN100355122C 的气氛条件是什么？"),
        },
    )
    bundle = canonicalize_patent_graph_rows(
        plan=plan,
        rows=[{"patent_id": "CN100355122C", "atmosphere_options": "氮气", "atmosphere_preferred": "true", "stub": True}],
    )

    result = render_patent_direct_answer(decision=decision, plan=plan, bundle=bundle)

    assert bundle.direct_answerable is True
    assert result.handled is True
    assert "氮气" in result.answer
