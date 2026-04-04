#!/usr/bin/env python
from __future__ import annotations

import argparse
import io
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
    discover_patent_source_dirs,
    upload_patent_original_backfill_plan,
)


class _MinioUploadTarget:
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

    def upload_file(self, *, object_name: str, source_path: str, content_type: str) -> None:
        self._client.fput_object(self._bucket, object_name, source_path, content_type=content_type)

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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill patent original assets into MinIO")
    parser.add_argument("--source-root", default=str(REPO_ROOT / "resource" / "patentQA"))
    parser.add_argument("--source-dir", action="append", default=[])
    parser.add_argument("--provider", default="patent_source_x")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--progress", action="store_true")
    return parser.parse_args()


def _resolve_source_dirs(args: argparse.Namespace) -> list[Path]:
    if args.source_dir:
        return [Path(item).resolve() for item in args.source_dir]
    return discover_patent_source_dirs(args.source_root)


def _render_progress_bar(completed: int, total: int, *, width: int = 24) -> str:
    if total <= 0:
        return "[" + ("." * width) + "]"
    filled = min(width, int(width * (float(completed) / float(total))))
    return "[" + ("#" * filled) + ("." * (width - filled)) + "]"


def main() -> int:
    args = _parse_args()
    source_dirs = _resolve_source_dirs(args)
    target = None if args.dry_run else _MinioUploadTarget()

    results = []
    total_plans = len(source_dirs)
    total_uploaded = 0
    total_skipped = 0
    failures = 0

    for plan_index, source_dir in enumerate(source_dirs, start=1):
        plan = build_patent_original_backfill_plan(source_dir, provider=args.provider)
        try:
            result = upload_patent_original_backfill_plan(
                plan,
                target=target,  # type: ignore[arg-type]
                dry_run=args.dry_run,
                skip_existing=args.skip_existing,
            )
            result["source_dir"] = str(source_dir)
            result["manifest"] = plan.manifest
            results.append(result)
            total_uploaded += int(result.get("uploaded_count") or 0)
            total_skipped += int(result.get("skipped_count") or 0)
        except Exception as exc:
            failures += 1
            results.append(
                {
                    "canonical_patent_id": plan.canonical_patent_id,
                    "source_dir": str(source_dir),
                    "error": str(exc),
                    "dry_run": bool(args.dry_run),
                }
            )
        finally:
            if args.progress:
                percent = (float(plan_index) / float(total_plans) * 100.0) if total_plans else 100.0
                line = (
                    f"{_render_progress_bar(plan_index, total_plans)} "
                    f"{plan_index}/{total_plans} {percent:6.2f}% "
                    f"uploaded={total_uploaded} skipped={total_skipped} failed={failures} "
                    f"current={plan.canonical_patent_id}"
                )
                print(line, file=sys.stderr, flush=True)

    print(
        json.dumps(
            {
                "results": results,
                "dry_run": bool(args.dry_run),
                "skip_existing": bool(args.skip_existing),
                "summary": {
                    "plans_total": total_plans,
                    "plans_failed": failures,
                    "uploaded_count": total_uploaded,
                    "skipped_count": total_skipped,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
