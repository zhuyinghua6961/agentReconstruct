from app.modules.qa_tabular.planner import plan_tabular_query


def _sample_profile(*, file_id: int, file_name: str) -> dict:
    return {
        "file_id": file_id,
        "file_name": file_name,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "normalized_sheet_name": "sheet1",
                "columns": [
                    {"name": "供应商", "normalized_name": "供应商", "is_numeric": False},
                    {"name": "容量", "normalized_name": "容量", "is_numeric": True},
                ],
                "numeric_columns": ["容量"],
            }
        ],
    }


def test_hybrid_compare_question_forces_summary_operation():
    plan = plan_tabular_query(
        question="对比一下这些文献和表格",
        profile=_sample_profile(file_id=1, file_name="demo.csv"),
        profiles=[_sample_profile(file_id=1, file_name="demo.csv")],
        workbook_count=1,
        route_hint="hybrid_qa",
        table_file_count=1,
        selection_strategy="explicit_selection",
    )

    assert plan.get("needs_clarification") is False
    assert plan.get("operation") == "summary"


def test_single_table_compare_question_forces_summary_operation():
    plan = plan_tabular_query(
        question="对比一下这两个表的数据",
        profile=_sample_profile(file_id=1, file_name="demo.csv"),
        profiles=[_sample_profile(file_id=1, file_name="demo.csv")],
        workbook_count=1,
        route_hint="tabular_qa",
        table_file_count=1,
        selection_strategy="explicit_selection",
    )

    assert plan.get("needs_clarification") is False
    assert plan.get("operation") == "summary"


def test_two_table_compare_question_keeps_compare_tables():
    profiles = [
        _sample_profile(file_id=1, file_name="a.csv"),
        _sample_profile(file_id=2, file_name="b.csv"),
    ]
    plan = plan_tabular_query(
        question="对比一下这两个表格",
        profile=profiles[0],
        profiles=profiles,
        workbook_count=2,
        route_hint="tabular_qa",
        table_file_count=2,
        selection_strategy="explicit_selection",
    )

    assert plan.get("needs_clarification") is False
    assert plan.get("operation") == "compare_tables"
