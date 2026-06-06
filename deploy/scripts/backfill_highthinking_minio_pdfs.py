#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote


DEFAULT_REPORT = Path("/tmp/highthinking_missing_pdf_local_found.tsv")
DEFAULT_STAGE_DIR = Path("/tmp/highthinking-minio-pdf-backfill")
DEFAULT_BUCKET = "agentcode"
DEFAULT_ACCESS_KEY = "admin"
DEFAULT_SECRET_KEY = "12345678"
DEFAULT_TARGETS = (
    "local9101=http://127.0.0.1:9101",
    "local19001=http://127.0.0.1:19001",
)


@dataclass(frozen=True)
class MinioTarget:
    alias: str
    endpoint: str
    bucket: str
    access_key: str = DEFAULT_ACCESS_KEY
    secret_key: str = DEFAULT_SECRET_KEY


@dataclass(frozen=True)
class UploadItem:
    source_path: Path
    object_key: str
    target: MinioTarget
    doi: str
    match_type: str


@dataclass
class UploadPlan:
    items: list[UploadItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def unique_object_count(self) -> int:
        return len({item.object_key for item in self.items})

    @property
    def unique_source_count(self) -> int:
        return len({item.source_path for item in self.items})


def parse_target(value: str, *, bucket: str, access_key: str, secret_key: str) -> MinioTarget:
    if "=" not in value:
        raise argparse.ArgumentTypeError(f"target must use alias=endpoint: {value}")
    alias, endpoint = value.split("=", 1)
    alias = alias.strip()
    endpoint = endpoint.strip().rstrip("/")
    if not alias or not endpoint:
        raise argparse.ArgumentTypeError(f"invalid target: {value}")
    if not alias.replace("_", "").isalnum():
        raise argparse.ArgumentTypeError(f"target alias must be alphanumeric or underscore: {alias}")
    if not endpoint.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError(f"target endpoint must include http:// or https://: {endpoint}")
    return MinioTarget(alias=alias, endpoint=endpoint, bucket=bucket, access_key=access_key, secret_key=secret_key)


def _resolve_case_insensitive_path(path: Path) -> Path | None:
    if path.exists():
        return path
    parent = path.parent
    if not parent.is_dir():
        return None
    target_name = path.name.lower()
    for child in parent.iterdir():
        if child.name.lower() == target_name and child.is_file():
            return child
    return None


def _validate_object_key(key: str) -> str:
    normalized = key.strip().lstrip("/")
    if not normalized.startswith("papers/"):
        raise ValueError(f"expected_key must be under papers/: {key}")
    if "/" in normalized.removeprefix("papers/"):
        raise ValueError(f"nested paper object keys are not expected: {key}")
    if not normalized.lower().endswith(".pdf"):
        raise ValueError(f"expected_key must be a PDF object: {key}")
    return normalized


def build_upload_plan(report_path: str | Path, targets: list[MinioTarget]) -> UploadPlan:
    report = Path(report_path)
    plan = UploadPlan()
    if not report.is_file():
        raise FileNotFoundError(f"report not found: {report}")

    seen: set[tuple[str, str]] = set()
    with report.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        required = {"match_type", "doi", "expected_key", "local_path"}
        missing_columns = required - set(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(f"report missing columns: {sorted(missing_columns)}")
        for line_no, row in enumerate(reader, start=2):
            raw_key = str(row.get("expected_key") or "")
            try:
                object_key = _validate_object_key(raw_key)
            except ValueError as exc:
                plan.skipped.append(f"line {line_no}: {exc}")
                continue

            raw_path = Path(str(row.get("local_path") or ""))
            source_path = _resolve_case_insensitive_path(raw_path)
            if source_path is None:
                plan.skipped.append(f"line {line_no}: local file missing: {raw_path}")
                continue

            for target in targets:
                dedupe_key = (target.alias, object_key)
                if dedupe_key in seen:
                    plan.skipped.append(f"line {line_no}: duplicate target object skipped: {target.alias}/{object_key}")
                    continue
                seen.add(dedupe_key)
                plan.items.append(
                    UploadItem(
                        source_path=source_path,
                        object_key=object_key,
                        target=target,
                        doi=str(row.get("doi") or ""),
                        match_type=str(row.get("match_type") or ""),
                    )
                )
    return plan


def stage_unique_objects(plan: UploadPlan, stage_dir: str | Path, *, mode: str = "hardlink", clean: bool = True) -> Path:
    stage = Path(stage_dir).resolve()
    if clean and stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    staged: set[str] = set()
    for item in plan.items:
        if item.object_key in staged:
            continue
        staged.add(item.object_key)
        destination = stage / item.object_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        if mode == "copy":
            shutil.copy2(item.source_path, destination)
        elif mode == "symlink":
            destination.symlink_to(item.source_path)
        else:
            try:
                os.link(item.source_path, destination)
            except OSError:
                shutil.copy2(item.source_path, destination)
    return stage


def _mc_host_env(target: MinioTarget) -> str:
    endpoint = target.endpoint.split("://", 1)
    scheme = endpoint[0]
    host = endpoint[1]
    user = quote(target.access_key, safe="")
    password = quote(target.secret_key, safe="")
    return f"{scheme}://{user}:{password}@{host}"


def _run_docker_mc(command: list[str], *, target: MinioTarget, stage_dir: Path | None, image: str, docker_bin: str) -> None:
    docker_command = [
        docker_bin,
        "run",
        "--rm",
        "--network",
        "host",
        "-e",
        f"MC_HOST_{target.alias}={_mc_host_env(target)}",
    ]
    if stage_dir is not None:
        docker_command.extend(["-v", f"{stage_dir}:/stage:ro"])
    docker_command.extend([image, *command])
    subprocess.run(docker_command, check=True)


def upload_stage_to_targets(
    stage_dir: str | Path,
    targets: list[MinioTarget],
    *,
    image: str,
    docker_bin: str,
) -> None:
    stage = Path(stage_dir).resolve()
    for target in targets:
        remote_bucket = f"{target.alias}/{target.bucket}"
        _run_docker_mc(["mb", "--ignore-existing", remote_bucket], target=target, stage_dir=None, image=image, docker_bin=docker_bin)
        _run_docker_mc(
            ["mirror", "--overwrite", "/stage", remote_bucket],
            target=target,
            stage_dir=stage,
            image=image,
            docker_bin=docker_bin,
        )


def summarize_plan(plan: UploadPlan) -> None:
    targets = sorted({item.target.alias for item in plan.items})
    print(f"upload items: {len(plan.items)}")
    print(f"unique pdf objects: {plan.unique_object_count}")
    print(f"unique local sources: {plan.unique_source_count}")
    print(f"targets: {', '.join(targets) if targets else '(none)'}")
    print(f"skipped rows: {len(plan.skipped)}")
    if plan.skipped:
        print("first skipped rows:")
        for message in plan.skipped[:20]:
            print(f"  - {message}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill highThinking PDFs that exist locally but are missing from MinIO papers/."
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help=f"TSV report from local PDF matching, default: {DEFAULT_REPORT}")
    parser.add_argument("--stage-dir", type=Path, default=DEFAULT_STAGE_DIR, help=f"temporary staging directory, default: {DEFAULT_STAGE_DIR}")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help=f"MinIO bucket, default: {DEFAULT_BUCKET}")
    parser.add_argument("--access-key", default=DEFAULT_ACCESS_KEY, help=f"MinIO access key, default: {DEFAULT_ACCESS_KEY}")
    parser.add_argument("--secret-key", default=DEFAULT_SECRET_KEY, help="MinIO secret key")
    parser.add_argument("--target", action="append", help="MinIO target in alias=endpoint form; repeatable")
    parser.add_argument("--mc-image", default="minio/mc:latest", help="Docker image used for mc")
    parser.add_argument("--docker-bin", default="docker", help="Docker executable")
    parser.add_argument("--stage-mode", choices=("hardlink", "copy", "symlink"), default="hardlink")
    parser.add_argument("--keep-stage", action="store_true", help="keep existing stage files before staging")
    parser.add_argument("--execute", action="store_true", help="actually upload to MinIO; omit for dry-run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    raw_targets = args.target or list(DEFAULT_TARGETS)
    targets = [
        parse_target(value, bucket=args.bucket, access_key=args.access_key, secret_key=args.secret_key)
        for value in raw_targets
    ]

    plan = build_upload_plan(args.report, targets)
    summarize_plan(plan)
    if not plan.items:
        print("nothing to upload")
        return 1 if plan.skipped else 0

    if not args.execute:
        print(f"dry-run only; would stage under: {args.stage_dir.resolve()}")
        print("rerun with --execute to stage files and upload")
        return 0

    stage = stage_unique_objects(plan, args.stage_dir, mode=args.stage_mode, clean=not args.keep_stage)
    print(f"staged directory: {stage}")
    upload_stage_to_targets(stage, targets, image=args.mc_image, docker_bin=args.docker_bin)
    print("backfill upload complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
