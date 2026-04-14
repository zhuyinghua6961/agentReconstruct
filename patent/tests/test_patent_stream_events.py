from __future__ import annotations

import importlib

import pytest


def _load_stream_events_module():
    try:
        return importlib.import_module("server.patent.stream_events")
    except ModuleNotFoundError as exc:
        pytest.fail(f"server.patent.stream_events must exist: {exc}")


def test_preview_streaming_is_only_enabled_for_hybrid_routes():
    stream_events = _load_stream_events_module()

    assert stream_events.preview_streaming_enabled(
        options={"patent_stream_capability": "preview_v1"},
        route="hybrid_qa",
        source_scope="pdf+kb",
    ) is True
    assert stream_events.preview_streaming_enabled(
        options={"patent_stream_capability": "preview_v1"},
        route="pdf_qa",
        source_scope="pdf",
    ) is False
    assert stream_events.preview_streaming_enabled(
        options={"patent_stream_capability": "preview_v1"},
        route="kb_qa",
        source_scope="kb",
    ) is False


def test_patent_content_stream_state_rejects_delta_before_start():
    stream_events = _load_stream_events_module()
    state = stream_events.PatentContentStreamState()

    with pytest.raises(ValueError, match="delta"):
        state.observe(
            {
                "type": "content",
                "content": "chunk",
                "content_role": "preview",
                "content_source": "pdf",
                "content_stream_id": "pdf:primary",
                "content_phase": "delta",
            }
        )


def test_patent_content_stream_state_accepts_ordered_preview_then_final_snapshot():
    stream_events = _load_stream_events_module()
    state = stream_events.PatentContentStreamState()

    state.observe(
        {
            "type": "content",
            "content": "chunk-1",
            "content_role": "preview",
            "content_source": "pdf",
            "content_stream_id": "pdf:primary",
            "content_phase": "start",
        }
    )
    state.observe(
        {
            "type": "content",
            "content": "chunk-2",
            "content_role": "preview",
            "content_source": "pdf",
            "content_stream_id": "pdf:primary",
            "content_phase": "delta",
        }
    )
    state.observe(
        {
            "type": "content",
            "content": "",
            "content_role": "preview",
            "content_source": "pdf",
            "content_stream_id": "pdf:primary",
            "content_phase": "end",
        }
    )
    state.observe(
        {
            "type": "content",
            "content": "final answer",
            "content_role": "final",
            "content_source": "hybrid",
            "content_phase": "snapshot",
        }
    )


def test_patent_content_stream_state_accepts_multipart_final_stream():
    stream_events = _load_stream_events_module()
    state = stream_events.PatentContentStreamState()

    state.observe(
        {
            "type": "content",
            "content": "final answer",
            "content_role": "final",
            "content_source": "hybrid",
            "content_phase": "start",
        }
    )
    state.observe(
        {
            "type": "content",
            "content": " more",
            "content_role": "final",
            "content_source": "hybrid",
            "content_phase": "delta",
        }
    )
    state.observe(
        {
            "type": "content",
            "content": "",
            "content_role": "final",
            "content_source": "hybrid",
            "content_phase": "end",
        }
    )


def test_structured_content_router_rejects_preview_after_final_starts():
    stream_events = _load_stream_events_module()
    forwarded: list[dict[str, object]] = []
    router = stream_events.PatentStructuredContentRouter(callback=forwarded.append)
    final_emitter = router.final_emitter(content_source="hybrid")
    preview_emitter = router.preview_emitter(content_source="pdf", content_stream_id="pdf:primary")

    final_emitter("final answer")

    with pytest.raises(ValueError, match="preview"):
        preview_emitter("late preview")

    assert forwarded[0]["type"] == "content"
