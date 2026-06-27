from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.patent.abstract_vector_ingest import (
    EXPECTED_EMBEDDING_DIM,
    apply_ingest_embedding_env,
    build_abstract_metadata,
    load_abstract_ingest_records,
    upsert_abstract_records,
)


def test_apply_ingest_embedding_env_uses_local_bge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    model_dir = tmp_path / "BGE"
    model_dir.mkdir()
    monkeypatch.delenv("EMBEDDING_API_URL", raising=False)
    apply_ingest_embedding_env(model_path=model_dir, force_local=True)
    import os

    assert os.environ["EMBEDDING_MODEL_TYPE"] == "local"
    assert os.environ["EMBEDDING_MODEL_PATH"] == str(model_dir.resolve())
    assert "EMBEDDING_API_URL" not in os.environ


def test_build_abstract_metadata_matches_runtime_contract():
    metadata = build_abstract_metadata(patent_id="cn100355122c", source_json="CN100355122C.json")
    assert metadata == {
        "patent_id": "CN100355122C",
        "kind": "abstract",
        "source_json": "CN100355122C.json",
    }


def test_load_abstract_ingest_records_from_summary_dir(tmp_path: Path):
    summary_dir = tmp_path / "summary"
    archive_root = tmp_path / "archive"
    summary_dir.mkdir()
    archive_root.mkdir()
    (archive_root / "CN100355122C").mkdir()
    (archive_root / "WO2026037031A1").mkdir()
    (summary_dir / "CN100355122C.json").write_text(
        json.dumps({"generated_summary": "一种制备掺杂磷酸铁锂的方法。"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (summary_dir / "WO2026037031A1.json").write_text(
        json.dumps({"generated_summary": "一种二次电池。"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (summary_dir / "CN999999999A.json").write_text(
        json.dumps({"generated_summary": "不在归档中的专利。"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (summary_dir / "CN100370644C.json").write_text(
        json.dumps({"generated_summary": ""}, ensure_ascii=False),
        encoding="utf-8",
    )

    records = load_abstract_ingest_records(summary_dir, archive_root=archive_root, require_archive=True)
    assert [item.patent_id for item in records] == ["CN100355122C", "WO2026037031A1"]
    assert records[0].source_json == "CN100355122C.json"
    assert "磷酸铁锂" in records[0].document


def test_upsert_abstract_records_writes_expected_shape(tmp_path: Path):
    chromadb = pytest.importorskip("chromadb")
    db_path = tmp_path / "vector_db_patent_abstracts"
    collection = chromadb.PersistentClient(path=str(db_path)).get_or_create_collection("patent_abstracts")

    class _FakeEmbeddingClient:
        def encode(self, texts: list[str]) -> list[list[float]]:
            return [[float(index), 1.0] + [0.0] * (EXPECTED_EMBEDDING_DIM - 2) for index, _ in enumerate(texts)]

    stats = upsert_abstract_records(
        collection=collection,
        embedding_client=_FakeEmbeddingClient(),  # type: ignore[arg-type]
        records=load_abstract_ingest_records(
            _write_summary_dir(tmp_path / "summary"),
            archive_root=None,
            require_archive=False,
        ),
        batch_size=8,
        encode_fn=_FakeEmbeddingClient().encode,
    )
    assert stats["written"] == 1
    stored = collection.get(ids=["CN100355122C"], include=["documents", "metadatas", "embeddings"])
    assert stored["documents"] == ["一种制备掺杂磷酸铁锂的方法。"]
    assert stored["metadatas"] == [
        {
            "patent_id": "CN100355122C",
            "kind": "abstract",
            "source_json": "CN100355122C.json",
        }
    ]
    assert len(stored["embeddings"][0]) == EXPECTED_EMBEDDING_DIM


def _write_summary_dir(path: Path) -> Path:
    path.mkdir()
    (path / "CN100355122C.json").write_text(
        json.dumps({"generated_summary": "一种制备掺杂磷酸铁锂的方法。"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path
