#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = REPO_ROOT / "resource" / "patentQA"


class TablesBackfillTarget(Protocol):
    def object_exists(self, *, object_name: str) -> bool:
        ...

    def upload_bytes(self, *, object_name: str, payload: bytes, content_type: str) -> None:
        ...

    def read_object_bytes(self, *, object_name: str) -> bytes | None:
        ...


class _MinioTablesBackfillTarget:
    def __init__(self) -> None:
        try:
            from minio import Minio  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("minio dependency not installed") from exc

        endpoint = str(os.getenv("MINIO_ENDPOINT") or "").strip()
        access_key = str(os.getenv("MINIO_ACCESS_KEY") or "").strip()
        secret_key = str(os.getenv("MINIO_SECRET_KEY") or "").strip()
        bucket = str(os.getenv("MINIO_BUCKET") or "agentcode").strip() or "agentcode"
        secure = str(os.getenv("MINIO_SECURE") or "0").strip().lower() in {"1", "true", "yes", "on"}
        if not endpoint or not access_key or not secret_key:
            raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")

        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def object_exists(self, *, object_name: str) -> bool:
        try:
            self._client.stat_object(self._bucket, object_name)
            return True
        except Exception:
            return False

    def upload_bytes(self, *, object_name: str, payload: bytes, content_type: str) -> None:
        self._client.put_object(
            self._bucket,
            object_name,
            io.BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )

    def read_object_bytes(self, *, object_name: str) -> bytes | None:
        try:
            response = self._client.get_object(self._bucket, object_name)
        except Exception:
            return None
        try:
            return response.read()
        finally:
            try:
                response.close()
            except Exception:
                pass
            try:
                response.release_conn()
            except Exception:
                pass


def discover_table_source_dirs(source_root: str | Path) -> list[Path]:
    root = Path(source_root).resolve()
    return sorted(path.parent for path in root.rglob("*_tables.json") if path.is_file())


def build_object_prefix(canonical_patent_id: str) -> str:
    return f"patent/originals/{str(canonical_patent_id).strip().upper()}"


