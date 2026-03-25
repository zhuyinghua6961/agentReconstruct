import pandas as pd

from app.modules.qa_tabular.executor import execute_tabular_plan
from app.modules.qa_tabular.renderer import _build_tabular_prompt, build_tabular_result_context


def _workbook(frame):
    return {
        "file_name": "cells.csv",
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "sheet_index": 0,
                "dataframe": frame,
            }
        ],
    }


def test_summary_execution_exposes_whole_table_statistics():
    frame = pd.DataFrame(
        {
            "供应商": ["宁德时代", "宁德时代", "亿纬锂能", "国轩高科", "国轩高科", "国轩高科"],
            "实际容量_Ah": [147.56, 152.10, 69.77, 128.36, 170.37, 160.11],
            "异常标记": ["正常", "正常", "容量衰减异常", "容量衰减异常", "容量衰减异常", "正常"],
        }
    )
    result = execute_tabular_plan(
        workbook=_workbook(frame),
        plan={"operation": "summary", "sheet_name": "Sheet1", "filters": []},
    )

    summary = result["summary_stats"]
    assert summary["row_count"] == 6
    assert summary["column_count"] == 3
    assert "column_profiles" in summary
    assert any(item["name"] == "实际容量_Ah" for item in summary["column_profiles"])
    assert "numeric_summaries" in summary
    assert summary["numeric_summaries"]["实际容量_Ah"]["max"] == 170.37
    assert summary["numeric_summaries"]["实际容量_Ah"]["min"] == 69.77
    assert "categorical_summaries" in summary
    assert summary["categorical_summaries"]["供应商"]["top_values"][0]["value"] == "国轩高科"
    assert len(result["result_rows"]) == 5




def test_summary_execution_uses_filtered_row_count_in_summary_stats():
    frame = pd.DataFrame(
        {
            "供应商": ["宁德时代", "宁德时代", "亿纬锂能", "国轩高科"],
            "实际容量_Ah": [147.56, 152.10, 69.77, 128.36],
            "异常标记": ["正常", "正常", "容量衰减异常", "容量衰减异常"],
        }
    )
    result = execute_tabular_plan(
        workbook=_workbook(frame),
        plan={
            "operation": "summary",
            "sheet_name": "Sheet1",
            "filters": [{"column": "异常标记", "op": "==", "value": "容量衰减异常"}],
        },
    )

    assert result["row_count_before"] == 4
    assert result["row_count_after"] == 2
    assert result["summary_stats"]["row_count"] == 2
    assert result["summary_stats"]["categorical_summaries"]["供应商"]["top_values"][0]["value"] in {"亿纬锂能", "国轩高科"}

def test_summary_context_mentions_whole_table_stats_and_marks_samples():
    result = {
        "operation": "summary",
        "sheet_name": "Sheet1",
        "row_count_before": 6,
        "row_count_after": 6,
        "summary_stats": {
            "row_count": 6,
            "column_count": 3,
            "columns": ["供应商", "实际容量_Ah", "异常标记"],
            "column_profiles": [
                {"name": "供应商", "kind": "categorical", "missing_ratio": 0.0, "unique_count": 3},
                {"name": "实际容量_Ah", "kind": "numeric", "missing_ratio": 0.0, "unique_count": 6},
            ],
            "numeric_summaries": {
                "实际容量_Ah": {"min": 69.77, "max": 170.37, "mean": 138.04, "median": 149.83}
            },
            "categorical_summaries": {
                "供应商": {
                    "top_values": [
                        {"value": "国轩高科", "count": 3, "ratio": 0.5},
                        {"value": "宁德时代", "count": 2, "ratio": 0.3333},
                    ]
                }
            },
        },
        "result_rows": [
            {"供应商": "宁德时代", "实际容量_Ah": 147.56, "异常标记": "正常"},
            {"供应商": "亿纬锂能", "实际容量_Ah": 69.77, "异常标记": "容量衰减异常"},
        ],
        "warnings": [],
    }

    context = build_tabular_result_context(
        file_name="cells.csv",
        plan={"operation": "summary", "filters": []},
        result=result,
    )

    assert "全表统计摘要" in context
    assert "数值列摘要" in context
    assert "类别列分布摘要" in context
    assert "实际容量_Ah" in context
    assert "供应商" in context
    assert "下方仅展示少量代表性样例" in context


def test_summary_execution_uses_representative_samples_not_only_first_rows():
    frame = pd.DataFrame(
        {
            "供应商": ["宁德时代", "宁德时代", "宁德时代", "宁德时代", "宁德时代", "稀有供应商"],
            "实际容量_Ah": [101.0, 102.0, 103.0, 104.0, 105.0, 999.0],
            "异常标记": ["正常", "正常", "正常", "正常", "正常", "极值样本"],
        }
    )
    result = execute_tabular_plan(
        workbook=_workbook(frame),
        plan={"operation": "summary", "sheet_name": "Sheet1", "filters": []},
    )

    assert len(result["result_rows"]) == 5
    assert any(row["供应商"] == "稀有供应商" for row in result["result_rows"])
    assert any(row["实际容量_Ah"] == 999.0 for row in result["result_rows"])


def test_summary_prompt_focuses_on_question_matched_columns_and_deemphasizes_samples():
    result = {
        "operation": "summary",
        "sheet_name": "Sheet1",
        "row_count_before": 6,
        "row_count_after": 6,
        "summary_stats": {
            "row_count": 6,
            "column_count": 4,
            "columns": ["供应商", "实际容量_Ah", "异常标记", "生产备注"],
            "column_profiles": [
                {"name": "供应商", "kind": "categorical", "missing_ratio": 0.0, "unique_count": 3},
                {"name": "实际容量_Ah", "kind": "numeric", "missing_ratio": 0.0, "unique_count": 6},
                {"name": "异常标记", "kind": "categorical", "missing_ratio": 0.0, "unique_count": 2},
                {"name": "生产备注", "kind": "categorical", "missing_ratio": 0.0, "unique_count": 6},
            ],
            "numeric_summaries": {
                "实际容量_Ah": {"min": 69.77, "max": 170.37, "mean": 138.04, "median": 149.83}
            },
            "categorical_summaries": {
                "供应商": {
                    "top_values": [
                        {"value": "国轩高科", "count": 3, "ratio": 0.5},
                        {"value": "宁德时代", "count": 2, "ratio": 0.3333},
                    ]
                },
                "异常标记": {
                    "top_values": [
                        {"value": "正常", "count": 3, "ratio": 0.5},
                        {"value": "容量衰减异常", "count": 3, "ratio": 0.5},
                    ]
                },
                "生产备注": {
                    "top_values": [
                        {"value": "批次A", "count": 1, "ratio": 0.1667},
                        {"value": "批次B", "count": 1, "ratio": 0.1667},
                    ]
                },
            },
        },
        "result_rows": [
            {"供应商": "宁德时代", "实际容量_Ah": 147.56, "异常标记": "正常", "生产备注": "批次A"},
            {"供应商": "亿纬锂能", "实际容量_Ah": 69.77, "异常标记": "容量衰减异常", "生产备注": "批次B"},
        ],
        "warnings": [],
    }

    prompt, context = _build_tabular_prompt(
        question="请总结各供应商的实际容量差异",
        file_name="cells.csv",
        plan={"operation": "summary", "filters": []},
        result=result,
        route_hint="tabular_qa",
    )

    assert "优先根据全表统计摘要作答" in prompt
    assert "不能把少量样例当成整体结论" in prompt
    assert "供应商" in context
    assert "实际容量_Ah" in context
    assert "生产备注" not in context
