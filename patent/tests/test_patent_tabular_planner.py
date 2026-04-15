from __future__ import annotations

from server.patent.tabular.planner import plan_tabular_query


def _profile():
    return {
        "file_name": "metrics.csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "normalized_sheet_name": "sheet1",
                "row_count": 4,
                "column_count": 5,
                "column_names": ["Material", "Batch", "Capacity", "Retention", "Note"],
                "numeric_columns": ["Capacity", "Retention"],
                "date_like_columns": [],
                "text_columns": ["Material", "Batch", "Note"],
                "columns": [
                    {"name": "Material", "normalized_name": "material", "is_numeric": False, "is_date_like": False},
                    {"name": "Batch", "normalized_name": "batch", "is_numeric": False, "is_date_like": False},
                    {"name": "Capacity", "normalized_name": "capacity", "is_numeric": True, "is_date_like": False},
                    {"name": "Retention", "normalized_name": "retention", "is_numeric": True, "is_date_like": False},
                    {"name": "Note", "normalized_name": "note", "is_numeric": False, "is_date_like": False},
                ],
            }
        ],
    }


def _profile_zh():
    return {
        "file_name": "metrics.csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "normalized_sheet_name": "sheet1",
                "row_count": 4,
                "column_count": 5,
                "column_names": ["材料", "批次", "容量", "保持率", "备注"],
                "numeric_columns": ["容量", "保持率"],
                "date_like_columns": [],
                "text_columns": ["材料", "批次", "备注"],
                "columns": [
                    {"name": "材料", "normalized_name": "材料", "is_numeric": False, "is_date_like": False},
                    {"name": "批次", "normalized_name": "批次", "is_numeric": False, "is_date_like": False},
                    {"name": "容量", "normalized_name": "容量", "is_numeric": True, "is_date_like": False},
                    {"name": "保持率", "normalized_name": "保持率", "is_numeric": True, "is_date_like": False},
                    {"name": "备注", "normalized_name": "备注", "is_numeric": False, "is_date_like": False},
                ],
            }
        ],
    }


def test_plan_tabular_query_prefers_metric_and_group_columns_from_profile():
    profile = _profile()

    plan = plan_tabular_query(question="比较不同材料的容量均值", profile=profile)

    assert plan["operation"] == "compare"
    assert plan["sheet_name"] == "Sheet1"
    assert "Capacity" in plan["metric_columns"]
    assert plan["group_by"] == "Material"
    assert plan["needs_clarification"] is False


def test_plan_tabular_query_extracts_lookup_filters():
    profile = _profile()

    plan = plan_tabular_query(question="Material=LMFP 时 Capacity 是多少", profile=profile)

    assert plan["operation"] == "lookup"
    assert plan["lookup_columns"] == ["Capacity"]
    assert plan["filters"] == [{"column": "Material", "value": "LMFP"}]


def test_plan_tabular_query_defaults_analysis_questions_to_summary():
    plan = plan_tabular_query(question="分析这个表格有什么特点", profile=_profile_zh())

    assert plan["operation"] == "summary"


def test_plan_tabular_query_keeps_grouped_count_inside_aggregate():
    plan = plan_tabular_query(question="按批次统计数量", profile=_profile_zh())

    assert plan["operation"] == "aggregate"
    assert plan["aggregate"] == "count"
    assert plan["group_by"] == "批次"


def test_plan_tabular_query_keeps_explicit_mean_aggregate():
    plan = plan_tabular_query(question="平均容量是多少", profile=_profile_zh())

    assert plan["operation"] == "aggregate"
    assert plan["aggregate"] == "mean"


def test_plan_tabular_query_keeps_explicit_mean_aggregate_even_with_summary_keywords():
    plan = plan_tabular_query(question="总结一下平均容量是多少", profile=_profile_zh())

    assert plan["operation"] == "aggregate"
    assert plan["aggregate"] == "mean"


def test_plan_tabular_query_keeps_grouped_count_aggregate_even_with_summary_keywords():
    plan = plan_tabular_query(question="概述按批次统计数量", profile=_profile_zh())

    assert plan["operation"] == "aggregate"
    assert plan["aggregate"] == "count"
    assert plan["group_by"] == "批次"


def test_plan_tabular_query_keeps_compare_for_explicit_difference_question():
    plan = plan_tabular_query(question="比较不同材料的容量差异", profile=_profile_zh())

    assert plan["operation"] == "compare"


def test_plan_tabular_query_focus_columns_can_include_non_numeric_columns():
    plan = plan_tabular_query(question="总结不同批次和材料分布", profile=_profile_zh())

    assert "批次" in plan["focus_columns"]
    assert "材料" in plan["focus_columns"]


def test_plan_tabular_query_general_summary_does_not_fallback_to_first_numeric_focus_column():
    plan = plan_tabular_query(question="总结这个表格", profile=_profile_zh())

    assert plan["operation"] == "summary"
    assert plan["focus_columns"] == []
