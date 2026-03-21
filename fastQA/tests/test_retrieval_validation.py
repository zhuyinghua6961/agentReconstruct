from __future__ import annotations

from types import SimpleNamespace

from app.modules.generation_pipeline.retrieval_validation import validate_retrieval_relevance


class _Logger(SimpleNamespace):
    def __init__(self):
        super().__init__(info=lambda *a, **k: None, warning=lambda *a, **k: None, debug=lambda *a, **k: None)


def test_validate_retrieval_relevance_stringifies_list_docs_when_hit_is_kept():
    result = validate_retrieval_relevance(
        search_results={
            "documents": [
                ["Fe2P", "good"],
                ["noise", "text"],
            ],
            "metadatas": [
                {"doi": "10.1/a"},
                {"doi": "10.1/b"},
            ],
            "distances": [0.1, 1.4],
        },
        query="LiFePO4 Fe2P 抑制",
        claim_text="Fe2P 杂相抑制",
        logger=_Logger(),
    )

    assert result["documents"] == ["Fe2P good"]
    assert result["metadatas"] == [{"doi": "10.1/a"}]
    assert result["distances"] == [0.1]


def test_validate_retrieval_relevance_keeps_top3_by_distance_on_fallback():
    result = validate_retrieval_relevance(
        search_results={
            "documents": [
                ["bad", "snippet"],
                ["also", "bad"],
                ["still", "bad"],
                ["worst", "case"],
            ],
            "metadatas": [
                {"doi": "10.1/a"},
                {"doi": "10.1/b"},
                {"doi": "10.1/c"},
                {"doi": "10.1/d"},
            ],
            "distances": [1.4, 1.3, 1.2, 1.1],
        },
        query="unrelated words",
        claim_text="another unrelated claim",
        logger=_Logger(),
    )

    assert len(result["documents"]) == 3
    assert result["documents"] == [["worst", "case"], ["still", "bad"], ["also", "bad"]]
    assert result["metadatas"] == [{"doi": "10.1/d"}, {"doi": "10.1/c"}, {"doi": "10.1/b"}]
    assert result["distances"] == [1.1, 1.2, 1.3]
