from __future__ import annotations

from server.patent.models import PatentRetrievalClaim
from server.patent.question_anchors import (
    extract_rule_based_anchor_terms,
    merge_anchor_terms_into_claims,
    resolve_question_anchor_terms,
)


def test_extract_rule_based_anchor_terms_keeps_material_and_process_tokens():
    question = "以铁红、二烧为铁源，葡萄糖和 PEG 为碳源，最佳混合比例是多少？"
    terms = extract_rule_based_anchor_terms(question, max_terms=12)
    assert "铁红" in terms
    assert "二烧" in terms
    assert "葡萄糖" in terms
    assert "PEG" in terms
    assert "铁源" in terms
    assert "碳源" in terms


def test_resolve_question_anchor_terms_merges_llm_and_rule_terms():
    question = "以铁红、二烧为铁源，葡萄糖和 PEG 为碳源，最佳混合比例是多少？"
    merged = resolve_question_anchor_terms(
        user_question=question,
        intent_result={
            "ok": True,
            "anchor_terms": ["铁红", "葡萄糖", "混合比例"],
        },
    )
    assert "铁红" in merged
    assert "二烧" in merged
    assert "PEG" in merged


def test_merge_anchor_terms_into_claims_prepends_missing_keywords():
    claims = [
        PatentRetrievalClaim(
            claim="混合碳源比例影响 LiFePO4 电化学性能",
            keywords=["LiFePO4"],
        )
    ]
    merged = merge_anchor_terms_into_claims(
        claims,
        ["铁红", "二烧", "葡萄糖", "PEG"],
    )
    assert merged[0].keywords[:4] == ["铁红", "二烧", "葡萄糖", "PEG"]
    assert "LiFePO4" in merged[0].keywords
