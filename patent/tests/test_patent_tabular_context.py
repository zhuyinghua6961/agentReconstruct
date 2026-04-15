from __future__ import annotations

import server.patent.tabular_service as tabular_service_module
from server.patent.tabular.renderer import infer_tabular_summary_focus_columns
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


def _summary_workbook() -> dict:
    columns = [
        "batch",
        "material",
        *[f"cat_{index}" for index in range(1, 11)],
        "capacity_mah",
        "retention_pct",
        *[f"extra_num_{index}" for index in range(1, 9)],
    ]
    row = {column: f"value_{column}" for column in columns}
    row["batch"] = "B1"
    row["material"] = "LFP"
    row["capacity_mah"] = "120"
    row["retention_pct"] = "95"
    for index in range(1, 9):
        row[f"extra_num_{index}"] = str(100 + index)
    return {
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "column_names": columns,
                "numeric_columns": ["capacity_mah", "retention_pct", *[f"extra_num_{index}" for index in range(1, 9)]],
                "row_count": 6,
                "rows": [dict(row) for _ in range(6)],
            }
        ]
    }


def _summary_plan() -> dict:
    return {
        "sheet_name": "Sheet1",
        "operation": "summary",
        "aggregate": "mean",
        "metric_columns": ["capacity_mah"],
        "focus_columns": ["batch", "material"],
        "group_by": "",
        "lookup_columns": [],
        "filters": [],
    }


def _summary_plan_without_focus() -> dict:
    return {
        **_summary_plan(),
        "focus_columns": [],
        "metric_columns": ["capacity_mah"],
    }