def table_file_for_source_dir(source_dir: str | Path) -> Path:
    base = Path(source_dir).resolve()
    preferred = base / f"{base.name.strip().upper()}_tables.json"
    if preferred.is_file():
        return preferred
    matches = sorted(path for path in base.glob("*_tables.json") if path.is_file())
    if not matches:
        raise FileNotFoundError(f"tables json not found under {base}")
    return matches[0]


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _read_json_bytes(payload: bytes, *, object_name: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"invalid json object: {object_name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"json object is not a manifest: {object_name}")
    return value


def _target_object_matches(target: TablesBackfillTarget, *, object_name: str, payload: bytes) -> bool:
    try:
        existing = target.read_object_bytes(object_name=object_name)
    except Exception:
        return False
    if existing is None:
        return False
    if bytes(existing) == bytes(payload):
        return True
    try:
        return json.loads(bytes(existing).decode("utf-8")) == json.loads(bytes(payload).decode("utf-8"))
    except Exception:
        return False


def _build_updated_manifest(
    *,
    existing_manifest: dict[str, Any],
    tables_object_name: str,
    tables_payload: list[Any],
) -> dict[str, Any]:
    manifest = dict(existing_manifest)
    objects = dict(manifest.get("objects") or {})
    structured = dict(objects.get("structured") or {})
    structured["tables"] = tables_object_name
    objects["structured"] = structured
    manifest["objects"] = objects

    availability = dict(manifest.get("availability") or {})
    availability["tables"] = bool(tables_payload)
    manifest["availability"] = availability
    manifest["original_version"] = _compute_manifest_version(manifest=manifest, tables_payload=tables_payload)
    return manifest


def _compute_manifest_version(*, manifest: dict[str, Any], tables_payload: list[Any]) -> str:
    manifest_for_hash = dict(manifest)
    manifest_for_hash["original_version"] = ""
    digest = hashlib.sha256()
    digest.update(canonical_json_bytes(manifest_for_hash))
    digest.update(b"\0")
    digest.update(canonical_json_bytes(tables_payload))
    return f"sha256:{digest.hexdigest()}"


def backfill_tables_for_source_dir(
    *,
    source_dir: str | Path,
    target: TablesBackfillTarget,
    dry_run: bool = False,
) -> dict[str, Any]:
    base = Path(source_dir).resolve()
    canonical_patent_id = base.name.strip().upper()
    prefix = build_object_prefix(canonical_patent_id)
    manifest_object_name = f"{prefix}/manifest.json"
    tables_object_name = f"{prefix}/structured/tables.json"

    tables_path = table_file_for_source_dir(base)
    tables_payload_raw = json.loads(tables_path.read_text(encoding="utf-8"))
    if not isinstance(tables_payload_raw, list):
        raise RuntimeError(f"tables json is not a list: {tables_path}")
    tables_payload = list(tables_payload_raw)
    tables_bytes = canonical_json_bytes(tables_payload)

    manifest_bytes = target.read_object_bytes(object_name=manifest_object_name)
    if manifest_bytes is None:
        raise RuntimeError(f"manifest not found in MinIO: {manifest_object_name}")
    existing_manifest = _read_json_bytes(manifest_bytes, object_name=manifest_object_name)
    updated_manifest = _build_updated_manifest(
        existing_manifest=existing_manifest,
        tables_object_name=tables_object_name,
        tables_payload=tables_payload,
    )
    updated_manifest_bytes = canonical_json_bytes(updated_manifest)

    uploaded: list[str] = []
    skipped: list[str] = []
    would_upload: list[str] = []

    if _target_object_matches(target, object_name=tables_object_name, payload=tables_bytes):
        skipped.append(tables_object_name)
    else:
        would_upload.append(tables_object_name)
        if not dry_run:
            target.upload_bytes(
                object_name=tables_object_name,
                payload=tables_bytes,
                content_type="application/json",
            )
            uploaded.append(tables_object_name)

    if _target_object_matches(target, object_name=manifest_object_name, payload=updated_manifest_bytes):
        skipped.append(manifest_object_name)
    else:
        would_upload.append(manifest_object_name)
        if not dry_run:
            target.upload_bytes(
                object_name=manifest_object_name,
                payload=updated_manifest_bytes,
                content_type="application/json",
            )
            uploaded.append(manifest_object_name)

    table_count = len(tables_payload)
    row_count = 0
    for item in tables_payload:
        if isinstance(item, dict) and isinstance(item.get("rows"), list):
            row_count += len(item["rows"])

    status = "skipped" if not would_upload else "updated"
    return {
        "canonical_patent_id": canonical_patent_id,
        "source_dir": str(base),
        "tables_path": str(tables_path),
        "tables_object_name": tables_object_name,
        "manifest_object_name": manifest_object_name,
        "status": status,
        "dry_run": bool(dry_run),
        "uploaded_objects": uploaded,
        "skipped_objects": skipped,
        "would_upload_objects": would_upload,
        "table_count": table_count,
        "row_count": row_count,
        "tables_size_bytes": len(tables_bytes),
        "original_version": updated_manifest["original_version"],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill patent table JSON objects into MinIO original manifests")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    parser.add_argument("--source-dir", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    return parser.parse_args()


def _resolve_source_dirs(args: argparse.Namespace) -> list[Path]:
    if args.source_dir:
        source_dirs = [Path(item).resolve() for item in args.source_dir]
    else:
        source_dirs = discover_table_source_dirs(args.source_root)
    if args.limit and args.limit > 0:
        return source_dirs[: args.limit]
    return source_dirs


def _render_progress_bar(completed: int, total: int, *, width: int = 24) -> str:
    if total <= 0:
        return "[" + ("." * width) + "]"
    filled = min(width, int(width * (float(completed) / float(total))))
    return "[" + ("#" * filled) + ("." * (width - filled)) + "]"


def main() -> int:
    args = _parse_args()
    source_dirs = _resolve_source_dirs(args)
    target = _MinioTablesBackfillTarget()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    uploaded_count = 0
    skipped_count = 0
    would_upload_count = 0
    table_count = 0
    row_count = 0
    total = len(source_dirs)

    for index, source_dir in enumerate(source_dirs, start=1):
        current_id = Path(source_dir).name.strip().upper()
        try:
            result = backfill_tables_for_source_dir(source_dir=source_dir, target=target, dry_run=args.dry_run)
            results.append(result)
            uploaded_count += len(result.get("uploaded_objects") or [])
            skipped_count += len(result.get("skipped_objects") or [])
            would_upload_count += len(result.get("would_upload_objects") or [])
            table_count += int(result.get("table_count") or 0)
            row_count += int(result.get("row_count") or 0)
        except Exception as exc:
            failure = {"canonical_patent_id": current_id, "source_dir": str(source_dir), "error": str(exc)}
            failures.append(failure)
            results.append({**failure, "status": "failed", "dry_run": bool(args.dry_run)})
            if args.fail_fast:
                raise
        finally:
            if args.progress:
                percent = (float(index) / float(total) * 100.0) if total else 100.0
                print(
                    f"{_render_progress_bar(index, total)} {index}/{total} {percent:6.2f}% "
                    f"uploaded={uploaded_count} skipped={skipped_count} failed={len(failures)} current={current_id}",
                    file=sys.stderr,
                    flush=True,
                )

    output = {
        "dry_run": bool(args.dry_run),
        "summary": {
            "source_dirs_total": total,
            "failed_count": len(failures),
            "uploaded_object_count": uploaded_count,
            "skipped_object_count": skipped_count,
            "would_upload_object_count": would_upload_count,
            "table_count": table_count,
            "row_count": row_count,
        },
    }
    if args.summary_only:
        output["failures"] = failures
    else:
        output["results"] = results
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
