#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PATENT_ROOT = REPO_ROOT / "patent"
if str(PATENT_ROOT) not in sys.path:
    sys.path.insert(0, str(PATENT_ROOT))

from server.patent.original_assets_tooling import (  # noqa: E402
    build_patent_original_backfill_plan,
    check_patent_original_parity,
    discover_patent_source_dirs,
)


class _MinioObjectInspector:
    def __init__(self) -> None:
        try:
            from minio import Minio  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("minio dependency not installed") from exc

        endpoint = str(os.getenv("MINIO_ENDPOINT") or "").strip()
        access_key = str(os.getenv("MINIO_ACCESS_KEY") or "").strip()
        secret_key = str(os.getenv("MINIO_SECRET_KEY") or "").strip()
        bucket = str(os.getenv("MINIO_BUCKET") or "agentcode").strip() or "agentcode"
        secure = str(os.getenv("MINIO_SECURE") or "0").strip().lower() in {"1", "true", "yes"}
        if not endpoint or not access_key or not secret_key:
            raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")

        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket

    def existing_object_names(self, object_names: list[str]) -> list[str]:
        present = []
        for object_name in object_names:
            try:
                self._client.stat_object(self._bucket, object_name)
            except Exception:
                continue
            present.append(object_name)
        return present

    def read_object_bytes(self, object_name: str) -> bytes | None:
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check MinIO parity for patent original assets")
    parser.add_argument("--source-root", default=str(REPO_ROOT / "resource" / "patentQA"))
    parser.add_argument("--source-dir", action="append", default=[])
    parser.add_argument("--provider", default="patent_source_x")
    return parser.parse_args()


def _resolve_source_dirs(args: argparse.Namespace) -> list[Path]:
    if args.source_dir:
        return [Path(item).resolve() for item in args.source_dir]
    return discover_patent_source_dirs(args.source_root)


def main() -> int:
    args = _parse_args()
    source_dirs = _resolve_source_dirs(args)
    inspector = _MinioObjectInspector()

    reports = []
    has_failure = False
    for source_dir in source_dirs:
        plan = build_patent_original_backfill_plan(source_dir, provider=args.provider)
        expected = [item.object_name for item in plan.uploads]
        existing_names = inspector.existing_object_names(expected)
        report = check_patent_original_parity(
            plan,
            existing_object_names=existing_names,
            existing_object_bytes={name: inspector.read_object_bytes(name) or b"" for name in existing_names},
        )
        reports.append(
            {
                "canonical_patent_id": report.canonical_patent_id,
                "ok": report.ok,
                "missing_manifest": report.missing_manifest,
                "missing_structured_objects": report.missing_structured_objects,
                "missing_figure_objects": report.missing_figure_objects,
                "missing_fulltext_objects": report.missing_fulltext_objects,
                "drifted_objects": report.drifted_objects,
                "source_dir": str(source_dir),
            }
        )
        has_failure = has_failure or (not report.ok)

    print(json.dumps({"reports": reports}, ensure_ascii=False, indent=2))
    return 1 if has_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
