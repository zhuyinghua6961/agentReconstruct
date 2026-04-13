from __future__ import annotations

from server.patent.tabular.schema_profiler import profile_workbook


def test_profile_workbook_marks_numeric_and_text_columns():
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
                    {"Material": "NCM", "Capacity": "140", "Retention": ""},
                ],
            }
        ],
    }

    profile = profile_workbook(workbook)

    assert profile["file_name"] == "metrics.csv"
    assert profile["sheet_count"] == 1
    sheet = profile["sheets"][0]
    assert sheet["sheet_name"] == "Sheet1"
    assert "Capacity" in sheet["numeric_columns"]
    assert "Retention" in sheet["numeric_columns"]
    assert "Material" in sheet["text_columns"]
    assert sheet["column_count"] == 3
    assert sheet["row_count"] == 3


def test_profile_workbook_marks_date_like_columns():
    workbook = {
        "file_name": "schedule.csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "sheet_index": 0,
                "headers": ["RunDate", "Material", "Capacity"],
                "rows": [
                    {"RunDate": "2026-04-13", "Material": "LMFP", "Capacity": "120"},
                    {"RunDate": "2026-04-14", "Material": "LFP", "Capacity": "118"},
                    {"RunDate": "", "Material": "NCM", "Capacity": "140"},
                ],
            }
        ],
    }

    profile = profile_workbook(workbook)

    sheet = profile["sheets"][0]
    assert "RunDate" in sheet["date_like_columns"]
    run_date_column = next(column for column in sheet["columns"] if column["name"] == "RunDate")
    assert run_date_column["is_date_like"] is True
