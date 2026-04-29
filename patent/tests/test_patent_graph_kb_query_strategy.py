from __future__ import annotations

from server.patent.graph_kb.models import PatentGraphSemanticDecision
from server.patent.graph_kb.query_strategy import (
    can_build_patent_parametric_query,
    can_use_patent_legacy_template,
    select_patent_query_strategy,
)


def test_query_strategy_prefers_legacy_template_for_existing_questions():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")

    assert can_use_patent_legacy_template("CN100355122C 的工艺步骤是什么？") is True
    assert (
        select_patent_query_strategy(
            question="CN100355122C 的工艺步骤是什么？",
            decision=decision,
        )
        == "parametric"
    )


def test_query_strategy_marks_inventor_agency_and_ipc_subclass_as_parametric():
    direct_precise = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")

    assert can_build_patent_parametric_query(
        question="发明人张三有哪些专利？",
        decision=direct_precise,
    )
    assert (
        select_patent_query_strategy(
            question="发明人张三有哪些专利？",
            decision=direct_precise,
        )
        == "parametric"
    )
    assert (
        select_patent_query_strategy(
            question="代理机构北京理工专利事务所有哪些专利？",
            decision=direct_precise,
        )
        == "parametric"
    )
    assert (
        select_patent_query_strategy(
            question="H01M10 下有哪些专利？",
            decision=direct_precise,
        )
        == "parametric"
    )


def test_query_strategy_marks_multi_patent_compare_as_parametric():
    decision = PatentGraphSemanticDecision(mode="graph_for_rag", route_family="hybrid")

    assert can_build_patent_parametric_query(
        question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
        decision=decision,
    )
    assert (
        select_patent_query_strategy(
            question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
            decision=decision,
        )
        == "parametric"
    )


def test_query_strategy_returns_none_for_broad_semantic_questions():
    decision = PatentGraphSemanticDecision(mode="skip_graph", route_family="semantic")

    assert can_build_patent_parametric_query(
        question="为什么这种技术路线更有前景？",
        decision=decision,
    ) is False
    assert select_patent_query_strategy(
        question="为什么这种技术路线更有前景？",
        decision=decision,
    ) is None


def test_query_strategy_keeps_llm_cypher_disabled_by_default():
    decision = PatentGraphSemanticDecision(mode="graph_for_rag", route_family="hybrid")

    assert can_build_patent_parametric_query(
        question="总结 发明人张三相关专利的常见技术方案",
        decision=decision,
    ) is True
    assert select_patent_query_strategy(
        question="总结 发明人张三相关专利的常见技术方案",
        decision=decision,
    ) == "parametric"


def test_query_strategy_specific_facets_outrank_generic_lookup():
    decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise")

    assert select_patent_query_strategy(question="CN100355122C 的气氛条件是什么？", decision=decision) == "parametric"
    assert select_patent_query_strategy(question="CN100355122C 的实施例洞察是什么？", decision=decision) == "parametric"
    assert select_patent_query_strategy(question="CN100355122C 的发明点是什么？", decision=decision) == "parametric"


def test_query_strategy_material_process_analysis_stays_parametric_graph_for_rag():
    decision = PatentGraphSemanticDecision(mode="graph_for_rag", route_family="hybrid")

    assert select_patent_query_strategy(question="为什么喷雾干燥能提升磷酸铁锂倍率性能？", decision=decision) == "parametric"
