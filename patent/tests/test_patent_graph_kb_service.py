from __future__ import annotations

from server.patent.graph_kb.models import PatentGraphKbDecision, PatentGraphKbQueryPlan
from server.patent.graph_kb.rendering import render_patent_graph_answer
import server.patent.graph_kb.service as patent_graph_kb_service
from server.patent.graph_kb.service import try_patent_graph_kb_answer


def test_render_patent_graph_answer_for_lookup_template():
    answer, references, reference_objects, metadata = render_patent_graph_answer(
        PatentGraphKbQueryPlan("lookup_patent_by_id", {"patent_id": "CN100355122C"}),
        [
            {
                "patent_id": "CN100355122C",
                "title": "一种提高磷酸铁锂大电流放电性能的方法",
                "abstract": "通过材料体系和工艺协同优化改善放电性能。",
                "application_date": "2005-01-01",
                "publication_date": "2007-01-01",
                "ipc_main": "H01M10/0525",
                "patent_type": "发明",
                "legal_status": "有效",
                "source_file": "CN100355122C.json",
                "stub": None,
                "ipc_codes": ["H01M10/0525"],
                "ipc_subclasses": ["H01M10"],
                "applicants": ["宁德时代新能源科技股份有限公司"],
                "agencies": ["示例代理机构"],
                "inventors": ["张三"],
            }
        ],
    )

    assert "CN100355122C" in answer
    assert "一种提高磷酸铁锂大电流放电性能的方法" in answer
    assert "宁德时代新能源科技股份有限公司" in answer
    assert references == ("CN100355122C",)
    assert reference_objects == (
        {
            "canonical_patent_id": "CN100355122C",
            "patent_id": "CN100355122C",
            "title": "一种提高磷酸铁锂大电流放电性能的方法",
            "source": "patent_graph",
        },
    )
    assert metadata["stub_filtered_count"] == 0


def test_render_patent_graph_answer_for_process_template():
    answer, references, reference_objects, metadata = render_patent_graph_answer(
        PatentGraphKbQueryPlan("list_patent_process_steps", {"patent_id": "CN100355122C"}),
        [
            {
                "patent_id": "CN100355122C",
                "stub": None,
                "step_order": 1,
                "step_name": "配料混合",
                "step_operation": "混合原料",
                "step_params_json": '{"temperature":"25C"}',
                "step_template": "配料混合",
            },
            {
                "patent_id": "CN100355122C",
                "stub": None,
                "step_order": 2,
                "step_name": "煅烧",
                "step_operation": "高温处理",
                "step_params_json": '{"temperature":"700C"}',
                "step_template": "煅烧",
            },
        ],
    )

    assert "配料混合" in answer
    assert "煅烧" in answer
    assert references == ("CN100355122C",)
    assert reference_objects[0]["patent_id"] == "CN100355122C"
    assert metadata["stub_filtered_count"] == 0


def test_render_patent_graph_answer_for_material_template():
    answer, references, _, _ = render_patent_graph_answer(
        PatentGraphKbQueryPlan("list_patent_material_roles", {"patent_id": "CN100355122C"}),
        [
            {
                "patent_id": "CN100355122C",
                "stub": None,
                "role_name": "正极材料",
                "role_type": "cathode",
                "role_ratio": "60%",
                "role_note": "优选",
                "material_name": "磷酸铁锂",
                "material_type": "active_material",
                "material_canonical_key": "lfp",
            }
        ],
    )

    assert "正极材料" in answer
    assert "磷酸铁锂" in answer
    assert "60%" in answer
    assert references == ("CN100355122C",)


def test_render_patent_graph_answer_for_material_template_keeps_readable_role_name():
    answer, references, _, _ = render_patent_graph_answer(
        PatentGraphKbQueryPlan("list_patent_material_roles", {"patent_id": "CN100371239C"}),
        [
            {
                "patent_id": "CN100371239C",
                "stub": None,
                "role_name": "掺杂源",
                "role_type": "main",
                "role_ratio": "M与Fe摩尔比在0-0.3之间",
                "role_note": "掺杂源的化合物是硫酸盐、硝酸盐、醋酸盐、氯化物的一种",
                "material_name": "锌源",
                "material_type": "dopant",
                "material_canonical_key": "zn",
            }
        ],
    )

    assert "掺杂源" in answer
    assert "main" not in answer
    assert references == ("CN100371239C",)


def test_render_patent_graph_answer_for_experiment_template():
    answer, _, _, _ = render_patent_graph_answer(
        PatentGraphKbQueryPlan("list_patent_experiment_tables", {"patent_id": "CN100355122C"}),
        [
            {
                "patent_id": "CN100355122C",
                "stub": None,
                "table_title": "性能表",
                "row_label": "实施例1",
                "measurement_name": "容量",
                "measurement_value": "120",
                "measurement_unit": "mAh",
                "measurement_note": "常温",
            }
        ],
    )

    assert "性能表" in answer
    assert "实施例1" in answer
    assert "容量" in answer
    assert "120" in answer


