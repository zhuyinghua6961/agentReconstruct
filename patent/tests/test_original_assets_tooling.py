from __future__ import annotations

import json
import hashlib
from importlib import import_module
from pathlib import Path

import pytest


CANONICAL_PATENT_ID = "CN123456789A"


def _load_tooling_module():
    try:
        return import_module("server.patent.original_assets_tooling")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing original assets tooling module: {exc}")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_source_archive(root: Path) -> Path:
    source_dir = root / CANONICAL_PATENT_ID
    source_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        source_dir / "权利要求.json",
        {
            "status": True,
            "error_code": 0,
            "data": [
                {
                    "pn": CANONICAL_PATENT_ID,
                    "claim_count": 2,
                    "claims": [
                        {
                            "claim_text": (
                                '<div class="indep-clm" num="1"><seg-refi>一种电池热管理系统。</seg-refi></div>'
                                '<div class="indep-clm" num="2"><seg-refi>根据权利要求1所述的系统。</seg-refi></div>'
                            )
                        }
                    ],
                }
            ],
        },
    )
    _write_json(
        source_dir / "说明书.json",
        {
            "status": True,
            "error_code": 0,
            "data": [
                {
                    "pn": CANONICAL_PATENT_ID,
                    "description": [
                        {
                            "text": (
                                '技术领域<b class="d_n">[0001]</b>本发明涉及电池热管理。'
                                '<b class="d_n">[0002]</b>系统包括冷却回路。'
                            )
                        }
                    ],
                }
            ],
        },
    )
    _write_json(
        source_dir / "著录项目.json",
        {
            "status": True,
            "error_code": 0,
            "data": [
                {
                    "pn": CANONICAL_PATENT_ID,
                    "bibliographic_data": {
                        "publication_reference": {
                            "country": "CN",
                            "kind": "A",
                            "doc_number": "123456789",
                        },
                        "application_reference": {
                            "doc_number": "CN202410001234X",
                        },
                        "invention_title": [
                            {"lang": "CN", "text": "一种电池热管理系统", "data_format": "original"}
                        ],
                        "abstracts": [
                            {"lang": "CN", "text": "本发明公开了一种电池热管理系统。", "data_format": "original"}
                        ],
                    },
                }
            ],
        },
    )

    (source_dir / f"{CANONICAL_PATENT_ID}.pdf").write_bytes(b"%PDF-1.4\n")

    summary_dir = source_dir / "summary_figures"
    summary_dir.mkdir()
    (summary_dir / "figure-010.png").write_bytes(b"summary-10")
    (summary_dir / "figure-002.png").write_bytes(b"summary-02")

    fulltext_dir = source_dir / "fulltext_figures"
    fulltext_dir.mkdir()
    (fulltext_dir / "figure-003.png").write_bytes(b"fulltext-03")

    return source_dir


def _write_relative_aux_figure_archive(root: Path) -> Path:
    source_dir = _write_source_archive(root)
    for path in (source_dir / "summary_figures").rglob("*"):
        if path.is_file():
            path.unlink()
    (source_dir / "summary_figures").rmdir()
    for path in (source_dir / "fulltext_figures").rglob("*"):
        if path.is_file():
            path.unlink()
    (source_dir / "fulltext_figures").rmdir()

    _write_json(
        source_dir / "说明书.json",
        {
            "status": True,
            "error_code": 0,
            "data": [
                {
                    "pn": CANONICAL_PATENT_ID,
                    "description": [
                        {
                            "text": (
                                '技术领域<b class="d_n">[0001]</b>本发明涉及电池热管理。'
                                '<img src="../shared/fulltext/page-1.png">'
                            )
                        }
                    ],
                }
            ],
        },
    )
    _write_json(
        source_dir / f"{CANONICAL_PATENT_ID}_tables.json",
        [
            {
                "_source_image": "../shared/tables/page-2.png",
            }
        ],
    )
    (root / "shared" / "fulltext").mkdir(parents=True, exist_ok=True)
    (root / "shared" / "fulltext" / "page-1.png").write_bytes(b"aux-fulltext")
    (root / "shared" / "tables").mkdir(parents=True, exist_ok=True)
    (root / "shared" / "tables" / "page-2.png").write_bytes(b"aux-table")
    return source_dir


