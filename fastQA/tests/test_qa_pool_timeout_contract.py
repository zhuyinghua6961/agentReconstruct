from __future__ import annotations

import asyncio
import json
import traceback
from types import SimpleNamespace

import httpx

import app.routers.qa as qa_router
from app.main import app
from app.modules.generation_pipeline.query_expander import QueryExpander
from app.modules.generation_pipeline.stage1_planning import run_stage1_pre_answer_and_planning
from app.modules.generation_pipeline.stage2_retrieval import run_stage2_targeted_retrieval
from app.modules.qa_pdf.engine import answer_from_pdf as answer_from_pdf_impl
from app.routers.qa import AskRequest, ask, ask_stream


class _FakeRequest:
    def __init__(self, app_instance, path: str) -> None:
        self.app = app_instance
        self.headers = {}
        self.url = SimpleNamespace(path=path)

    async def is_disconnected(self) -> bool:
        return False


async def _collect_streaming_body(response) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(str(chunk))
    return "".join(chunks)


class _NoopLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


class _PoolTimeoutChatClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        raise httpx.PoolTimeout("pool exhausted")


class _EmptyExpert:
    def search(self, query: str, **kwargs):
        _ = (query, kwargs)
        return {"documents": [], "metadatas": [], "distances": []}


class _Stage1PoolTimeoutRuntime:
    def stage1_pre_answer_and_planning(
        self,
        user_question: str,
        conversation_context: dict | None = None,
        graph_context: str | None = None,
    ) -> dict:
        return run_stage1_pre_answer_and_planning(
            user_question=user_question,
            stage1_prompt="prompt",
            vector_db_context="context",
            client=_PoolTimeoutChatClient(),
            model="gpt-test",
            logger=_NoopLogger(),
            conversation_context=conversation_context,
            graph_context=graph_context,
        )

    def stage2_targeted_retrieval(self, *args, **kwargs):  # pragma: no cover - should not be reached
        raise AssertionError("stage2 should not be reached")

    def stage25_md_expansion(self, **kwargs):  # pragma: no cover - should not be reached
        raise AssertionError("stage25 should not be reached")

    def _extract_dois_from_results(self, retrieval_results: dict) -> list[str]:
        _ = retrieval_results
        return []

    def stage3_load_pdf_chunks(self, *args, **kwargs):  # pragma: no cover - should not be reached
        raise AssertionError("stage3 should not be reached")

    def stage4_synthesis_with_pdf_chunks(self, *args, **kwargs):  # pragma: no cover - should not be reached
        raise AssertionError("stage4 should not be reached")


class _Stage2QueryExpansionPoolTimeoutRuntime:
    def __init__(self) -> None:
        self._expander = QueryExpander(api_key="key", base_url="https://example.com/v1", model="gpt-test")

    def stage1_pre_answer_and_planning(
        self,
        user_question: str,
        conversation_context: dict | None = None,
        graph_context: str | None = None,
    ) -> dict:
        _ = (user_question, conversation_context, graph_context)
        return {
            "success": True,
            "deep_answer": "draft answer",
            "retrieval_claims": [{"claim": "claim one", "keywords": []}],
        }

    def stage2_targeted_retrieval(
        self,
        retrieval_claims: list[dict],
        n_results_per_claim: int = 10,
        user_question: str | None = None,
        should_cancel=None,
        active_stream_count: int | None = None,
        graph_evidence=None,
    ) -> dict:
        return run_stage2_targeted_retrieval(
            retrieval_claims=retrieval_claims,
            n_results_per_claim=n_results_per_claim,
            user_question=user_question,
            literature_expert=_EmptyExpert(),
            logger=_NoopLogger(),
            client=None,
            model=None,
            preprocess_retrieval_query_fn=lambda query: query,
            validate_retrieval_relevance_fn=lambda results, query, claim: results,
            expand_query_fn=self._expander.expand,
            should_cancel=should_cancel,
            active_stream_count=active_stream_count,
            graph_evidence=graph_evidence,
        )

    def stage25_md_expansion(self, **kwargs):  # pragma: no cover - should not be reached
        raise AssertionError("stage25 should not be reached")

    def _extract_dois_from_results(self, retrieval_results: dict) -> list[str]:
        _ = retrieval_results
        return []

    def stage3_load_pdf_chunks(self, *args, **kwargs):  # pragma: no cover - should not be reached
        raise AssertionError("stage3 should not be reached")

    def stage4_synthesis_with_pdf_chunks(self, *args, **kwargs):  # pragma: no cover - should not be reached
        raise AssertionError("stage4 should not be reached")


