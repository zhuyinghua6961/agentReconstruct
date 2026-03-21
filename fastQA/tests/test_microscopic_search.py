from __future__ import annotations

from app.modules.microscopic_search import normalize_chroma_query_result, run_semantic_search


class _Embedding:
    def encode(self, values):
        assert values == ["lfp"]
        return type("Array", (), {"tolist": lambda self: [[0.1, 0.2]]})()


class _Collection:
    def count(self):
        return 4

    def query(self, **_kwargs):
        return {
            "documents": [["doc1", "doc2"]],
            "distances": [[0.1, 0.2]],
            "metadatas": [[{"doi": "10.1/a"}, {"doi": "10.2/b"}]],
            "ids": [["id1", "id2"]],
        }


def test_normalize_chroma_query_result_flattens_single_query_payload():
    normalized = normalize_chroma_query_result(
        {
            "documents": [["doc1"]],
            "distances": [[0.1]],
            "metadatas": [[{"doi": "10.1/a"}]],
            "ids": [["id1"]],
        }
    )

    assert normalized["documents"] == ["doc1"]
    assert normalized["distances"] == [0.1]


def test_run_semantic_search_returns_trimmed_payload():
    result = run_semantic_search(
        user_question="lfp",
        n_results=1,
        embedding_model=_Embedding(),
        collection=_Collection(),
        translator=None,
        translate=False,
    )

    assert result["documents"] == ["doc1"]
    assert result["metadatas"] == [{"doi": "10.1/a"}]
    assert result["distances"] == [0.1]


def test_run_semantic_search_handles_embedding_failure():
    class _BadEmbedding:
        def encode(self, _values):
            raise RuntimeError("bad embedding")

    result = run_semantic_search(
        user_question="lfp",
        n_results=1,
        embedding_model=_BadEmbedding(),
        collection=_Collection(),
        translator=None,
        translate=False,
    )

    assert result["documents"] == []


def test_run_semantic_search_marks_empty_rerank_output_as_fallback():
    result = run_semantic_search(
        user_question="lfp",
        n_results=2,
        embedding_model=type("Model", (), {"encode": lambda self, values: type("Encoded", (), {"tolist": lambda self: [[0.1, 0.2]]})()})(),
        collection=type(
            "Collection",
            (),
            {
                "count": lambda self: 5,
                "query": lambda self, **kwargs: {
                    "documents": [["doc-1", "doc-2", "doc-3"]],
                    "metadatas": [[{"doi": "10.1/a"}, {"doi": "10.1/b"}, {"doi": "10.1/c"}]],
                    "distances": [[0.1, 0.2, 0.3]],
                    "ids": [["a", "b", "c"]],
                },
            },
        )(),
        translator=None,
        translate=False,
        use_rerank=True,
        rerank_candidates=5,
        rerank_fn=lambda **kwargs: {"documents": [], "metadatas": [], "rerank_scores": []},
    )

    assert result["documents"] == ["doc-1", "doc-2", "doc-3"]
    assert result["rerank"]["enabled"] is True
    assert result["rerank"]["fallback"] is True
    assert result["rerank"]["reason"] == "empty_rerank_output"
