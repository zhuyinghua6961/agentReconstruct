from __future__ import annotations

from pathlib import Path

import pytest

from server.services.documents_service import documents_service
from server.storage import paper_storage


class _FakeS3Error(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _FakeBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        return None

    def release_conn(self) -> None:
        return None


class _FakeMinio:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)

    def stat_object(self, bucket: str, object_name: str):
        _ = bucket
        payload = self.objects.get(object_name)
        if payload is None:
            raise _FakeS3Error("NoSuchKey")
        return type("_Stat", (), {"etag": f"etag-{object_name}", "size": len(payload), "metadata": {}, "content_type": "application/pdf"})()

    def get_object(self, bucket: str, object_name: str):
        _ = bucket
        payload = self.objects.get(object_name)
        if payload is None:
            raise _FakeS3Error("NoSuchKey")
        return _FakeBody(payload)


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


def test_view_pdf_path_normalizes_polluted_doi(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    captured: dict[str, str] = {}

    def _fake_ensure_local_paper_pdf(*, doi: str, papers_dir: Path, logger=None):
        captured["doi"] = doi
        return pdf_path

    monkeypatch.setattr("server.services.documents_service.ensure_local_paper_pdf", _fake_ensure_local_paper_pdf)

    payload, status_code, resolved = documents_service.view_pdf_path("doi:10.1007_s11581-021-04073-2).", logger=None)

    assert status_code == 200
    assert resolved == pdf_path
    assert payload["doi"] == "10.1007/s11581-021-04073-2"
    assert captured["doi"] == "10.1007/s11581-021-04073-2"


def test_view_pdf_path_ignores_local_pdf_when_minio_missing(monkeypatch, tmp_path):
    (tmp_path / "10.1000_demo.pdf").write_bytes(b"%PDF-local")
    monkeypatch.setenv("HIGHTHINKING_ORIGINAL_MINIO_ONLY", "true")
    monkeypatch.setattr(documents_service, "_papers_dir", tmp_path)
    monkeypatch.setattr(paper_storage, "_build_minio_client_from_env", lambda: (_FakeMinio({}), "agentcode", _FakeS3Error))

    payload, status_code, resolved = documents_service.view_pdf_path("10.1000/demo", logger=None)

    assert status_code == 404
    assert resolved is None
    assert payload["code"] == "NOT_FOUND"


def test_view_pdf_path_materializes_pdf_from_minio(monkeypatch, tmp_path):
    object_name = paper_storage.build_paper_object_name("10.1000/demo")
    monkeypatch.setenv("HIGHTHINKING_ORIGINAL_MINIO_ONLY", "true")
    monkeypatch.setattr(documents_service, "_papers_dir", tmp_path)
    monkeypatch.setattr(
        paper_storage,
        "_build_minio_client_from_env",
        lambda: (_FakeMinio({object_name: b"%PDF-minio"}), "agentcode", _FakeS3Error),
    )

    payload, status_code, resolved = documents_service.view_pdf_path("10.1000/demo", logger=None)

    assert status_code == 200
    assert resolved is not None
    assert resolved.read_bytes() == b"%PDF-minio"
    assert resolved.parent.name == "object-cache"
    assert payload["filename"] == "10.1000_demo.pdf"


def test_highthinking_object_reader_records_read_failure_metric(tmp_path):
    from server.storage.object_reader import ObjectReader, ObjectReaderUnavailableError

    metrics = _FakeMetrics()
    reader = ObjectReader(client=_FakeMinio({}), metrics=metrics, runtime_root=tmp_path)

    with pytest.raises(ObjectReaderUnavailableError):
        reader.read_bytes("minio://agentcode/papers/10.1000_demo.pdf")

    assert metrics.count(
        "qa_original_minio_read_failed_total",
        service="highThinkingQA",
        source_family="paper_pdf",
        result="failure",
        reason="object_read_failed",
    ) == 1