class _PdfInvokePoolTimeoutThenFallbackLLM:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def invoke(self, payload):
        self.calls.append(payload)
        if len(self.calls) == 1:
            raise httpx.PoolTimeout("pool exhausted")
        return "fallback answer"


class _PdfStreamPoolTimeoutThenFallbackLLM:
    def __init__(self) -> None:
        self.invocations: list[object] = []

    def stream(self, payload):
        self.invocations.append(("stream", payload))
        raise httpx.PoolTimeout("pool exhausted")

    def invoke(self, payload):
        self.invocations.append(("invoke", payload))
        return "fallback answer"


def _install_generation_runtime(monkeypatch, runtime) -> None:
    monkeypatch.setattr(app.state, "generation_runtime", runtime, raising=False)
    monkeypatch.setattr(app.state, "generation_runtime_ready", True, raising=False)
    monkeypatch.setattr(app.state, "redis_service", None, raising=False)
    monkeypatch.setattr(qa_router, "generation_runtime_is_ready", lambda _state: True)
    monkeypatch.setitem(app.state.component_status, "generation_runtime", {"status": "ok"})
    monkeypatch.setattr(
        qa_router,
        "route_graph_kb_v2",
        lambda **kwargs: SimpleNamespace(mode="skip_graph", direct_result=None, rag_payload=None, diagnostics={}),
    )
    monkeypatch.setattr(
        qa_router,
        "try_graph_kb_answer",
        lambda **kwargs: SimpleNamespace(handled=False),
    )


def _install_pdf_bindings(monkeypatch, *, llm) -> None:
    monkeypatch.setenv("UPLOAD_QA_USE_SIDECAR", "0")

    def _answer_from_pdf(question, pdf_content, **kwargs):
        return answer_from_pdf_impl(
            question,
            pdf_content,
            llm=llm,
            max_pdf_chars=12000,
            smart_truncate_fn=lambda content, max_chars, **_inner_kwargs: content[:max_chars],
            logger=_NoopLogger(),
            traceback_module=traceback,
            kb_verification=kwargs.get("kb_verification"),
            stream=bool(kwargs.get("stream")),
            first_token_timeout_sec=kwargs.get("first_token_timeout_sec"),
            is_cancelled=kwargs.get("is_cancelled"),
        )

    monkeypatch.setattr(
        "app.services.file_routes.load_pdf_content_for_streaming",
        lambda **kwargs: ("PDF content " * 30, None),
    )
    monkeypatch.setattr(
        "app.services.file_routes.get_pdf_bindings",
        lambda app_state, logger: SimpleNamespace(
            answer_from_pdf=_answer_from_pdf,
            extract_pdf_text=lambda *_args, **_kwargs: "PDF content " * 30,
        ),
    )


def test_sync_kb_route_surfaces_upstream_pool_timeout_as_http_503(monkeypatch):
    def _raise(**_kwargs):
        raise httpx.PoolTimeout("pool exhausted")
        yield  # pragma: no cover

    monkeypatch.setattr(qa_router, "_iter_route_events", _raise)

    response = ask(
        AskRequest(question="hello", requested_mode="fast", trace_id="trace-sync"),
        _FakeRequest(app, "/api/ask"),
    )

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload == {
        "success": False,
        "code": "UPSTREAM_POOL_TIMEOUT",
        "error": "upstream_pool_timeout",
        "message": "upstream_pool_timeout",
        "retriable": True,
        "route": "kb_qa",
        "trace_id": "trace-sync",
    }


def test_kb_stream_route_surfaces_upstream_pool_timeout_with_http_503_before_first_byte(monkeypatch):
    def _raise(**_kwargs):
        raise httpx.PoolTimeout("pool exhausted")
        yield  # pragma: no cover

    monkeypatch.setattr(qa_router, "_iter_route_events", _raise)

    response = ask_stream(
        AskRequest(question="hello", requested_mode="fast", trace_id="trace-stream-503"),
        _FakeRequest(app, "/api/ask_stream"),
    )

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload == {
        "success": False,
        "code": "UPSTREAM_POOL_TIMEOUT",
        "error": "upstream_pool_timeout",
        "message": "upstream_pool_timeout",
        "retriable": True,
        "route": "kb_qa",
        "trace_id": "trace-stream-503",
    }


