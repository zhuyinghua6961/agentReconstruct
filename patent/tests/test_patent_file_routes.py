from __future__ import annotations

import sys
import pytest
from pathlib import Path

from server.patent.file_contract import build_patent_file_contract
from server.patent.file_routes import dispatch_patent_file_route, plan_patent_file_route
from server.patent.pdf_service import PatentPdfService
from server.patent.tabular_service import PatentTabularService


PDF_FILE = {
    "file_id": 11,
    "file_type": "pdf",
    "file_name": "battery-paper.pdf",
}

PDF_FILE_2 = {
    "file_id": 12,
    "file_type": "pdf",
    "file_name": "battery-paper-2.pdf",
}

TABLE_FILE = {
    "file_id": 33,
    "file_type": "xlsx",
    "file_name": "cells.xlsx",
}


def _write_csv(path: Path) -> None:
    path.write_text(
        "material,capacity_mAh,note\n"
        "LMFP,120,stable\n"
        "LFP,115,safe\n"
        "NCM,140,higher energy\n",
        encoding="utf-8",
    )


def test_build_patent_file_contract_consumes_gateway_canonical_fields_without_recanonicalizing():
    contract = build_patent_file_contract(
        route="hybrid_qa",
        source_scope="pdf+table+kb",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[PDF_FILE, TABLE_FILE],
        file_selection={
            "strategy": "explicit_selection",
            "selected_file_ids": [11, 33],
            "source_scope": "pdf+table+kb",
        },
        kb_enabled=True,
        allow_kb_verification=True,
    )

    assert contract.route == "hybrid_qa"
    assert contract.source_scope == "pdf+table+kb"
    assert contract.selected_file_ids == [11, 33]
    assert contract.primary_file_id == 11
    assert [item.family for item in contract.execution_files] == ["pdf", "table"]
    assert contract.file_selection["source_scope"] == "pdf+table+kb"
    assert contract.includes_kb is True


def test_build_patent_file_contract_rejects_source_scope_that_disagrees_with_selected_files():
    with pytest.raises(ValueError, match="source_scope"):
        build_patent_file_contract(
            route="hybrid_qa",
            source_scope="pdf+table",
            selected_file_ids=[11],
            primary_file_id=11,
            execution_files=[PDF_FILE],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=False,
            allow_kb_verification=False,
        )


def test_build_patent_file_contract_rejects_kb_scope_without_allow_kb_verification():
    with pytest.raises(ValueError, match="allow_kb_verification"):
        build_patent_file_contract(
            route="hybrid_qa",
            source_scope="pdf+kb",
            selected_file_ids=[11],
            primary_file_id=11,
            execution_files=[PDF_FILE],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=True,
            allow_kb_verification=False,
        )


def test_build_patent_file_contract_rejects_selected_files_outside_source_scope():
    with pytest.raises(ValueError, match="selected files"):
        build_patent_file_contract(
            route="pdf_qa",
            source_scope="pdf",
            selected_file_ids=[11, 33],
            primary_file_id=11,
            execution_files=[PDF_FILE, TABLE_FILE],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=False,
            allow_kb_verification=False,
        )


def test_build_patent_file_contract_accepts_xlsm_and_legacy_xls_payload():
    contract = build_patent_file_contract(
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "xlsm", "file_name": "cells.xlsm"}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    assert contract.execution_files[0].file_type == "xlsm"
    legacy = build_patent_file_contract(
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "excel", "file_name": "legacy.xls", "local_path": "/tmp/legacy.xls"}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    assert legacy.execution_files[0].file_type == "excel"

    with pytest.raises(ValueError, match="unsupported spreadsheet extension"):
        build_patent_file_contract(
            route="tabular_qa",
            source_scope="table",
            selected_file_ids=[33],
            primary_file_id=33,
            execution_files=[{"file_id": 33, "file_type": "table", "file_name": "cells.ods", "local_path": "/tmp/upload_blob"}],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=False,
            allow_kb_verification=False,
        )


@pytest.mark.parametrize(
    ("selected_file_ids", "primary_file_id", "execution_file_id"),
    [
        ([True], 33, 33),
        ([33], True, 33),
        ([33], 33, True),
        ([33], 33, 33.2),
    ],
)
def test_build_patent_file_contract_rejects_non_integer_file_identifiers(
    selected_file_ids,
    primary_file_id,
    execution_file_id,
):
    with pytest.raises(ValueError, match="file_id|selected_file_ids|primary_file_id"):
        build_patent_file_contract(
            route="tabular_qa",
            source_scope="table",
            selected_file_ids=selected_file_ids,
            primary_file_id=primary_file_id,
            execution_files=[{"file_id": execution_file_id, "file_type": "csv", "file_name": "cells.csv"}],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=False,
            allow_kb_verification=False,
        )


def test_dispatch_pdf_route_uses_patent_pdf_service():
    contract = build_patent_file_contract(
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[PDF_FILE],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert result["handler"] == "pdf"
    assert result["route"] == "pdf_qa"
    assert result["source_scope"] == "pdf"
    assert result["query_mode"] == "patent_pdf_qa"
    assert result["answer_text"]
    assert result["used_files"] == [PDF_FILE]
    assert result["steps"][0]["title"] == "进入 PDF 分支"
    assert result["timings"]["patent_pdf_route_ms"] == 1
    assert result["kb_enabled"] is False


def test_dispatch_only_uses_gateway_selected_files_when_execution_pool_is_larger():
    contract = build_patent_file_contract(
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[PDF_FILE, PDF_FILE_2],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11]},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert result["used_files"] == [PDF_FILE]
    assert result["selected_file_ids"] == [11]


