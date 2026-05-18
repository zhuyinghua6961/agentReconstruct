from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.integrations.storage.local import LocalStorageBackend
from app.integrations.storage.minio import MinIOStorageBackend
from app.core.errors import AppError
from app.modules.documents.schemas import PatentOriginalManifest
from app.modules.documents.service import documents_service
from app.modules.storage.service import storage_service


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "patent_original_store"
CANONICAL_PATENT_ID = "CN123456789A"


def _load_store_module():
    try:
        return import_module("app.modules.documents.patent_original_store")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing patent original store module: {exc}")


class _FakeObjectBackend:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = dict(objects)

    def read_object_bytes(self, *, object_name: str, bucket: str | None = None) -> bytes | None:
        _ = bucket
        return self._objects.get(object_name)

    def stat_object(self, *, object_name: str, bucket: str | None = None) -> dict[str, object] | None:
        _ = bucket
        payload = self._objects.get(object_name)
        if payload is None:
            return None
        return {
            "object_name": object_name,
            "etag": f"etag-{Path(object_name).name}",
            "size": len(payload),
        }


class _ExplodingBackend(_FakeObjectBackend):
    def read_object_bytes(self, *, object_name: str, bucket: str | None = None) -> bytes | None:
        _ = object_name, bucket
        raise RuntimeError("minio unavailable")


class _CountingBackend(_FakeObjectBackend):
    def __init__(self, objects: dict[str, bytes]) -> None:
        super().__init__(objects)
        self.read_calls: list[str] = []

    def read_object_bytes(self, *, object_name: str, bucket: str | None = None) -> bytes | None:
        _ = bucket
        self.read_calls.append(object_name)
        return super().read_object_bytes(object_name=object_name, bucket=bucket)


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


class _FakeResolved:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeManifest:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeStore:
    def __init__(self, *, manifest: object, resolved: object) -> None:
        self._manifest = manifest
        self._resolved = resolved

    def load_manifest(self, canonical_patent_id: str):
        _ = canonical_patent_id
        return self._manifest

    def resolve_section(self, **kwargs):
        _ = kwargs
        return self._resolved


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def _manifest_payload() -> dict[str, object]:
    return json.loads(_fixture_bytes("manifest.json").decode("utf-8"))


def _backend(
    *,
    remove_summary_source: bool = False,
    summary_primary_drift: bool = False,
) -> _FakeObjectBackend:
    manifest_payload = _manifest_payload()
    if remove_summary_source:
        manifest_payload["objects"]["figures"].pop("summary", None)
    if summary_primary_drift:
        manifest_payload["objects"]["figures"]["summary"]["primary_object"] = (
            "patent/originals/CN123456789A/figures/summary/figure-missing.png"
        )
        manifest_payload["objects"]["figures"]["summary"]["ordered_objects"] = [
            "patent/originals/CN123456789A/figures/summary/figure-missing.png",
            "patent/originals/CN123456789A/figures/summary/figure-backup.png",
        ]

    objects = {
        storage_service.build_patent_original_manifest_object_name(CANONICAL_PATENT_ID): json.dumps(
            manifest_payload,
            ensure_ascii=False,
        ).encode("utf-8"),
        "patent/originals/CN123456789A/structured/claims.json": _fixture_bytes("claims.json"),
        "patent/originals/CN123456789A/structured/description.json": _fixture_bytes("description.json"),
        "patent/originals/CN123456789A/structured/bibliography.json": _fixture_bytes("bibliography.json"),
        "patent/originals/CN123456789A/figures/summary/figure-001.png": b"summary-figure",
        "patent/originals/CN123456789A/figures/summary/figure-backup.png": b"backup-figure",
        "patent/originals/CN123456789A/figures/fulltext/figure-002.png": b"fulltext-figure",
        "patent/originals/CN123456789A/fulltext/original.pdf": b"%PDF-1.4\n",
    }
    return _FakeObjectBackend(objects)


