from __future__ import annotations

from server.patent.graph_kb.metadata import build_patent_graph_route_metadata, summarize_patent_graph_slots
from server.patent.graph_kb.slots import extract_patent_graph_slots


def test_route_metadata_contains_stable_graph_keys_without_raw_rows_or_secrets():
    metadata = build_patent_graph_route_metadata(
        attempted=True,
        mode="graph_for_rag",
        route_family="hybrid",
        strategy="parametric",
        template_id="",
        path_id="compare_patents_process_steps",
        fingerprint="patent-graph:abc",
        row_count=2,
        evidence_quality={"has_rows": True, "raw_rows": [{"secret": "x"}]},
        downgrade_reason="",
        stage2_behavior="filter_applied",
    )

    assert metadata["graph_pipeline_version"] == "v2"
    assert metadata["graph_kb_attempted"] is True
    assert metadata["graph_kb_mode"] == "graph_for_rag"
    assert metadata["graph_kb_route_family"] == "hybrid"
    assert metadata["graph_kb_strategy"] == "parametric"
    assert metadata["graph_kb_path_id"] == "compare_patents_process_steps"
    assert metadata["graph_kb_fingerprint"] == "patent-graph:abc"
    assert metadata["graph_kb_row_count"] == 2
    assert metadata["graph_kb_evidence_quality"] == {"has_rows": True}
    assert metadata["graph_kb_stage2_behavior"] == "filter_applied"
    assert "secret" not in str(metadata)


def test_summarize_slots_is_bounded():
    slots = extract_patent_graph_slots("比较 CN100355122C 和 CN100369314C 的工艺步骤差异")

    summary = summarize_patent_graph_slots(slots)

    assert summary["patent_ids"] == ["CN100355122C", "CN100369314C"]
    assert summary["counts"]["patent_ids"] == 2
    assert summary["asks_compare"] is True

