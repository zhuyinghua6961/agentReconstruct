from __future__ import annotations

import csv
import hashlib
import posixpath
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from server.patent.object_reader import parse_minio_storage_ref

_XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

_WORKBOOK_CACHE: dict[str, dict[str, Any]] = {}


def _collapse_whitespace(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_row(values: list[str]) -> list[str]:
    normalized = [_collapse_whitespace(item) for item in values]
    while normalized and not normalized[-1]:
        normalized.pop()
    return normalized


def _sanitize_headers(values: list[str]) -> list[str]:
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index, value in enumerate(values, start=1):
        header = _collapse_whitespace(value) or f"column_{index}"
        count = seen.get(header, 0) + 1
        seen[header] = count
        if count > 1:
            header = f"{header}__{count}"
        headers.append(header)
    return headers


def _rows_to_sheet(*, sheet_name: str, sheet_index: int, rows: list[list[str]], source_format: str) -> dict[str, Any]:
    normalized_rows = [_normalize_row(row) for row in rows if any(_collapse_whitespace(cell) for cell in row)]
    if not normalized_rows:
        return {
            "sheet_name": sheet_name,
            "sheet_index": sheet_index,
            "headers": [],
            "rows": [],
            "row_count": 0,
            "source_format": source_format,
        }

    headers = _sanitize_headers(normalized_rows[0])
    row_dicts: list[dict[str, str]] = []
    for raw_row in normalized_rows[1:]:
        expanded = list(raw_row)
        if len(expanded) > len(headers):
            extra_headers = _sanitize_headers([f"column_{index}" for index in range(len(headers) + 1, len(expanded) + 1)])
            headers = [*headers, *extra_headers]
        row_dict = {
            headers[index]: expanded[index] if index < len(expanded) else ""
            for index in range(len(headers))
        }
        row_dicts.append(row_dict)

    return {
        "sheet_name": sheet_name,
        "sheet_index": sheet_index,
        "headers": headers,
        "rows": row_dicts,
        "row_count": len(row_dicts),
        "source_format": source_format,
    }


def _read_csv_rows(path: str) -> list[list[str]]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, "r", encoding=encoding, newline="") as handle:
                return [_normalize_row(row) for row in csv.reader(handle)]
        except UnicodeDecodeError:
            continue
    return []


def _cell_reference_to_index(reference: str) -> int:
    letters = "".join(char for char in str(reference or "") if char.isalpha()).upper()
    if not letters:
        return 0
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return max(0, index - 1)


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for item in root.findall("main:si", _XML_NS):
        text = "".join(node.text or "" for node in item.findall(".//main:t", _XML_NS))
        values.append(_collapse_whitespace(text))
    return values


def _xlsx_sheet_rows(xml_bytes: bytes, *, shared_strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(xml_bytes)
    rows: list[list[str]] = []
    for row in root.findall(".//main:sheetData/main:row", _XML_NS):
        values: list[str] = []
        expected_index = 0
        for cell in row.findall("main:c", _XML_NS):
            cell_ref = str(cell.attrib.get("r") or "")
            column_index = _cell_reference_to_index(cell_ref)
            while expected_index < column_index:
                values.append("")
                expected_index += 1
            cell_type = str(cell.attrib.get("t") or "")
            raw_value = cell.findtext("main:v", default="", namespaces=_XML_NS)
            if cell_type == "s":
                try:
                    value = shared_strings[int(raw_value)]
                except (ValueError, IndexError):
                    value = ""
            elif cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall("main:is//main:t", _XML_NS))
            else:
                value = raw_value
            values.append(_collapse_whitespace(value))
            expected_index += 1
        rows.append(_normalize_row(values))
    return rows


