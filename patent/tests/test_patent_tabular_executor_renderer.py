from __future__ import annotations

from server.patent.tabular.executor import execute_tabular_plan


def test_execute_tabular_plan_returns_rows_and_summary_stats():
    workbook = {
        "file_name": "metrics.csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "sheet_index": 0,
                "headers": ["Material", "Capacity", "Retention"],
                "rows": [
                    {"Material": "LMFP", "Capacity": "120", "Retention": "95.5"},
                    {"Material": "LFP", "Capacity": "115", "Retention": "96.0"},
                    {"Material": "LMFP", "Capacity": "122", "Retention": "95.8"},
                ],
                "row_count": 3,
            }
        ],
    }
    plan = {
        "operation": "aggregate",
        "sheet_name": "Sheet1",
        "metric_columns": ["Capacity"],
        "group_by": "Material",
        "aggregate": "mean",
    }

    result = execute_tabular_plan(workbook=workbook, plan=plan)

    assert result["row_count"] > 0
    assert result["sheet_name"] == "Sheet1"
    assert "summary_stats" in result
    assert result["summary_stats"]["aggregate"] == "mean"
    assert any(row["Material"] == "LMFP" for row in result["rows"])


def test_execute_tabular_plan_returns_deterministic_empty_result_for_missing_sheet():
    workbook = {
        "file_name": "metrics.csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "sheet_index": 0,
                "headers": ["Material", "Capacity"],
                "rows": [{"Material": "LMFP", "Capacity": "120"}],
                "row_count": 1,
            }
        ],
    }
    plan = {
        "operation": "compare",
        "sheet_name": "MissingSheet",
        "metric_columns": ["Capacity"],
        "group_by": "Material",
        "aggregate": "mean",
    }

    result = execute_tabular_plan(workbook=workbook, plan=plan)

    assert result["row_count"] == 0
    assert result["rows"] == []
    assert result["empty_reason"] == "sheet_not_found"
    assert result["summary_stats"]["aggregate"] == "mean"


def test_execute_tabular_plan_applies_filters_before_compare_aggregation():
    workbook = {
        "file_name": "metrics.csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "sheet_index": 0,
                "headers": ["Material", "Temperature", "Capacity"],
                "rows": [
                    {"Material": "LMFP", "Temperature": "25", "Capacity": "120"},
                    {"Material": "LMFP", "Temperature": "35", "Capacity": "140"},
                    {"Material": "LFP", "Temperature": "25", "Capacity": "115"},
                ],
                "row_count": 3,
            }
        ],
    }
    plan = {
        "operation": "compare",
        "sheet_name": "Sheet1",
        "metric_columns": ["Capacity"],
        "group_by": "Material",
        "aggregate": "mean",
        "filters": [{"column": "Temperature", "value": "25"}],
    }

    result = execute_tabular_plan(workbook=workbook, plan=plan)

    assert result["row_count"] == 2
    lmfp_row = next(row for row in result["rows"] if row["Material"] == "LMFP")
    assert lmfp_row["Capacity"] == 120.0
    assert result["summary_stats"]["source_row_count"] == 2


def test_execute_tabular_plan_lookup_without_columns_returns_empty_result():
    workbook = {
        "file_name": "metrics.csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "sheet_index": 0,
                "headers": ["Material", "Capacity"],
                "rows": [{"Material": "LMFP", "Capacity": "120"}],
                "row_count": 1,
            }
        ],
    }
    plan = {
        "operation": "lookup",
        "sheet_name": "Sheet1",
        "lookup_columns": [],
        "filters": [{"column": "Material", "value": "LMFP"}],
        "aggregate": "mean",
    }

    result = execute_tabular_plan(workbook=workbook, plan=plan)

    assert result["row_count"] == 0
    assert result["rows"] == []
    assert result["empty_reason"] == "lookup_columns_missing"
