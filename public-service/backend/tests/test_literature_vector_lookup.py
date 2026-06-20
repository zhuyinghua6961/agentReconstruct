from __future__ import annotations

from app.modules.documents.literature_vector_lookup import (
    _build_from_highthinking_result,
    lookup_literature_from_vector_dbs,
)


class _FakeCollection:
    def __init__(self, *, mapping: dict[str, dict]):
        self.mapping = mapping

    def get(self, *, where, include=None):
        _ = include
        doi = where.get("doi")
        payload = self.mapping.get(doi)
        if not payload:
            return {"ids": [], "metadatas": [], "documents": []}
        return payload


def test_lookup_literature_from_highthinking_chunks():
    collection = _FakeCollection(
        mapping={
            "10.1000/test": {
                "ids": ["a", "b"],
                "metadatas": [
                    {"doi": "10.1000/test", "title": "Paper A", "section_name": "Intro", "chunk_index": 0},
                    {"doi": "10.1000/test", "title": "Paper A", "section_name": "Methods", "chunk_index": 1},
                ],
                "documents": ["intro text", "methods text"],
            }
        }
    )
    payload = lookup_literature_from_vector_dbs(
        doi="10.1000/test",
        fastqa_collection=None,
        highthinking_collection=collection,
    )
    assert payload is not None
    assert payload["title"] == "Paper A"
    assert payload["source"] == "highthinking_chroma"
    assert "intro text" in payload["content"]
    assert "methods text" in payload["content"]


def test_lookup_literature_prefers_fastqa_metadata_with_highthinking_content():
    fastqa = _FakeCollection(
        mapping={
            "10.1000/test": {
                "ids": ["f1"],
                "metadatas": [
                    {
                        "doi": "10.1000/test",
                        "title": "Fast Title",
                        "authors": "Alice",
                        "journal": "J",
                        "date": "2024",
                        "abstract": "Fast abstract",
                    }
                ],
                "documents": ["short"],
            }
        }
    )
    highthinking = _FakeCollection(
        mapping={
            "10.1000/test": {
                "ids": ["h1"],
                "metadatas": [{"doi": "10.1000/test", "title": "HT Title", "section_name": "Body", "chunk_index": 0}],
                "documents": ["much longer highthinking body content"],
            }
        }
    )
    payload = lookup_literature_from_vector_dbs(
        doi="10.1000/test",
        fastqa_collection=fastqa,
        highthinking_collection=highthinking,
    )
    assert payload is not None
    assert payload["title"] == "Fast Title"
    assert payload["authors"] == "Alice"
    assert payload["abstract"] == "Fast abstract"
    assert "much longer highthinking body content" in payload["content"]
    assert payload["source"] == "fastqa_chroma+highthinking_chroma"


def test_build_from_highthinking_result_sorts_chunks():
    payload = _build_from_highthinking_result(
        doi="10.1000/test",
        result={
            "metadatas": [
                {"title": "Paper", "section_name": "Second", "chunk_index": 2},
                {"title": "Paper", "section_name": "First", "chunk_index": 1},
            ],
            "documents": ["second", "first"],
        },
    )
    assert payload["content"].index("First") < payload["content"].index("Second")