def _read_xlsx_rows(path: str, *, max_sheets: int) -> list[tuple[str, list[list[str]]]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_targets = {
            str(rel.attrib.get("Id") or ""): str(rel.attrib.get("Target") or "")
            for rel in relationships.findall("pkgrel:Relationship", _XML_NS)
        }
        sheets: list[tuple[str, list[list[str]]]] = []
        for sheet in workbook.findall("main:sheets/main:sheet", _XML_NS)[:max_sheets]:
            sheet_name = str(sheet.attrib.get("name") or f"Sheet{len(sheets) + 1}")
            rel_id = str(sheet.attrib.get(f"{{{_XML_NS['rel']}}}id") or "")
            target = rel_targets.get(rel_id, "")
            if not target:
                continue
            sheet_path = posixpath.normpath(target if target.startswith("xl/") else f"xl/{target.lstrip('/')}")
            rows = _xlsx_sheet_rows(archive.read(sheet_path), shared_strings=shared_strings)
            sheets.append((sheet_name, rows))
        return sheets


def _read_legacy_excel_rows(path: str, *, max_sheets: int) -> list[tuple[str, list[list[str]]]]:
    try:
        import pandas as pd
    except Exception as exc:
        raise RuntimeError(f"pandas not available: {exc}") from exc
    workbook = pd.read_excel(path, sheet_name=None, header=None)
    sheets: list[tuple[str, list[list[str]]]] = []
    for index, (sheet_name, frame) in enumerate(workbook.items()):
        if index >= max_sheets:
            break
        rows: list[list[str]] = []
        for raw_row in frame.fillna("").itertuples(index=False, name=None):
            rows.append(_normalize_row([str(value or "") for value in raw_row]))
        sheets.append((str(sheet_name), rows))
    return sheets


def _build_cache_key(*, path: str, file_name: str, file_type: str, max_sheets: int) -> str:
    resolved = Path(path)
    stat_key = "missing"
    try:
        stat = resolved.stat()
        stat_key = f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
    except OSError:
        pass
    raw = "|".join([str(resolved), str(file_name), str(file_type), str(max_sheets), stat_key])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _storage_ref_suffix(*, storage_ref: str, file_name: str, file_type: str) -> str:
    suffix = Path(str(file_name or "")).suffix.lower()
    if not suffix:
        try:
            _, object_name = parse_minio_storage_ref(storage_ref)
            suffix = Path(object_name).suffix.lower()
        except Exception:
            suffix = ""
    if not suffix:
        normalized_type = str(file_type or "").strip().lower()
        if normalized_type in {"csv", "xls", "xlsx", "xlsm"}:
            suffix = f".{normalized_type}"
        elif normalized_type in {"excel", "table"}:
            suffix = ".xlsx"
    return suffix or ".bin"


def _build_storage_cache_key(*, reader: Any, storage_ref: str, file_name: str, file_type: str, max_sheets: int) -> str:
    stat_key = "unstated"
    stater = getattr(reader, "stat", None)
    if callable(stater):
        try:
            stat = stater(storage_ref)
            stat_key = "|".join(
                [
                    str(getattr(stat, "bucket", "") or ""),
                    str(getattr(stat, "object_name", "") or ""),
                    str(getattr(stat, "etag", "") or ""),
                    str(getattr(stat, "size", "") or ""),
                    str(getattr(stat, "sha256", "") or ""),
                ]
            )
        except Exception:
            stat_key = "stat_failed"
    raw = "|".join([str(storage_ref), str(file_name), str(file_type), str(max_sheets), stat_key])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def load_workbook(*, path: str, file_name: str, file_type: str, max_sheets: int = 3) -> dict[str, Any]:
    resolved = Path(path)
    if not resolved.exists() or not resolved.is_file():
        raise RuntimeError(f"table file not found: {path}")

    name_suffix = Path(file_name).suffix.lower()
    suffix = resolved.suffix.lower() or name_suffix
    normalized_type = str(file_type or suffix.lstrip(".")).lower()
    if normalized_type in {"excel", "table"} and suffix in {".csv", ".xls", ".xlsx", ".xlsm"}:
        normalized_type = suffix.lstrip(".")
    sheets: list[dict[str, Any]] = []

    if normalized_type == "csv" or suffix == ".csv":
        sheets.append(
            _rows_to_sheet(
                sheet_name="Sheet1",
                sheet_index=0,
                rows=_read_csv_rows(str(resolved)),
                source_format="csv",
            )
        )
    elif normalized_type in {"xlsx", "xlsm", "excel", "table"} or suffix in {".xlsx", ".xlsm"}:
        for index, (sheet_name, rows) in enumerate(_read_xlsx_rows(str(resolved), max_sheets=max_sheets)):
            sheets.append(
                _rows_to_sheet(
                    sheet_name=sheet_name,
                    sheet_index=index,
                    rows=rows,
                    source_format="excel",
                )
            )
    elif normalized_type == "xls" or suffix == ".xls":
        legacy_sheets = _read_legacy_excel_rows(str(resolved), max_sheets=max_sheets)
        if not legacy_sheets:
            raise RuntimeError(f"failed to load legacy excel file: {path}")
        for index, (sheet_name, rows) in enumerate(legacy_sheets):
            sheets.append(
                _rows_to_sheet(
                    sheet_name=sheet_name,
                    sheet_index=index,
                    rows=rows,
                    source_format="excel",
                )
            )
    else:
        raise RuntimeError(f"unsupported tabular file type: {file_type or suffix}")

    return {
        "file_name": str(file_name or resolved.name),
        "file_type": normalized_type or suffix.lstrip("."),
        "local_path": str(resolved),
        "sheet_count": len(sheets),
        "sheets": sheets,
    }


def load_workbook_cached(*, path: str, file_name: str, file_type: str, max_sheets: int = 3) -> dict[str, Any]:
    cache_key = _build_cache_key(path=path, file_name=file_name, file_type=file_type, max_sheets=max_sheets)
    cached = _WORKBOOK_CACHE.get(cache_key)
    if cached is not None:
        return cached
    workbook = load_workbook(path=path, file_name=file_name, file_type=file_type, max_sheets=max_sheets)
    _WORKBOOK_CACHE[cache_key] = workbook
    return workbook


def load_workbook_from_execution_file(*, item: Any, reader: Any, max_sheets: int = 3) -> dict[str, Any]:
    payload = dict(getattr(item, "payload", {}) or {})
    storage_ref = str(payload.get("storage_ref") or "").strip()
    if not storage_ref:
        raise RuntimeError("storage_ref_missing")
    file_name = str(getattr(item, "file_name", "") or payload.get("file_name") or "")
    file_type = str(getattr(item, "file_type", "") or payload.get("file_type") or "").strip().lower()
    suffix = _storage_ref_suffix(storage_ref=storage_ref, file_name=file_name, file_type=file_type)
    cache_key = _build_storage_cache_key(
        reader=reader,
        storage_ref=storage_ref,
        file_name=file_name,
        file_type=file_type,
        max_sheets=max_sheets,
    )
    cached = _WORKBOOK_CACHE.get(cache_key)
    if cached is not None:
        return cached
    materializer = getattr(reader, "materialize_temp", None)
    if not callable(materializer):
        raise RuntimeError("object_reader_materialize_unavailable")
    scratch_path = materializer(storage_ref, suffix=suffix)
    workbook = load_workbook(
        path=str(scratch_path),
        file_name=file_name or Path(str(scratch_path)).name,
        file_type=file_type or suffix.lstrip("."),
        max_sheets=max_sheets,
    )
    workbook["local_path"] = ""
    workbook["storage_ref"] = storage_ref
    _WORKBOOK_CACHE[cache_key] = workbook
    return workbook


__all__ = [
    "load_workbook",
    "load_workbook_cached",
    "load_workbook_from_execution_file",
]