def _write_local_fixture_corpus(root: Path, *, drop_field: str | None = None) -> LocalStorageBackend:
    manifest_payload = _manifest_payload()
    if drop_field:
        manifest_payload.pop(drop_field, None)
    manifest_path = root / storage_service.build_patent_original_manifest_object_name(CANONICAL_PATENT_ID)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False), encoding="utf-8")

    for name, object_name in (
        ("claims.json", "patent/originals/CN123456789A/structured/claims.json"),
        ("description.json", "patent/originals/CN123456789A/structured/description.json"),
        ("bibliography.json", "patent/originals/CN123456789A/structured/bibliography.json"),
    ):
        path = root / object_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_fixture_bytes(name))

    (root / "patent/originals/CN123456789A/figures/summary").mkdir(parents=True, exist_ok=True)
    (root / "patent/originals/CN123456789A/figures/summary/figure-001.png").write_bytes(b"summary-figure")
    (root / "patent/originals/CN123456789A/figures/fulltext").mkdir(parents=True, exist_ok=True)
    (root / "patent/originals/CN123456789A/figures/fulltext/figure-002.png").write_bytes(b"fulltext-figure")
    (root / "patent/originals/CN123456789A/fulltext").mkdir(parents=True, exist_ok=True)
    (root / "patent/originals/CN123456789A/fulltext/original.pdf").write_bytes(b"%PDF-1.4\n")
    return LocalStorageBackend(root_dir=str(root))


def test_storage_service_builds_patent_original_object_names():
    assert storage_service.build_patent_original_prefix("cn123456789a") == "patent/originals/CN123456789A"
    assert (
        storage_service.build_patent_original_manifest_object_name("cn123456789a")
        == "patent/originals/CN123456789A/manifest.json"
    )


def test_patent_original_store_loads_manifest_and_original_version():
    store_module = _load_store_module()
    store = store_module.PatentOriginalStore(backend=_backend())

    manifest = store.load_manifest(CANONICAL_PATENT_ID)

    assert manifest.canonical_patent_id == CANONICAL_PATENT_ID
    assert manifest.original_version == "2026-03-31T12:00:00Z#sha256:test"
    assert manifest.country == "CN"
    assert manifest.kind_code == "A"
    assert manifest.publication_number == "CN123456789A"
    assert manifest.application_number == "CN202410001234X"
    assert store.get_original_version(CANONICAL_PATENT_ID) == manifest.original_version


def test_patent_original_store_records_manifest_read_metric():
    store_module = _load_store_module()
    metrics = _FakeMetrics()
    store = store_module.PatentOriginalStore(backend=_backend(), metrics=metrics)

    store.load_manifest(CANONICAL_PATENT_ID)

    assert metrics.count(
        "qa_original_minio_read_total",
        service="public-service",
        source_family="patent_structured",
        result="success",
    ) == 1


def test_patent_manifest_defaults_tables_availability_false_without_object():
    payload = _manifest_payload()
    assert "tables" not in payload["availability"]
    assert "tables" not in payload["objects"]["structured"]

    manifest = PatentOriginalManifest.model_validate(payload)

    assert manifest.availability["tables"] is False
    assert "tables" not in manifest.objects.structured


def test_patent_original_store_tables_unavailable_does_not_block_original_sections():
    store_module = _load_store_module()
    manifest_payload = _manifest_payload()
    manifest_payload["availability"]["tables"] = False
    manifest_payload["objects"]["structured"].pop("tables", None)
    backend = _backend()
    backend._objects[storage_service.build_patent_original_manifest_object_name(CANONICAL_PATENT_ID)] = json.dumps(
        manifest_payload,
        ensure_ascii=False,
    ).encode("utf-8")
    store = store_module.PatentOriginalStore(backend=backend)

    claim = store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="claim", claim_number=1)

    assert claim.claim_number == 1
    assert store.load_manifest(CANONICAL_PATENT_ID).availability["tables"] is False


