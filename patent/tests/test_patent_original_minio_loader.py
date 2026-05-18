from __future__ import annotations

import json
from pathlib import Path

import pytest


class _FakeBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.closed = False

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.closed = True


class _FakeStat:
    def __init__(self, *, etag: str, size: int, content_type: str = "application/json", metadata: dict[str, str] | None = None) -> None:
        self.etag = etag
        self.size = size
        self.content_type = content_type
        self.metadata = metadata or {}
        self.last_modified = None


class _FakeMinio:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.stats: dict[tuple[str, str], _FakeStat] = {}

    def put_json(self, bucket: str, object_name: str, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.objects[(bucket, object_name)] = body
        self.stats[(bucket, object_name)] = _FakeStat(etag=f"etag-{Path(object_name).name}", size=len(body))

    def put_bytes(self, bucket: str, object_name: str, payload: bytes, *, etag: str = "", metadata: dict[str, str] | None = None) -> None:
        self.objects[(bucket, object_name)] = payload
        self.stats[(bucket, object_name)] = _FakeStat(
            etag=etag,
            size=len(payload),
            content_type="application/octet-stream",
            metadata=metadata,
        )

    def get_object(self, bucket: str, object_name: str):
        payload = self.objects.get((bucket, object_name))
        if payload is None:
            raise FileNotFoundError(object_name)
        return _FakeBody(payload)

    def stat_object(self, bucket: str, object_name: str):
        stat = self.stats.get((bucket, object_name))
        if stat is None:
            raise FileNotFoundError(object_name)
        return stat


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


def _loader(*, client: _FakeMinio | None = None, archive_root: Path | None = None, metrics: _FakeMetrics | None = None):
    from server.patent.object_reader import ObjectReader
    from server.patent.original_minio_loader import PatentOriginalMinioLoader

    reader = ObjectReader(client=client or _FakeMinio(), runtime_root=archive_root or Path("/tmp/patent-object-cache"))
    return PatentOriginalMinioLoader(reader=reader, bucket="agentcode", archive_root=archive_root, metrics=metrics)


def test_original_minio_loader_loads_tables_from_manifest(tmp_path):
    client = _FakeMinio()
    client.put_json(
        "agentcode",
        "patent/originals/CN1/manifest.json",
        {
            "canonical_patent_id": "CN1",
            "original_version": "v1",
            "objects": {"structured": {"tables": "patent/originals/CN1/structured/tables.json"}},
            "availability": {"tables": True},
        },
    )
    client.put_json(
        "agentcode",
        "patent/originals/CN1/structured/tables.json",
        [
            {
                "table_title": "T1",
                "columns": ["capacity"],
                "rows": [{"capacity": "150"}],
            }
        ],
    )

    loader = _loader(client=client, archive_root=tmp_path)
    tables = loader.load_tables("CN1")

    assert loader.diagnostics == []
    assert len(tables) == 1
    assert tables[0].table_title == "T1"
    assert tables[0].rows == [{"capacity": "150"}]


def test_original_minio_loader_records_loaded_metric(tmp_path):
    client = _FakeMinio()
    client.put_json(
        "agentcode",
        "patent/originals/CN1/manifest.json",
        {
            "canonical_patent_id": "CN1",
            "original_version": "v1",
            "objects": {"structured": {"tables": "patent/originals/CN1/structured/tables.json"}},
            "availability": {"tables": True},
        },
    )
    client.put_json(
        "agentcode",
        "patent/originals/CN1/structured/tables.json",
        [{"table_title": "T1", "columns": ["capacity"], "rows": [{"capacity": "150"}]}],
    )
    metrics = _FakeMetrics()

    loader = _loader(client=client, archive_root=tmp_path, metrics=metrics)
    loader.load_tables("CN1")

    assert metrics.count(
        "patent_tables_minio_loaded_total",
        service="patent",
        source_family="patent_table",
        result="success",
    ) == 1


def test_original_minio_loader_materializes_fulltext_pdf_from_manifest(tmp_path):
    client = _FakeMinio()
    client.put_json(
        "agentcode",
        "patent/originals/CN1/manifest.json",
        {
            "canonical_patent_id": "CN1",
            "original_version": "v1",
            "objects": {"structured": {}, "fulltext_pdf": "patent/originals/CN1/fulltext/original.pdf"},
            "availability": {"tables": False, "fulltext": True},
        },
    )
    client.put_bytes(
        "agentcode",
        "patent/originals/CN1/fulltext/original.pdf",
        b"%PDF-minio\n",
        etag="pdf-etag",
    )

    loader = _loader(client=client, archive_root=tmp_path)
    document = loader.load_pdf_document("CN1")

    assert document is not None
    assert document["filename"] == "original.pdf"
    assert document["size_bytes"] == len(b"%PDF-minio\n")
    assert Path(str(document["path"])).read_bytes() == b"%PDF-minio\n"


@pytest.mark.parametrize(
    "manifest_payload",
    [
        None,
        {"canonical_patent_id": "CN1", "original_version": "v1", "objects": {"structured": {}}, "availability": {"tables": False}},
    ],
)
def test_original_minio_loader_missing_manifest_or_tables_returns_empty(tmp_path, manifest_payload):
    client = _FakeMinio()
    if manifest_payload is not None:
        client.put_json("agentcode", "patent/originals/CN1/manifest.json", manifest_payload)

    loader = _loader(client=client, archive_root=tmp_path)
    tables = loader.load_tables("CN1")

    assert tables == []
    assert loader.diagnostics
    assert loader.diagnostics[0] in {"original_manifest_unavailable", "tables_unavailable"}


def test_original_minio_loader_records_missing_diagnostics_metric(tmp_path):
    metrics = _FakeMetrics()

    loader = _loader(client=_FakeMinio(), archive_root=tmp_path, metrics=metrics)
    tables = loader.load_tables("CN1")

    assert tables == []
    assert metrics.count(
        "patent_tables_minio_missing_total",
        service="patent",
        source_family="patent_table",
        result="missing",
        reason="original_manifest_unavailable",
    ) == 1


def test_original_minio_loader_does_not_read_local_tables(tmp_path):
    local_tables = tmp_path / "CN1_tables.json"
    local_tables.write_text(json.dumps([{"rows": [{"x": "local"}]}], ensure_ascii=False), encoding="utf-8")

    client = _FakeMinio()
    client.put_json(
        "agentcode",
        "patent/originals/CN1/manifest.json",
        {
            "canonical_patent_id": "CN1",
            "original_version": "v1",
            "objects": {"structured": {}},
            "availability": {"tables": True},
        },
    )

    loader = _loader(client=client, archive_root=tmp_path)
    tables = loader.load_tables("CN1")

    assert tables == []
    assert loader.diagnostics == ["tables_unavailable"]
