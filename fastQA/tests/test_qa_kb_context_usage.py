from types import SimpleNamespace

from app.modules.generation_pipeline.stage1_planning import run_stage1_pre_answer_and_planning
from app.modules.qa_kb.models import QaKbRequest
from app.modules.qa_kb.service import qa_kb_service


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class _Runtime:
    def __init__(self) -> None:
        self.stage1_context = None

    def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict:
        self.stage1_context = {
            "user_question": user_question,
            "conversation_context": conversation_context,
        }
        return {"success": True, "deep_answer": "deep", "retrieval_claims": []}

    def stage2_targeted_retrieval(self, retrieval_claims, n_results_per_claim=10, user_question=None, should_cancel=None, active_stream_count=None) -> dict:
        return {"success": False, "error": "retrieval_failed"}

    def stage25_md_expansion(self, *, retrieval_results: dict, user_question: str, dois: list[str]) -> dict:
        return {}

    def _extract_dois_from_results(self, retrieval_results: dict) -> list[str]:
        return []

    def stage3_load_pdf_chunks(self, dois, max_chunks_per_doi=3, should_cancel=None):
        return {}

    def stage4_synthesis_with_pdf_chunks(self, user_question, deep_answer, pdf_chunks, retrieval_results=None, should_cancel=None, conversation_context=None):
        yield {"success": True, "final_answer": deep_answer, "query_mode": "kb_qa", "references": []}



def test_kb_service_threads_sanitized_context_into_stage1_runtime():
    runtime = _Runtime()
    request = QaKbRequest(
        question="那它的缺点呢",
        route_hint="kb_qa",
        trace_id="trace-1",
        recent_turns_for_llm=[
            {"role": "user", "content": "介绍磷酸铁锂的优点", "trace_id": "trace-u1"},
            {"role": "assistant", "content": "它的优点包括安全性和寿命", "trace_id": "trace-a1"},
        ],
        summary_for_llm={"short_summary": "之前在讨论LFP优缺点", "steps": [{"name": "ignore"}]},
        conversation_state={
            "last_turn_route": "kb_qa",
            "last_focus_file_ids": [5, "6"],
            "last_assistant_trace_id": "trace-prev",
        },
        source_selection={
            "source_scope": "pdf+kb",
            "selected_file_ids": [5, "6", "bad"],
            "used_files": [
                {
                    "file_id": 5,
                    "file_type": "pdf",
                    "file_name": "paper-a.pdf",
                    "selected_reason": "selected_single",
                    "source": "gateway_file_context",
                    "local_path": "/tmp/a.pdf",
                    "storage_ref": "minio://bucket/a.pdf",
                }
            ],
        },
    )

    events = list(
        qa_kb_service.iter_answer_events(
            request=request,
            generation_runtime=runtime,
            redis_service=None,
            sse_event=lambda payload: payload,
        )
    )

    assert events[-1]["type"] == "done"
    assert runtime.stage1_context == {
        "user_question": "那它的缺点呢",
        "conversation_context": {
            "recent_turns_for_llm": [
                {"role": "user", "content": "介绍磷酸铁锂的优点"},
                {"role": "assistant", "content": "它的优点包括安全性和寿命"},
            ],
            "summary_for_llm": {"short_summary": "之前在讨论LFP优缺点"},
            "conversation_state": {"last_turn_route": "kb_qa", "last_focus_file_ids": [5, 6]},
            "source_selection": {
                "source_scope": "pdf+kb",
                "selected_file_ids": [5, 6],
                "used_files": [
                    {
                        "file_id": 5,
                        "file_type": "pdf",
                        "file_name": "paper-a.pdf",
                        "selected_reason": "selected_single",
                        "source": "gateway_file_context",
                    }
                ],
            },
        },
    }



def test_stage1_planning_includes_conversation_context_but_excludes_trace_fields():
    client = _FakeClient('{"deep_answer":"answer","retrieval_claims":[]}')

    run_stage1_pre_answer_and_planning(
        user_question="那它的缺点呢",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
        conversation_context={
            "recent_turns_for_llm": [
                {"role": "user", "content": "介绍磷酸铁锂的优点", "trace_id": "trace-u1"},
                {"role": "assistant", "content": "它的优点包括安全性和寿命", "trace_id": "trace-a1"},
            ],
            "summary_for_llm": {
                "short_summary": "之前在讨论LFP优缺点",
                "steps": [{"name": "should-not-leak"}],
                "timings": {"stage1": 12},
                "trace_id": "trace-summary",
            },
        },
    )

    user_message = client.calls[0]["messages"][1]["content"]
    assert "介绍磷酸铁锂的优点" in user_message
    assert "它的优点包括安全性和寿命" in user_message
    assert "之前在讨论LFP优缺点" in user_message
    assert "trace-u1" not in user_message
    assert "trace-summary" not in user_message
    assert "should-not-leak" not in user_message
    assert "stage1" not in user_message
