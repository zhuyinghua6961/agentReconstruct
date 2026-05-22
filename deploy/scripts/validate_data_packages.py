#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REQUIRED_PACKAGES = (
    "minio-originals",
    "fastqa-ref",
    "highthinking-ref",
    "patentqa-ref",
    "public-service-ref",
    "neo4j-literature",
    "neo4j-patent",
)


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    checked_packages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: "ValidationResult") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.counts.update(other.counts)
        self.checked_packages.extend(other.checked_packages)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"invalid json: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"json root is not an object: {path}")
    return payload


def validate_manifest_files(
    data_dir: str | Path,
    *,
    require_all: bool = False,
    expected_version: str | None = None,
) -> ValidationResult:
    root = Path(data_dir).resolve()
    result = ValidationResult()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        result.errors.append(f"manifest not found: {manifest_path}")
        return result
    try:
        manifest = _load_json_file(manifest_path)
    except RuntimeError as exc:
        result.errors.append(str(exc))
        return result
    packages = manifest.get("packages")
    if not isinstance(packages, dict):
        result.errors.append("manifest packages must be an object")
        return result
    expected_version = str(expected_version or "").strip()
    if expected_version:
        actual_version = str(manifest.get("data_version") or "").strip()
        if actual_version != expected_version:
            result.errors.append(f"manifest data_version mismatch: expected={expected_version} actual={actual_version}")
    if require_all:
        missing = sorted(set(REQUIRED_PACKAGES) - set(str(key) for key in packages))
        for name in missing:
            result.errors.append(f"manifest missing required package: {name}")
    for name, spec in sorted(packages.items()):
        result.checked_packages.append(str(name))
        if not isinstance(spec, dict):
            result.errors.append(f"manifest package spec must be an object: {name}")
            continue
        if expected_version and str(spec.get("version") or "").strip() != expected_version:
            result.errors.append(f"manifest package version mismatch for {name}: expected={expected_version} actual={spec.get('version')}")
        file_name = str(spec.get("file") or "").strip()
        expected_sha = str(spec.get("sha256") or "").strip().lower()
        if not file_name:
            result.errors.append(f"manifest package missing file: {name}")
            continue
        package_path = root / file_name
        if not package_path.is_file():
            result.errors.append(f"package file not found: {name}: {package_path}")
            continue
        if not expected_sha:
            result.errors.append(f"manifest package missing sha256: {name}")
            continue
        actual_sha = sha256_file(package_path)
        if actual_sha.lower() != expected_sha:
            result.errors.append(f"sha256 mismatch for {name}: expected={expected_sha} actual={actual_sha}")
    return result


def validate_minio_originals_tree(root: str | Path) -> ValidationResult:
    base = Path(root).resolve()
    result = ValidationResult()
    papers_dir = base / "papers"
    patent_root = base / "patent" / "originals"
    papers_count = len([path for path in papers_dir.iterdir() if path.is_file()]) if papers_dir.is_dir() else 0
    patent_dirs = [path for path in patent_root.iterdir() if path.is_dir()] if patent_root.is_dir() else []
    table_files = sorted(patent_root.glob("*/structured/tables.json")) if patent_root.is_dir() else []
    result.counts.update(
        {
            "papers": papers_count,
            "patent_dirs": len(patent_dirs),
            "patent_tables": len(table_files),
        }
    )
    if not papers_dir.is_dir():
        result.errors.append(f"missing minio originals papers dir: {papers_dir}")
    if not patent_root.is_dir():
        result.errors.append(f"missing minio originals patent dir: {patent_root}")
    for tables_path in table_files:
        patent_dir = tables_path.parents[1]
        patent_id = patent_dir.name
        tables_ref = f"patent/originals/{patent_id}/structured/tables.json"
        manifest_path = patent_dir / "manifest.json"
        if not manifest_path.is_file():
            result.errors.append(f"missing manifest for tables object: {tables_ref}")
            continue
        try:
            manifest = _load_json_file(manifest_path)
        except RuntimeError as exc:
            result.errors.append(str(exc))
            continue
        structured = dict((manifest.get("objects") or {}).get("structured") or {})
        availability = dict(manifest.get("availability") or {})
        if structured.get("tables") != tables_ref:
            result.errors.append(f"manifest does not register tables for {patent_id}")
        if "tables" not in availability:
            result.errors.append(f"manifest availability missing tables for {patent_id}")
            continue
        try:
            tables_payload = json.loads(tables_path.read_text(encoding="utf-8"))
        except Exception as exc:
            result.errors.append(f"invalid tables json for {patent_id}: {exc}")
            continue
        if isinstance(tables_payload, list) and tables_payload and availability.get("tables") is not True:
            result.errors.append(f"manifest availability.tables is not true for non-empty tables: {patent_id}")
    return result


