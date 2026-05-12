from pathlib import Path

from server.storage.file_delivery_service import resolve_uploaded_file_delivery


def test_resolve_uploaded_file_delivery_prefers_existing_local_path(tmp_path):
    sample = tmp_path / "demo.pdf"
    sample.write_bytes(b"pdf")

    plan = resolve_uploaded_file_delivery(
        file_row={
            "file_name": "demo.pdf",
            "local_path": str(sample),
            "storage_ref": "",
        },
        logger=None,
    )

    assert plan is not None
    assert plan.kind == "file"
    assert plan.local_path == str(sample)
    assert plan.cleanup_path is None
    assert plan.download_name == "demo.pdf"


def test_resolve_uploaded_file_delivery_uses_proxy_for_minio_when_env_disabled(monkeypatch):
    class DummyBackend:
        def download_file(self, *, object_name, local_path):
            assert object_name == "folder/demo.pdf"
            Path(local_path).write_bytes(b"pdf")
            return True

        def get_file_url(self, *, object_name, expires_seconds):
            raise AssertionError("MINIO_USE_PROXY=0 must not disable proxy delivery")

    monkeypatch.setattr(
        "server.storage.file_delivery_service.get_storage_backend",
        lambda project_root: DummyBackend(),
    )
    monkeypatch.setenv("MINIO_USE_PROXY", "0")

    plan = resolve_uploaded_file_delivery(
        file_row={
            "file_name": "demo.pdf",
            "local_path": "",
            "storage_ref": "minio://bucket/folder/demo.pdf",
        },
        logger=None,
    )

    assert plan is not None
    assert plan.kind == "file"
    assert plan.local_path
    assert Path(plan.local_path).exists()
    assert plan.download_name == "demo.pdf"
