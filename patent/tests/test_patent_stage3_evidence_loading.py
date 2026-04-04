from __future__ import annotations

import json
from pathlib import Path

from server.patent.archive_loader import PatentArchiveLoader
from server.patent.pdf_service import PatentPdfService
from server.patent.retrieval_models import PatentCatalogRecord, PatentTableSupplement
from server.patent.runtime import PatentRuntime
from server.patent.stages.evidence_loading import run_stage3_load_patent_evidence


def test_stage3_evidence_loading_groups_stage2_documents_caps_retrieval_chunks_and_attaches_table_markdown():
    pdf_calls: list[str] = []
    retrieval_results = {
        "documents": [
            "摘要命中：LMFP/LFP/三元复配改善充电安全与低SOC放电功率，且支持快充窗口。",
            "摘要命中：LMFP/LFP/三元复配改善充电安全与低SOC放电功率，且支持快充窗口。重复版本。",
            "权利要求1：正极活性材料包括LMFP、LFP与三元材料，按质量比复配。",
            "说明书段落p-001：在高SOC充电条件下抑制析锂。",
            "说明书段落p-002：在低SOC区域维持更高放电功率。",
            "另一篇专利摘要：关注负极预锂化窗口。",
        ],
        "metadatas": [
            {
                "patent_id": "CN115132975B",
                "stage2_source": "abstract",
                "section_type": "abstract",
                "section_label": "Abstract",
                "distance": 0.12,
            },
            {
                "patent_id": "CN115132975B",
                "stage2_source": "abstract",
                "section_type": "abstract",
                "section_label": "Abstract Duplicate",
                "distance": 0.13,
            },
            {
                "patent_id": "CN115132975B",
                "stage2_source": "chunk",
                "section_type": "claim",
                "section_label": "Claim 1",
                "claim_number": 1,
                "distance": 0.21,
            },
            {
                "patent_id": "CN115132975B",
                "stage2_source": "chunk",
                "section_type": "description",
                "section_label": "Paragraph p-001",
                "paragraph_id": "p-001",
                "distance": 0.29,
            },
            {
                "patent_id": "CN115132975B",
                "stage2_source": "chunk",
                "section_type": "description",
                "section_label": "Paragraph p-002",
                "paragraph_id": "p-002",
                "distance": 0.35,
            },
            {
                "patent_id": "CN999999999A",
                "stage2_source": "abstract",
                "section_type": "abstract",
                "section_label": "Abstract",
                "distance": 0.41,
            },
        ],
        "distances": [0.12, 0.13, 0.21, 0.29, 0.35, 0.41],
        "reference_objects": [
            {
                "canonical_patent_id": "CN115132975B",
                "publication_number": "CN115132975B",
                "title": "一种锂离子电池及动力车辆",
                "provider": "patent_archive",
                "original_available": True,
            },
            {
                "canonical_patent_id": "CN999999999A",
                "publication_number": "CN999999999A",
                "title": "另一篇相关专利",
                "provider": "patent_archive",
                "original_available": True,
            },
        ],
    }

    bundle = run_stage3_load_patent_evidence(
        retrieval_results=retrieval_results,
        source_ids=["cn115132975b", "CN999999999A"],
        catalog_loader=lambda patent_id: PatentCatalogRecord(
            canonical_patent_id=patent_id,
            publication_number=patent_id,
            application_number=None,
            title="一种锂离子电池及动力车辆" if patent_id == "CN115132975B" else "另一篇相关专利",
            abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"
            if patent_id == "CN115132975B"
            else "关注负极预锂化窗口。",
        ),
        table_loader=lambda patent_id: [
            PatentTableSupplement(
                table_title="表1 各实施例性能对比",
                columns=["实验序号", "1C放电容量保持率"],
                rows=[{"实验序号": "实施例1", "1C放电容量保持率": "91.2%"}],
            )
        ]
        if patent_id == "CN115132975B"
        else [],
        pdf_loader=lambda patent_id: pdf_calls.append(patent_id),
        force_pdf=False,
        max_snippets_per_patent=3,
    )

    assert bundle["source_ids"] == ["CN115132975B", "CN999999999A"]
    assert pdf_calls == []

    first = bundle["evidences"][0]
    first_items = list(first["matched_evidence"])
    retrieval_items = [item for item in first_items if item["section_type"] != "table"]
    table_items = [item for item in first_items if item["section_type"] == "table"]

    assert first["canonical_patent_id"] == "CN115132975B"
    assert len(retrieval_items) == 3
    assert [item["section_label"] for item in retrieval_items] == ["Abstract", "Claim 1", "Paragraph p-001"]
    assert len(table_items) == 1
    assert table_items[0]["text"].startswith("### 表1 各实施例性能对比")
    assert "| 实验序号 | 1C放电容量保持率 |" in table_items[0]["text"]
    assert first["table_supplements"][0]["table_title"] == "表1 各实施例性能对比"
    assert first["metadata"]["publication_number"] == "CN115132975B"
    assert "pdf_document" not in first["metadata"]

    second = bundle["evidences"][1]
    assert second["canonical_patent_id"] == "CN999999999A"
    assert len(second["matched_evidence"]) == 1
    assert second["matched_evidence"][0]["section_label"] == "Abstract"


