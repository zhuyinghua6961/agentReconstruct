"""Tests for Stage2 focus-aware DOI ordering before source gate."""

from __future__ import annotations

import pytest

from app.modules.qa_kb.orchestrators.generation import select_source_dois_for_evidence
from app.modules.generation_pipeline.stage2_focus_policy import (
    expand_focus_evidence_terms,
    lexical_focus_hit_count,
    rerank_dois_for_focus_evidence,
)


@pytest.fixture(autouse=True)
def _clear_focus_env(monkeypatch):
    monkeypatch.delenv("QA_STAGE2_FOCUS_POLICY_ENABLED", raising=False)
    monkeypatch.delenv("QA_FOCUS_POLICY_RELAX_MIN_FOCUS_DOIS", raising=False)
    monkeypatch.delenv("QA_FOCUS_POLICY_MAX_AUX_DEGENERATE_DOIS", raising=False)


def test_expand_focus_synonyms_and_user_hints():
    out = expand_focus_evidence_terms(
        query_focus_terms=["球形颗粒"],
        user_question="如何制备高压实型磷酸铁锂",
    )
    assert "高压实型" in out or "高压实" in out
    assert "球形颗粒" in out
    # 「高压实型」未定指标：并列拉上粉体振实与电极压实侧代表词，避免仅偏向单侧文献轴。
    assert "压实密度" in out or "极片压实" in out or "极片辊压" in out
    assert "振实密度" in out or "tap density" in out


def test_expand_focus_respects_explicit_compaction_question():
    out = expand_focus_evidence_terms(
        query_focus_terms=[],
        user_question="磷酸铁锂极片的压实密度一般是多少？",
    )
    assert any(x in "".join(out) for x in ("压实密度", "极片压实", "电极压实"))


def test_lexical_focus_counts_distinct_terms():
    text = "本研究给出了振实密度与辊压孔隙率测试结果"
    n = lexical_focus_hit_count(
        text=text,
        expanded_terms=["振实密度", "辊压", "孔隙率", "无关词"],
    )
    assert n == 3


def test_focus_policy_moves_focus_backed_doi_forward(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FOCUS_POLICY_ENABLED", "1")
    monkeypatch.setenv("QA_FOCUS_POLICY_RELAX_MIN_FOCUS_DOIS", "0")
    monkeypatch.setenv("QA_FOCUS_POLICY_MAX_AUX_DEGENERATE_DOIS", "99")
    dois = ["10.1/aux", "10.2/core"]
    claim_map = {
        "碳缺陷化学表征": {
            "documents": ["pure lifepo4 nanoparticle conductivity"],
            "metadatas": [{"doi": "10.1/aux", "title": "LiFePO4 defects"}],
            "distances": [0.2],
        },
        "球形颗粒致密化可提高振实密度与粉末堆积": {
            "documents": ["tap density compaction spherical lfp pellets"],
            "metadatas": [{"doi": "10.2/core", "title": "high tap density spherical LFP"}],
            "distances": [0.25],
        },
    }
    retrieval = {"claim_to_results": claim_map}

    reranked, audit = rerank_dois_for_focus_evidence(
        ordered_dois=dois,
        retrieval_results=retrieval,
        user_question="制备高压实型磷酸铁锂",
        query_focus_terms=["球形颗粒"],
    )

    assert audit.get("enabled") is True
    assert reranked[0] == "10.2/core"


def test_select_source_keeps_comparison_round_robin_stable(monkeypatch):
    monkeypatch.setenv("QA_SOURCE_DOI_MAX_PER_COMPARISON_OBJECT", "2")
    monkeypatch.setenv("QA_SOURCE_DOI_MAX_TOTAL", "5")
    retrieval_results = {
        "comparison_groups": [
            {"label": "磷酸铁", "doi_candidates": ["10.1/a", "10.1/b", "10.1/c"]},
            {"label": "草酸亚铁", "doi_candidates": ["10.2/a", "10.2/b", "10.2/c"]},
            {"label": "铁红", "doi_candidates": ["10.3/a", "10.3/b", "10.3/c"]},
        ],
        "claim_to_results": {},
    }

    monkeypatch.setenv("QA_STAGE2_FOCUS_POLICY_ENABLED", "0")

    selected = select_source_dois_for_evidence(
        retrieval_results=retrieval_results,
        dois=["10.1/a", "10.1/b", "10.1/c", "10.2/a", "10.2/b", "10.2/c", "10.3/a", "10.3/b", "10.3/c"],
        user_question="比较三种铁源",
        query_focus_terms=["比较"],
    )

    assert selected == ["10.1/a", "10.2/a", "10.3/a", "10.1/b", "10.2/b"]
