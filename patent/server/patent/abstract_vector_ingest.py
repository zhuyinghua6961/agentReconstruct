from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import chromadb

from server.patent.resource_registry import PatentResourceRegistry
from server.patent.runtime import PatentEmbeddingClient

LOGGER = logging.getLogger(__name__)

EXPECTED_EMBEDDING_DIM = 1024
DEFAULT_SUMMARY_DIR = Path("/home/cqy/专利/摘要")
DEFAULT_EMBEDDING_MODEL_PATH = Path("/home/cqy/BGE")
ABSTRACT_KIND = "abstract"


@dataclass(frozen=True)
class AbstractIngestRecord:
    patent_id: str
    document: str
    source_json: str


def normalize_patent_id(value: str) -> str:
    return str(value or "").strip().upper()


def build_abstract_metadata(*, patent_id: str, source_json: str) -> dict[str, str]:
    normalized_id = normalize_patent_id(patent_id)
    return {
        "patent_id": normalized_id,
        "kind": ABSTRACT_KIND,
        "source_json": str(source_json or f"{normalized_id}.json").strip(),
    }


def load_abstract_ingest_records(
    summary_dir: str | Path,
    *,
    archive_root: str | Path | None = None,
    require_archive: bool = True,
) -> list[AbstractIngestRecord]:
    root = Path(summary_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"summary directory not found: {root}")

    archive_ids: set[str] | None = None
    if archive_root is not None:
        archive_path = Path(archive_root).expanduser().resolve()
        if archive_path.is_dir():
            archive_ids = {
                normalize_patent_id(item.name)
                for item in archive_path.iterdir()
                if item.is_dir() and normalize_patent_id(item.name)
            }
        elif require_archive:
            raise FileNotFoundError(f"archive directory not found: {archive_path}")

    records: list[AbstractIngestRecord] = []
    skipped_empty = 0
    skipped_archive = 0
    for path in sorted(root.glob("*.json")):
        patent_id = normalize_patent_id(path.stem)
        if not patent_id:
            continue
        if archive_ids is not None and patent_id not in archive_ids:
            skipped_archive += 1
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"invalid summary JSON object: {path}")
        document = str(payload.get("generated_summary") or "").strip()
        if not document:
            skipped_empty += 1
            LOGGER.warning("skip empty generated_summary patent_id=%s path=%s", patent_id, path)
            continue
        records.append(
            AbstractIngestRecord(
                patent_id=patent_id,
                document=document,
                source_json=path.name,
            )
        )

    LOGGER.info(
        "abstract ingest records loaded summary_dir=%s total=%s skipped_empty=%s skipped_not_in_archive=%s",
        root,
        len(records),
        skipped_empty,
        skipped_archive,
    )
    return records


def _validate_embeddings(embeddings: Sequence[Sequence[float]], *, expected_dim: int = EXPECTED_EMBEDDING_DIM) -> None:
    if not embeddings:
        raise RuntimeError("embedding service returned no vectors")
    dim = len(list(embeddings[0] or []))
    if dim != expected_dim:
        raise RuntimeError(
            f"embedding dimension mismatch: expected={expected_dim} actual={dim}. "
            "Use the same embedding model as patentQA retrieval."
        )
    for index, embedding in enumerate(embeddings):
        if len(list(embedding or [])) != expected_dim:
            raise RuntimeError(f"embedding dimension mismatch at index={index}: expected={expected_dim}")


def get_or_create_abstract_collection(
    *,
    db_path: str | Path,
    collection_name: str,
    rebuild: bool = False,
) -> chromadb.Collection:
    path = Path(db_path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(path))
    if rebuild:
        try:
            client.delete_collection(collection_name)
            LOGGER.info("deleted existing collection name=%s path=%s", collection_name, path)
        except Exception:
            LOGGER.info("collection delete skipped name=%s path=%s", collection_name, path)
    return client.get_or_create_collection(collection_name)


def backup_db_path(db_path: str | Path) -> Path:
    source = Path(db_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"database path not found for backup: {source}")
    backup_path = source.with_name(f"{source.name}.bak.{int(time.time())}")
    shutil.copytree(source, backup_path)
    LOGGER.info("database backup created source=%s backup=%s", source, backup_path)
    return backup_path


def filter_existing_ids(collection: chromadb.Collection, patent_ids: list[str]) -> set[str]:
    if not patent_ids:
        return set()
    result = collection.get(ids=list(patent_ids), include=[])
    existing = {normalize_patent_id(item) for item in list(result.get("ids") or []) if str(item).strip()}
    return existing


