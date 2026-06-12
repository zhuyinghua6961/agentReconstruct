from __future__ import annotations

from server.patent.retrieval_scoring import (
    aggregate_patent_candidates,
    derive_patent_retrieval_intent,
)


def test_patent_level_scoring_prefers_metric_section_evidence_over_generic_abstract():
    hits = [
        {
            "patent_id": "CN123456789A",
            "document": "LiFePO4 放电容量 156 mAh/g，循环 500 次。",
            "section_type": "description",
            "score": 0.75,
            "channel": "chunk_vector_candidate",
            "metadata": {},
        },
        {
            "patent_id": "US20240001234A1",
            "document": "Cathode material with good performance",
            "section_type": "abstract",
            "score": 0.85,
            "channel": "abstract_vector",
            "metadata": {},
        },
    ]
    intent = derive_patent_retrieval_intent(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claims=[],
        graph_context=None,
    )

    ranked = aggregate_patent_candidates(hits=hits, intent=intent)

    assert ranked[0].patent_id == "CN123456789A"
    assert "metric_threshold_match" in ranked[0].reasons


def test_c_graph_candidates_are_bounded_boosts_not_hard_filters():
    hits = [
        {
            "patent_id": "CN123456789A",
            "document": "Graph seeded patent with weak generic battery text",
            "section_type": "abstract",
            "score": 0.40,
            "channel": "graph_candidate",
            "metadata": {},
        },
        {
            "patent_id": "US20240001234A1",
            "document": "LiFePO4 放电容量 156 mAh/g 实施例",
            "section_type": "description",
            "score": 0.80,
            "channel": "chunk_vector_global",
            "metadata": {},
        },
    ]
    intent = derive_patent_retrieval_intent(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claims=[],
        graph_context={"stage2_patent_candidates": ["CN123456789A"]},
    )

    ranked = aggregate_patent_candidates(hits=hits, intent=intent)

    assert {item.patent_id for item in ranked} == {"CN123456789A", "US20240001234A1"}
    assert ranked[0].patent_id == "US20240001234A1"
    assert any("graph_candidate_boost" in item.reasons for item in ranked if item.patent_id == "CN123456789A")


def test_c_explicit_patent_ids_remain_hard_constraints():
    hits = [
        {"patent_id": "CN123456789A", "document": "explicit id evidence", "section_type": "claim", "score": 0.3, "channel": "exact_id", "metadata": {}},
        {"patent_id": "US20240001234A1", "document": "better generic evidence", "section_type": "description", "score": 0.9, "channel": "chunk_vector_global", "metadata": {}},
    ]
    intent = derive_patent_retrieval_intent(
        user_question="请总结 CN123456789A",
        retrieval_claims=[],
        graph_context={"stage2_patent_candidates": ["US20240001234A1"]},
    )

    ranked = aggregate_patent_candidates(hits=hits, intent=intent)

    assert [item.patent_id for item in ranked] == ["CN123456789A"]


def test_c_graph_seed_claim_ids_can_be_excluded_from_explicit_constraints():
    hits = [
        {"patent_id": "CN123456789A", "document": "generic graph evidence", "section_type": "abstract", "score": 0.3, "channel": "graph_candidate", "metadata": {}},
        {"patent_id": "US20240001234A1", "document": "LiFePO4 压实密度 2.6 g/cm3 制备", "section_type": "description", "score": 0.8, "channel": "chunk_vector_global", "metadata": {}},
    ]
    intent = derive_patent_retrieval_intent(
        user_question="如何制备高压实磷酸铁锂",
        retrieval_claims=[{"claim": "优先核验图谱候选专利与结构化实体线索", "keywords": ["CN123456789A"]}],
        graph_context={"stage2_patent_candidates": ["CN123456789A"]},
        explicit_patent_ids=[],
    )

    ranked = aggregate_patent_candidates(hits=hits, intent=intent)

    assert {item.patent_id for item in ranked} == {"CN123456789A", "US20240001234A1"}
    assert ranked[0].patent_id == "US20240001234A1"


def test_table_metric_boost_changes_ranking_only_for_candidate_pool():
    hits = [
        {
            "patent_id": "CN123456789A",
            "document": "LiFePO4 embodiment table candidate",
            "section_type": "description",
            "score": 0.60,
            "channel": "chunk_vector_candidate",
            "metadata": {
                "table_supplements": [
                    {
                        "table_title": "表1 放电容量",
                        "rows": [{"材料": "LFP", "放电容量": "156 mAh/g"}],
                    }
                ]
            },
        },
        {
            "patent_id": "US20240001234A1",
            "document": "LiFePO4 generic high capacity abstract",
            "section_type": "abstract",
            "score": 0.75,
            "channel": "abstract_vector",
            "metadata": {},
        },
    ]
    intent = derive_patent_retrieval_intent(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claims=[],
        graph_context=None,
    )

    ranked = aggregate_patent_candidates(hits=hits, intent=intent, table_metric_boost_enabled=True)

    assert ranked[0].patent_id == "CN123456789A"
    assert "table_metric_match" in ranked[0].reasons


def test_table_metric_boost_supports_density_unit():
    hits = [
        {
            "patent_id": "CN123456789A",
            "document": "LFP density table candidate",
            "section_type": "description",
            "score": 0.50,
            "channel": "chunk_vector_candidate",
            "metadata": {
                "table_supplements": [
                    {
                        "table_title": "表1 压实密度",
                        "rows": [{"材料": "LFP", "压实密度": "2.45 g/cm3"}],
                    }
                ]
            },
        },
        {
            "patent_id": "US20240001234A1",
            "document": "LFP generic density abstract",
            "section_type": "abstract",
            "score": 0.75,
            "channel": "abstract_vector",
            "metadata": {},
        },
    ]
    intent = derive_patent_retrieval_intent(
        user_question="找 LFP 压实密度超过 2.4 g/cm3 的专利",
        retrieval_claims=[],
        graph_context=None,
    )

    ranked = aggregate_patent_candidates(hits=hits, intent=intent, table_metric_boost_enabled=True)

    assert ranked[0].patent_id == "CN123456789A"
    assert "table_metric_match" in ranked[0].reasons
