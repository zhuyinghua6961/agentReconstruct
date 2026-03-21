from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.modules.retrieval.models import ChromaBootstrapResult, VectorCountSnapshot


class VectorDbClient:
    def __init__(self, *, db_path: str | Path, collection_name: str = "lfp_papers"):
        self.db_path = Path(db_path)
        self.collection_name = str(collection_name or "lfp_papers").strip() or "lfp_papers"

    def resolve_sqlite_path(self) -> Path:
        if self.db_path.suffix == ".sqlite3":
            return self.db_path
        return self.db_path / "chroma.sqlite3"

    def count(self, *, collection: Any | None = None) -> VectorCountSnapshot:
        if collection is not None:
            try:
                return VectorCountSnapshot(
                    count=int(collection.count() or 0),
                    source="collection",
                    collection_name=self.collection_name,
                    sqlite_path=self.resolve_sqlite_path(),
                )
            except Exception:
                pass
        return self.count_from_sqlite()

    def count_from_sqlite(self) -> VectorCountSnapshot:
        sqlite_path = self.resolve_sqlite_path()
        if not sqlite_path.exists():
            return VectorCountSnapshot(
                count=0,
                source="sqlite_missing",
                collection_name=self.collection_name,
                sqlite_path=sqlite_path,
            )

        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(sqlite_path), timeout=3)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(e.id)
                FROM embeddings e
                JOIN segments s ON e.segment_id = s.id
                JOIN collections c ON s.collection = c.id
                WHERE c.name = ?
                """,
                (self.collection_name,),
            )
            row = cur.fetchone()
            count = int(row[0] or 0) if row else 0
            source = "sqlite_collection"
            if count <= 0:
                cur.execute("SELECT COUNT(1) FROM embeddings")
                total_row = cur.fetchone()
                count = int(total_row[0] or 0) if total_row else 0
                source = "sqlite_total"
            return VectorCountSnapshot(
                count=count,
                source=source,
                collection_name=self.collection_name,
                sqlite_path=sqlite_path,
            )
        finally:
            if conn is not None:
                conn.close()


def bootstrap_chroma_collection(*, db_path: str | Path, collection_name: str) -> ChromaBootstrapResult:
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        return ChromaBootstrapResult(client=None, collection=None, available=False, error=str(exc))

    resolved_db_path = Path(db_path).resolve()
    try:
        client = chromadb.PersistentClient(path=str(resolved_db_path))
        collection = client.get_collection(str(collection_name or "lfp_papers"))
        return ChromaBootstrapResult(client=client, collection=collection, available=True, error=None)
    except Exception as exc:
        return ChromaBootstrapResult(client=None, collection=None, available=False, error=str(exc))
