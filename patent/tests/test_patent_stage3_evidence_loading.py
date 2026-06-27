from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from server.patent.archive_loader import PatentArchiveLoader
from server.patent.pdf_service import PatentPdfService
from server.patent.retrieval_models import PatentCatalogRecord, PatentTableSupplement
from server.patent.runtime import PatentRuntime
from server.patent.stages.evidence_loading import run_stage3_load_patent_evidence
from server.utils.upstream_errors import UpstreamCallError


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


def test_stage3_parallel_matches_serial_success_bundle_order():
    retrieval_results = {
        "documents": [
            "CN115132975B 摘要证据",
            "US20240001234A1 摘要证据",
        ],
        "metadatas": [
            {"patent_id": "CN115132975B", "section_type": "abstract", "section_label": "Abstract", "distance": 0.12},
            {"patent_id": "US20240001234A1", "section_type": "abstract", "section_label": "Abstract", "distance": 0.18},
        ],
        "distances": [0.12, 0.18],
        "reference_objects": [
            {"canonical_patent_id": "CN115132975B", "publication_number": "CN115132975B", "title": "专利一"},
            {"canonical_patent_id": "US20240001234A1", "publication_number": "US20240001234A1", "title": "专利二"},
        ],
    }

    def _catalog_loader(patent_id: str) -> PatentCatalogRecord:
        return PatentCatalogRecord(
            canonical_patent_id=patent_id,
            publication_number=patent_id,
            application_number=None,
            title="专利一" if patent_id == "CN115132975B" else "专利二",
            abstract_text=f"{patent_id} abstract",
        )

    serial_bundle = run_stage3_load_patent_evidence(
        retrieval_results=retrieval_results,
        source_ids=["CN115132975B", "US20240001234A1"],
        catalog_loader=_catalog_loader,
        parallel_workers=1,
    )
    parallel_bundle = run_stage3_load_patent_evidence(
        retrieval_results=retrieval_results,
        source_ids=["CN115132975B", "US20240001234A1"],
        catalog_loader=_catalog_loader,
        parallel_workers=2,
    )

    assert serial_bundle["source_ids"] == parallel_bundle["source_ids"]
    assert [item["canonical_patent_id"] for item in parallel_bundle["evidences"]] == parallel_bundle["source_ids"]
    assert serial_bundle["evidences"] == parallel_bundle["evidences"]


def test_stage3_parallel_drops_failed_patents_from_source_ids_and_keeps_alignment():
    retrieval_results = {
        "documents": ["CN115132975B 摘要证据", "US20240001234A1 摘要证据"],
        "metadatas": [
            {"patent_id": "CN115132975B", "section_type": "abstract", "section_label": "Abstract", "distance": 0.12},
            {"patent_id": "US20240001234A1", "section_type": "abstract", "section_label": "Abstract", "distance": 0.18},
        ],
        "distances": [0.12, 0.18],
        "reference_objects": [
            {"canonical_patent_id": "CN115132975B", "publication_number": "CN115132975B", "title": "专利一"},
            {"canonical_patent_id": "US20240001234A1", "publication_number": "US20240001234A1", "title": "专利二"},
        ],
    }

    def _catalog_loader(patent_id: str) -> PatentCatalogRecord:
        if patent_id == "US20240001234A1":
            raise RuntimeError("bad patent")
        return PatentCatalogRecord(
            canonical_patent_id=patent_id,
            publication_number=patent_id,
            application_number=None,
            title="专利一",
            abstract_text=f"{patent_id} abstract",
        )

    bundle = run_stage3_load_patent_evidence(
        retrieval_results=retrieval_results,
        source_ids=["CN115132975B", "US20240001234A1"],
        catalog_loader=_catalog_loader,
        parallel_workers=2,
    )

    assert bundle["source_ids"] == ["CN115132975B"]
    assert [item["canonical_patent_id"] for item in bundle["evidences"]] == ["CN115132975B"]


def test_stage3_parallel_raises_when_all_patents_fail():
    with pytest.raises(UpstreamCallError) as exc_info:
        run_stage3_load_patent_evidence(
            retrieval_results={"documents": [], "metadatas": [], "reference_objects": []},
            source_ids=["CN115132975B", "US20240001234A1"],
            catalog_loader=lambda _patent_id: (_ for _ in ()).throw(RuntimeError("bad patent")),
            parallel_workers=2,
        )
    assert exc_info.value.code == "RETRIEVAL_FAILED"
    assert exc_info.value.stage == "stage3"


