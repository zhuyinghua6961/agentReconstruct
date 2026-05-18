from __future__ import annotations

from pathlib import Path

import app.modules.storage.upload_materializer as upload_materializer
import pytest


class _FakeStat:
    def __init__(self, *, etag: str = "", size: int = 0, metadata: dict[str, str] | None = None) -> None:
        self.etag = etag
        self.size = size
        self.metadata = metadata or {}
        self.content_type = "application/octet-stream"
        self.last_modified = None


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        return None

    def release_conn(self) -> None:
        return None


class _FakeMinio:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, _FakeStat]] = {}

    def put(self, bucket: str, object_name: str, payload: bytes, *, etag: str = "", metadata: dict[str, str] | None = None) -> None:
        self.objects[(bucket, object_name)] = (
            payload,
            _FakeStat(etag=etag, size=len(payload), metadata=metadata),
        )

    def stat_object(self, bucket: str, object_name: str):
        return self.objects[(bucket, object_name)][1]

    def get_object(self, bucket: str, object_name: str):
        return _FakeResponse(self.objects[(bucket, object_name)][0])


class _FakeMetrics:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def increment(self, name: str, **labels: object) -> None:
        self.events.append((name, dict(labels)))

    def count(self, name: str, **labels: object) -> int:
        return sum(
            1
            for metric_name, metric_labels in self.events
            if metric_name == name and all(metric_labels.get(key) == value for key, value in labels.items())
        )


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


def test_materialize_uploaded_file_rejects_local_storage_ref(tmp_path):
    source_file = tmp_path / "demo.csv"
    source_file.write_text("a,b\n1,2\n", encoding="utf-8")

    prepared = upload_materializer.materialize_uploaded_file(
        {
            "file_id": 1,
            "file_name": "demo.csv",
            "storage_ref": f"local://{source_file}",
        }
    )

    assert prepared["local_path"] == ""
    assert prepared["storage_error"] == "storage_ref_not_minio"
    assert prepared["storage_ref"] == f"local://{source_file}"


def test_materialize_uploaded_file_ignores_existing_local_path_when_storage_ref_missing(tmp_path):
    source_file = tmp_path / "demo.pdf"
    source_file.write_bytes(b"%PDF-local\n")

    prepared = upload_materializer.materialize_uploaded_file(
        {
            "file_id": 3,
            "file_name": "demo.pdf",
            "file_type": "pdf",
            "local_path": str(source_file),
            "storage_ref": "",
        }
    )

    assert prepared["local_path"] == ""
    assert prepared["storage_error"] == "storage_ref_missing"


def test_materialize_uploaded_file_downloads_minio_storage_ref_into_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    calls: list[tuple[str, str]] = []
    target_file = cache_dir / "demo.pdf"
    metrics = _FakeMetrics()

    class FakeReader:
        def __init__(self, *, runtime_root=None, metrics=None):
            assert runtime_root == cache_dir
            assert metrics is not None

        def materialize_temp(self, storage_ref: str, *, suffix: str):
            calls.append((storage_ref, suffix))
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"%PDF-1.4\n%materialized\n")
            return target_file

        def stat(self, storage_ref: str):
            return type("ObjectStat", (), {"bucket": "bucket", "object_name": "uploads/pdf/demo.pdf", "etag": "etag", "size": target_file.stat().st_size if target_file.exists() else 0})()

    monkeypatch.setattr(
        upload_materializer,
        "ObjectReader",
        FakeReader,
    )

    prepared = upload_materializer.materialize_uploaded_file(
        {
            "file_id": 2,
            "file_name": "demo.pdf",
            "storage_ref": "minio://bucket/uploads/pdf/demo.pdf",
        },
        cache_dir=cache_dir,
        metrics=metrics,
    )

    assert calls == [("minio://bucket/uploads/pdf/demo.pdf", ".pdf")]
    assert Path(prepared["local_path"]).exists()
    assert prepared["local_path"].startswith(str(cache_dir.resolve()))
    assert metrics.count("qa_original_local_fallback_attempt_total") == 0