def test_build_patent_file_contract_ignores_unselected_unsupported_execution_files():
    contract = build_patent_file_contract(
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[PDF_FILE, {"file_id": 99, "file_type": "docx", "file_name": "ignored.docx"}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11]},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    assert [item.file_id for item in contract.execution_files] == [11]


def test_dispatch_pdf_route_honors_primary_file_id_and_redacts_local_path():
    contract = build_patent_file_contract(
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=12,
        execution_files=[
            {**PDF_FILE, "local_path": "/tmp/first.pdf"},
            {**PDF_FILE_2, "local_path": "/tmp/second.pdf"},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert [item["file_id"] for item in result["used_files"]] == [12, 11]
    assert "local_path" not in result["used_files"][0]
    assert "local_path" not in result["used_files"][1]


def test_tabular_service_reads_legacy_xls_via_optional_pandas_bridge(monkeypatch):
    class _FakeFrame:
        def __init__(self, rows):
            self._rows = rows

        def fillna(self, _value):
            return self

        def itertuples(self, index=False, name=None):
            return iter(self._rows)

    class _FakePandas:
        @staticmethod
        def read_excel(_path, sheet_name=None, header=None):
            assert sheet_name is None
            assert header is None
            return {"Legacy": _FakeFrame([("material", "capacity_mAh"), ("LMFP", 120), ("LFP", 115)])}

    monkeypatch.setitem(sys.modules, "pandas", _FakePandas)

    sheets = PatentTabularService._read_legacy_excel_rows("/tmp/legacy.xls", max_sheets=2)

    assert sheets == [("Legacy", [["material", "capacity_mAh"], ["LMFP", "120"], ["LFP", "115"]])]


def test_dispatch_pdf_route_uses_real_pdf_text_summary_when_local_path_is_available(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a battery recycling catalyst. Experiments show 15% efficiency improvement and lower cost.",
        answer_question_fn=lambda **kwargs: "真实总结：本文提出电池回收催化方案，实验显示效率提升 15%，同时降低成本。",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["handler"] == "pdf"
    assert result["metadata"]["answer_mode"] == "pdf_text_summary"
    assert "真实总结" in result["answer_text"]
    assert "Patent PDF route answered" not in result["answer_text"]


def test_dispatch_tabular_route_uses_patent_tabular_service():
    contract = build_patent_file_contract(
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[TABLE_FILE],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert result["handler"] == "tabular"
    assert result["route"] == "tabular_qa"
    assert result["source_scope"] == "table"
    assert result["query_mode"] == "patent_tabular_qa"
    assert result["answer_text"]
    assert result["used_files"] == [TABLE_FILE]
    assert result["steps"][0]["title"] == "进入文件分支"
    assert result["timings"]["patent_tabular_route_ms"] == 1
    assert result["kb_enabled"] is False


def test_dispatch_tabular_route_uses_real_table_content_when_local_path_is_available(tmp_path):
    csv_path = tmp_path / "cells.csv"
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请总结这个表格的重点",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 方案容量 120mAh，LFP 更安全，NCM 能量更高。",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=service,
    )

    assert result["handler"] == "tabular"
    assert result["metadata"]["answer_mode"] == "table_text_summary"
    assert "真实表格总结" in result["answer_text"]
    assert "Patent tabular route answered" not in result["answer_text"]


def test_dispatch_tabular_route_uses_file_name_suffix_when_local_path_has_no_extension(tmp_path):
    opaque_path = tmp_path / "upload_blob"
    _write_csv(opaque_path)
    contract = build_patent_file_contract(
        question="请总结这个表格的重点",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "excel", "file_name": "cells.csv", "local_path": str(opaque_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "table_text_summary"
    assert "table_text_unavailable" not in str(result["metadata"])


def test_dispatch_hybrid_route_uses_real_pdf_and_table_content_when_local_paths_are_available(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格总结结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
        answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
    )
    tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=pdf_service,
        tabular_service=tabular_service,
    )

    assert result["handler"] == "hybrid"
    assert result["metadata"]["answer_mode"] == "hybrid_file_synthesis"
    assert "真实 PDF 总结" in result["answer_text"]
    assert "真实表格总结" in result["answer_text"]
    assert "Patent hybrid route combined selected PDF and table files" not in result["answer_text"]


@pytest.mark.parametrize(
    ("source_scope", "handler", "include_kb", "families"),
    [
        ("pdf+kb", "pdf", True, ["pdf"]),
        ("table+kb", "tabular", True, ["table"]),
        ("pdf+table", "hybrid", False, ["pdf", "table"]),
        ("pdf+table+kb", "hybrid", True, ["pdf", "table"]),
    ],
)
def test_hybrid_route_planning_covers_all_supported_source_scopes(source_scope, handler, include_kb, families):
    selected_file_ids = [11] if families == ["pdf"] else [33] if families == ["table"] else [11, 33]
    primary_file_id = selected_file_ids[0]
    execution_files = [PDF_FILE] if families == ["pdf"] else [TABLE_FILE] if families == ["table"] else [PDF_FILE, TABLE_FILE]
    contract = build_patent_file_contract(
        route="hybrid_qa",
        source_scope=source_scope,
        selected_file_ids=selected_file_ids,
        primary_file_id=primary_file_id,
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "source_scope": source_scope},
        kb_enabled=include_kb,
        allow_kb_verification=include_kb,
    )

    plan = plan_patent_file_route(contract)
    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert plan.handler == handler
    assert list(plan.file_families) == families
    assert plan.include_kb is include_kb
    assert result["handler"] == handler
    assert result["source_scope"] == source_scope
    assert result["query_mode"] == "patent_hybrid_qa"
    assert result["answer_text"]
    assert result["kb_enabled"] is include_kb
