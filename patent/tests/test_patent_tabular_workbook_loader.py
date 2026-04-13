from __future__ import annotations

import sys
import zipfile

import pytest

from server.patent.tabular.workbook_loader import load_workbook_cached


def test_load_workbook_cached_reads_csv_into_sheet_rows(tmp_path):
    csv_path = tmp_path / "metrics.csv"
    csv_path.write_text(
        "column_a,capacity_mAh\n"
        "header value,120\n"
        "second row,115\n",
        encoding="utf-8",
    )

    workbook = load_workbook_cached(
        path=str(csv_path),
        file_name="metrics.csv",
        file_type="csv",
    )

    assert workbook["file_name"] == "metrics.csv"
    assert workbook["sheet_count"] == 1
    assert workbook["sheets"][0]["sheet_name"] == "Sheet1"
    assert workbook["sheets"][0]["headers"] == ["column_a", "capacity_mAh"]
    assert workbook["sheets"][0]["rows"][0]["column_a"] == "header value"
    assert workbook["sheets"][0]["rows"][0]["capacity_mAh"] == "120"


def test_load_workbook_cached_reads_xlsx_inline_strings(tmp_path):
    xlsx_path = tmp_path / "inline.xlsx"
    with zipfile.ZipFile(xlsx_path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
              <Default Extension="xml" ContentType="application/xml"/>
              <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
              <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
            </Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets>
                <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
              </sheets>
            </workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1">
                  <c r="A1" t="inlineStr"><is><t>Material</t></is></c>
                  <c r="B1" t="inlineStr"><is><t>Capacity</t></is></c>
                </row>
                <row r="2">
                  <c r="A2" t="inlineStr"><is><t>LMFP</t></is></c>
                  <c r="B2"><v>120</v></c>
                </row>
              </sheetData>
            </worksheet>""",
        )

    workbook = load_workbook_cached(
        path=str(xlsx_path),
        file_name="inline.xlsx",
        file_type="xlsx",
    )

    assert workbook["sheets"][0]["headers"] == ["Material", "Capacity"]
    assert workbook["sheets"][0]["rows"][0]["Material"] == "LMFP"
    assert workbook["sheets"][0]["rows"][0]["Capacity"] == "120"


def test_load_workbook_cached_reads_xlsx_inline_rich_text_strings(tmp_path):
    xlsx_path = tmp_path / "inline-rich.xlsx"
    with zipfile.ZipFile(xlsx_path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
              <Default Extension="xml" ContentType="application/xml"/>
              <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
              <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
            </Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets>
                <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
              </sheets>
            </workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1">
                  <c r="A1" t="inlineStr"><is><r><t>Mat</t></r><r><t>erial</t></r></is></c>
                  <c r="B1" t="inlineStr"><is><r><t>Cap</t></r><r><t>acity</t></r></is></c>
                </row>
                <row r="2">
                  <c r="A2" t="inlineStr"><is><r><t>LM</t></r><r><t>FP</t></r></is></c>
                  <c r="B2"><v>120</v></c>
                </row>
              </sheetData>
            </worksheet>""",
        )

    workbook = load_workbook_cached(
        path=str(xlsx_path),
        file_name="inline-rich.xlsx",
        file_type="xlsx",
    )

    assert workbook["sheets"][0]["headers"] == ["Material", "Capacity"]
    assert workbook["sheets"][0]["rows"][0]["Material"] == "LMFP"
    assert workbook["sheets"][0]["rows"][0]["Capacity"] == "120"


def test_load_workbook_legacy_xls_raises_when_pandas_bridge_is_unavailable(monkeypatch, tmp_path):
    legacy_path = tmp_path / "legacy.xls"
    legacy_path.write_bytes(b"placeholder")
    monkeypatch.setitem(sys.modules, "pandas", None)

    with pytest.raises(RuntimeError, match="pandas not available"):
        load_workbook_cached(
            path=str(legacy_path),
            file_name="legacy.xls",
            file_type="xls",
        )