def test_stage3_parallel_force_pdf_keeps_pdf_chunks_on_the_right_patent():
    bundle = run_stage3_load_patent_evidence(
        retrieval_results={"documents": [], "metadatas": [], "reference_objects": []},
        source_ids=["CN115132975B", "US20240001234A1"],
        pdf_loader=lambda patent_id: {
            "path": f"/tmp/{patent_id}.pdf",
            "filename": f"{patent_id}.pdf",
            "size_bytes": 8,
        },
        pdf_text_extractor=lambda pdf_path: f"{Path(pdf_path).stem} PDF段落一\n\n{Path(pdf_path).stem} PDF段落二",
        force_pdf=True,
        parallel_workers=2,
    )

    assert bundle["source_ids"] == ["CN115132975B", "US20240001234A1"]
    first = bundle["evidences"][0]
    second = bundle["evidences"][1]
    assert all("CN115132975B" in item["text"] for item in first["matched_evidence"] if item["section_type"] == "pdf_paragraph")
    assert all("US20240001234A1" in item["text"] for item in second["matched_evidence"] if item["section_type"] == "pdf_paragraph")


def test_stage3_parallel_honors_explicit_should_cancel():
    release = threading.Event()
    started = threading.Event()

    def _catalog_loader(patent_id: str) -> PatentCatalogRecord:
        started.set()
        release.wait(timeout=0.5)
        return PatentCatalogRecord(
            canonical_patent_id=patent_id,
            publication_number=patent_id,
            application_number=None,
            title=patent_id,
            abstract_text=f"{patent_id} abstract",
        )

    started_at = time.perf_counter()
    try:
        bundle = run_stage3_load_patent_evidence(
            retrieval_results={"documents": [], "metadatas": [], "reference_objects": []},
            source_ids=["CN115132975B", "US20240001234A1"],
            catalog_loader=_catalog_loader,
            parallel_workers=2,
            should_cancel=lambda: started.is_set(),
        )
    finally:
        release.set()

    elapsed = time.perf_counter() - started_at
    assert elapsed < 0.3
    assert bundle["source_ids"] == []
    assert bundle["metadata"]["cancelled"] is True


def test_stage3_logs_parallel_workers_and_source_id_count(caplog):
    with caplog.at_level("INFO", logger="patent.stage3"):
        bundle = run_stage3_load_patent_evidence(
            retrieval_results={"documents": [], "metadatas": [], "reference_objects": []},
            source_ids=["CN115132975B", "US20240001234A1"],
            parallel_workers=2,
            force_pdf=True,
        )

    assert bundle["source_ids"] == ["CN115132975B", "US20240001234A1"]
    messages = [record.message for record in caplog.records if record.name == "patent.stage3"]
    assert any(
        "patent stage3 evidence loading start" in message
        and "source_id_count=2" in message
        and "parallel_workers=2" in message
        and "force_pdf=True" in message
        for message in messages
    )
    assert any(
        "patent stage3 diagnostic input" in message
        and "documents=0" in message
        and "metadatas=0" in message
        and "source_ids=['CN115132975B', 'US20240001234A1']" in message
        for message in messages
    )
    assert any(
        "patent stage3 source diagnostic" in message
        and "patent_id=CN115132975B" in message
        and "retrieval_rows=0" in message
        and "matched_evidence=0" in message
        for message in messages
    )
    assert any(
        "patent stage3 diagnostic completed" in message
        and "requested=2" in message
        and "successful=2" in message
        and "failed=0" in message
        and "total_matched_evidence=0" in message
        for message in messages
    )


def test_stage3_logs_force_pdf_loading_diagnostics(monkeypatch, caplog):
    monkeypatch.setenv("QA_STAGE3_DIAGNOSTIC_LOG", "1")
    monkeypatch.setenv("QA_STAGE3_LOG_SOURCE_DETAILS", "1")
    monkeypatch.setenv("QA_STAGE3_LOG_CHUNK_DETAILS", "1")
    monkeypatch.setenv("QA_STAGE3_LOG_CHUNK_MAX", "2")
    monkeypatch.setenv("QA_STAGE3_LOG_TEXT_MAX_CHARS", "80")

    with caplog.at_level("INFO", logger="patent.stage3"):
        bundle = run_stage3_load_patent_evidence(
            retrieval_results={"documents": [], "metadatas": [], "reference_objects": []},
            source_ids=["CN115132975B"],
            pdf_loader=lambda patent_id: {
                "path": f"/tmp/{patent_id}.pdf",
                "filename": f"{patent_id}.pdf",
                "size_bytes": 16,
            },
            pdf_text_extractor=lambda pdf_path: "PDF段落一：LMFP 复配提升功率。\n\nPDF段落二：高SOC快充更稳定。",
            force_pdf=True,
        )

    assert bundle["source_ids"] == ["CN115132975B"]
    messages = [record.message for record in caplog.records if record.name == "patent.stage3"]
    assert any(
        "patent stage3 pdf diagnostic" in message
        and "patent_id=CN115132975B" in message
        and "pdf_loaded=True" in message
        and "text_chars=" in message
        and "pdf_chunks=2" in message
        for message in messages
    )
    assert any(
        "patent stage3 evidence diagnostic" in message
        and "patent_id=CN115132975B" in message
        and "section=pdf_paragraph" in message
        and "PDF段落一" in message
        for message in messages
    )


