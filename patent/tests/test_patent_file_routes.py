from __future__ import annotations

import sys
import pytest
from pathlib import Path

import server.patent.pdf_service as pdf_service_module
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


def test_dispatch_pdf_route_formats_two_selected_pdfs_for_compare_questions(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    captured: dict[str, str] = {}
    texts = {
        str(pdf_path_a): "Abstract A.\n\nResults A show 15% improvement.\n\nConclusion A supports route A.",
        str(pdf_path_b): "Abstract B.\n\nResults B show 5% decline.\n\nConclusion B rejects route A.",
    }

    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: texts[path],
        answer_question_fn=lambda **kwargs: captured.update(
            {
                "pdf_text": str(kwargs["pdf_text"]),
                "file_name": str(kwargs["file_name"]),
            }
        )
        or "对比结果：文献 1 与文献 2 存在明显差异。",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "==== 文献 1: paper-a.pdf ====" in captured["pdf_text"]
    assert "==== 文献 2: paper-b.pdf ====" in captured["pdf_text"]
    assert "paper-a.pdf" in captured["file_name"]
    assert "paper-b.pdf" in captured["file_name"]


def test_dispatch_pdf_route_returns_explicit_compare_failure_when_only_one_pdf_is_readable(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A.\n\nResults A show 15% improvement.\n\nConclusion A supports route A."
            if path == str(pdf_path_a)
            else ""
        ),
        answer_question_fn=lambda **kwargs: "",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "paper-b.pdf" in result["answer_text"]
    assert "文档要点如下" not in result["answer_text"]


def test_dispatch_pdf_route_returns_explicit_compare_failure_when_model_returns_no_answer(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A.\n\nResults A show 15% improvement.\n\nConclusion A supports route A."
            if path == str(pdf_path_a)
            else "Abstract B.\n\nResults B show 5% decline.\n\nConclusion B rejects route A."
        ),
        answer_question_fn=lambda **kwargs: "",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "模型未返回可用的比较结果" in result["answer_text"]
    assert "文档要点如下" not in result["answer_text"]


def test_dispatch_pdf_route_preserves_tail_evidence_from_each_large_pdf_in_compare_mode(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    front_matter = "作者信息与版权页。 " * 1200
    captured: dict[str, str] = {}
    texts = {
        str(pdf_path_a): f"{front_matter}\n\nAbstract A.\n\nResults A show 15% improvement.\n\nConclusion A supports route A.",
        str(pdf_path_b): f"{front_matter}\n\nAbstract B.\n\nResults B show 5% decline.\n\nConclusion B rejects route A.",
    }

    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: texts[path],
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])}) or "对比结果",
    )

    dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert "Conclusion A supports route A." in captured["pdf_text"]
    assert "Conclusion B rejects route A." in captured["pdf_text"]


def test_dispatch_pdf_route_preserves_per_document_abstract_for_four_doc_compare(tmp_path):
    pdf_paths = []
    execution_files = []
    selected_ids = []
    for index in range(4):
        pdf_path = tmp_path / f"paper-{index + 1}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
        pdf_paths.append(pdf_path)
        execution_files.append(
            {
                "file_id": 100 + index,
                "file_type": "pdf",
                "file_name": f"paper-{index + 1}.pdf",
                "local_path": str(pdf_path),
            }
        )
        selected_ids.append(100 + index)
    contract = build_patent_file_contract(
        question="对比一下这四篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=selected_ids,
        primary_file_id=selected_ids[0],
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "selected_file_ids": selected_ids},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    front_matter = "作者信息与版权页。 " * 200
    captured: dict[str, str] = {}
    texts = {
        str(path): (
            f"{front_matter}\n\n"
            f"Abstract {index} short.\n\n"
            f"Method {index} uses condition {index}.\n\n"
            f"Results {index} observed.\n\n"
            f"Conclusion {index} final."
        )
        for index, path in enumerate(pdf_paths, start=1)
    }
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: texts[path],
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])}) or "对比结果",
        max_pdf_chars=1000,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    for index in range(1, 5):
        assert f"Abstract {index} short." in captured["pdf_text"]
        assert f"Results {index} observed." in captured["pdf_text"] or f"Conclusion {index} final." in captured["pdf_text"]


