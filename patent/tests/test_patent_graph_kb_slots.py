from __future__ import annotations

from server.patent.graph_kb.slots import extract_patent_graph_slots


def test_extracts_patent_ids_preserving_order_and_deduping():
    slots = extract_patent_graph_slots("比较 CN100355122C 和 cn100355122c 以及 CN100369314C")

    assert slots.patent_ids == ("CN100355122C", "CN100369314C")


def test_distinguishes_ipc_grains():
    slots = extract_patent_graph_slots("H01M H01M10 H01M10/0525 下有哪些专利")

    assert slots.ipc_prefixes == ("H01M",)
    assert slots.ipc_code_prefixes == ("H01M10",)
    assert slots.ipc_full_codes == ("H01M10/0525",)


def test_extracts_applicant_without_stealing_inventor_prefix():
    assert extract_patent_graph_slots("宁德时代新能源科技股份有限公司有哪些专利").applicant_names == (
        "宁德时代新能源科技股份有限公司",
    )
    assert extract_patent_graph_slots("发明人李长东有哪些专利").inventor_names == ("李长东",)
    assert extract_patent_graph_slots("代理机构北京三聚阳光知识产权代理有限公司有哪些专利").agency_names == (
        "北京三聚阳光知识产权代理有限公司",
    )


def test_extracts_process_material_metric_and_intents():
    slots = extract_patent_graph_slots("为什么喷雾干燥能提升磷酸铁锂倍率性能？")

    assert "喷雾干燥" in slots.process_terms
    assert "磷酸铁锂" in slots.material_terms
    assert "倍率性能" in slots.metric_terms
    assert slots.asks_why_how is True


def test_extracts_single_patent_facet_intents():
    slots = extract_patent_graph_slots("CN100355122C 的发明点、保护范围和气氛条件是什么？")

    assert slots.patent_ids == ("CN100355122C",)
    assert slots.asks_inventive_scope is True
    assert slots.asks_atmosphere is True
    assert slots.asks_lookup is True


def test_extracts_agency_from_suffix_agent_phrase():
    assert extract_patent_graph_slots("北京品源专利代理有限公司代理了哪些专利？").agency_names == (
        "北京品源专利代理有限公司",
    )
    assert extract_patent_graph_slots("北京品源专利代理有限公司代理了多少专利？").agency_names == (
        "北京品源专利代理有限公司",
    )


def test_extracts_material_and_role_terms_from_graph_questions():
    assert extract_patent_graph_slots("涉及石墨烯的专利有哪些？").material_terms == ("石墨烯",)
    assert extract_patent_graph_slots("石墨烯材料对性能有什么影响？").material_terms == ("石墨烯",)
    assert extract_patent_graph_slots("涉及 main 材料角色的专利有哪些？").material_role_terms == ("main",)


def test_does_not_extract_process_phrase_as_loose_material_term():
    slots = extract_patent_graph_slots("涉及干燥工艺的专利有哪些？")

    assert slots.material_terms == ()
    assert slots.process_terms == ("干燥",)


def test_extracts_performance_metric_hint_for_compare_questions():
    slots = extract_patent_graph_slots("比较 CN100355122C 和 CN100371239C 的性能指标差异")

    assert slots.asks_compare is True
    assert "性能指标" in slots.metric_terms


def test_extracts_material_attribute_value_intent_without_counting():
    cases = [
        "磷酸铁锂的电压是多少？",
        "磷酸铁锂电压范围是多少？",
        "磷酸铁锂容量是多少？",
        "磷酸铁锂的压实密度是多少？",
    ]

    for question in cases:
        slots = extract_patent_graph_slots(question)
        assert "磷酸铁锂" in slots.material_terms
        assert slots.asks_attribute_value is True
        assert slots.asks_count is False


def test_extracts_patent_count_intent_without_confusing_attribute_value():
    count_cases = [
        "涉及磷酸铁锂的专利有多少？",
        "磷酸铁锂相关专利数量是多少？",
        "磷酸铁锂电压相关申请数量是多少？",
        "磷酸铁锂电压相关授权数量是多少？",
        "磷酸铁锂电压相关公开数量是多少？",
        "宁德时代有多少专利？",
        "H01M10 下有多少专利？",
    ]

    for question in count_cases:
        slots = extract_patent_graph_slots(question)
        assert slots.asks_count is True