def validate_patentqa_ref_tree(root: str | Path) -> ValidationResult:
    base = Path(root).resolve()
    result = ValidationResult()
    forbidden = sorted(
        path
        for pattern in ("*.pdf", "*.png", "*.jpg", "*.jpeg")
        for path in base.rglob(pattern)
        if path.is_file()
    )
    for path in forbidden[:50]:
        result.errors.append(f"forbidden original binary in patentqa-ref: {path.relative_to(base)}")
    if len(forbidden) > 50:
        result.errors.append(f"forbidden original binary in patentqa-ref: ... {len(forbidden) - 50} more")
    result.counts["patent_ref_forbidden_binaries"] = len(forbidden)
    archive_dirs = [path for path in base.iterdir() if path.is_dir() and path.name.startswith("__")] if base.is_dir() else []
    result.counts["patent_ref_archive_dirs"] = len(archive_dirs)
    for required in ("vector_db_patent_abstracts", "vector_db_patent_chunks"):
        sqlite_path = base / required / "chroma.sqlite3"
        if not sqlite_path.is_file():
            result.errors.append(f"missing Chroma sqlite in patentqa-ref: {sqlite_path.relative_to(base)}")
    return result


def validate_fastqa_ref_tree(root: str | Path) -> ValidationResult:
    base = Path(root).resolve()
    result = ValidationResult()
    for required in ("vector_database", "vector_database_md"):
        sqlite_path = base / required / "chroma.sqlite3"
        if not sqlite_path.is_file():
            result.errors.append(f"missing Chroma sqlite in fastqa-ref: {sqlite_path.relative_to(base)}")
    if not (base / "vector_db_topic_index.json").is_file():
        result.errors.append("missing fastqa-ref topic index: vector_db_topic_index.json")
    return result


def validate_highthinking_ref_tree(root: str | Path) -> ValidationResult:
    base = Path(root).resolve()
    result = ValidationResult()
    if not (base / "vectordb" / "chroma.sqlite3").is_file():
        result.errors.append("missing highthinking-ref Chroma sqlite: vectordb/chroma.sqlite3")
    return result


def validate_public_service_ref_tree(root: str | Path) -> ValidationResult:
    base = Path(root).resolve()
    result = ValidationResult()
    if not (base / "vector_database" / "chroma.sqlite3").is_file():
        result.errors.append("missing public-service-ref Chroma sqlite: vector_database/chroma.sqlite3")
    return result


def validate_staging_root(staging_root: str | Path) -> ValidationResult:
    root = Path(staging_root).resolve()
    result = ValidationResult()
    validators = {
        "minio-originals": validate_minio_originals_tree,
        "fastqa-ref": validate_fastqa_ref_tree,
        "highthinking-ref": validate_highthinking_ref_tree,
        "patentqa-ref": validate_patentqa_ref_tree,
        "public-service-ref": validate_public_service_ref_tree,
    }
    for name, validator in validators.items():
        package_root = root / name
        if not package_root.exists():
            result.errors.append(f"missing staging package tree: {name}")
            continue
        package_result = validator(package_root)
        result.extend(package_result)
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate highThinking offline data packages")
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parents[1] / "data"))
    parser.add_argument("--staging-root", default="")
    parser.add_argument("--skip-manifest", action="store_true")
    parser.add_argument("--require-all", action="store_true")
    parser.add_argument("--expected-version", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = ValidationResult()
    if not args.skip_manifest:
        result.extend(
            validate_manifest_files(
                args.data_dir,
                require_all=bool(args.require_all),
                expected_version=str(args.expected_version or "").strip() or None,
            )
        )
    if str(args.staging_root or "").strip():
        result.extend(validate_staging_root(args.staging_root))
    payload = {
        "ok": result.ok,
        "errors": result.errors,
        "warnings": result.warnings,
        "counts": result.counts,
        "checked_packages": result.checked_packages,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
