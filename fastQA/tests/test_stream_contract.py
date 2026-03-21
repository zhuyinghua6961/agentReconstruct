from app.services.stream_contract import AskStreamTap, normalize_stream_event


def test_normalize_thinking_event_into_step():
    payload = normalize_stream_event({"type": "thinking", "message": "阶段1：检索候选文献"})
    assert payload["type"] == "step"
    assert payload["status"] == "processing"
    assert payload["title"] == "阶段1"


def test_ask_stream_tap_collects_done_summary():
    tap = AskStreamTap()
    events = [
        {"type": "metadata", "query_mode": "kb_qa"},
        {"type": "content", "content": "hello "},
        {"type": "content", "content": "world"},
        {"type": "done", "route": "kb_qa", "references": [{"doi": "x"}], "trace_id": "t-1"},
    ]
    wrapped = list(tap.wrap(events))
    assert wrapped[-1]["type"] == "done"
    assert tap.summary.assistant_content == "hello world"
    assert tap.summary.query_mode == "kb_qa"
    assert tap.summary.route == "kb_qa"
    assert tap.summary.trace_id == "t-1"
    assert tap.summary.reference_objects == [{"doi": "x"}]
