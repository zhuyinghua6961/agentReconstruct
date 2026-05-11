from __future__ import annotations

from app.modules.qa_kb.comparison_intent import (
    build_comparison_plan,
    build_retrieval_claims_from_comparison_plan,
)


def test_build_comparison_plan_extracts_lfp_precursor_objects():
    plan = build_comparison_plan(
        "磷酸铁锂粉体制备过程中，以磷酸铁为原料、以草酸亚铁为原料、以铁红为原料，各有什么优劣势",
        stage1_result={},
        retrieval_claims=[],
    )

    assert plan["enabled"] is True
    labels = [item["label"] for item in plan["objects"]]
    assert labels == ["磷酸铁", "草酸亚铁", "铁红"]
    assert plan["objects"][0]["aliases"] == ["FePO4", "iron phosphate"]
    assert "磷酸铁锂" in plan["objects"][0]["avoid_confusions"]
    assert "FeC2O4" in plan["objects"][1]["aliases"]
    assert "red iron oxide" in plan["objects"][2]["aliases"]
    assert "成本" in plan["dimensions"]
    assert "电化学表现" in plan["dimensions"]


def test_build_comparison_plan_extracts_method_objects():
    plan = build_comparison_plan(
        "固相法、水热法、溶胶凝胶法制备 LFP 各有什么优劣势？",
        stage1_result={},
        retrieval_claims=[],
    )

    assert plan["enabled"] is True
    assert [item["label"] for item in plan["objects"]] == ["固相法", "水热法", "溶胶凝胶法"]
    assert "solid-state" in plan["objects"][0]["aliases"]
    assert "hydrothermal" in plan["objects"][1]["aliases"]
    assert "sol-gel" in plan["objects"][2]["aliases"]


def test_build_retrieval_claims_from_comparison_plan_locks_each_object():
    plan = build_comparison_plan(
        "葡萄糖、蔗糖、柠檬酸作为碳源或还原剂各适用什么场景？",
        stage1_result={"retrieval_claims": [{"claim": "比较碳源", "keywords": ["LFP"]}]},
        retrieval_claims=[{"claim": "比较碳源", "keywords": ["LFP"]}],
    )

    claims = build_retrieval_claims_from_comparison_plan(plan)

    assert [claim["comparison_object"] for claim in claims] == ["葡萄糖", "蔗糖", "柠檬酸"]
    assert all(claim["comparison_group"] for claim in claims)
    assert all("LFP" in claim["keywords"] for claim in claims)
    assert any("glucose" in claim["keywords"] for claim in claims)
    assert any("sucrose" in claim["keywords"] for claim in claims)
    assert any("citric acid" in claim["keywords"] for claim in claims)
