from __future__ import annotations

from server.patent.tabular.planner import plan_tabular_query


def test_plan_tabular_query_prefers_metric_and_group_columns_from_profile():
    profile = {
        "file_name": "metrics.csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "normalized_sheet_name": "sheet1",
                "row_count": 3,
                "column_count": 3,
                "column_names": ["Material", "Capacity", "Retention"],
                "numeric_columns": ["Capacity", "Retention"],
                "date_like_columns": [],
                "text_columns": ["Material"],
                "columns": [
                    {"name": "Material", "normalized_name": "material", "is_numeric": False, "is_date_like": False},
                    {"name": "Capacity", "normalized_name": "capacity", "is_numeric": True, "is_date_like": False},
                    {"name": "Retention", "normalized_name": "retention", "is_numeric": True, "is_date_like": False},
                ],
            }
        ],
    }

    plan = plan_tabular_query(question="比较不同材料的容量均值", profile=profile)

    assert plan["operation"] == "compare"
    assert plan["sheet_name"] == "Sheet1"
    assert "Capacity" in plan["metric_columns"]
    assert plan["group_by"] == "Material"
    assert plan["needs_clarification"] is False


def test_plan_tabular_query_extracts_lookup_filters():
    profile = {
        "file_name": "metrics.csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "normalized_sheet_name": "sheet1",
                "row_count": 3,
                "column_count": 3,
                "column_names": ["Material", "Capacity", "Retention"],
                "numeric_columns": ["Capacity", "Retention"],
                "date_like_columns": [],
                "text_columns": ["Material"],
                "columns": [
                    {"name": "Material", "normalized_name": "material", "is_numeric": False, "is_date_like": False},
                    {"name": "Capacity", "normalized_name": "capacity", "is_numeric": True, "is_date_like": False},
                    {"name": "Retention", "normalized_name": "retention", "is_numeric": True, "is_date_like": False},
                ],
            }
        ],
    }

    plan = plan_tabular_query(question="Material=LMFP 时 Capacity 是多少", profile=profile)

    assert plan["operation"] == "lookup"
    assert plan["lookup_columns"] == ["Capacity"]
    assert plan["filters"] == [{"column": "Material", "value": "LMFP"}]