def test_patent_original_store_resolve_section_can_reuse_loaded_manifest():
    store_module = _load_store_module()
    backend = _CountingBackend(_backend()._objects)
    store = store_module.PatentOriginalStore(backend=backend)

    manifest = store.load_manifest(CANONICAL_PATENT_ID)
    claim = store.resolve_section(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="claim",
        claim_number=1,
        manifest=manifest,
    )

    assert claim.claim_number == 1
    assert backend.read_calls.count(storage_service.build_patent_original_manifest_object_name(CANONICAL_PATENT_ID)) == 1


def test_patent_original_store_requires_manifest_minimum_fields(tmp_path):
    store_module = _load_store_module()
    backend = _write_local_fixture_corpus(tmp_path, drop_field="country")
    store = store_module.PatentOriginalStore(backend=backend, strict_minio_only=False)

    with pytest.raises(ValidationError):
        store.load_manifest(CANONICAL_PATENT_ID)


@pytest.mark.parametrize("missing_field", ["title", "provider", "availability"])
def test_patent_original_store_requires_remaining_manifest_minimum_fields(tmp_path, missing_field: str):
    store_module = _load_store_module()
    backend = _write_local_fixture_corpus(tmp_path, drop_field=missing_field)
    store = store_module.PatentOriginalStore(backend=backend, strict_minio_only=False)

    with pytest.raises(ValidationError):
        store.load_manifest(CANONICAL_PATENT_ID)


def test_minio_not_found_helper_does_not_mask_missing_bucket():
    no_such_key = type("_FakeS3Error", (), {"code": "NoSuchKey"})()
    no_such_bucket = type("_FakeS3Error", (), {"code": "NoSuchBucket"})()

    assert MinIOStorageBackend._is_not_found_error(no_such_key) is True
    assert MinIOStorageBackend._is_not_found_error(no_such_bucket) is False


def test_patent_original_store_surfaces_backend_failures():
    store_module = _load_store_module()
    store = store_module.PatentOriginalStore(backend=_ExplodingBackend({}))

    with pytest.raises(store_module.PatentOriginalStoreBackendError):
        store.load_manifest(CANONICAL_PATENT_ID)


def test_patent_original_store_resolves_claim_and_section_fallback():
    store_module = _load_store_module()
    store = store_module.PatentOriginalStore(backend=_backend())

    claim = store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="claim", claim_number=1)
    fallback = store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="claim", claim_number=99)

    assert claim.anchor_hit is True
    assert claim.claim_number == 1
    assert claim.section_label == "权利要求1"
    assert claim.content["claim_number"] == 1
    assert fallback.anchor_hit is False
    assert fallback.section_label == "权利要求"
    assert [item["claim_number"] for item in fallback.content["claims"]] == [1, 2]


def test_patent_original_store_resolves_description_and_section_fallback():
    store_module = _load_store_module()
    store = store_module.PatentOriginalStore(backend=_backend())

    paragraph = store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="description", paragraph_id="p-002")
    fallback = store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="description", paragraph_id="missing")

    assert paragraph.anchor_hit is True
    assert paragraph.paragraph_id == "p-002"
    assert paragraph.section_label == "段落2"
    assert paragraph.content["paragraph_id"] == "p-002"
    assert fallback.anchor_hit is False
    assert fallback.section_label == "说明书"
    assert [item["paragraph_id"] for item in fallback.content["paragraphs"]] == ["p-001", "p-002"]


def test_patent_original_store_prefers_summary_figure_then_fulltext():
    store_module = _load_store_module()

    preferred = store_module.PatentOriginalStore(backend=_backend()).resolve_section(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="figure",
    )
    fallback = store_module.PatentOriginalStore(backend=_backend(remove_summary_source=True)).resolve_section(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="figure",
    )

    assert preferred.figure_source == "summary"
    assert preferred.served_object_key == "patent/originals/CN123456789A/figures/summary/figure-001.png"
    assert fallback.figure_source == "fulltext"
    assert fallback.served_object_key == "patent/originals/CN123456789A/figures/fulltext/figure-002.png"


