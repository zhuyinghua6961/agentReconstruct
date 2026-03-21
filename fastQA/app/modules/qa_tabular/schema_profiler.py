from __future__ import annotations

import re
from typing import Any


def normalize_identifier(text: str) -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"\[[^\]]*\]", "", value)
    value = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)
    return value


def _sample_values(frame, column_name: str, *, limit: int = 5) -> list[str]:
    try:
        series = frame[column_name].dropna().astype(str)
    except Exception:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for value in series.tolist():
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        values.append(cleaned)
        if len(values) >= limit:
            break
    return values


def _is_numeric_series(frame, column_name: str) -> bool:
    try:
        import pandas as pd
    except Exception:
        return False
    try:
        series = frame[column_name]
        normalized = (
            series.astype(str)
            .str.strip()
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
        )
        numeric = pd.to_numeric(normalized, errors="coerce")
    except Exception:
        return False
    valid = int(numeric.notna().sum())
    if valid <= 0:
        return False
    total = max(1, int(series.notna().sum()))
    return (valid / total) >= 0.6


def profile_workbook(workbook: dict) -> dict[str, Any]:
    sheets_out: list[dict[str, Any]] = []
    for sheet in workbook.get("sheets") or []:
        frame = sheet.get("dataframe")
        if frame is None:
            continue
        sheet_name = str(sheet.get("sheet_name") or "Sheet")
        row_count = int(len(frame.index))
        column_names = [str(col) for col in list(frame.columns)]
        data_quality = sheet.get("data_quality") if isinstance(sheet.get("data_quality"), dict) else {}
        columns_out: list[dict[str, Any]] = []
        numeric_columns: list[str] = []
        for col in column_names:
            normalized = normalize_identifier(col)
            sample_values = _sample_values(frame, col)
            is_numeric = _is_numeric_series(frame, col)
            if is_numeric:
                numeric_columns.append(col)
            missing_ratio = 0.0
            try:
                total = max(1, row_count)
                missing_ratio = float(frame[col].isna().sum()) / float(total)
            except Exception:
                pass
            columns_out.append(
                {
                    "name": col,
                    "normalized_name": normalized,
                    "dtype": str(getattr(frame[col], "dtype", "unknown")),
                    "is_numeric": bool(is_numeric),
                    "missing_ratio": round(missing_ratio, 4),
                    "sample_values": sample_values,
                }
            )
        sheets_out.append(
            {
                "sheet_name": sheet_name,
                "normalized_sheet_name": normalize_identifier(sheet_name),
                "sheet_index": int(sheet.get("sheet_index") or 0),
                "row_count": row_count,
                "column_count": len(column_names),
                "data_quality": data_quality,
                "columns": columns_out,
                "column_names": column_names,
                "numeric_columns": numeric_columns,
            }
        )
    return {
        "file_id": int(workbook.get("file_id") or 0),
        "file_name": str(workbook.get("file_name") or ""),
        "sheet_count": len(sheets_out),
        "sheets": sheets_out,
    }


__all__ = ["normalize_identifier", "profile_workbook"]
