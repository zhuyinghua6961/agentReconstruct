"""Tests for Stage2 retrieval snippet merge into Stage3/3.5 evidence."""

from __future__ import annotations

import pytest

from app.modules.generation_pipeline.stage2_evidence_merge import (
    extract_stage2_retrieval_chunks_by_doi,
    merge_stage2_chunks_into_pdf_chunks,
    maybe_merge_stage2_retrieval_evidence,
    resolve_doi_bucket,
)


def test_resolve_doi_bucket_matches_slash_and_underscore():
    hints = ["10.1016_j.foo", "10.1021/other"]
    assert resolve_doi_bucket("10.1016/j.foo", hints) == "10.1016_j.foo"
    assert resolve_doi_bucket("10.1016_j.foo", hints) == "10.1016_j.foo"
    assert resolve_doi_bucket("10.1021/other", hints) == "10.1021/other"
    assert resolve_doi_bucket("10.9999/nope", hints) is None


def test_extract_sorts_by_distance_and_caps_per_doi():
    dois = ["10.1016_j.a", "10.1016_j.b"]
    retrieval = {
        "documents": [
            "far-a-text-" + "x" * 80,
            "near-a-text-" + "y" * 80,
            "near-b-text-" + "z" * 80,
            "far-b-text-" + "w" * 80,
        ],
        "metadatas": [
            {"doi": "10.1016/j.a"},
            {"doi": "10.1016/j.a"},
            {"doi": "10.1016/j.b"},
            {"doi": "10.1016/j.b"},
        ],
        "distances": [0.9, 0.1, 0.15, 0.85],
    }
    out = extract_stage2_retrieval_chunks_by_doi(
        retrieval_results=retrieval,
        dois_ordered=dois,
        max_chunks_total=10,
        max_chunks_per_doi=1,
    )
    assert len(out["10.1016_j.a"]) == 1
    assert out["10.1016_j.a"][0]["text"].startswith("near-a-text")
    assert len(out["10.1016_j.b"]) == 1
    assert out["10.1016_j.b"][0]["text"].startswith("near-b-text")
    assert out["10.1016_j.a"][0]["source"] == "stage2_retrieval"


def test_extract_skips_dois_not_in_gate_list():
    retrieval = {
        "documents": ["only-other-" + "x" * 80],
        "metadatas": [{"doi": "10.9999/j.other"}],
        "distances": [0.05],
    }
    out = extract_stage2_retrieval_chunks_by_doi(
        retrieval_results=retrieval,
        dois_ordered=["10.1016_j.a"],
        max_chunks_total=5,
        max_chunks_per_doi=2,
    )
    assert out == {}


def test_merge_prepends_stage2_and_dedupes():
    s2 = {
        "10.1016_j.a": [
            {"text": "alpha claim " + "x" * 80, "source": "stage2_retrieval"},
        ]
    }
    pdf = {
        "10.1016_j.a": [
            {"text": "alpha claim " + "x" * 80, "source": "pdf"},
            {"text": "beta pdf " + "y" * 80, "source": "pdf"},
        ]
    }
    merged = merge_stage2_chunks_into_pdf_chunks(pdf_chunks=pdf, stage2_by_doi=s2)
    texts = [c["text"][:30] for c in merged["10.1016_j.a"]]
    assert texts[0].startswith("alpha claim")
    assert merged["10.1016_j.a"][0]["source"] == "stage2_retrieval"
    assert len(merged["10.1016_j.a"]) == 2


def test_maybe_merge_respects_disabled(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_RETRIEVAL_EVIDENCE_MERGE_ENABLED", "0")
    pdf = {"10.1016_j.a": [{"text": "pdf " + "p" * 80}]}
    retrieval = {
        "documents": ["s2 " + "s" * 80],
        "metadatas": [{"doi": "10.1016/j.a"}],
        "distances": [0.01],
    }
    logger = type("L", (), {"info": lambda *a, **k: None})()
    out = maybe_merge_stage2_retrieval_evidence(
        retrieval_results=retrieval,
        dois_ordered=["10.1016_j.a"],
        pdf_chunks=pdf,
        logger=logger,
    )
    assert len(out["10.1016_j.a"]) == 1
    assert out["10.1016_j.a"][0]["text"].startswith("pdf")


@pytest.fixture(autouse=True)
def clear_stage2_evidence_env(monkeypatch):
    """Avoid cross-test pollution from QA_STAGE2_* env vars."""
    monkeypatch.delenv("QA_STAGE2_RETRIEVAL_EVIDENCE_MERGE_ENABLED", raising=False)
    monkeypatch.delenv("QA_STAGE2_RETRIEVAL_EVIDENCE_MAX_TOTAL", raising=False)
    monkeypatch.delenv("QA_STAGE2_RETRIEVAL_EVIDENCE_MAX_PER_DOI", raising=False)