def test_patent_original_store_raises_when_summary_primary_object_drifts():
    store_module = _load_store_module()
    store = store_module.PatentOriginalStore(backend=_backend(summary_primary_drift=True))
    figure = store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="figure")

    assert figure.figure_source == "summary"
    assert figure.served_object_key == "patent/originals/CN123456789A/figures/summary/figure-backup.png"


def test_patent_original_store_keeps_local_backend_figure_media_type(tmp_path):
    store_module = _load_store_module()
    backend = _write_local_fixture_corpus(tmp_path)
    store = store_module.PatentOriginalStore(backend=backend, strict_minio_only=False)

    figure = store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="figure")

    assert figure.media_type == "image/png"


def test_patent_original_store_rejects_local_backend_in_strict_mode(tmp_path):
    store_module = _load_store_module()
    backend = _write_local_fixture_corpus(tmp_path)
    store = store_module.PatentOriginalStore(backend=backend, strict_minio_only=True)

    with pytest.raises(store_module.PatentOriginalStoreBackendError, match="local storage backend"):
        store.load_manifest(CANONICAL_PATENT_ID)


def test_documents_service_renders_patent_original_claim_payloads(monkeypatch):
    manifest = _FakeManifest(
        canonical_patent_id=CANONICAL_PATENT_ID,
        title="一种电池热管理系统",
        provider="patent_source_x",
        original_version="version-1",
    )
    resolved = _FakeResolved(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="claim",
        section_label="权利要求1",
        original_version="version-1",
        content={
            "claim_number": 1,
            "label": "权利要求1",
            "text": "一种电池热管理系统。",
            "html": "<p>一种电池热管理系统。</p>",
        },
        anchor_hit=True,
        claim_number=1,
        paragraph_id=None,
        figure_source=None,
        served_object_key=None,
        object_key=None,
        media_type=None,
    )
    monkeypatch.setattr(
        documents_service,
        "_get_patent_original_store",
        lambda: _FakeStore(manifest=manifest, resolved=resolved),
    )

    as_json = documents_service.patent_original_view(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="claim",
        claim_number=1,
        paragraph_id=None,
        response_format=None,
        head_only=False,
        logger=None,
    )
    as_html = documents_service.patent_original_view(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="claim",
        claim_number=1,
        paragraph_id=None,
        response_format="html",
        head_only=False,
        logger=None,
    )
    as_text = documents_service.patent_original_view(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="claim",
        claim_number=1,
        paragraph_id=None,
        response_format="text",
        head_only=False,
        logger=None,
    )

    assert as_json["status_code"] == 200
    assert as_json["body"]["section_label"] == "权利要求1"
    assert as_json["body"]["content"]["claim_number"] == 1
    assert as_json["body"]["content_format"] == "json"
    assert as_json["headers"]["etag"] == '"patent-original:version-1"'
    assert as_json["headers"]["cache-control"] == "public, max-age=300"
    assert as_html["media_type"] == "text/html; charset=utf-8"
    assert "<p>一种电池热管理系统。</p>" in as_html["body"]
    assert as_text["media_type"] == "text/plain; charset=utf-8"
    assert "一种电池热管理系统。" in as_text["body"]


def test_documents_service_allows_claim_section_level_payload(monkeypatch):
    manifest = _FakeManifest(
        canonical_patent_id=CANONICAL_PATENT_ID,
        title="一种电池热管理系统",
        provider="patent_source_x",
        original_version="version-1",
    )
    resolved = _FakeResolved(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="claim",
        section_label="权利要求",
        original_version="version-1",
        content={
            "claims": [
                {"claim_number": 1, "label": "权利要求1", "text": "一种电池热管理系统。"},
                {"claim_number": 2, "label": "权利要求2", "text": "根据权利要求1所述的系统。"},
            ],
        },
        anchor_hit=False,
        claim_number=None,
        paragraph_id=None,
        figure_source=None,
        served_object_key=None,
        object_key=None,
        media_type=None,
    )
    monkeypatch.setattr(
        documents_service,
        "_get_patent_original_store",
        lambda: _FakeStore(manifest=manifest, resolved=resolved),
    )

    result = documents_service.patent_original_view(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="claim",
        claim_number=None,
        paragraph_id=None,
        response_format=None,
        head_only=False,
        logger=None,
    )

    assert result["status_code"] == 200
    assert result["body"]["content_format"] == "json"
    assert len(result["body"]["content"]["claims"]) == 2