def test_runtime_stage3_passes_parallel_workers_and_should_cancel(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run_stage3_load_patent_evidence(**kwargs):
        captured.update(kwargs)
        return {"source_ids": ["CN115132975B"], "evidences": [], "metadata": {}}

    monkeypatch.setattr("server.patent.runtime.run_stage3_load_patent_evidence", _fake_run_stage3_load_patent_evidence)

    runtime = PatentRuntime(
        retrieval_service=object(),  # type: ignore[arg-type]
        resources=[],
        stage3_parallel_workers=5,
    )
    should_cancel = object()

    runtime.stage3_load_patent_evidence(
        retrieval_results={"documents": [], "metadatas": [], "reference_objects": []},
        source_ids=["CN115132975B"],
        should_cancel=should_cancel,
    )

    assert captured["parallel_workers"] == 5
    assert captured["should_cancel"] is should_cancel


def test_patent_runtime_stage3_uses_configured_table_loader_instead_of_archive_tables():
    class _ArchiveLoader:
        def load_catalog_record(self, patent_id: str) -> PatentCatalogRecord:
            return PatentCatalogRecord(
                canonical_patent_id=patent_id,
                publication_number=patent_id,
                application_number=None,
                title=patent_id,
                abstract_text=f"{patent_id} abstract",
            )

        def load_tables(self, patent_id: str):
            raise AssertionError(f"archive table loader should not be used for {patent_id}")

        def load_pdf_document(self, patent_id: str):
            return None

    runtime = PatentRuntime(
        retrieval_service=object(),  # type: ignore[arg-type]
        resources=[],
        archive_loader=_ArchiveLoader(),  # type: ignore[arg-type]
        table_loader=lambda patent_id: [
            PatentTableSupplement(
                table_title="MinIO Table",
                columns=["x"],
                rows=[{"x": patent_id}],
            )
        ],
    )

    bundle = runtime.stage3_load_patent_evidence(
        retrieval_results={"documents": [], "metadatas": [], "reference_objects": []},
        source_ids=["CN115132975B"],
    )

    assert bundle["evidences"][0]["table_supplements"][0]["table_title"] == "MinIO Table"


def test_patent_runtime_stage3_strict_default_does_not_use_archive_tables_or_pdf(monkeypatch):
    monkeypatch.delenv("PATENT_ORIGINAL_MINIO_ONLY", raising=False)

    class _ArchiveLoader:
        def load_catalog_record(self, patent_id: str) -> PatentCatalogRecord:
            return PatentCatalogRecord(
                canonical_patent_id=patent_id,
                publication_number=patent_id,
                application_number=None,
                title=patent_id,
                abstract_text=f"{patent_id} abstract",
            )

        def load_tables(self, patent_id: str):
            raise AssertionError(f"archive table loader should not be used for {patent_id}")

        def load_pdf_document(self, patent_id: str):
            raise AssertionError(f"archive PDF loader should not be used for {patent_id}")

    runtime = PatentRuntime(
        retrieval_service=object(),  # type: ignore[arg-type]
        resources=[],
        archive_loader=_ArchiveLoader(),  # type: ignore[arg-type]
        stage3_force_pdf=True,
    )

    bundle = runtime.stage3_load_patent_evidence(
        retrieval_results={"documents": [], "metadatas": [], "reference_objects": []},
        source_ids=["CN115132975B"],
    )

    evidence = bundle["evidences"][0]
    assert evidence["table_supplements"] == []
    assert "pdf_document" not in evidence["metadata"]


def test_patent_runtime_stage3_load_patent_evidence_uses_archive_tables_and_pdf_chunks(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PATENT_ORIGINAL_MINIO_ONLY", "false")
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
