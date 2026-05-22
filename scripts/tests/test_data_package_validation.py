from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "deploy" / "scripts" / "validate_data_packages.py"
    spec = importlib.util.spec_from_file_location("validate_data_packages", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_validate_manifest_files_accepts_matching_sha256(tmp_path: Path) -> None:
    module = _load_module()
    package = tmp_path / "sample.tar.zst"
    package.write_bytes(b"sample payload")
    manifest = {
        "data_version": "2026-05-19",
        "packages": {
            "sample": {
                "file": package.name,
                "version": "2026-05-19",
                "sha256": module.sha256_file(package),
            }
        }
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = module.validate_manifest_files(tmp_path, expected_version="2026-05-19")

    assert result.errors == []
    assert result.checked_packages == ["sample"]


def test_validate_manifest_files_reports_version_mismatch(tmp_path: Path) -> None:
    module = _load_module()
    package = tmp_path / "sample.tar.zst"
    package.write_bytes(b"sample payload")
    manifest = {
        "data_version": "2026-05-18",
        "packages": {
            "sample": {
                "file": package.name,
                "version": "2026-05-18",
                "sha256": module.sha256_file(package),
            }
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = module.validate_manifest_files(tmp_path, expected_version="2026-05-19")

    assert any("data_version mismatch" in item for item in result.errors)
    assert any("package version mismatch" in item for item in result.errors)


def test_validate_manifest_files_reports_bad_sha256(tmp_path: Path) -> None:
    module = _load_module()
    package = tmp_path / "sample.tar.zst"
    package.write_bytes(b"sample payload")
    manifest = {"packages": {"sample": {"file": package.name, "sha256": "0" * 64}}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = module.validate_manifest_files(tmp_path)

    assert any("sha256 mismatch" in item for item in result.errors)


def test_validate_minio_originals_tree_requires_tables_manifest_registration(tmp_path: Path) -> None:
    module = _load_module()
    root = tmp_path / "minio-originals"
    patent_dir = root / "patent" / "originals" / "CNTEST"
    tables_key = "patent/originals/CNTEST/structured/tables.json"
    (root / "papers").mkdir(parents=True)
    (root / "papers" / "paper.pdf").write_bytes(b"pdf")
    (patent_dir / "structured").mkdir(parents=True)
    (patent_dir / "structured" / "tables.json").write_text("[]", encoding="utf-8")
    (patent_dir / "manifest.json").write_text(
        json.dumps(
            {
                "objects": {"structured": {"tables": tables_key}},
                "availability": {"tables": True},
            }
        ),
        encoding="utf-8",
    )

    result = module.validate_minio_originals_tree(root)

    assert result.errors == []
    assert result.counts["papers"] == 1
    assert result.counts["patent_tables"] == 1


def test_validate_patentqa_ref_tree_rejects_original_binaries(tmp_path: Path) -> None:
    module = _load_module()
    root = tmp_path / "patentqa-ref"
    archive = root / "__archive" / "CNTEST"
    archive.mkdir(parents=True)
    (archive / "著录项目.json").write_text("{}", encoding="utf-8")
    (archive / "CNTEST.pdf").write_bytes(b"pdf")

    result = module.validate_patentqa_ref_tree(root)

    assert any("forbidden original binary" in item for item in result.errors)