def test_documents_service_renders_patent_original_fulltext_pdf(monkeypatch):
    manifest = _FakeManifest(
        canonical_patent_id=CANONICAL_PATENT_ID,
        title="一种电池热管理系统",
        provider="patent_source_x",
        original_version="version-2",
    )
    resolved = _FakeResolved(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="fulltext",
        section_label="全文",
        original_version="version-2",
        content=None,
        anchor_hit=False,
        claim_number=None,
        paragraph_id=None,
        figure_source=None,
        served_object_key=None,
        object_key="patent/originals/CN123456789A/fulltext/original.pdf",
        media_type="application/pdf",
    )
    monkeypatch.setattr(
        documents_service,
        "_get_patent_original_store",
        lambda: _FakeStore(manifest=manifest, resolved=resolved),
    )
    monkeypatch.setattr(
        storage_service,
        "read_object_bytes",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("fulltext should stream instead of reading full bytes")),
    )
    monkeypatch.setattr(
        storage_service,
        "iter_object_bytes",
        lambda **kwargs: iter([b"%PDF-1.4\n", b"tail"]),
    )

    result = documents_service.patent_original_view(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="fulltext",
        claim_number=None,
        paragraph_id=None,
        response_format=None,
        head_only=False,
        logger=None,
    )

    assert result["status_code"] == 200
    assert result["media_type"] == "application/pdf"
    assert b"".join(result["body_iter"]) == b"%PDF-1.4\ntail"
    assert result["headers"]["etag"] == '"patent-original:version-2"'


def test_documents_service_rejects_invalid_patent_original_query_combinations():
    with pytest.raises(AppError) as exc:
        documents_service.patent_original_view(
            canonical_patent_id=CANONICAL_PATENT_ID,
            section="abstract",
            claim_number=1,
            paragraph_id=None,
            response_format=None,
            head_only=False,
            logger=None,
        )

    assert exc.value.code == "INVALID_REQUEST"


def test_documents_service_rejects_non_positive_claim_number():
    with pytest.raises(AppError) as exc:
        documents_service.patent_original_view(
            canonical_patent_id=CANONICAL_PATENT_ID,
            section="claim",
            claim_number=0,
            paragraph_id=None,
            response_format=None,
            head_only=False,
            logger=None,
        )

    assert exc.value.code == "INVALID_REQUEST"


def test_documents_service_rejects_redirect_for_structured_sections():
    with pytest.raises(AppError) as exc:
        documents_service.patent_original_view(
            canonical_patent_id=CANONICAL_PATENT_ID,
            section="claim",
            claim_number=1,
            paragraph_id=None,
            response_format="redirect",
            head_only=False,
            logger=None,
        )

    assert exc.value.code == "INVALID_REQUEST"


def test_documents_service_rejects_non_stream_formats_for_fulltext():
    with pytest.raises(AppError) as exc:
        documents_service.patent_original_view(
            canonical_patent_id=CANONICAL_PATENT_ID,
            section="fulltext",
            claim_number=None,
            paragraph_id=None,
            response_format="json",
            head_only=False,
            logger=None,
        )

    assert exc.value.code == "INVALID_REQUEST"


