from __future__ import annotations

import app.modules.qa_tabular.workbook_loader as workbook_loader


def test_load_workbook_reads_csv_from_minio_when_local_path_missing(tmp_path, monkeypatch):
    source_file = tmp_path / "demo.csv"
    source_file.write_text("a,b\n1,2\n", encoding="utf-8")

    class FakeObjectReader:
        def materialize_temp(self, storage_ref: str, *, suffix: str):
            assert storage_ref == "minio://agentcode/uploads/demo.csv"
            assert suffix == ".csv"
            return source_file.resolve()

        def stat(self, storage_ref: str):
            assert storage_ref == "minio://agentcode/uploads/demo.csv"
            return type("ObjectStat", (), {"bucket": "agentcode", "object_name": "uploads/demo.csv", "etag": "csv-etag", "size": source_file.stat().st_size, "sha256": ""})()

    def _fake_load_csv(local_path: Path):
        assert local_path == source_file.resolve()
        return {
            "sheet_name": "Sheet1",
            "sheet_index": 0,
            "dataframe": object(),
            "source_format": "csv",
            "data_quality": {},
        }

    from pathlib import Path

    monkeypatch.setattr(workbook_loader, "ObjectReader", lambda: FakeObjectReader(), raising=False)
    monkeypatch.setattr(workbook_loader, "_load_csv", _fake_load_csv)

    workbook = workbook_loader.load_workbook(
        {
            "file_id": 7,
            "file_name": "demo.csv",
            "storage_ref": "minio://agentcode/uploads/demo.csv",
            "local_path": str(tmp_path / "missing.csv"),
        }
    )

    assert workbook["file_id"] == 7
    assert workbook["file_name"] == "demo.csv"
    assert workbook["local_path"] == ""
    assert workbook["storage_ref"] == "minio://agentcode/uploads/demo.csv"