def test_dispatch_pdf_route_drops_reference_tail_from_compare_context(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    front_matter = "作者信息与版权页。 " * 120
    references = "参考文献\n[1] filler citation block. " * 80
    captured: dict[str, str] = {}
    texts = {
        str(pdf_path_a): (
            f"{front_matter}\n\nAbstract A short.\n\nMethod A.\n\n"
            f"Results A observed.\n\nConclusion A final.\n\n{references}"
        ),
        str(pdf_path_b): (
            f"{front_matter}\n\nAbstract B short.\n\nMethod B.\n\n"
            f"Results B observed.\n\nConclusion B final.\n\n{references}"
        ),
    }
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: texts[path],
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])}) or "对比结果",
        max_pdf_chars=560,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "参考文献" not in captured["pdf_text"]
    assert "Results A observed." in captured["pdf_text"] or "Conclusion A final." in captured["pdf_text"]
    assert "Results B observed." in captured["pdf_text"] or "Conclusion B final." in captured["pdf_text"]


def test_dispatch_pdf_route_rejects_invalid_compare_excerpt_after_truncation(tmp_path, monkeypatch):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A short.\n\nResults A observed.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B short.\n\nResults B observed.\n\nConclusion B final."
        ),
        answer_question_fn=lambda **kwargs: "不应该进入生成阶段",
    )

    monkeypatch.setattr(
        pdf_service_module,
        "smart_truncate_pdf_content",
        lambda *args, **kwargs: (
            "==== 文献 1: paper-a.pdf ====\n作者信息与版权页。\n\n"
            "==== 文献 2: paper-b.pdf ====\n作者信息与版权页。"
        ),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "最小比较上下文" in result["answer_text"]


def test_dispatch_pdf_route_allows_appendix_word_inside_body_content(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    captured: dict[str, str] = {}
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A mentions appendix-based evaluation setup.\n\n"
            "Results A observed.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B mentions appendix-based evaluation setup.\n\n"
            "Results B observed.\n\nConclusion B final."
        ),
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])}) or "对比结果",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "appendix-based evaluation setup" in captured["pdf_text"]


def test_dispatch_pdf_route_rejects_compare_excerpt_when_only_other_document_keeps_shared_targets(tmp_path, monkeypatch):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract shared compare anchor.\n\nResults shared compare anchor.\n\nConclusion shared compare anchor."
            if path == str(pdf_path_a)
            else "Abstract shared compare anchor.\n\nResults shared compare anchor.\n\nConclusion shared compare anchor."
        ),
        answer_question_fn=lambda **kwargs: "不应该进入生成阶段",
    )

    monkeypatch.setattr(
        pdf_service_module,
        "smart_truncate_pdf_content",
        lambda *args, **kwargs: (
            "==== 文献 1: paper-a.pdf ====\n"
            "Abstract shared compare anchor.\n\nResults shared compare anchor.\n\nConclusion shared compare anchor.\n\n"
            "==== 文献 2: paper-b.pdf ====\n作者信息与版权页。"
        ),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_dispatch_pdf_route_accepts_long_compare_paragraphs_when_required_slices_are_preserved(tmp_path):
    pdf_paths = []
    execution_files = []
    selected_ids = []
    for index in range(4):
        pdf_path = tmp_path / f"paper-{index + 1}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
        pdf_paths.append(pdf_path)
        execution_files.append(
            {
                "file_id": 200 + index,
                "file_type": "pdf",
                "file_name": f"paper-{index + 1}.pdf",
                "local_path": str(pdf_path),
            }
        )
        selected_ids.append(200 + index)
    contract = build_patent_file_contract(
        question="对比一下这四篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=selected_ids,
        primary_file_id=selected_ids[0],
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "selected_file_ids": selected_ids},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    long_abstract = "Abstract section with detailed compare evidence. " * 12
    long_conclusion = "Conclusion section with detailed tail evidence. " * 12
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            f"{long_abstract}\n\nMethod {Path(path).stem}.\n\nResults {Path(path).stem} observed.\n\n{long_conclusion}"
        ),
        answer_question_fn=lambda **kwargs: "对比结果",
        max_pdf_chars=1000,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"


def test_dispatch_pdf_route_preserves_compare_slices_for_flattened_single_newline_pdf_text(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    flat_front_matter = "作者信息与版权页。 " * 220
    captured: dict[str, str] = {}
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            f"{flat_front_matter}\nAbstract A short.\nMethod A.\nResults A observed.\nConclusion A final."
            if path == str(pdf_path_a)
            else f"{flat_front_matter}\nAbstract B short.\nMethod B.\nResults B observed.\nConclusion B final."
        ),
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])}) or "对比结果",
        max_pdf_chars=560,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "Abstract A short." in captured["pdf_text"]
    assert "Results A observed." in captured["pdf_text"] or "Conclusion A final." in captured["pdf_text"]
    assert "Abstract B short." in captured["pdf_text"]
    assert "Results B observed." in captured["pdf_text"] or "Conclusion B final." in captured["pdf_text"]


