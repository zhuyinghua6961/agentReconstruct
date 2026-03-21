from app.modules.qa_kb.models import QaKbRequest
from app.modules.qa_kb.service import qa_kb_service


def test_service_wires_md_skip_and_merge_hooks():
    orchestrator = qa_kb_service._generation_orchestrator

    assert callable(orchestrator.evaluate_stage3_pdf_skip_fn)
    assert callable(orchestrator.merge_pdf_chunks_with_md_fn)


def test_phase1_placeholder_events_match_stream_contract():
    request = QaKbRequest(question='hello', route_hint='kb_qa', trace_id='trace-1')
    events = list(qa_kb_service.iter_phase1_placeholder_events(request=request))

    assert events[0]['type'] == 'metadata'
    assert events[0]['query_mode'] == 'kb_qa'
    assert events[1]['type'] == 'step'
    assert events[2]['type'] == 'error'
    assert events[2]['code'] == 'FASTQA_NOT_READY'
    assert events[3]['type'] == 'done'
    assert events[3]['trace_id'] == 'trace-1'


def test_resolve_pipeline_mode_defaults_to_new():
    resolved = qa_kb_service.resolve_pipeline_mode(
        request_use_generation_driven=False,
        env_get=lambda name, default='new': 'new',
    )

    assert resolved.mode == 'new'
    assert resolved.use_generation_driven is True


def test_iter_answer_events_returns_explicit_error_for_unsupported_legacy_mode():
    request = QaKbRequest(question='hello', route_hint='kb_qa', trace_id='trace-legacy')

    events = list(
        qa_kb_service.iter_answer_events(
            request=request,
            generation_runtime=object(),
            redis_service=None,
            sse_event=lambda payload: payload,
            env_get=lambda name, default='new': 'legacy',
        )
    )

    assert events[0]['type'] == 'error'
    assert events[0]['code'] == 'FASTQA_PIPELINE_MODE_UNSUPPORTED'
    assert events[0]['trace_id'] == 'trace-legacy'
