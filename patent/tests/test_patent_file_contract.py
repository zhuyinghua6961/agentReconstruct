from __future__ import annotations

import pytest

from server.patent.file_contract import build_patent_file_contract
from server.patent.pdf_service import PatentPdfService


def test_patent_file_contract_rejects_local_only_table(tmp_path, monkeypatch):
    monkeypatch.setenv("PATENT_ORIGINAL_MINIO_ONLY", "true")
    local = tmp_path / "a.xlsx"
    local.write_bytes(b"placeholder")

    with pytest.raises(ValueError, match="storage_ref"):
        build_patent_file_contract(
            route="tabular_qa",
            source_scope="table",
            selected_file_ids=[1],
            primary_file_id=1,
            execution_files=[
                {
                    "file_id": 1,
                    "file_type": "excel",
                    "file_name": "a.xlsx",
                    "local_path": str(local),
                    "storage_ref": "",
                }
            ],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=False,
            allow_kb_verification=False,
        )


def test_patent_file_contract_derives_table_suffix_from_file_name_without_local_path():
    contract = build_patent_file_contract(
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[1],
        primary_file_id=1,
        execution_files=[
            {
                "file_id": 1,
                "file_type": "excel",
                "file_name": "a.csv",
                "local_path": "",
                "storage_ref": "minio://agentcode/uploads/a.csv",
            }
        ],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    assert contract.execution_files[0].payload["storage_ref"] == "minio://agentcode/uploads/a.csv"
    assert contract.execution_files[0].payload["local_path"] == ""


def test_patent_pdf_service_reads_pdf_from_minio_ref_when_local_path_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_ORIGINAL_MINIO_ONLY", "true")
    scratch_pdf = tmp_path / "scratch.pdf"
    scratch_pdf.write_bytes(b"%PDF-minio\n")

    class _FakeObjectReader:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def materialize_temp(self, storage_ref: str, *, suffix: str):
            self.calls.append((storage_ref, suffix))
            return scratch_pdf

    reader = _FakeObjectReader()
    captured: dict[str, object] = {}

    def _extract(pdf_path: str, **kwargs):
        captured["pdf_path"] = pdf_path
        captured["kwargs"] = kwargs
        return "MinIO PDF text"

    contract = build_patent_file_contract(
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[1],
        primary_file_id=1,
        execution_files=[
            {
                "file_id": 1,
                "file_type": "pdf",
                "file_name": "a.pdf",
                "local_path": "",
                "storage_ref": "minio://agentcode/uploads/a.pdf",
            }
        ],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=_extract,
        answer_question_fn=lambda **kwargs: "unused",
        object_reader=reader,
    )

    documents = service._load_pdf_documents(execution_files=contract.selected_execution_files)

    assert reader.calls == [("minio://agentcode/uploads/a.pdf", ".pdf")]
    assert captured["pdf_path"] == str(scratch_pdf)
    assert documents == [{"label": "a.pdf", "text": "MinIO PDF text"}]