def _summary_result() -> dict:
    columns = [
        "batch",
        "material",
        *[f"cat_{index}" for index in range(1, 11)],
        "capacity_mah",
        "retention_pct",
        *[f"extra_num_{index}" for index in range(1, 9)],
    ]
    column_profiles = []
    for column in columns:
        kind = "numeric" if column in {"capacity_mah", "retention_pct"} or column.startswith("extra_num_") else "categorical"
        column_profiles.append(
            {
                "name": column,
                "kind": kind,
                "missing_ratio": 0.0,
                "unique_count": 2,
            }
        )
    numeric_summaries = {
        "capacity_mah": {"min": 50, "max": 280, "mean": 131.2, "median": 115.0},
        "retention_pct": {"min": 89, "max": 96, "mean": 92.8, "median": 93.5},
        **{
            f"extra_num_{index}": {"min": index, "max": index + 10, "mean": index + 5.5, "median": index + 5.0}
            for index in range(1, 9)
        },
    }
    categorical_summaries = {
        "batch": {"top_values": [{"value": "B1", "count": 2, "ratio": 0.3333}, {"value": "B2", "count": 2, "ratio": 0.3333}]},
        "material": {"top_values": [{"value": "LFP", "count": 2, "ratio": 0.3333}, {"value": "LMFP", "count": 2, "ratio": 0.3333}]},
        **{
            f"cat_{index}": {
                "top_values": [
                    {"value": f"A{index}", "count": 2, "ratio": 0.3333},
                    {"value": f"B{index}", "count": 2, "ratio": 0.3333},
                ]
            }
            for index in range(1, 11)
        },
    }
    rows = [
        {"batch": "B1", "material": "LFP", "capacity_mah": "50", "retention_pct": "90"},
        {"batch": "B2", "material": "LMFP", "capacity_mah": "100", "retention_pct": "95"},
        {"batch": "B3", "material": "LFP", "capacity_mah": "110", "retention_pct": "94"},
        {"batch": "B4", "material": "LMFP", "capacity_mah": "120", "retention_pct": "93"},
        {"batch": "B5", "material": "NCA", "capacity_mah": "280", "retention_pct": "89"},
    ]
    return {
        "sheet_name": "Sheet1",
        "operation": "summary",
        "rows": rows,
        "row_count": 6,
        "row_count_before": 6,
        "row_count_after": 6,
        "empty_reason": "",
        "summary_stats": {
            "aggregate": "mean",
            "source_row_count": 6,
            "row_count": 6,
            "column_count": len(columns),
            "columns": columns,
            "column_profiles": column_profiles,
            "numeric_summaries": numeric_summaries,
            "categorical_summaries": categorical_summaries,
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
        workbook=_summary_workbook(),
        plan=_summary_plan(),
        result=_summary_result(),
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=6000,
    )

    assert "全表统计摘要:" in bundle["answer_context"]
    assert "数值列摘要:" in bundle["answer_context"]
    assert "代表性样例:" in bundle["answer_context"]


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


def test_build_tabular_context_bundle_summary_renders_full_table_sections_before_examples():
    bundle = build_tabular_context_bundle(
        question="总结这个表格",
        workbook=_summary_workbook(),
        plan=_summary_plan(),
        result=_summary_result(),
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=6000,
    )

    text = bundle["answer_context"]

    assert "全表统计摘要:" in text
    assert "列画像摘要:" in text
    assert "数值列摘要:" in text
    assert "类别列分布摘要:" in text
    assert text.index("全表统计摘要:") < text.index("代表性样例:")


def test_build_tabular_context_bundle_summary_budget_is_deterministic():
    bundle1 = build_tabular_context_bundle(
        question="总结这个表格",
        workbook=_summary_workbook(),
        plan=_summary_plan(),
        result=_summary_result(),
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=6000,
    )
    bundle2 = build_tabular_context_bundle(
        question="总结这个表格",
        workbook=_summary_workbook(),
        plan=_summary_plan(),
        result=_summary_result(),
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=6000,
    )

    assert bundle1["answer_context"] == bundle2["answer_context"]
    assert bundle1["synthesis_context"] == bundle2["synthesis_context"]


def test_build_tabular_context_bundle_summary_caps_answer_and_synthesis_sections():
    bundle = build_tabular_context_bundle(
        question="总结这个表格",
        workbook=_summary_workbook(),
        plan=_summary_plan(),
        result=_summary_result(),
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=12000,
    )

    assert bundle["answer_context"].count("kind=") <= 12
    assert bundle["answer_context"].count("ratio=") <= 6 * 5
    assert bundle["answer_context"].count("- 样例 ") <= 5
    assert bundle["synthesis_context"].count("kind=") <= 20
    assert bundle["synthesis_context"].count("ratio=") <= 10 * 5
    assert bundle["synthesis_context"].count("- 样例 ") <= 5


def test_build_tabular_context_bundle_summary_retains_focus_columns_before_other_columns():
    bundle = build_tabular_context_bundle(
        question="总结批次和材料分布",
        workbook=_summary_workbook(),
        plan=_summary_plan(),
        result=_summary_result(),
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=6000,
    )

    text = bundle["answer_context"]

    assert "focus_columns: batch, material" in text
    assert text.index("focus_columns: batch, material") < text.index("capacity_mah")


def test_build_tabular_context_bundle_summary_keeps_stable_order_for_profiles_and_categories():
    bundle = build_tabular_context_bundle(
        question="总结材料分布",
        workbook=_summary_workbook(),
        plan=_summary_plan(),
        result=_summary_result(),
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=6000,
    )

    text = bundle["answer_context"]

    assert text.index("- batch: kind=") < text.index("- material: kind=")
    assert text.index("LFP(2, ratio=0.3333)") < text.index("LMFP(2, ratio=0.3333)")


def test_infer_tabular_summary_focus_columns_keeps_generic_summary_as_whole_table_view():
    focus_columns = infer_tabular_summary_focus_columns(
        question="总结这个表格",
        plan=_summary_plan_without_focus(),
        result=_summary_result(),
    )

    assert focus_columns == []


def test_build_tabular_context_bundle_general_summary_does_not_reintroduce_metric_focus_fallback():
    bundle = build_tabular_context_bundle(
        question="总结这个表格",
        workbook=_summary_workbook(),
        plan=_summary_plan_without_focus(),
        result=_summary_result(),
        file_name="claims.csv",
        compact_limit=1200,
        answer_limit=12000,
        synthesis_limit=6000,
    )

    text = bundle["answer_context"]

    assert "focus_columns:" not in text
    assert "- 样例 1: batch=B1; material=LFP; capacity_mah=50; retention_pct=90" in text


def test_build_patent_tabular_prompt_summary_requires_distribution_difference_anomaly_first():
    prompt = tabular_service_module._build_patent_tabular_prompt(
        question="分析这个表格有什么特点",
        table_text="全表统计摘要:\n- row_count: 6\n数值列摘要:\n- capacity_mah: min=50, max=280, mean=131.2, median=115.0",
        route_hint="tabular_qa",
        source_scope="table",
        include_kb=False,
    )

    assert "先总结整体分布、差异、异常" in prompt
    assert "不能把少量样例当成整体结论" in prompt


def test_build_patent_tabular_prompt_hybrid_table_summary_preserves_table_fact_boundary():
    prompt = tabular_service_module._build_patent_tabular_prompt(
        question="总结这个表格",
        table_text="全表统计摘要:\n- row_count: 6",
        route_hint="hybrid_qa",
        source_scope="pdf+table+kb",
        include_kb=True,
    )

    assert "只能用于后续交叉验证" in prompt
    assert "不能把 PDF 或知识库内容写成当前表格事实" in prompt
    assert "优先根据全表统计摘要作答" in prompt
    assert "先总结整体分布、差异、异常" in prompt
    assert "不能把少量样例当成整体结论" in prompt
