from __future__ import annotations

from server.patent.tabular.executor import execute_tabular_plan


def _summary_workbook():
    return {
        "file_name": "metrics.csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "sheet_index": 0,
                "headers": ["Material", "Batch", "Capacity", "Retention"],
                "rows": [
                    {"Material": "LFP", "Batch": "B1", "Capacity": "100", "Retention": "95"},
                    {"Material": "LMFP", "Batch": "B1", "Capacity": "120", "Retention": "96"},
                    {"Material": "LFP", "Batch": "B2", "Capacity": "110", "Retention": "94"},
                    {"Material": "LMFP", "Batch": "B3", "Capacity": "130", "Retention": "93"},
                    {"Material": "NCA", "Batch": "B4", "Capacity": "50", "Retention": "90"},
                    {"Material": "NCM", "Batch": "B5", "Capacity": "280", "Retention": "89"},
                ],
                "row_count": 6,
            }
        ],
    }


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


def test_execute_tabular_plan_summary_returns_rich_summary_stats():
    result = execute_tabular_plan(
        workbook=_summary_workbook(),
        plan={"operation": "summary", "sheet_name": "Sheet1"},
    )

    stats = result["summary_stats"]

    assert result["operation"] == "summary"
    assert result["row_count_before"] == 6
    assert result["row_count_after"] == 6
    assert stats["row_count"] == 6
    assert stats["column_count"] == 4
    assert stats["columns"] == ["Material", "Batch", "Capacity", "Retention"]
    assert "numeric_summaries" in stats
    assert "categorical_summaries" in stats
    assert "column_profiles" in stats


def test_execute_tabular_plan_summary_column_profiles_expose_shape_fields():
    result = execute_tabular_plan(
        workbook=_summary_workbook(),
        plan={"operation": "summary", "sheet_name": "Sheet1"},
    )

    material_profile = next(
        item for item in result["summary_stats"]["column_profiles"] if item["name"] == "Material"
    )

    assert set(material_profile) >= {"name", "kind", "missing_ratio", "unique_count"}
    assert material_profile["kind"] == "categorical"


def test_execute_tabular_plan_summary_numeric_stats_include_median():
    result = execute_tabular_plan(
        workbook=_summary_workbook(),
        plan={"operation": "summary", "sheet_name": "Sheet1"},
    )

    assert result["summary_stats"]["numeric_summaries"]["Capacity"]["median"] == 115.0


def test_execute_tabular_plan_summary_categorical_top_values_are_stably_sorted():
    result = execute_tabular_plan(
        workbook=_summary_workbook(),
        plan={"operation": "summary", "sheet_name": "Sheet1"},
    )

    top_values = result["summary_stats"]["categorical_summaries"]["Material"]["top_values"]

    assert top_values[0]["value"] == "LFP"
    assert top_values[1]["value"] == "LMFP"
    assert top_values[0]["count"] == 2
    assert top_values[1]["count"] == 2


def test_execute_tabular_plan_summary_uses_representative_rows_not_head_only():
    result = execute_tabular_plan(
        workbook=_summary_workbook(),
        plan={"operation": "summary", "sheet_name": "Sheet1"},
    )

    capacities = {row["Capacity"] for row in result["rows"]}

    assert "50" in capacities or 50 in capacities
    assert "280" in capacities or 280 in capacities