def test_kb_stream_route_emits_sse_error_after_stream_started_on_pool_timeout(monkeypatch):
    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa"}
        raise httpx.PoolTimeout("pool exhausted")

    monkeypatch.setattr(qa_router, "_iter_route_events", _events)

    response = ask_stream(
        AskRequest(question="hello", requested_mode="fast", trace_id="trace-stream-sse"),
        _FakeRequest(app, "/api/ask_stream"),
    )
    body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"type": "metadata"' in body
    assert '"type": "error"' in body
    assert '"code": "UPSTREAM_POOL_TIMEOUT"' in body
    assert '"error": "upstream_pool_timeout"' in body
    assert '"message": "upstream_pool_timeout"' in body
    assert '"retriable": true' in body
    assert '"route": "kb_qa"' in body
    assert '"trace_id": "trace-stream-sse"' in body


def test_sync_kb_route_surfaces_stage1_pool_timeout_from_generation_pipeline(monkeypatch):
    _install_generation_runtime(monkeypatch, _Stage1PoolTimeoutRuntime())

    response = ask(
        AskRequest(question="hello stage1 timeout", requested_mode="fast", route="kb_qa", trace_id="trace-kb-stage1"),
        _FakeRequest(app, "/api/ask"),
    )

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload == {
        "success": False,
        "code": "UPSTREAM_POOL_TIMEOUT",
        "error": "upstream_pool_timeout",
        "message": "upstream_pool_timeout",
        "retriable": True,
        "route": "kb_qa",
        "trace_id": "trace-kb-stage1",
    }


def test_kb_stream_route_surfaces_query_expander_pool_timeout_from_generation_pipeline(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.query_expander.build_chat_completions_client",
        lambda **kwargs: _PoolTimeoutChatClient(),
    )
    _install_generation_runtime(monkeypatch, _Stage2QueryExpansionPoolTimeoutRuntime())

    response = ask_stream(
        AskRequest(question="hello stage2 timeout", requested_mode="fast", route="kb_qa", trace_id="trace-kb-stage2"),
        _FakeRequest(app, "/api/ask_stream"),
    )
    body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"type": "step"' in body
    assert '"type": "error"' in body
    assert '"code": "UPSTREAM_POOL_TIMEOUT"' in body
    assert '"error": "upstream_pool_timeout"' in body
    assert '"route": "kb_qa"' in body
    assert '"trace_id": "trace-kb-stage2"' in body


def test_sync_pdf_route_surfaces_pool_timeout_from_pdf_engine(monkeypatch):
    _install_pdf_bindings(monkeypatch, llm=_PdfStreamPoolTimeoutThenFallbackLLM())

    response = ask(
        AskRequest(
            question="hello",
            requested_mode="fast",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            trace_id="trace-pdf-sync",
            current_pdf_path="/tmp/demo.pdf",
        ),
        _FakeRequest(app, "/api/ask"),
    )

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload == {
        "success": False,
        "code": "UPSTREAM_POOL_TIMEOUT",
        "error": "upstream_pool_timeout",
        "message": "upstream_pool_timeout",
        "retriable": True,
        "route": "pdf_qa",
        "trace_id": "trace-pdf-sync",
    }


def test_pdf_stream_route_surfaces_pool_timeout_from_pdf_engine_after_stream_started(monkeypatch):
    _install_pdf_bindings(monkeypatch, llm=_PdfStreamPoolTimeoutThenFallbackLLM())

    response = ask_stream(
        AskRequest(
            question="hello",
            requested_mode="fast",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            trace_id="trace-pdf-stream",
            current_pdf_path="/tmp/demo.pdf",
        ),
        _FakeRequest(app, "/api/ask_stream"),
    )
    body = asyncio.run(_collect_streaming_body(response))

    assert response.status_code == 200
    assert '"route": "pdf_qa"' in body
    assert '"type": "error"' in body
    assert '"code": "UPSTREAM_POOL_TIMEOUT"' in body
    assert '"error": "upstream_pool_timeout"' in body
    assert '"trace_id": "trace-pdf-stream"' in body
