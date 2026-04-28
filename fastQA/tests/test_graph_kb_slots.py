from __future__ import annotations

from app.modules.graph_kb.slots import extract_graph_slots


def test_extracts_doi():
    slots = extract_graph_slots("10.1021/jp1005692 这篇文献是什么？")

    assert slots.doi == "10.1021/jp1005692"
    assert slots.doi_intent == "lookup"


def test_extracts_doi_expansion_intent():
    slots = extract_graph_slots("展开 10.1021/jp1005692 的测试、工艺和原料信息")

    assert slots.doi == "10.1021/jp1005692"
    assert slots.doi_intent == "expand"


def test_extracts_carbon_source_and_entity():
    slots = extract_graph_slots("列出使用蔗糖作为碳源的 LiFePO4 文献")

    assert "sucrose" in slots.recipe_terms["carbon_source"] or "蔗糖" in slots.recipe_terms["carbon_source"]
    assert "lifepo4" in slots.entities


def test_extracts_numeric_property_threshold():
    slots = extract_graph_slots("放电容量超过150 mAh/g的LFP有哪些特点？")

    assert slots.property_field == "discharge_capacity"
    assert slots.operator in {">", ">="}
    assert slots.threshold == 150
    assert slots.analysis_signal is True


def test_extracts_community_signal():
    slots = extract_graph_slots("LiFePO4的关系网络和机制关联是什么？")

    assert slots.community_signal is True
    assert "lifepo4" in slots.entities


def test_extracts_count_signal_for_structured_field():
    slots = extract_graph_slots("统计使用 sucrose 作为碳源的文献数量")

    assert slots.count_signal is True
    assert "sucrose" in slots.recipe_terms["carbon_source"]
