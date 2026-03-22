from __future__ import annotations

from pathlib import Path

import app.modules.qa_tabular.workbook_loader as workbook_loader


def test_load_workbook_materializes_storage_ref_when_local_path_missing(tmp_path, monkeypatch):
    source_file = tmp_path / "demo.csv"
    source_file.write_text("a,b\n1,2\n", encoding="utf-8")

    def _fake_load_csv(local_path: Path):
        assert local_path == source_file.resolve()
        return {
            "sheet_name": "Sheet1",
            "sheet_index": 0,
            "dataframe": object(),
            "source_format": "csv",
            "data_quality": {},
        }

    monkeypatch.setattr(workbook_loader, "_load_csv", _fake_load_csv)

    workbook = workbook_loader.load_workbook(
        {
            "file_id": 7,
            "file_name": "demo.csv",
            "storage_ref": f"local://{source_file}",
        }
    )

    assert workbook["file_id"] == 7
    assert workbook["file_name"] == "demo.csv"
    assert workbook["local_path"] == str(source_file.resolve())
    assert workbook["storage_ref"] == f"local://{source_file}"