def test_render_patent_graph_answer_for_problem_solution_template():
    answer, references, _, _ = render_patent_graph_answer(
        PatentGraphKbQueryPlan("list_patent_problem_solution", {"patent_id": "CN100355122C"}),
        [
            {
                "patent_id": "CN100355122C",
                "stub": None,
                "problem_texts": ["倍率放电性能不足"],
                "solution_texts": ["优化磷酸铁锂颗粒形貌"],
                "scenario_texts": ["动力电池"],
            }
        ],
    )

    assert "倍率放电性能不足" in answer
    assert "优化磷酸铁锂颗粒形貌" in answer
    assert "动力电池" in answer
    assert references == ("CN100355122C",)


def test_render_patent_graph_answer_for_inventive_scope_template():
    answer, references, _, _ = render_patent_graph_answer(
        PatentGraphKbQueryPlan("list_patent_inventive_scope", {"patent_id": "CN100355122C"}),
        [
            {
                "patent_id": "CN100355122C",
                "stub": None,
                "inventive_point_texts": ["复配体系稳定性提升"],
                "inventive_categories": ["composition"],
                "performance_fact_texts": ["低温容量保持率提升"],
                "performance_categories": ["performance"],
                "protection_scope_texts": ["保护复配比例范围"],
                "protection_kinds": ["claim"],
                "claim_step_labels": ["步骤A", "步骤B"],
            }
        ],
    )

    assert "复配体系稳定性提升" in answer
    assert "保护复配比例范围" in answer
    assert "步骤A" in answer
    assert references == ("CN100355122C",)


def test_render_patent_graph_answer_for_listing_and_citation_filters_stub_rows():
    answer, references, reference_objects, metadata = render_patent_graph_answer(
        PatentGraphKbQueryPlan("list_patent_citations", {"patent_id": "CN100355122C"}),
        [
            {
                "patent_id": "CN100355122C",
                "stub": None,
                "cited_patent_id": "US1234567A",
                "cited_title": "stub citation",
                "cited_publication_date": "2001-01-01",
                "cited_stub": True,
            },
            {
                "patent_id": "CN100355122C",
                "stub": None,
                "cited_patent_id": "CN7654321B",
                "cited_title": "valid citation",
                "cited_publication_date": "2002-01-01",
                "cited_stub": False,
            },
        ],
    )

    assert "CN7654321B" in answer
    assert "US1234567A" not in answer
    assert references == ("CN7654321B",)
    assert reference_objects[0]["patent_id"] == "CN7654321B"
    assert metadata["stub_filtered_count"] == 1


def test_render_patent_graph_answer_returns_empty_for_target_stub():
    answer, references, reference_objects, metadata = render_patent_graph_answer(
        PatentGraphKbQueryPlan("lookup_patent_by_id", {"patent_id": "CN100355122C"}),
        [
            {
                "patent_id": "CN100355122C",
                "title": "stub patent",
                "stub": True,
            }
        ],
    )

    assert answer == ""
    assert references == ()
    assert reference_objects == ()
    assert metadata["stub_filtered_count"] == 0


def test_render_patent_graph_answer_returns_empty_for_lookup_without_semantic_payload():
    answer, references, reference_objects, metadata = render_patent_graph_answer(
        PatentGraphKbQueryPlan("lookup_patent_by_id", {"patent_id": "CN100502103C"}),
        [
            {
                "patent_id": "CN100502103C",
                "title": "",
                "abstract": "",
                "stub": None,
                "ipc_codes": [],
                "ipc_subclasses": [],
                "applicants": [],
                "agencies": [],
                "inventors": [],
            }
        ],
    )

    assert answer == ""
    assert references == ()
    assert reference_objects == ()
    assert metadata["stub_filtered_count"] == 0


def test_render_patent_graph_answer_returns_empty_for_problem_solution_without_facts():
    answer, references, reference_objects, metadata = render_patent_graph_answer(
        PatentGraphKbQueryPlan("list_patent_problem_solution", {"patent_id": "CN100502103C"}),
        [
            {
                "patent_id": "CN100502103C",
                "stub": None,
                "problem_texts": [],
                "solution_texts": [],
                "scenario_texts": [],
            }
        ],
    )

    assert answer == ""
    assert references == ()
    assert reference_objects == ()
    assert metadata["stub_filtered_count"] == 0


def test_render_patent_graph_answer_returns_empty_for_inventive_scope_without_facts():
    answer, references, reference_objects, metadata = render_patent_graph_answer(
        PatentGraphKbQueryPlan("list_patent_inventive_scope", {"patent_id": "CN100502103C"}),
        [
            {
                "patent_id": "CN100502103C",
                "stub": None,
                "inventive_point_texts": [],
                "inventive_categories": [],
                "performance_fact_texts": [],
                "performance_categories": [],
                "protection_scope_texts": [],
                "protection_kinds": [],
                "claim_step_labels": [],
            }
        ],
    )

    assert answer == ""
    assert references == ()
    assert reference_objects == ()
    assert metadata["stub_filtered_count"] == 0


