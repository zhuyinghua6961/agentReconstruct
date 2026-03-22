from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from threading import Lock
from typing import Any

from app.modules.storage.uploaded_file_storage import materialize_uploaded_file


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


class _TTLCache:
    def __init__(self, *, max_size: int = 64, ttl_seconds: int = 1800) -> None:
        self._max_size = max(1, int(max_size))
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._lock = Lock()
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        import time

        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            ts, value = item
            if now - ts > self._ttl_seconds:
                self._items.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        import time

        now = time.time()
        with self._lock:
            if len(self._items) >= self._max_size:
                oldest_key = min(self._items.keys(), key=lambda k: self._items[k][0])
                self._items.pop(oldest_key, None)
            self._items[key] = (now, value)


_WORKBOOK_CACHE = _TTLCache(max_size=64, ttl_seconds=1800)
_LARGE_TABLE_ROW_THRESHOLD = 50000


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value)


def build_file_signature(file_item: dict) -> str:
    local_path = str(file_item.get("local_path") or "").strip()
    stat_bits = "missing"
    if local_path:
        try:
            st = Path(local_path).stat()
            stat_bits = f"{int(st.st_mtime_ns)}:{int(st.st_size)}"
        except Exception:
            pass
    raw = _stable_json(
        {
            "file_id": _safe_int(file_item.get("file_id"), 0),
            "file_name": str(file_item.get("file_name") or ""),
            "local_path": local_path,
            "storage_ref": str(file_item.get("storage_ref") or ""),
            "status_updated_at": str(file_item.get("status_updated_at") or ""),
            "stat": stat_bits,
        }
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _sanitize_column_names(columns: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: dict[str, int] = {}
    for idx, raw in enumerate(columns, start=1):
        text = str(raw or "").strip()
        duplicate_match = re.match(r"^(.*)\.(\d+)$", text) if text else None
        if duplicate_match and duplicate_match.group(1).strip():
            text = duplicate_match.group(1).strip()
        if not text or text.lower().startswith("unnamed:"):
            text = f"column_{idx}"
        count = seen.get(text, 0) + 1
        seen[text] = count
        if count > 1:
            text = f"{text}__{count}"
        normalized.append(text)
    return normalized


def _sanitize_frame(frame):
    try:
        import pandas as pd
    except Exception as exc:
        raise RuntimeError(f"pandas not available: {exc}") from exc
    if frame is None:
        return frame, {}
    cleaned = frame.copy()
    cleaned.columns = _sanitize_column_names(list(cleaned.columns))
    cleaned = cleaned.replace(r"^\s*$", pd.NA, regex=True)
    row_count_before = int(len(cleaned.index))
    col_count_before = int(len(cleaned.columns))
    cleaned = cleaned.dropna(axis=0, how="all")
    cleaned = cleaned.dropna(axis=1, how="all")
    cleaned.columns = _sanitize_column_names(list(cleaned.columns))
    row_count_after = int(len(cleaned.index))
    col_count_after = int(len(cleaned.columns))
    return cleaned, {
        "row_count_before": row_count_before,
        "row_count_after": row_count_after,
        "column_count_before": col_count_before,
        "column_count_after": col_count_after,
        "dropped_empty_rows": max(0, row_count_before - row_count_after),
        "dropped_empty_columns": max(0, col_count_before - col_count_after),
        "large_table": int(row_count_after >= _LARGE_TABLE_ROW_THRESHOLD),
    }


def _load_csv(local_path: Path) -> dict[str, Any]:
    try:
        import pandas as pd
    except Exception as exc:
        raise RuntimeError(f"pandas not available: {exc}") from exc

    read_errors: list[str] = []
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            frame = pd.read_csv(
                local_path,
                encoding=encoding,
                sep=None,
                engine="python",
            )
            cleaned, quality = _sanitize_frame(frame)
            return {
                "sheet_name": local_path.stem or "Sheet1",
                "sheet_index": 0,
                "dataframe": cleaned,
                "source_format": "csv",
                "data_quality": quality,
            }
        except Exception as exc:
            read_errors.append(f"{encoding}:{exc}")

    raise RuntimeError("read csv failed: " + " | ".join(read_errors[-2:]))


def _load_excel(local_path: Path) -> list[dict[str, Any]]:
    try:
        import pandas as pd
    except Exception as exc:
        raise RuntimeError(f"pandas not available: {exc}") from exc

    try:
        workbook = pd.read_excel(local_path, sheet_name=None)
    except Exception as exc:
        raise RuntimeError(f"read excel failed: {exc}") from exc

    sheets: list[dict[str, Any]] = []
    for idx, (sheet_name, frame) in enumerate(workbook.items()):
        cleaned, quality = _sanitize_frame(frame)
        sheets.append(
            {
                "sheet_name": str(sheet_name),
                "sheet_index": idx,
                "dataframe": cleaned,
                "source_format": "excel",
                "data_quality": quality,
            }
        )
    return sheets


def load_workbook(file_item: dict) -> dict[str, Any]:
    resolved_file_item = materialize_uploaded_file(file_item=file_item)
    local_path_text = str(resolved_file_item.get("local_path") or "").strip()
    if not local_path_text:
        raise RuntimeError("missing readable source for uploaded table")
    local_path = Path(local_path_text)
    if not local_path.exists() or not local_path.is_file():
        raise RuntimeError(f"table file not found: {local_path_text}")

    suffix = local_path.suffix.lower()
    if suffix == ".csv":
        sheets = [_load_csv(local_path)]
    elif suffix in {".xls", ".xlsx"}:
        sheets = _load_excel(local_path)
    else:
        raise RuntimeError(f"unsupported tabular suffix: {suffix}")

    return {
        "file_id": _safe_int(resolved_file_item.get("file_id"), 0),
        "file_name": str(resolved_file_item.get("file_name") or local_path.name),
        "local_path": str(local_path),
        "storage_ref": str(resolved_file_item.get("storage_ref") or ""),
        "signature": build_file_signature(resolved_file_item),
        "sheets": sheets,
    }


def load_workbook_cached(file_item: dict) -> dict[str, Any]:
    resolved_file_item = materialize_uploaded_file(file_item=file_item)
    signature = build_file_signature(resolved_file_item)
    cached = _WORKBOOK_CACHE.get(signature)
    if isinstance(cached, dict):
        return cached
    workbook = load_workbook(resolved_file_item)
    _WORKBOOK_CACHE.set(signature, workbook)
    return workbook


__all__ = ["build_file_signature", "load_workbook", "load_workbook_cached"]