def test_documents_service_maps_fulltext_read_failures_to_object_store_unavailable(monkeypatch):
    manifest = _FakeManifest(
        canonical_patent_id=CANONICAL_PATENT_ID,
        title="一种电池热管理系统",
        provider="patent_source_x",
        original_version="version-3",
    )
    resolved = _FakeResolved(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="fulltext",
        section_label="全文",
        original_version="version-3",
        content=None,
        anchor_hit=False,
        claim_number=None,
        paragraph_id=None,
        figure_source=None,
        served_object_key=None,
        object_key="patent/originals/CN123456789A/fulltext/original.pdf",
        media_type="application/pdf",
    )
    monkeypatch.setattr(
        documents_service,
        "_get_patent_original_store",
        lambda: _FakeStore(manifest=manifest, resolved=resolved),
    )
    monkeypatch.setattr(
        storage_service,
        "iter_object_bytes",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("minio unavailable")),
    )

    with pytest.raises(AppError) as exc:
        documents_service.patent_original_view(
            canonical_patent_id=CANONICAL_PATENT_ID,
            section="fulltext",
            claim_number=None,
            paragraph_id=None,
            response_format=None,
            head_only=False,
            logger=None,
        )

    assert exc.value.code == "OBJECT_STORE_UNAVAILABLE"


def test_documents_service_maps_empty_fulltext_stream_to_not_found(monkeypatch):
    manifest = _FakeManifest(
        canonical_patent_id=CANONICAL_PATENT_ID,
        title="一种电池热管理系统",
        provider="patent_source_x",
        original_version="version-4",
    )
    resolved = _FakeResolved(
        canonical_patent_id=CANONICAL_PATENT_ID,
        section="fulltext",
        section_label="全文",
        original_version="version-4",
        content=None,
        anchor_hit=False,
        claim_number=None,
        paragraph_id=None,
        figure_source=None,
        served_object_key=None,
        object_key="patent/originals/CN123456789A/fulltext/original.pdf",
        media_type="application/pdf",
    )
    monkeypatch.setattr(
        documents_service,
        "_get_patent_original_store",
        lambda: _FakeStore(manifest=manifest, resolved=resolved),
    )
    monkeypatch.setattr(
        storage_service,
        "iter_object_bytes",
        lambda **kwargs: iter(()),
    )

    with pytest.raises(AppError) as exc:
        documents_service.patent_original_view(
            canonical_patent_id=CANONICAL_PATENT_ID,
            section="fulltext",
            claim_number=None,
            paragraph_id=None,
            response_format=None,
            head_only=False,
            logger=None,
        )

    assert exc.value.code == "ORIGINAL_NOT_AVAILABLE"


def test_patent_original_store_resolves_fulltext_pdf_reference():
    store_module = _load_store_module()
    store = store_module.PatentOriginalStore(backend=_backend())

    fulltext = store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="fulltext")

    assert fulltext.object_key == "patent/originals/CN123456789A/fulltext/original.pdf"
    assert fulltext.media_type == "application/pdf"
    assert fulltext.original_version == "2026-03-31T12:00:00Z#sha256:test"


