"""Contract tests for upstream error helpers."""

from server.utils.upstream_errors import UpstreamCallError, build_sse_error_event


def test_build_sse_error_event_includes_extended_fields():
    err = UpstreamCallError.llm_unavailable(stage="decompose", status_code=503)
    event = build_sse_error_event(err, trace_id="trace-x")
    assert event["type"] == "error"
    assert event["code"] == "LLM_UNAVAILABLE"
    assert event["status_code"] == 503
    assert event["failure_stage"] == "decompose"
    assert event["component"] == "llm"
    assert event["retriable"] is True
    assert event["trace_id"] == "trace-x"
