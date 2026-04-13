from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def normalize_identifier(text: str) -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"\[[^\]]*\]", "", value)
    value = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)
    return value


def _sample_values(rows: list[dict[str, str]], column_name: str, *, limit: int = 5) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for row in rows:
        value = str(row.get(column_name) or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= limit:
            break
    return values


def _is_numeric_value(value: str) -> bool:
    normalized = str(value or "").strip().replace(",", "").replace("%", "")
    if not normalized:
        return False
    try:
        float(normalized)
    except ValueError:
        return False
    return True


def _is_numeric_column(rows: list[dict[str, str]], column_name: str) -> bool:
    observed = [str(row.get(column_name) or "").strip() for row in rows]
    non_empty = [value for value in observed if value]
    if not non_empty:
        return False
    numeric_count = sum(1 for value in non_empty if _is_numeric_value(value))
    return (numeric_count / len(non_empty)) >= 0.6


def _is_date_like_value(value: str) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", normalized):
        try:
            datetime.fromisoformat(normalized.replace("/", "-"))
        except ValueError:
            return False
        return True
    if re.fullmatch(r"\d{1,2}[-/]\d{1,2}[-/]\d{4}", normalized):
        return True
    return False


def _is_date_like_column(rows: list[dict[str, str]], column_name: str) -> bool:
    observed = [str(row.get(column_name) or "").strip() for row in rows]
    non_empty = [value for value in observed if value]
    if not non_empty:
        return False
    date_like_count = sum(1 for value in non_empty if _is_date_like_value(value))
    return (date_like_count / len(non_empty)) >= 0.6


def profile_workbook(workbook: dict[str, Any]) -> dict[str, Any]:
    sheets_out: list[dict[str, Any]] = []
    for sheet in workbook.get("sheets") or []:
        headers = [str(header) for header in list(sheet.get("headers") or [])]
        rows = [dict(row) for row in list(sheet.get("rows") or []) if isinstance(row, dict)]
        numeric_columns: list[str] = []
        date_like_columns: list[str] = []
        text_columns: list[str] = []
        columns_out: list[dict[str, Any]] = []

        for header in headers:
            is_numeric = _is_numeric_column(rows, header)
            is_date_like = _is_date_like_column(rows, header)
            if is_numeric:
                numeric_columns.append(header)
            elif is_date_like:
                date_like_columns.append(header)
            else:
                text_columns.append(header)
            missing_ratio = 0.0
            if rows:
                missing_ratio = sum(1 for row in rows if not str(row.get(header) or "").strip()) / len(rows)
            columns_out.append(
                {
                    "name": header,
                    "normalized_name": normalize_identifier(header),
                    "is_numeric": is_numeric,
                    "is_date_like": is_date_like,
                    "missing_ratio": round(missing_ratio, 4),
                    "sample_values": _sample_values(rows, header),
                }
            )

        sheets_out.append(
            {
                "sheet_name": str(sheet.get("sheet_name") or "Sheet1"),
                "normalized_sheet_name": normalize_identifier(str(sheet.get("sheet_name") or "Sheet1")),
                "sheet_index": int(sheet.get("sheet_index") or 0),
                "row_count": int(sheet.get("row_count") or len(rows)),
                "column_count": len(headers),
                "column_names": headers,
                "numeric_columns": numeric_columns,
                "date_like_columns": date_like_columns,
                "text_columns": text_columns,
                "columns": columns_out,
            }
        )

    return {
        "file_name": str(workbook.get("file_name") or ""),
        "file_type": str(workbook.get("file_type") or ""),
        "sheet_count": len(sheets_out),
        "sheets": sheets_out,
    }


__all__ = ["normalize_identifier", "profile_workbook"]