def _write_duplicate_basename_archive(root: Path) -> Path:
    source_dir = _write_source_archive(root)
    fulltext_dir = source_dir / "fulltext_figures"
    for path in sorted(fulltext_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    (fulltext_dir / "a").mkdir()
    (fulltext_dir / "b").mkdir()
    (fulltext_dir / "a" / "page-1.png").write_bytes(b"fulltext-a")
    (fulltext_dir / "b" / "page-1.png").write_bytes(b"fulltext-b")
    return source_dir


def _write_external_duplicate_aux_archive(root: Path) -> tuple[Path, Path, Path]:
    source_dir = _write_source_archive(root)
    for path in (source_dir / "summary_figures").rglob("*"):
        if path.is_file():
            path.unlink()
    (source_dir / "summary_figures").rmdir()
    for path in sorted((source_dir / "fulltext_figures").rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()

    _write_json(
        source_dir / "说明书.json",
        {
            "status": True,
            "error_code": 0,
            "data": [
                {
                    "pn": CANONICAL_PATENT_ID,
                    "description": [
                        {
                            "text": (
                                '技术领域<b class="d_n">[0001]</b>本发明涉及电池热管理。'
                                '<img src="../shared/a/page-1.png">'
                                '<img src="../shared/b/page-1.png">'
                            )
                        }
                    ],
                }
            ],
        },
    )
    first = root / "shared" / "a" / "page-1.png"
    second = root / "shared" / "b" / "page-1.png"
    first.parent.mkdir(parents=True, exist_ok=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"external-a")
    second.write_bytes(b"external-b")
    return source_dir, first.resolve(), second.resolve()


def test_backfill_plan_uses_expected_object_key_layout(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir = _write_source_archive(tmp_path)

    plan = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")

    object_names = {item.object_name for item in plan.uploads}
    prefix = f"patent/originals/{CANONICAL_PATENT_ID}"
    assert object_names == {
        f"{prefix}/structured/claims.json",
        f"{prefix}/structured/description.json",
        f"{prefix}/structured/bibliography.json",
        f"{prefix}/figures/summary/figure-002.png",
        f"{prefix}/figures/summary/figure-010.png",
        f"{prefix}/figures/fulltext/figure-003.png",
        f"{prefix}/fulltext/original.pdf",
        f"{prefix}/manifest.json",
    }
    assert plan.manifest["objects"]["structured"]["claims"] == f"{prefix}/structured/claims.json"
    assert plan.manifest["objects"]["fulltext_pdf"] == f"{prefix}/fulltext/original.pdf"


def test_backfill_plan_generates_stable_original_version_and_manifest(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir = _write_source_archive(tmp_path)

    first = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")
    second = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")

    assert first.original_version
    assert first.original_version == first.manifest["original_version"]
    assert first.original_version == second.original_version
    assert first.manifest["title"] == "一种电池热管理系统"
    assert first.manifest["availability"] == {
        "claims": True,
        "description": True,
        "abstract": True,
        "figure": True,
        "fulltext_pdf": True,
    }


def test_backfill_plan_original_version_changes_when_manifest_only_fields_change(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir = _write_source_archive(tmp_path)

    first = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")
    second = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_y")

    assert first.original_version != second.original_version


def test_backfill_plan_chooses_deterministic_primary_figure_object(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir = _write_source_archive(tmp_path)

    plan = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")

    assert plan.manifest["objects"]["figures"]["summary"]["primary_object"] == (
        "patent/originals/CN123456789A/figures/summary/figure-002.png"
    )
    assert plan.manifest["objects"]["figures"]["summary"]["ordered_objects"] == [
        "patent/originals/CN123456789A/figures/summary/figure-002.png",
        "patent/originals/CN123456789A/figures/summary/figure-010.png",
    ]
    assert plan.manifest["objects"]["figures"]["fulltext"]["primary_object"] == (
        "patent/originals/CN123456789A/figures/fulltext/figure-003.png"
    )


def test_parity_check_reports_missing_structured_figure_and_pdf_objects(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir = _write_source_archive(tmp_path)

    plan = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")
    existing_object_names = {
        f"patent/originals/{CANONICAL_PATENT_ID}/manifest.json",
        f"patent/originals/{CANONICAL_PATENT_ID}/structured/description.json",
        f"patent/originals/{CANONICAL_PATENT_ID}/structured/bibliography.json",
        f"patent/originals/{CANONICAL_PATENT_ID}/figures/summary/figure-010.png",
    }

    report = tooling.check_patent_original_parity(plan, existing_object_names=existing_object_names)

    assert report.ok is False
    assert f"patent/originals/{CANONICAL_PATENT_ID}/structured/claims.json" in report.missing_structured_objects
    assert f"patent/originals/{CANONICAL_PATENT_ID}/figures/summary/figure-002.png" in report.missing_figure_objects
    assert f"patent/originals/{CANONICAL_PATENT_ID}/fulltext/original.pdf" in report.missing_fulltext_objects


def test_parity_check_reports_drifted_manifest_and_structured_objects(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir = _write_source_archive(tmp_path)
    plan = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")

    object_bytes = {}
    for item in plan.uploads:
        payload = item.read_bytes()
        if item.object_name.endswith("/manifest.json"):
            payload = payload.replace(b"patent_source_x", b"patent_source_y")
        if item.object_name.endswith("/structured/claims.json"):
            payload = payload.replace("\u70ed\u7ba1\u7406".encode("utf-8"), "\u6e29\u63a7".encode("utf-8"), 1)
        object_bytes[item.object_name] = payload

    report = tooling.check_patent_original_parity(
        plan,
        existing_object_names=set(object_bytes),
        existing_object_bytes=object_bytes,
    )

    assert report.ok is False
    assert f"patent/originals/{CANONICAL_PATENT_ID}/manifest.json" in report.drifted_objects
    assert f"patent/originals/{CANONICAL_PATENT_ID}/structured/claims.json" in report.drifted_objects


def test_backfill_plan_resolves_relative_aux_figure_paths_from_source_dir(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir = _write_relative_aux_figure_archive(tmp_path)

    plan = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")
    first = (tmp_path / "shared" / "fulltext" / "page-1.png").resolve()
    second = (tmp_path / "shared" / "tables" / "page-2.png").resolve()
    first_hash = hashlib.sha256(str(first).encode("utf-8")).hexdigest()[:12]
    second_hash = hashlib.sha256(str(second).encode("utf-8")).hexdigest()[:12]

    figure_uploads = sorted(
        item.object_name
        for item in plan.uploads
        if "/figures/" in item.object_name
    )
    assert figure_uploads == sorted(
        [
            f"patent/originals/CN123456789A/figures/fulltext/external/{first_hash}/page-1.png",
            f"patent/originals/CN123456789A/figures/fulltext/external/{second_hash}/page-2.png",
        ]
    )


def test_backfill_plan_keeps_duplicate_figure_basenames_in_distinct_object_keys(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir = _write_duplicate_basename_archive(tmp_path)

    plan = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")
    fulltext_objects = plan.manifest["objects"]["figures"]["fulltext"]["ordered_objects"]

    assert fulltext_objects == [
        "patent/originals/CN123456789A/figures/fulltext/a/page-1.png",
        "patent/originals/CN123456789A/figures/fulltext/b/page-1.png",
    ]


def test_backfill_plan_keeps_external_duplicate_aux_figure_basenames_distinct(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir, first, second = _write_external_duplicate_aux_archive(tmp_path)

    plan = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")
    fulltext_objects = [
        item.object_name
        for item in plan.uploads
        if "/figures/fulltext/" in item.object_name and item.object_name.endswith("/page-1.png")
    ]

    expected_first = hashlib.sha256(str(first).encode("utf-8")).hexdigest()[:12]
    expected_second = hashlib.sha256(str(second).encode("utf-8")).hexdigest()[:12]
    assert sorted(fulltext_objects) == sorted(
        [
            f"patent/originals/CN123456789A/figures/fulltext/external/{expected_first}/page-1.png",
            f"patent/originals/CN123456789A/figures/fulltext/external/{expected_second}/page-1.png",
        ]
    )


def test_upload_backfill_plan_can_skip_existing_objects_and_report_progress(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir = _write_source_archive(tmp_path)
    plan = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")

    class _FakeUploadTarget:
        def __init__(self, existing: set[str]) -> None:
            self._existing = set(existing)
            self.uploaded: list[str] = []

        def object_exists(self, *, object_name: str) -> bool:
            return object_name in self._existing

        def upload_bytes(self, *, object_name: str, payload: bytes, content_type: str) -> None:
            _ = payload, content_type
            self.uploaded.append(object_name)

        def upload_file(self, *, object_name: str, source_path: str, content_type: str) -> None:
            _ = source_path, content_type
            self.uploaded.append(object_name)

    progress_events: list[dict[str, object]] = []
    target = _FakeUploadTarget(
        {
            f"patent/originals/{CANONICAL_PATENT_ID}/structured/claims.json",
            f"patent/originals/{CANONICAL_PATENT_ID}/fulltext/original.pdf",
        }
    )

    result = tooling.upload_patent_original_backfill_plan(
        plan,
        target=target,
        skip_existing=True,
        progress_callback=progress_events.append,
    )

    assert result["uploaded_count"] == len(plan.uploads) - 2
    assert result["skipped_count"] == 2
    assert f"patent/originals/{CANONICAL_PATENT_ID}/structured/claims.json" not in target.uploaded
    assert f"patent/originals/{CANONICAL_PATENT_ID}/fulltext/original.pdf" not in target.uploaded
    assert progress_events[-1]["completed"] == len(plan.uploads)
    assert progress_events[-1]["uploaded"] == len(plan.uploads) - 2
    assert progress_events[-1]["skipped"] == 2


def test_upload_backfill_plan_overwrites_drifted_existing_objects_when_skip_existing(tmp_path: Path):
    tooling = _load_tooling_module()
    source_dir = _write_source_archive(tmp_path)
    plan = tooling.build_patent_original_backfill_plan(source_dir, provider="patent_source_x")

    class _FakeUploadTarget:
        def __init__(self, existing_bytes: dict[str, bytes]) -> None:
            self._existing_bytes = dict(existing_bytes)
            self.uploaded: list[str] = []

        def object_exists(self, *, object_name: str) -> bool:
            return object_name in self._existing_bytes

        def read_object_bytes(self, *, object_name: str) -> bytes | None:
            return self._existing_bytes.get(object_name)

        def upload_bytes(self, *, object_name: str, payload: bytes, content_type: str) -> None:
            _ = content_type
            self.uploaded.append(object_name)
            self._existing_bytes[object_name] = payload

        def upload_file(self, *, object_name: str, source_path: str, content_type: str) -> None:
            _ = content_type
            self.uploaded.append(object_name)
            self._existing_bytes[object_name] = Path(source_path).read_bytes()

    existing_bytes = {}
    for item in plan.uploads:
        payload = item.read_bytes()
        if item.object_name.endswith("/structured/claims.json"):
            payload = payload.replace("\u70ed\u7ba1\u7406".encode("utf-8"), "\u6e29\u63a7".encode("utf-8"), 1)
        existing_bytes[item.object_name] = payload

    target = _FakeUploadTarget(existing_bytes)
    result = tooling.upload_patent_original_backfill_plan(
        plan,
        target=target,
        skip_existing=True,
    )

    assert f"patent/originals/{CANONICAL_PATENT_ID}/structured/claims.json" in target.uploaded
    assert result["uploaded_count"] >= 1
