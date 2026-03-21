from app.modules.qa_pdf.service import pdf_qa_service


def test_pdf_service_streams_single_pdf_answer_events():
    events = list(
        pdf_qa_service.iter_route_answer_events(
            question="总结这篇文献",
            pdf_path="/tmp/10.1_demo.pdf",
            pdf_content="This is a valid PDF content block. " * 10,
            performance_mode="balanced",
            allow_kb_verification=False,
            turn_mode="file_only",
            selected_pdf_files=[{"file_name": "10.1_demo.pdf", "local_path": "/tmp/10.1_demo.pdf"}],
            agent=None,
            executor=None,
            timeout_error_cls=None,
            sse_event=lambda event: event,
            answer_from_pdf_fn=lambda *_args, **_kwargs: iter(["答", "案"]),
            clean_answer_for_frontend=lambda text, **_kwargs: text,
            filter_literature_markers_for_streaming=lambda text: text,
            log_qa_interaction=lambda **_kwargs: None,
            cache_key_mode="pdf_qa",
            cache_key_question="总结这篇文献",
            cache_set_fn=lambda *_args, **_kwargs: None,
            load_pdf_content_fn=lambda **_kwargs: ("unused", None),
        )
    )

    assert events[0]["type"] == "metadata"
    assert events[1]["type"] == "thinking"
    assert any(event.get("type") == "content" and event.get("content") == "答" for event in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["route"] == "pdf_qa"
    assert events[-1]["references"] == ["10.1/demo"]