def test_materialize_uploaded_file_records_legacy_local_fallback_metric(tmp_path):
    source_file = tmp_path / "demo.pdf"
    source_file.write_bytes(b"%PDF-local\n")
    metrics = _FakeMetrics()

    prepared = upload_materializer.materialize_uploaded_file(
        {
            "file_id": 3,
            "file_name": "demo.pdf",
            "file_type": "pdf",
            "local_path": str(source_file),
            "storage_ref": "",
        },
        strict_minio_only=False,
        metrics=metrics,
    )

    assert prepared["local_path"] == str(source_file.resolve())
    assert metrics.count(
        "qa_original_local_fallback_attempt_total",
        service="fastQA",
        source_family="upload_pdf",
        result="legacy_local_path",
    ) == 1


def test_object_reader_reads_minio_bytes_without_local_path(tmp_path):
    from app.modules.storage.object_reader import ObjectReader

    fake_minio = _FakeMinio()
    fake_minio.put("agentcode", "uploads/a.csv", b"a,b\n1,2\n", etag="e1")
    reader = ObjectReader(client=fake_minio, runtime_root=tmp_path)

    assert reader.read_bytes("minio://agentcode/uploads/a.csv") == b"a,b\n1,2\n"


def test_object_reader_rejects_local_storage_ref(tmp_path):
    from app.modules.storage.object_reader import ObjectReader, ObjectReaderProtocolError

    local = tmp_path / "a.csv"
    local.write_text("a,b\n1,2\n", encoding="utf-8")
    reader = ObjectReader(client=_FakeMinio(), runtime_root=tmp_path)

    with pytest.raises(ObjectReaderProtocolError):
        reader.read_bytes(f"local://{local}")


def test_object_reader_scratch_key_includes_sha256_metadata(tmp_path):
    from app.modules.storage.object_reader import ObjectReader

    fake_minio = _FakeMinio()
    fake_minio.put("agentcode", "uploads/a.csv", b"a,b\n1,2\n", etag="same", metadata={"sha256": "sha-a"})
    fake_minio.put("agentcode", "uploads/b.csv", b"a,b\n1,2\n", etag="same", metadata={"sha256": "sha-b"})
    reader = ObjectReader(client=fake_minio, runtime_root=tmp_path)

    path_a = reader.materialize_temp("minio://agentcode/uploads/a.csv", suffix=".csv")
    path_b = reader.materialize_temp("minio://agentcode/uploads/b.csv", suffix=".csv")

    assert path_a != path_b
    assert path_a.read_bytes() == b"a,b\n1,2\n"


def test_object_reader_scratch_key_computes_sha256_when_etag_missing(tmp_path):
    from app.modules.storage.object_reader import ObjectReader

    fake_minio = _FakeMinio()
    fake_minio.put("agentcode", "uploads/a.csv", b"a,b\n1,2\n", etag="")
    reader = ObjectReader(client=fake_minio, runtime_root=tmp_path)

    path = reader.materialize_temp("minio://agentcode/uploads/a.csv", suffix=".csv")

    assert path.read_bytes() == b"a,b\n1,2\n"
    assert path.name.endswith(".csv")


def test_object_reader_records_read_failure_metric(tmp_path):
    from app.modules.storage.object_reader import ObjectReader, ObjectReaderUnavailableError

    metrics = _FakeMetrics()
    reader = ObjectReader(client=_FakeMinio(), metrics=metrics, runtime_root=tmp_path)

    with pytest.raises(ObjectReaderUnavailableError):
        reader.read_bytes("minio://agentcode/uploads/missing.pdf")

    assert metrics.count(
        "qa_original_minio_read_failed_total",
        service="fastQA",
        source_family="upload_pdf",
        result="failure",
        reason="object_read_failed",
    ) == 1