def test_documents_service_translate_document_for_doi_uses_extracted_paragraphs(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(documents_service, "_ensure_local_pdf", lambda **kwargs: pdf_path)
    monkeypatch.setattr(
        documents_service,
        "_extract_pdf_body",
        lambda **kwargs: "First paragraph. Second sentence.\n\nThird paragraph.",
    )
    monkeypatch.setattr(
        documents_service,
        "_segment_paragraphs",
        lambda full_text: ["First paragraph. Second sentence.", "Third paragraph."],
    )

    captured: dict[str, object] = {}

    def _fake_translate(*, texts, logger):
        captured["texts"] = list(texts)
        return (
            {
                "success": True,
                "translations": ["第一段。", "第二段。"],
                "count": 2,
                "cache_hits": 2,
            },
            200,
        )

    monkeypatch.setattr(documents_service, "translate", _fake_translate)

    payload, status_code = documents_service.translate_document(
        document_type="doi",
        document_id="10.1000/test",
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
    )

    assert status_code == 200
    assert captured["texts"] == ["First paragraph. Second sentence.", "Third paragraph."]
    assert payload["translated_text"] == "第一段。\n\n第二段。"
    assert payload["segment_count"] == 2
    assert payload["cache_hits"] == 2
    assert payload["cache_status"] == "hit"


def test_documents_service_translate_document_for_patent_assembles_structured_sections(monkeypatch):
    class _FakeManifest:
        canonical_patent_id = CANONICAL_PATENT_ID
        title = "一种电池热管理系统"
        original_version = "2026-03-31T12:00:00Z#sha256:test"

    class _FakeStore:
        def load_manifest(self, canonical_patent_id: str):
            assert canonical_patent_id == CANONICAL_PATENT_ID
            return _FakeManifest()

        def resolve_section(self, *, canonical_patent_id: str, section: str, manifest=None, **kwargs):
            _ = canonical_patent_id, manifest, kwargs
            if section == "abstract":
                return SimpleNamespace(content={"abstract_text": "An English patent abstract."})
            if section == "claim":
                return SimpleNamespace(content={"claims": [{"claim_number": 1, "text": "Claim one."}, {"claim_number": 2, "text": "Claim two."}]})
            if section == "description":
                return SimpleNamespace(content={"paragraphs": [{"paragraph_id": "p-001", "text": "Description paragraph one."}, {"paragraph_id": "p-002", "text": "Description paragraph two."}]})
            raise AssertionError(f"unexpected section: {section}")

    monkeypatch.setattr(documents_service, "_get_patent_original_store", lambda: _FakeStore())

    captured: dict[str, object] = {}

    def _fake_translate(*, texts, logger):
        captured["texts"] = list(texts)
        return (
            {
                "success": True,
                "translations": ["摘要译文", "权利要求译文", "说明书译文"],
                "count": 3,
                "cache_hits": 0,
            },
            200,
        )

    monkeypatch.setattr(documents_service, "translate", _fake_translate)

    payload, status_code = documents_service.translate_document(
        document_type="patent",
        document_id=CANONICAL_PATENT_ID,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
    )

    assert status_code == 200
    assert captured["texts"] == [
        "Abstract\nAn English patent abstract.",
        "Claims\n1. Claim one.\n2. Claim two.",
        "Description\nDescription paragraph one.\n\nDescription paragraph two.",
    ]
    assert payload["translated_text"] == "摘要译文\n\n权利要求译文\n\n说明书译文"
    assert payload["segment_count"] == 3
    assert payload["cache_hits"] == 0
    assert payload["cache_status"] == "miss"


def test_documents_service_stream_translate_document_emits_sse_events(monkeypatch):
    monkeypatch.setattr(
        documents_service,
        "_prepare_document_translation",
        lambda **kwargs: (
            ["Heading\n- item 1", "Paragraph body."],
            {
                "success": True,
                "document_type": "patent",
                "document_id": CANONICAL_PATENT_ID,
                "segment_count": 2,
                "truncated": False,
            },
            200,
        ),
    )

    call_index = {"value": 0}

    def _fake_translate(*, texts, logger):
        _ = logger
        idx = call_index["value"]
        call_index["value"] += 1
        payloads = [
            ({"success": True, "translations": ["标题\n- 条目1"], "cache_hits": 1, "data": {"provider": "dashscope"}}, 200),
            ({"success": True, "translations": ["段落正文。"], "cache_hits": 0, "data": {"provider": "dashscope"}}, 200),
        ]
        assert len(texts) == 1
        return payloads[idx]

    monkeypatch.setattr(documents_service, "translate", _fake_translate)

    result = documents_service.stream_translate_document(
        document_type="patent",
        document_id=CANONICAL_PATENT_ID,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
    )

    body = b"".join(result["body_iter"])

    assert result["status_code"] == 200
    assert result["media_type"] == "text/event-stream"
    assert b'"type":"start"' in body
    assert b'"type":"segment"' in body
    assert b'"type":"done"' in body
    assert b'"cache_status":"partial"' in body