def test_stage3_evidence_loading_force_pdf_adds_pdf_text_evidence_and_keeps_tables():
    bundle = run_stage3_load_patent_evidence(
        retrieval_results={"documents": [], "metadatas": [], "reference_objects": []},
        source_ids=["CN115132975B"],
        table_loader=lambda patent_id: [
            PatentTableSupplement(
                table_title="表1 各实施例性能对比",
                columns=["实验序号"],
                rows=[{"实验序号": "实施例1"}],
            )
        ],
        pdf_loader=lambda patent_id: {
            "path": f"/tmp/{patent_id}.pdf",
            "filename": f"{patent_id}.pdf",
            "size_bytes": 8,
        },
        pdf_text_extractor=lambda pdf_path: (
            "PDF段落一：LMFP/LFP/三元复配提升高SOC充电安全。\n\n"
            "PDF段落二：低SOC放电功率优于对比例。"
        ),
        force_pdf=True,
    )

    evidence = bundle["evidences"][0]
    pdf_items = [item for item in evidence["matched_evidence"] if item["section_type"] == "pdf_paragraph"]
    table_items = [item for item in evidence["matched_evidence"] if item["section_type"] == "table"]

    assert len(pdf_items) == 2
    assert pdf_items[0]["section_label"] == "PDF Paragraph 1"
    assert "高SOC充电安全" in pdf_items[0]["text"]
    assert len(table_items) == 1
    assert evidence["table_supplements"][0]["table_title"] == "表1 各实施例性能对比"
    assert evidence["metadata"]["pdf_document"]["filename"] == "CN115132975B.pdf"


def test_patent_runtime_stage3_load_patent_evidence_uses_archive_tables_and_pdf_chunks(tmp_path: Path, monkeypatch):
    patent_dir = tmp_path / "CN115132975B"
    patent_dir.mkdir(parents=True)
    (patent_dir / "CN115132975B_tables.json").write_text(
        json.dumps(
            [
                {
                    "table_title": "表1 各实施例性能对比",
                    "columns": ["实验序号"],
                    "rows": [{"实验序号": "实施例1"}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (patent_dir / "CN115132975B.pdf").write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(
        PatentPdfService,
        "_extract_pdf_text",
        staticmethod(lambda pdf_path, max_pages=10: "PDF段落A：命中片段。\n\nPDF段落B：表征窗口。"),
    )

    runtime = PatentRuntime(
        retrieval_service=object(),  # type: ignore[arg-type]
        resources=[],
        archive_loader=PatentArchiveLoader(tmp_path),
        stage3_force_pdf=True,
    )
    retrieval_results = {
        "documents": ["摘要命中：LMFP/LFP/三元复配改善安全性。"],
        "metadatas": [
            {
                "patent_id": "CN115132975B",
                "stage2_source": "abstract",
                "section_type": "abstract",
                "section_label": "Abstract",
                "distance": 0.12,
            }
        ],
        "reference_objects": [
            {
                "canonical_patent_id": "CN115132975B",
                "publication_number": "CN115132975B",
                "title": "一种锂离子电池及动力车辆",
            }
        ],
    }

    bundle = runtime.stage3_load_patent_evidence(
        retrieval_results=retrieval_results,
        source_ids=["CN115132975B"],
    )

    evidence = bundle["evidences"][0]
    assert evidence["table_supplements"][0]["table_title"] == "表1 各实施例性能对比"
    assert evidence["metadata"]["pdf_document"]["filename"] == "CN115132975B.pdf"
    assert any(item["section_type"] == "pdf_paragraph" for item in evidence["matched_evidence"])
