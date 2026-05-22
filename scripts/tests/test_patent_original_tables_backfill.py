from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path


PATENT_ID = "CN123456789A"


class FakeTarget:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.uploads: list[tuple[str, bytes, str]] = []

    def object_exists(self, *, object_name: str) -> bool:
        return object_name in self.objects

    def upload_bytes(self, *, object_name: str, payload: bytes, content_type: str) -> None:
        self.objects[object_name] = payload
        self.uploads.append((object_name, payload, content_type))

    def read_object_bytes(self, *, object_name: str) -> bytes | None:
        return self.objects.get(object_name)


def _load_module():
    return import_module("scripts.patent_original_tables_backfill")


def _json_bytes(payload: dict | list) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _write_tables_file(root: Path, payload: list[dict] | None = None) -> Path:
    source_dir = root / PATENT_ID
    source_dir.mkdir(parents=True)
    tables = payload if payload is not None else [{"table_title": "表1", "columns": ["A"], "rows": [{"A": "1"}]}]
    path = source_dir / f"{PATENT_ID}_tables.json"
    path.write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")
    return source_dir


def _existing_manifest() -> dict:
    prefix = f"patent/originals/{PATENT_ID}"
    return {
        "canonical_patent_id": PATENT_ID,
        "title": "示例专利",
        "provider": "patent_source_x",
        "original_version": "sha256:old",
        "objects": {
            "structured": {
                "claims": f"{prefix}/structured/claims.json",
                "description": f"{prefix}/structured/description.json",
                "bibliography": f"{prefix}/structured/bibliography.json",
            },
            "figures": {},
            "fulltext_pdf": f"{prefix}/fulltext/original.pdf",
        },
        "availability": {
            "claims": True,
            "description": True,
            "abstract": True,
            "figure": False,
            "fulltext_pdf": True,
        },
    }


def test_backfill_uploads_tables_then_manifest_and_can_resume(tmp_path: Path) -> None:
    module = _load_module()
    source_dir = _write_tables_file(tmp_path)
    prefix = f"patent/originals/{PATENT_ID}"
    manifest_key = f"{prefix}/manifest.json"
    tables_key = f"{prefix}/structured/tables.json"
    target = FakeTarget({manifest_key: _json_bytes(_existing_manifest())})

    first = module.backfill_tables_for_source_dir(source_dir=source_dir, target=target)

    assert first["status"] == "updated"
    assert [item[0] for item in target.uploads] == [tables_key, manifest_key]
    assert json.loads(target.objects[tables_key].decode("utf-8")) == [
        {"table_title": "表1", "columns": ["A"], "rows": [{"A": "1"}]}
    ]
    manifest = json.loads(target.objects[manifest_key].decode("utf-8"))
    assert manifest["objects"]["structured"]["tables"] == tables_key
    assert manifest["availability"]["tables"] is True
    assert manifest["original_version"].startswith("sha256:")
    assert manifest["original_version"] != "sha256:old"

    target.uploads.clear()
    second = module.backfill_tables_for_source_dir(source_dir=source_dir, target=target)

    assert second["status"] == "skipped"
    assert second["skipped_objects"] == [tables_key, manifest_key]
    assert target.uploads == []


def test_backfill_resumes_when_table_uploaded_but_manifest_still_old(tmp_path: Path) -> None:
    module = _load_module()
    source_dir = _write_tables_file(tmp_path)
    prefix = f"patent/originals/{PATENT_ID}"
    manifest_key = f"{prefix}/manifest.json"
    tables_key = f"{prefix}/structured/tables.json"
    target = FakeTarget(
        {
            manifest_key: _json_bytes(_existing_manifest()),
            tables_key: (source_dir / f"{PATENT_ID}_tables.json").read_bytes(),
        }
    )

    result = module.backfill_tables_for_source_dir(source_dir=source_dir, target=target)

    assert result["status"] == "updated"
    assert result["skipped_objects"] == [tables_key]
    assert [item[0] for item in target.uploads] == [manifest_key]


def test_file_target_writes_tables_and_manifest_under_bucket_dir(tmp_path: Path) -> None:
    module = _load_module()
    source_dir = _write_tables_file(tmp_path / "source")
    bucket_dir = tmp_path / "seed"
    prefix = f"patent/originals/{PATENT_ID}"
    manifest_key = f"{prefix}/manifest.json"
    tables_key = f"{prefix}/structured/tables.json"
    manifest_path = bucket_dir / manifest_key
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(_json_bytes(_existing_manifest()))

    target = module.FileTablesBackfillTarget(bucket_dir)
    first = module.backfill_tables_for_source_dir(source_dir=source_dir, target=target)

    assert first["status"] == "updated"
    assert json.loads((bucket_dir / tables_key).read_text(encoding="utf-8")) == [
        {"table_title": "表1", "columns": ["A"], "rows": [{"A": "1"}]}
    ]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["objects"]["structured"]["tables"] == tables_key
    assert manifest["availability"]["tables"] is True

    second = module.backfill_tables_for_source_dir(source_dir=source_dir, target=target)

    assert second["status"] == "skipped"
    assert second["skipped_objects"] == [tables_key, manifest_key]


def test_backfill_marks_empty_tables_payload_unavailable(tmp_path: Path) -> None:
    module = _load_module()
    source_dir = _write_tables_file(tmp_path, payload=[])
    prefix = f"patent/originals/{PATENT_ID}"
    manifest_key = f"{prefix}/manifest.json"
    tables_key = f"{prefix}/structured/tables.json"
    target = FakeTarget({manifest_key: _json_bytes(_existing_manifest())})

    result = module.backfill_tables_for_source_dir(source_dir=source_dir, target=target)

    assert result["status"] == "updated"
    assert json.loads(target.objects[tables_key].decode("utf-8")) == []
    manifest = json.loads(target.objects[manifest_key].decode("utf-8"))
    assert manifest["objects"]["structured"]["tables"] == tables_key
    assert manifest["availability"]["tables"] is False


def test_backfill_dry_run_reports_missing_objects_without_uploading(tmp_path: Path) -> None:
    module = _load_module()
    source_dir = _write_tables_file(tmp_path, payload=[])
    prefix = f"patent/originals/{PATENT_ID}"
    manifest_key = f"{prefix}/manifest.json"
    tables_key = f"{prefix}/structured/tables.json"
    target = FakeTarget({manifest_key: _json_bytes(_existing_manifest())})

    result = module.backfill_tables_for_source_dir(source_dir=source_dir, target=target, dry_run=True)

    assert result["status"] == "updated"
    assert result["would_upload_objects"] == [tables_key, manifest_key]
    assert target.uploads == []
    assert tables_key not in target.objects
