from app.modules.graph_kb.models import GraphRagPayload
from app.modules.qa_kb.models import QaKbExecutionMetadata, QaKbExecutionResult, QaKbRequest
from app.modules.qa_kb.streaming import iter_result_events, iter_text_chunks


def test_qakb_request_defaults_match_phase1_contract():
    request = QaKbRequest(question="what is lfp?")
    assert request.question == "what is lfp?"
    assert request.request_use_generation_driven is False
    assert request.route_hint == "kb_qa"
    assert request.n_results_per_claim == 10
    assert request.active_stream_count is None
    assert request.trace_id == ""


def test_qakb_request_carries_graph_evidence_without_mutating_conversation_context():
    payload = GraphRagPayload(stage1_context_block="doi:10.1000/test", cache_fingerprint="graph:abc")
    request = QaKbRequest(question="q", graph_evidence=payload)

    assert request.graph_evidence is payload


def test_iter_text_chunks_splits_long_text():
    chunks = list(iter_text_chunks("abcdefghij", chunk_size=4))
    assert chunks == ["abcd", "efgh", "ij"]


def test_iter_result_events_emits_metadata_content_done_sequence():
    result = QaKbExecutionResult(
        success=True,
        final_answer="abcdef",
        metadata=QaKbExecutionMetadata(
            route="kb_qa",
            query_mode="kb_qa",
            pipeline_mode="new",
            use_generation_driven=True,
            doi_count=2,
            chunk_count=4,
            source_count=3,
            stage_timings_ms={"stage1": 12.5},
        ),
        raw={"synthesis_result": {"references": [{"doi": "10.1/demo"}, {"doi": "10.2/demo"}]}},
    )

    events = list(iter_result_events(result=result, sse_event=lambda payload: payload, chunk_size=2))

    assert events[0]["type"] == "metadata"
    assert events[0]["query_mode"] == "kb_qa"
    assert events[1] == {"type": "content", "content": "ab"}
    assert events[2] == {"type": "content", "content": "cd"}
    assert events[3] == {"type": "content", "content": "ef"}
    assert events[4]["type"] == "done"
    assert events[4]["doi_count"] == 2
    assert events[4]["chunk_count"] == 4
    assert events[4]["source_count"] == 3
    assert events[4]["references"] == ["10.1/demo", "10.2/demo"]
    assert events[4]["reference_objects"] == [{"doi": "10.1/demo"}, {"doi": "10.2/demo"}]
    assert events[4]["reference_links"][0]["pdf_url"] == "/api/v1/view_pdf/10.1/demo"
    assert events[4]["metadata"]["route"] == "kb_qa"
