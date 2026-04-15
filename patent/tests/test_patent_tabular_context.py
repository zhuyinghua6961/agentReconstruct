from __future__ import annotations

from server.patent.tabular_context import build_tabular_context_bundle


def _sample_workbook() -> dict:
    return {
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "column_names": ["material", "capacity_mah", "note"],
                "numeric_columns": ["capacity_mah"],
                "row_count": 3,
                "rows": [
                    {"material": "LMFP", "capacity_mah": "120", "note": "stable"},
                    {"material": "LFP", "capacity_mah": "115", "note": "safe"},
                    {"material": "NCM", "capacity_mah": "140", "note": "high energy"},
                ],
            }
        ]
    }


def _sample_plan() -> dict:
    return {
        "sheet_name": "Sheet1",
        "operation": "aggregate",
        "aggregate": "mean",
        "metric_columns": ["capacity_mah"],
        "group_by": "material",
        "lookup_columns": [],
        "filters": [],
    }


def _sample_result() -> dict:
    return {
        "sheet_name": "Sheet1",
        "operation": "aggregate",
        "rows": [
            {"material": "LMFP", "capacity_mah": 120.0},
            {"material": "LFP", "capacity_mah": 115.0},
            {"material": "NCM", "capacity_mah": 140.0},
        ],
        "row_count": 3,
        "empty_reason": "",
        "summary_stats": {
            "aggregate": "mean",
            "group_by": "material",
            "metric_columns": ["capacity_mah"],
            "source_row_count": 3,
            "filters": [],
        },
    }


def test_build_tabular_context_bundle_returns_compact_and_rich_contexts():
    bundle = build_tabular_context_bundle(
        question="哪个材料的容量更高",
        workbook=_sample_workbook(),
        plan=_sample_plan(),
        result=_sample_result(),
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=6000,
    )

    assert bundle["compact_evidence_context"]
    assert bundle["answer_context"]
    assert bundle["synthesis_context"]
    assert len(bundle["answer_context"]) >= len(bundle["compact_evidence_context"])
    assert len(bundle["synthesis_context"]) >= len(bundle["compact_evidence_context"])


def test_build_tabular_context_bundle_includes_summary_stats_and_top_rows():
    bundle = build_tabular_context_bundle(
        question="请总结这个表格的重点",
        workbook=_sample_workbook(),
        plan=_sample_plan(),
        result=_sample_result(),
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=6000,
    )

    assert "统计摘要:" in bundle["answer_context"]
    assert "代表性行:" in bundle["answer_context"]
    assert "命中结果:" in bundle["answer_context"]


def test_build_tabular_context_bundle_stats_follow_filtered_result_rows():
    result = {
        "sheet_name": "Sheet1",
        "operation": "lookup",
        "rows": [
            {"material": "LMFP", "capacity_mah": 120.0},
            {"material": "LFP", "capacity_mah": 115.0},
        ],
        "row_count": 2,
        "empty_reason": "",
        "summary_stats": {
            "aggregate": "lookup",
            "group_by": "",
            "metric_columns": ["capacity_mah"],
            "source_row_count": 2,
            "filters": [{"column": "material", "value": "LMFP/LFP"}],
        },
    }

    bundle = build_tabular_context_bundle(
        question="请对比 LMFP 和 LFP 的容量",
        workbook=_sample_workbook(),
        plan={
            **_sample_plan(),
            "operation": "lookup",
            "aggregate": "",
            "group_by": "",
            "filters": [{"column": "material", "value": "LMFP/LFP"}],
        },
        result=result,
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=6000,
    )

    assert "- 命中行数: 2" in bundle["answer_context"]
    assert "- capacity_mah: count=2, min=115, max=120, mean=117.5" in bundle["answer_context"]
    assert "max=140" not in bundle["answer_context"]
