#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from server.patent.abstract_vector_ingest import (
    DEFAULT_EMBEDDING_MODEL_PATH,
    DEFAULT_SUMMARY_DIR,
    discover_default_paths,
    ingest_abstract_vector_db,
)


def _build_parser() -> argparse.ArgumentParser:
    default_summary_dir, default_db_path, default_archive_root, default_collection = discover_default_paths()
    parser = argparse.ArgumentParser(
        description=(
            "Build patentQA abstract Chroma DB from generated_summary JSON files. "
            "Defaults to local BGE embedding at /home/cqy/BGE."
        )
    )
    parser.add_argument(
        "--embedding-model-path",
        default=str(DEFAULT_EMBEDDING_MODEL_PATH),
        help=f"Local BGE model directory (default: {DEFAULT_EMBEDDING_MODEL_PATH})",
    )
    parser.add_argument(
        "--summary-dir",
        default=str(default_summary_dir),
        help=f"Directory containing {{patent_id}}.json summary files (default: {default_summary_dir})",
    )
    parser.add_argument(
        "--db-path",
        default=str(default_db_path),
        help=f"Chroma persist directory for patent abstracts (default: {default_db_path})",
    )
    parser.add_argument(
        "--archive-root",
        default=str(default_archive_root),
        help="Patent archive root used to validate patent_id coverage",
    )
    parser.add_argument(
        "--collection-name",
        default=default_collection,
        help=f"Chroma collection name (default: {default_collection})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Embedding/upsert batch size (default: 32)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete and recreate the abstract collection before ingest",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip automatic backup when --rebuild is used",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip records whose patent_id already exists in the collection",
    )
    parser.add_argument(
        "--allow-missing-archive",
        action="store_true",
        help="Do not require archive_root to exist or contain each patent_id",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only scan summary files and print a sample record; do not embed or write",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        result = ingest_abstract_vector_db(
            summary_dir=args.summary_dir,
            db_path=args.db_path,
            collection_name=args.collection_name,
            archive_root=args.archive_root,
            require_archive=not args.allow_missing_archive,
            rebuild=bool(args.rebuild),
            backup_before_rebuild=not args.no_backup,
            batch_size=max(1, int(args.batch_size)),
            skip_existing=bool(args.skip_existing),
            dry_run=bool(args.dry_run),
            embedding_model_path=args.embedding_model_path,
        )
    except Exception as exc:
        logging.getLogger(__name__).error("abstract vector ingest failed: %s", exc)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