def test_try_patent_graph_kb_answer_returns_classifier_fallback():
    result = try_patent_graph_kb_answer(
        question="为什么这种技术路线更有前景？",
        conversation_context={},
        neo4j_client=object(),
        max_rows=10,
        timeout_ms=3000,
        generation_runtime=object(),
    )

    assert result.handled is False
    assert result.fallback_reason == "broad_semantic_question"


def test_try_patent_graph_kb_answer_returns_classifier_fallback_for_multi_patent_question():
    result = try_patent_graph_kb_answer(
        question="CN100355122C 和 CN100371239C 有什么区别？",
        conversation_context={},
        neo4j_client=object(),
        max_rows=10,
        timeout_ms=3000,
        generation_runtime=object(),
    )

    assert result.handled is False
    assert result.fallback_reason == "multiple_patent_ids"


def test_try_patent_graph_kb_answer_returns_no_plan(monkeypatch):
    monkeypatch.setattr(
        patent_graph_kb_service,
        "classify_patent_graph_kb_question",
        lambda *args, **kwargs: PatentGraphKbDecision("try_graph", "forced", True, ()),
    )
    monkeypatch.setattr(
        patent_graph_kb_service,
        "plan_patent_graph_query",
        lambda question: None,
    )

    result = try_patent_graph_kb_answer(
        question="CN100355122C 这件专利是什么？",
        conversation_context={},
        neo4j_client=object(),
        max_rows=10,
        timeout_ms=3000,
        generation_runtime=object(),
    )

    assert result.handled is False
    assert result.fallback_reason == "no_plan"


def test_try_patent_graph_kb_answer_returns_timeout_fallback():
    class _Client:
        available = True

        def query(self, cypher, params, *, timeout_ms):
            raise TimeoutError("query timed out")

    result = try_patent_graph_kb_answer(
        question="CN100355122C 这件专利是什么？",
        conversation_context={},
        neo4j_client=_Client(),
        max_rows=10,
        timeout_ms=3000,
        generation_runtime=object(),
    )

    assert result.handled is False
    assert result.fallback_reason == "timeout"
    assert result.template_id == "lookup_patent_by_id"


def test_try_patent_graph_kb_answer_returns_stub_only_result_for_listing():
    class _Client:
        available = True

        def query(self, cypher, params, *, timeout_ms):
            return [
                {
                    "patent_id": "CN100355122C",
                    "title": "stub patent",
                    "application_date": "2001-01-01",
                    "publication_date": "2002-01-01",
                    "ipc_match": "H01M10/0525",
                    "stub": True,
                }
            ]

    result = try_patent_graph_kb_answer(
        question="H01M10/0525 下有哪些专利？",
        conversation_context={},
        neo4j_client=_Client(),
        max_rows=10,
        timeout_ms=3000,
        generation_runtime=object(),
    )

    assert result.handled is False
    assert result.fallback_reason == "stub_only_result"


def test_try_patent_graph_kb_answer_returns_render_empty_for_problem_solution_without_content():
    class _Client:
        available = True

        def query(self, cypher, params, *, timeout_ms):
            return [
                {
                    "patent_id": "CN100502103C",
                    "stub": None,
                    "problem_texts": [],
                    "solution_texts": [],
                    "scenario_texts": [],
                }
            ]

    result = try_patent_graph_kb_answer(
        question="CN100502103C 解决了什么技术问题，提出了什么方案？",
        conversation_context={},
        neo4j_client=_Client(),
        max_rows=10,
        timeout_ms=3000,
        generation_runtime=object(),
    )

    assert result.handled is False
    assert result.fallback_reason == "render_empty"
    assert result.template_id == "list_patent_problem_solution"


def test_try_patent_graph_kb_answer_returns_handled_result_for_lookup():
    class _GuardRuntime:
        def __getattr__(self, name):
            raise AssertionError(f"generation_runtime should not be touched: {name}")

    class _Client:
        available = True

        def query(self, cypher, params, *, timeout_ms):
            return [
                {
                    "patent_id": "CN100355122C",
                    "title": "一种提高磷酸铁锂大电流放电性能的方法",
                    "abstract": "通过材料体系和工艺协同优化改善放电性能。",
                    "application_date": "2005-01-01",
                    "publication_date": "2007-01-01",
                    "ipc_main": "H01M10/0525",
                    "patent_type": "发明",
                    "legal_status": "有效",
                    "source_file": "CN100355122C.json",
                    "stub": None,
                    "ipc_codes": ["H01M10/0525"],
                    "ipc_subclasses": ["H01M10"],
                    "applicants": ["宁德时代新能源科技股份有限公司"],
                    "agencies": ["示例代理机构"],
                    "inventors": ["张三"],
                }
            ]

    result = try_patent_graph_kb_answer(
        question="CN100355122C 这件专利是什么？",
        conversation_context={},
        neo4j_client=_Client(),
        max_rows=10,
        timeout_ms=3000,
        generation_runtime=_GuardRuntime(),
    )

    assert result.handled is True
    assert result.query_mode == "patent_graph_kb"
    assert result.template_id == "lookup_patent_by_id"
    assert result.references == ("CN100355122C",)
    assert result.reference_objects[0]["patent_id"] == "CN100355122C"
    assert "一种提高磷酸铁锂大电流放电性能的方法" in result.answer
