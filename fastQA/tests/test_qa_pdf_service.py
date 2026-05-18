import itertools
from types import SimpleNamespace
import traceback

from app.modules.qa_pdf import engine as pdf_engine_module
from app.modules.qa_pdf.engine import answer_from_pdf
from app.modules.qa_pdf.service import pdf_qa_service


class _NoopLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _DelayedFirstChunkLLM:
    def invoke(self, _payload):
        return "fallback answer"

    def stream(self, _payload):
        import time

        time.sleep(0.05)
        yield SimpleNamespace(content="delayed answer")


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


def test_single_pdf_route_does_not_set_first_token_timeout_by_default():
    captured: dict[str, object] = {}

    def _answer_from_pdf(*_args, **kwargs):
        captured.update(kwargs)
        return iter(["ok"])

    list(
        pdf_qa_service.iter_route_answer_events(
            question="总结这篇文献",
            pdf_path="/tmp/demo.pdf",
            pdf_content="This is a valid PDF content block. " * 10,
            performance_mode="balanced",
            allow_kb_verification=False,
            turn_mode="file_only",
            selected_pdf_files=[{"file_name": "demo.pdf", "local_path": "/tmp/demo.pdf"}],
            agent=None,
            executor=None,
            timeout_error_cls=None,
            sse_event=lambda event: event,
            answer_from_pdf_fn=_answer_from_pdf,
            clean_answer_for_frontend=lambda text, **_kwargs: text,
            filter_literature_markers_for_streaming=lambda text: text,
            log_qa_interaction=lambda **_kwargs: None,
            cache_key_mode="pdf_qa",
            cache_key_question="总结这篇文献",
            cache_set_fn=lambda *_args, **_kwargs: None,
            load_pdf_content_fn=lambda **_kwargs: ("unused", None),
        )
    )

    assert captured["stream"] is True
    assert captured["first_token_timeout_sec"] is None


def test_multi_pdf_route_does_not_set_first_token_timeout_by_default():
    captured: dict[str, object] = {}

    def _answer_from_pdf(*_args, **kwargs):
        captured.update(kwargs)
        return iter(["ok"])

    list(
        pdf_qa_service.iter_route_answer_events(
            question="总结这些文献",
            pdf_path="",
            performance_mode="balanced",
            allow_kb_verification=False,
            turn_mode="file_only",
            selected_pdf_files=[
                {"file_name": "a.pdf", "local_path": "/tmp/a.pdf"},
                {"file_name": "b.pdf", "local_path": "/tmp/b.pdf"},
            ],
            agent=None,
            executor=None,
            timeout_error_cls=None,
            sse_event=lambda event: event,
            answer_from_pdf_fn=_answer_from_pdf,
            clean_answer_for_frontend=lambda text, **_kwargs: text,
            filter_literature_markers_for_streaming=lambda text: text,
            log_qa_interaction=lambda **_kwargs: None,
            cache_key_mode="pdf_qa",
            cache_key_question="总结这些文献",
            cache_set_fn=lambda *_args, **_kwargs: None,
            load_pdf_content_fn=lambda **_kwargs: ("PDF content " * 30, None),
        )
    )

    assert captured["stream"] is True
    assert captured["first_token_timeout_sec"] is None


def test_answer_from_pdf_none_first_token_timeout_disables_watchdog(monkeypatch):
    fake_clock = itertools.count(start=1000.0, step=10.0)
    monkeypatch.setattr(pdf_engine_module.time, "monotonic", lambda: next(fake_clock))

    answer = answer_from_pdf(
        "总结这篇文献",
        "This is a valid PDF content block. " * 10,
        llm=_DelayedFirstChunkLLM(),
        max_pdf_chars=12000,
        smart_truncate_fn=lambda content, _max_chars, **_kwargs: content,
        logger=_NoopLogger(),
        traceback_module=traceback,
        stream=True,
        first_token_timeout_sec=None,
    )

    assert list(answer) == ["delayed answer"]