def test_dispatch_pdf_route_allows_late_appendix_based_body_paragraph(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A short.\n\nMethod A.\n\nAppendix-based evaluation setup improved recall.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B short.\n\nMethod B.\n\nAppendix-based evaluation setup improved precision.\n\nConclusion B final."
        ),
        answer_question_fn=lambda **kwargs: "对比结果",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"


def test_dispatch_pdf_route_matches_compare_sections_by_exact_file_label(tmp_path, monkeypatch):
    pdf_path_short = tmp_path / "foo.pdf"
    pdf_path_long = tmp_path / "my-foo.pdf"
    pdf_path_short.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_long.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[21, 22],
        primary_file_id=21,
        execution_files=[
            {"file_id": 21, "file_type": "pdf", "file_name": "foo.pdf", "local_path": str(pdf_path_short)},
            {"file_id": 22, "file_type": "pdf", "file_name": "my-foo.pdf", "local_path": str(pdf_path_long)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [21, 22]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract foo exact.\n\nResults foo exact.\n\nConclusion foo exact."
            if path == str(pdf_path_short)
            else "Abstract myfoo exact.\n\nResults myfoo exact.\n\nConclusion myfoo exact."
        ),
        answer_question_fn=lambda **kwargs: "对比结果",
    )

    monkeypatch.setattr(
        pdf_service_module,
        "smart_truncate_pdf_content",
        lambda *args, **kwargs: (
            "==== 文献 1: my-foo.pdf ====\n"
            "Abstract myfoo exact.\n\nResults myfoo exact.\n\nConclusion myfoo exact.\n\n"
            "==== 文献 2: foo.pdf ====\n"
            "Abstract foo exact.\n\nResults foo exact.\n\nConclusion foo exact."
        ),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"


def test_dispatch_pdf_route_preserves_section_body_when_headings_are_standalone_lines(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    front_matter = "作者信息与版权页。 " * 200
    captured: dict[str, str] = {}
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            (
                f"{front_matter}\n\nAbstract\n\nAbstract body A keeps the real summary evidence.\n\n"
                "Methods\n\nMethod body A.\n\nResults\n\nResults body A keeps the real compare evidence.\n\n"
                "Conclusion\n\nConclusion body A keeps the real tail evidence."
            )
            if path == str(pdf_path_a)
            else (
                f"{front_matter}\n\nAbstract\n\nAbstract body B keeps the real summary evidence.\n\n"
                "Methods\n\nMethod body B.\n\nResults\n\nResults body B keeps the real compare evidence.\n\n"
                "Conclusion\n\nConclusion body B keeps the real tail evidence."
            )
        ),
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])}) or "对比结果",
        max_pdf_chars=560,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "Abstract body A keeps the real" in captured["pdf_text"]
    assert "Abstract body B keeps the real" in captured["pdf_text"]
    assert "Results body A keeps the real" in captured["pdf_text"] or "Conclusion body A keeps the real" in captured["pdf_text"]
    assert "Results body B keeps the real" in captured["pdf_text"] or "Conclusion body B keeps the real" in captured["pdf_text"]


def test_dispatch_pdf_route_fails_explicitly_when_compare_budget_is_too_small(tmp_path):
    pdf_paths = []
    execution_files = []
    selected_ids = []
    for index in range(5):
        pdf_path = tmp_path / f"paper-{index + 1}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
        pdf_paths.append(pdf_path)
        execution_files.append(
            {
                "file_id": 100 + index,
                "file_type": "pdf",
                "file_name": f"paper-{index + 1}.pdf",
                "local_path": str(pdf_path),
            }
        )
        selected_ids.append(100 + index)
    contract = build_patent_file_contract(
        question="对比一下这五篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=selected_ids,
        primary_file_id=selected_ids[0],
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "selected_file_ids": selected_ids},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            ("前置背景信息。 " * 120)
            + "\n\nAbstract.\n\nResults show measurable variation.\n\nConclusion contains unique tail evidence for "
            + Path(path).name
        ),
        answer_question_fn=lambda **kwargs: "",
        max_pdf_chars=120,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "compare 截断预算不足" in result["answer_text"]


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
