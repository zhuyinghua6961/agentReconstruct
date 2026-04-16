from __future__ import annotations

import csv
import io
import zipfile
from typing import Any
from xml.etree import ElementTree as ET


XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg_rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def load_rows(*, file_bytes: bytes, ext: str) -> dict[str, Any]:
    if ext == "csv":
        return _load_csv_rows(file_bytes)
    if ext == "xlsx":
        return _load_xlsx_rows(file_bytes)
    raise ValueError("unsupported extension")


def build_xlsx(*, headers: list[str], rows: list[list[str]], sheet_name: str) -> bytes:
    def _column_letters(index: int) -> str:
        value = index + 1
        letters: list[str] = []
        while value > 0:
            value, remainder = divmod(value - 1, 26)
            letters.append(chr(ord("A") + remainder))
        return "".join(reversed(letters))

    def _inline_cell(*, value: str, ref: str) -> str:
        escaped = (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'

    sheet_rows: list[str] = []
    all_rows = [headers] + rows
    for row_index, row in enumerate(all_rows, start=1):
        cells = "".join(
            _inline_cell(value=value, ref=f"{_column_letters(column_index)}{row_index}")
            for column_index, value in enumerate(row)
        )
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')

    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        f"{''.join(sheet_rows)}"
        "</sheetData>"
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        f'<sheet name="{sheet_name}" sheetId="1" r:id="rId1"/>'
        "</sheets>"
        "</workbook>"
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", root_rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
    return output.getvalue()


def _load_csv_rows(file_bytes: bytes) -> dict[str, Any]:
    text = _decode_csv_bytes(file_bytes)
    reader = csv.reader(io.StringIO(text))
    rows = [list(row) for row in reader]
    return _rows_to_items(rows)


def _decode_csv_bytes(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV文件编码无法识别")


def _load_xlsx_rows(file_bytes: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_name = _resolve_first_sheet_path(archive)
        sheet_xml = ET.fromstring(archive.read(sheet_name))

    rows: list[list[str]] = []
    for row_node in sheet_xml.findall(".//main:sheetData/main:row", XML_NS):
        row_values: dict[int, str] = {}
        max_index = -1
        for cell in row_node.findall("main:c", XML_NS):
            ref = str(cell.get("r") or "")
            col_index = _column_index_from_ref(ref)
            value = _read_cell_value(cell=cell, shared_strings=shared_strings)
            row_values[col_index] = value
            max_index = max(max_index, col_index)
        if max_index < 0:
            continue
        row = [row_values.get(index, "") for index in range(max_index + 1)]
        if any(str(item).strip() for item in row):
            rows.append(row)

    return _rows_to_items(rows)


def _resolve_first_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook_xml = ET.fromstring(archive.read("xl/workbook.xml"))
    first_sheet = workbook_xml.find(".//main:sheets/main:sheet", XML_NS)
    if first_sheet is None:
        raise ValueError("Excel文件缺少工作表")

    rel_id = first_sheet.get(f"{{{XML_NS['rel']}}}id")
    rels_xml = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relation in rels_xml.findall("pkg_rel:Relationship", XML_NS):
        if relation.get("Id") == rel_id:
            target = str(relation.get("Target") or "").lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError("Excel文件工作表关系缺失")


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for node in root.findall("main:si", XML_NS):
        text_parts = [text.text or "" for text in node.findall(".//main:t", XML_NS)]
        values.append("".join(text_parts))
    return values


def _read_cell_value(*, cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = str(cell.get("t") or "")
    if cell_type == "inlineStr":
        parts = [node.text or "" for node in cell.findall(".//main:t", XML_NS)]
        return "".join(parts)

    value_node = cell.find("main:v", XML_NS)
    if value_node is None or value_node.text is None:
        return ""

    raw_value = value_node.text
    if cell_type == "s":
        index = int(raw_value or 0)
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""
    return str(raw_value)


def _rows_to_items(rows: list[list[str]]) -> dict[str, Any]:
    if not rows:
        return {"columns": [], "items": []}

    columns = [str(item or "").strip() for item in rows[0]]
    items: list[dict[str, str]] = []
    for row in rows[1:]:
        padded = list(row) + [""] * max(0, len(columns) - len(row))
        items.append({columns[index]: str(padded[index] or "") for index in range(len(columns))})
    return {"columns": columns, "items": items}


def _column_index_from_ref(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha()).upper()
    if not letters:
        return 0
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return max(0, index - 1)
