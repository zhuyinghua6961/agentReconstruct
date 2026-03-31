from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.integrations.storage.local import LocalStorageBackend
from app.integrations.storage.minio import MinIOStorageBackend
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


def test_patent_original_store_requires_manifest_minimum_fields(tmp_path):
    store_module = _load_store_module()
    backend = _write_local_fixture_corpus(tmp_path, drop_field="country")
    store = store_module.PatentOriginalStore(backend=backend)

    with pytest.raises(ValidationError):
        store.load_manifest(CANONICAL_PATENT_ID)


@pytest.mark.parametrize("missing_field", ["title", "provider", "availability"])
def test_patent_original_store_requires_remaining_manifest_minimum_fields(tmp_path, missing_field: str):
    store_module = _load_store_module()
    backend = _write_local_fixture_corpus(tmp_path, drop_field=missing_field)
    store = store_module.PatentOriginalStore(backend=backend)

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

    with pytest.raises(store_module.PatentOriginalUnavailableError):
        store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="figure")


def test_patent_original_store_keeps_local_backend_figure_media_type(tmp_path):
    store_module = _load_store_module()
    backend = _write_local_fixture_corpus(tmp_path)
    store = store_module.PatentOriginalStore(backend=backend)

    figure = store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="figure")

    assert figure.media_type == "image/png"


def test_patent_original_store_resolves_fulltext_pdf_reference():
    store_module = _load_store_module()
    store = store_module.PatentOriginalStore(backend=_backend())

    fulltext = store.resolve_section(canonical_patent_id=CANONICAL_PATENT_ID, section="fulltext")

    assert fulltext.object_key == "patent/originals/CN123456789A/fulltext/original.pdf"
    assert fulltext.media_type == "application/pdf"
    assert fulltext.original_version == "2026-03-31T12:00:00Z#sha256:test"
