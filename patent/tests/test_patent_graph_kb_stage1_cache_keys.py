from server.patent.cache_keys import build_stage1_cache_fingerprint, build_stage2_cache_fingerprint


def test_stage1_cache_fingerprint_changes_when_graph_payload_changes():
    base_context = {
        "recent_turns_for_llm": [{"role": "user", "content": "上一轮问题"}],
        "summary_for_llm": {"short_summary": "专利对比上下文"},
    }
    fingerprint_a = build_stage1_cache_fingerprint(
        question="比较两篇专利的材料角色",
        conversation_context={
            **base_context,
            "graph_kb": {
                "mode": "graph_for_rag",
                "cache_fingerprint": "graph:a",
                "stage1_context_block": "graph context a",
                "stage2_patent_candidates": ["CN1001A"],
                "stage4_fact_block": "- fact a",
            },
        },
        runtime_signature={"planning_model": "test-model"},
    )
    fingerprint_b = build_stage1_cache_fingerprint(
        question="比较两篇专利的材料角色",
        conversation_context={
            **base_context,
            "graph_kb": {
                "mode": "graph_for_rag",
                "cache_fingerprint": "graph:b",
                "stage1_context_block": "graph context b",
                "stage2_patent_candidates": ["CN1002A"],
                "stage4_fact_block": "- fact b",
            },
        },
        runtime_signature={"planning_model": "test-model"},
    )

    assert fingerprint_a != fingerprint_b


def test_stage1_cache_fingerprint_is_stable_when_only_graph_diagnostics_change():
    base_graph_payload = {
        "mode": "graph_for_rag",
        "cache_fingerprint": "graph:stable",
        "stage1_context_block": "graph context",
        "stage2_patent_candidates": ["CN1001A", "CN1002A"],
        "stage2_constraints": [{"field": "ipc_code", "operator": "eq", "value": "H01M10/0525"}],
        "stage2_entity_hints": {"organizations": ["宁德时代"]},
        "stage4_fact_block": "- fact",
        "stage4_graph_candidate_patent_ids": ["CN1001A", "CN1002A"],
    }
    fingerprint_a = build_stage1_cache_fingerprint(
        question="总结宁德时代相关专利",
        conversation_context={
            "graph_kb": {
                **base_graph_payload,
                "diagnostics": {"latency_ms": 12.3, "matched_rule": "entity_keywords"},
            }
        },
        runtime_signature={"planning_model": "test-model"},
    )
    fingerprint_b = build_stage1_cache_fingerprint(
        question="总结宁德时代相关专利",
        conversation_context={
            "graph_kb": {
                **base_graph_payload,
                "diagnostics": {"latency_ms": 99.9, "matched_rule": "entity_keywords", "note": "different"},
            }
        },
        runtime_signature={"planning_model": "test-model"},
    )

    assert fingerprint_a == fingerprint_b


def test_stage2_cache_fingerprint_changes_when_graph_stage2_candidates_change():
    fingerprint_a = build_stage2_cache_fingerprint(
        question="比较两件专利",
        retrieval_claims=[],
        retrieval_plan={},
        conversation_context={"graph_kb": {"stage2_patent_candidates": ["CN1001A"]}},
        runtime_signature={"retrieval_version": "v1"},
    )
    fingerprint_b = build_stage2_cache_fingerprint(
        question="比较两件专利",
        retrieval_claims=[],
        retrieval_plan={},
        conversation_context={"graph_kb": {"stage2_patent_candidates": ["CN1002A"]}},
        runtime_signature={"retrieval_version": "v1"},
    )

    assert fingerprint_a != fingerprint_b


def test_stage2_cache_fingerprint_ignores_graph_diagnostics():
    base_graph = {
        "stage2_patent_candidates": ["CN1001A"],
        "stage2_constraints": [{"field": "patent.id", "operator": "eq", "value": "CN1001A"}],
    }
    fingerprint_a = build_stage2_cache_fingerprint(
        question="比较两件专利",
        retrieval_claims=[],
        retrieval_plan={},
        conversation_context={"graph_kb": {**base_graph, "diagnostics": {"latency_ms": 1}}},
        runtime_signature={"retrieval_version": "v1"},
    )
    fingerprint_b = build_stage2_cache_fingerprint(
        question="比较两件专利",
        retrieval_claims=[],
        retrieval_plan={},
        conversation_context={"graph_kb": {**base_graph, "diagnostics": {"latency_ms": 99}}},
        runtime_signature={"retrieval_version": "v1"},
    )

    assert fingerprint_a == fingerprint_b
