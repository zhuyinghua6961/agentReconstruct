from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "lithium iron phosphate 使用 glucose 的文献有多少篇？",
            {
                "entities": {"lifepo4"},
                "recipe": {"carbon_source": {"glucose"}},
                "count_signal": True,
            },
        ),
        (
            "LiFePO4 的制备方法有哪些？",
            {
                "entities": {"lifepo4"},
                "process": {"method"},
                "enumeration_signal": True,
            },
        ),
        (
            "压实密度最高的前10个样品，它们的碳源有什么规律？",
            {
                "property_field": "compaction_density",
                "ranking": "top",
                "limit": 10,
                "analysis_signal": True,
            },
        ),
        (
            "碳含量为5%的样品有哪些？",
            {
                "recipe": {"carbon_content": {"碳含量"}},
                "operator": "=",
                "threshold": 5,
                "unit": "%",
            },
        ),
        (
            "烧结温度大于700 C 的 LFP 工艺有哪些？",
            {
                "entities": {"lifepo4"},
                "process": {"sintering", "temperature"},
                "operator": ">",
                "threshold": 700,
            },
        ),
        (
            "按社区总结 LFP 制备路线和性能关系",
            {
                "entities": {"lifepo4"},
                "community_signal": True,
                "analysis_signal": True,
            },
        ),
    ],
)
def test_extract_graph_slots_expanded_matrix(question, expected):
    slots = extract_graph_slots(question)

    for entity in expected.get("entities", set()):
        assert entity in slots.entities
    for key, values in expected.get("recipe", {}).items():
        assert key in slots.recipe_terms
        for value in values:
            assert value in slots.recipe_terms[key]
    for key in expected.get("process", set()):
        assert key in slots.process_terms
    for key in (
        "property_field",
        "operator",
        "threshold",
        "unit",
        "ranking",
        "limit",
        "community_signal",
        "analysis_signal",
        "enumeration_signal",
        "count_signal",
    ):
        if key in expected:
            assert getattr(slots, key) == expected[key]