def upsert_abstract_records(
    *,
    collection: chromadb.Collection,
    embedding_client: PatentEmbeddingClient,
    records: Sequence[AbstractIngestRecord],
    batch_size: int = 32,
    skip_existing: bool = False,
    expected_dim: int = EXPECTED_EMBEDDING_DIM,
    encode_fn: Callable[[list[str]], list[list[float]]] | None = None,
) -> dict[str, int]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    encode = encode_fn or embedding_client.encode
    stats = {"total": len(records), "embedded": 0, "written": 0, "skipped_existing": 0}

    for start in range(0, len(records), batch_size):
        batch = list(records[start : start + batch_size])
        if skip_existing:
            existing_ids = filter_existing_ids(collection, [item.patent_id for item in batch])
            if existing_ids:
                batch = [item for item in batch if item.patent_id not in existing_ids]
                stats["skipped_existing"] += len(existing_ids)
            if not batch:
                continue

        texts = [item.document for item in batch]
        embeddings = encode(texts)
        _validate_embeddings(embeddings, expected_dim=expected_dim)
        stats["embedded"] += len(batch)

        collection.upsert(
            ids=[item.patent_id for item in batch],
            embeddings=[list(item) for item in embeddings],
            documents=texts,
            metadatas=[
                build_abstract_metadata(patent_id=item.patent_id, source_json=item.source_json)
                for item in batch
            ],
        )
        stats["written"] += len(batch)
        LOGGER.info(
            "abstract ingest batch written progress=%s/%s batch_size=%s collection_count=%s",
            min(start + batch_size, len(records)),
            len(records),
            len(batch),
            collection.count(),
        )
    return stats


def discover_default_paths() -> tuple[Path, Path, Path, str]:
    registry = PatentResourceRegistry.discover()
    return (
        DEFAULT_SUMMARY_DIR,
        registry.abstract_db_path,
        registry.archive_root,
        registry.abstract_collection,
    )


def apply_ingest_embedding_env(
    *,
    model_path: str | Path | None = None,
    force_local: bool = True,
) -> dict[str, str | None]:
    resolved_path = str(Path(model_path or DEFAULT_EMBEDDING_MODEL_PATH).expanduser().resolve())
    if not Path(resolved_path).is_dir():
        raise FileNotFoundError(f"local embedding model directory not found: {resolved_path}")

    previous = {
        "EMBEDDING_MODEL_TYPE": os.environ.get("EMBEDDING_MODEL_TYPE"),
        "EMBEDDING_MODEL_PATH": os.environ.get("EMBEDDING_MODEL_PATH"),
        "EMBEDDING_API_URL": os.environ.get("EMBEDDING_API_URL"),
    }
    os.environ["EMBEDDING_MODEL_TYPE"] = "local"
    os.environ["EMBEDDING_MODEL_PATH"] = resolved_path
    if force_local:
        os.environ.pop("EMBEDDING_API_URL", None)
    LOGGER.info(
        "abstract ingest embedding env configured mode=local model_path=%s force_local=%s",
        resolved_path,
        force_local,
    )
    return previous


def create_ingest_embedding_client(
    *,
    model_path: str | Path | None = None,
    force_local: bool = True,
) -> PatentEmbeddingClient:
    apply_ingest_embedding_env(model_path=model_path, force_local=force_local)
    return PatentEmbeddingClient()


def ingest_abstract_vector_db(
    *,
    summary_dir: str | Path,
    db_path: str | Path,
    collection_name: str,
    archive_root: str | Path | None = None,
    require_archive: bool = True,
    rebuild: bool = False,
    backup_before_rebuild: bool = True,
    batch_size: int = 32,
    skip_existing: bool = False,
    dry_run: bool = False,
    embedding_model_path: str | Path | None = None,
    force_local_embedding: bool = True,
    embedding_client: PatentEmbeddingClient | None = None,
    encode_fn: Callable[[list[str]], list[list[float]]] | None = None,
) -> dict[str, Any]:
    records = load_abstract_ingest_records(
        summary_dir,
        archive_root=archive_root,
        require_archive=require_archive,
    )
    if not records:
        raise RuntimeError("no abstract ingest records found")

    resolved_model_path = str(Path(embedding_model_path or DEFAULT_EMBEDDING_MODEL_PATH).expanduser().resolve())
    result: dict[str, Any] = {
        "summary_dir": str(Path(summary_dir).expanduser().resolve()),
        "db_path": str(Path(db_path).expanduser().resolve()),
        "collection_name": collection_name,
        "record_count": len(records),
        "embedding_model_type": "local",
        "embedding_model_path": resolved_model_path,
        "dry_run": dry_run,
    }
    if dry_run:
        result["sample"] = {
            "patent_id": records[0].patent_id,
            "source_json": records[0].source_json,
            "document_preview": records[0].document[:240],
            "metadata": build_abstract_metadata(
                patent_id=records[0].patent_id,
                source_json=records[0].source_json,
            ),
        }
        return result

    db = Path(db_path).expanduser().resolve()
    if rebuild and backup_before_rebuild and db.joinpath("chroma.sqlite3").is_file():
        result["backup_path"] = str(backup_db_path(db))

    owns_client = embedding_client is None
    client = embedding_client or create_ingest_embedding_client(
        model_path=embedding_model_path,
        force_local=force_local_embedding,
    )
    try:
        collection = get_or_create_abstract_collection(
            db_path=db_path,
            collection_name=collection_name,
            rebuild=rebuild,
        )
        stats = upsert_abstract_records(
            collection=collection,
            embedding_client=client,
            records=records,
            batch_size=batch_size,
            skip_existing=skip_existing and not rebuild,
            encode_fn=encode_fn,
        )
        result["stats"] = stats
        result["collection_count"] = collection.count()
        return result
    finally:
        if owns_client:
            client.close()
