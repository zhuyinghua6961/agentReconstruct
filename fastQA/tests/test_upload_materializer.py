from __future__ import annotations

from pathlib import Path

import app.modules.storage.upload_materializer as upload_materializer


def test_parse_storage_ref_supports_minio_and_local():
    assert upload_materializer.parse_storage_ref("minio://bucket/uploads/demo.pdf") == {
        "scheme": "minio",
        "bucket": "bucket",
        "object_name": "uploads/demo.pdf",
        "local_path": None,
    }
    assert upload_materializer.parse_storage_ref("local:///tmp/demo.pdf") == {
        "scheme": "local",
        "bucket": None,
        "object_name": None,
        "local_path": "/tmp/demo.pdf",
    }


def test_materialize_uploaded_file_resolves_local_storage_ref(tmp_path):
    source_file = tmp_path / "demo.csv"
    source_file.write_text("a,b\n1,2\n", encoding="utf-8")

    prepared = upload_materializer.materialize_uploaded_file(
        {
            "file_id": 1,
            "file_name": "demo.csv",
            "storage_ref": f"local://{source_file}",
        }
    )

    assert prepared["local_path"] == str(source_file.resolve())
    assert prepared["storage_ref"] == f"local://{source_file}"


def test_materialize_uploaded_file_downloads_minio_storage_ref_into_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    calls: list[tuple[str, str, str]] = []

    class FakeClient:
        def fget_object(self, bucket: str, object_name: str, local_path: str) -> None:
            calls.append((bucket, object_name, local_path))
            Path(local_path).write_bytes(b"%PDF-1.4\n%materialized\n")

    monkeypatch.setattr(
        upload_materializer,
        "_build_minio_client_from_env",
        lambda: (FakeClient(), RuntimeError),
    )

    prepared = upload_materializer.materialize_uploaded_file(
        {
            "file_id": 2,
            "file_name": "demo.pdf",
            "storage_ref": "minio://bucket/uploads/pdf/demo.pdf",
        },
        cache_dir=cache_dir,
    )

    assert calls == [("bucket", "uploads/pdf/demo.pdf", prepared["local_path"])]
    assert Path(prepared["local_path"]).exists()
    assert prepared["local_path"].startswith(str(cache_dir.resolve()))
