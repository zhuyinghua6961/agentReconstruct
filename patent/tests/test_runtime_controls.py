import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from server_fastapi.app import create_app
from server.runtime.ordered_task_dispatcher import OrderedTaskDispatcher
from server.runtime.request_context import clear_trace_id, get_trace_id, set_trace_id


def test_stream_slot_limit_rejects_overload():
    dispatcher = OrderedTaskDispatcher(stream_max_concurrent=1, ask_executor_max_workers=2)

    first = dispatcher.try_acquire_stream_slot()
    second = dispatcher.try_acquire_stream_slot()

    assert first is not None
    assert second is None
    assert dispatcher.runtime_state()["stream_slots_available"] == 0


def test_runtime_releases_stream_slot_after_completion():
    dispatcher = OrderedTaskDispatcher(stream_max_concurrent=1, ask_executor_max_workers=2)

    slot = dispatcher.try_acquire_stream_slot()
    assert slot is not None
    slot.release()

    second = dispatcher.try_acquire_stream_slot()
    assert second is not None
    assert dispatcher.runtime_state()["stream_slots_available"] == 0


def test_health_exposes_configured_concurrency_state(monkeypatch):
    monkeypatch.setenv("PATENT_ASK_STREAM_MAX_CONCURRENT", "2")
    monkeypatch.setenv("PATENT_ASK_EXECUTOR_MAX_WORKERS", "3")
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    runtime = payload["components"]["runtime"]
    assert runtime["ready"] is True
    assert runtime["stream_slots_capacity"] == 2
    assert runtime["stream_slots_available"] == 2
    assert runtime["ask_executor_max_workers"] == 3
    assert app.state.runtime_dispatcher.ask_limiter.total_tokens == 3



def test_trace_context_reset_restores_previous_value():
    outer = set_trace_id("req_outer")
    inner = set_trace_id("req_inner")

    assert get_trace_id() == "req_inner"
    clear_trace_id(inner)
    assert get_trace_id() == "req_outer"
    clear_trace_id(outer)

def test_trace_context_reuses_incoming_header(monkeypatch):
    monkeypatch.setenv("PATENT_ASK_STREAM_MAX_CONCURRENT", "2")
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health", headers={"X-Trace-ID": "req_external"})

    assert response.headers["X-Trace-ID"] == "req_external"


def test_trace_context_generates_header_when_missing():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health")

    generated = response.headers["X-Trace-ID"]
    assert generated.startswith("req_")
    assert len(generated) == 16


def test_app_bootstrap_wires_patent_runtime_into_executor_kb_boundary(monkeypatch):
    fake_runtime = type("_FakeRuntime", (), {"retrieval_service": object(), "close": lambda self: None})()
    monkeypatch.setattr(
        "server_fastapi.app.build_default_patent_runtime",
        lambda **kwargs: fake_runtime,
    )

    app = create_app()

    executor = app.state.ask_service._patent_executor
    assert executor._runtime is fake_runtime
    assert executor._kb_service is not None


def test_app_bootstrap_wires_execution_cache_into_runtime_retrieval_service(monkeypatch):
    fake_retrieval_service = type("_FakeRetrievalService", (), {"_execution_cache": None})()

    class _FakeRuntime:
        def __init__(self, retrieval_service):
            self.retrieval_service = retrieval_service

        def close(self):
            return None

    fake_runtime = _FakeRuntime(fake_retrieval_service)

    def _build_runtime(*, execution_cache=None, http_client=None):
        assert http_client is not None
        fake_retrieval_service._execution_cache = execution_cache
        return fake_runtime

    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", _build_runtime)

    app = create_app()

    assert fake_retrieval_service._execution_cache is app.state.execution_cache


def test_build_default_patent_runtime_degrades_to_no_vector_when_vector_bootstrap_fails(monkeypatch, tmp_path: Path):
    from server.patent.resource_registry import PatentResourceRegistry
    from server.patent.runtime import build_default_patent_runtime

    resource_root = tmp_path / "resource" / "patentQA"
    archive_dir = resource_root / "__archive__"
    patent_dir = archive_dir / "CN115132975B"
    patent_dir.mkdir(parents=True)
    for vector_dir in ("vector_db_patent_abstracts", "vector_db_patent_chunks"):
        db_dir = resource_root / vector_dir
        db_dir.mkdir(parents=True)
        (db_dir / "chroma.sqlite3").write_text("", encoding="utf-8")
    (patent_dir / "著录项目.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "pn": "CN115132975B",
                        "bibliographic_data": {
                            "publication_reference": {"country": "CN", "kind": "B", "doc_number": "115132975", "date": "2022-10-01"},
                            "application_reference": {"doc_number": "CN202110320984.1"},
                            "invention_title": [{"text": "一种锂离子电池及动力车辆"}],
                            "abstracts": [{"text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"}],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (patent_dir / "权利要求.json").write_text(
        json.dumps({"data": [{"claims": [{"claim_text": '<div num="1">一种锂离子电池。</div>'}]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (patent_dir / "说明书.json").write_text(
        json.dumps({"data": [{"description": [{"text": '<b class="d_n">[0001]</b>该电池能够改善高 SOC 充电安全性。'}]}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    class _AnswerBuilder:
        def close(self):
            return None

    class _EmbeddingClient:
        def close(self):
            return None

    monkeypatch.setattr(
        "server.patent.runtime.PatentResourceRegistry.discover",
        lambda: PatentResourceRegistry(
            repo_root=tmp_path,
            abstract_db_path=resource_root / "vector_db_patent_abstracts",
            chunk_db_path=resource_root / "vector_db_patent_chunks",
            archive_root=archive_dir,
        ),
    )
    monkeypatch.setattr("server.patent.runtime.PatentAnswerBuilder.from_env", lambda: _AnswerBuilder())
    monkeypatch.setattr("server.patent.runtime.PatentEmbeddingClient", _EmbeddingClient)

    def _raise_on_vector_init(*args, **kwargs):
        raise RuntimeError("vector init boom")

    monkeypatch.setattr("server.patent.runtime.ChromaPatentSearch", _raise_on_vector_init)

    runtime = build_default_patent_runtime()

    assert runtime is not None
    assert runtime.retrieval_service._vector_search_enabled() is False


def test_build_default_patent_runtime_wires_stage1_planner_from_env(monkeypatch, tmp_path: Path):
    from server.patent.resource_registry import PatentResourceRegistry
    from server.patent.runtime import build_default_patent_runtime

    resource_root = tmp_path / "resource" / "patentQA"
    archive_dir = resource_root / "__archive__"
    patent_dir = archive_dir / "CN115132975B"
    patent_dir.mkdir(parents=True)
    (patent_dir / "著录项目.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "pn": "CN115132975B",
                        "bibliographic_data": {
                            "publication_reference": {"country": "CN", "kind": "B", "doc_number": "115132975", "date": "2022-10-01"},
                            "application_reference": {"doc_number": "CN202110320984.1"},
                            "invention_title": [{"text": "一种锂离子电池及动力车辆"}],
                            "abstracts": [{"text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"}],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class _AnswerBuilder:
        def close(self):
            return None

    class _PlannerClient:
        def close(self):
            return None

    monkeypatch.setattr(
        "server.patent.runtime.PatentResourceRegistry.discover",
        lambda: PatentResourceRegistry(
            repo_root=tmp_path,
            abstract_db_path=resource_root / "vector_db_patent_abstracts",
            chunk_db_path=resource_root / "vector_db_patent_chunks",
            archive_root=archive_dir,
        ),
    )
    monkeypatch.setattr("server.patent.runtime.PatentAnswerBuilder.from_env", lambda: _AnswerBuilder())
    monkeypatch.setattr(
        "server.patent.runtime._build_patent_planning_runtime_inputs",
        lambda: (_PlannerClient(), "planner-model"),
    )

    runtime = build_default_patent_runtime()

    assert runtime is not None
    assert runtime.planning_model == "planner-model"
    assert runtime.planning_client is not None


def test_patent_planning_runtime_prefers_unified_llm_namespace(monkeypatch):
    from server.patent.runtime import _resolve_patent_planning_runtime_config

    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "llm-model")
    monkeypatch.setenv("LLM_READ_TIMEOUT_SECONDS", "55")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_API_KEY", "stage-key")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_BASE_URL", "https://stage.example/v1")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_MODEL", "stage-model")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_TIMEOUT_SECONDS", "66")
    monkeypatch.setenv("PATENT_OPENAI_API_KEY", "patent-key")
    monkeypatch.setenv("PATENT_OPENAI_BASE_URL", "https://patent.example/v1")
    monkeypatch.setenv("PATENT_OPENAI_MODEL", "patent-model")
    monkeypatch.setenv("PATENT_OPENAI_TIMEOUT_SECONDS", "77")

    api_key, base_url, model, timeout_seconds = _resolve_patent_planning_runtime_config()

    assert api_key == "llm-key"
    assert base_url == "https://llm.example/v1"
    assert model == "llm-model"
    assert timeout_seconds == 55.0


def test_patent_planning_runtime_ignores_retired_llm_aliases(monkeypatch):
    from server.patent.runtime import _resolve_patent_planning_runtime_config

    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_READ_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dash.example/v1")
    monkeypatch.setenv("DASHSCOPE_MODEL", "dash-model")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_API_KEY", "stage-key")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_BASE_URL", "https://stage.example/v1")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_MODEL", "stage-model")
    monkeypatch.setenv("PATENT_STAGE1_OPENAI_TIMEOUT_SECONDS", "66")
    monkeypatch.setenv("PATENT_OPENAI_API_KEY", "patent-key")
    monkeypatch.setenv("PATENT_OPENAI_BASE_URL", "https://patent.example/v1")
    monkeypatch.setenv("PATENT_OPENAI_MODEL", "patent-model")
    monkeypatch.setenv("PATENT_OPENAI_TIMEOUT_SECONDS", "77")

    api_key, base_url, model, timeout_seconds = _resolve_patent_planning_runtime_config()

    assert api_key == ""
    assert base_url == ""
    assert model == ""
    assert timeout_seconds == 30.0


def test_patent_embedding_client_prefers_unified_embedding_namespace(monkeypatch):
    from server.patent.runtime import PatentEmbeddingClient

    class _FakeHttpClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        def close(self):
            return None

    monkeypatch.setenv("EMBEDDING_MODEL_TYPE", "remote")
    monkeypatch.setenv("EMBEDDING_API_TIMEOUT_SECONDS", "33")
    monkeypatch.setenv("EMBEDDING_API_URL", "https://embedding.example/v1/embeddings")
    monkeypatch.setenv("EMBEDDING_API_MODEL", "embedding-target")
    monkeypatch.setenv("PATENT_EMBEDDING_MODEL_TYPE", "local")
    monkeypatch.setenv("PATENT_EMBEDDING_API_TIMEOUT_SECONDS", "44")
    monkeypatch.setenv("PATENT_EMBEDDING_API_URL", "https://legacy.example/v1/embeddings")
    monkeypatch.setenv("PATENT_EMBEDDING_API_MODEL", "legacy-embedding")
    monkeypatch.setattr("server.patent.runtime.httpx.Client", _FakeHttpClient)

    client = PatentEmbeddingClient()

    assert client._mode == "remote"
    assert client._http.timeout == 33.0
    assert client._api_url == "https://embedding.example/v1/embeddings"
    assert client._api_model == "embedding-target"


def test_patent_embedding_client_ignores_retired_embedding_aliases(monkeypatch):
    from server.patent.runtime import PatentEmbeddingClient

    class _FakeHttpClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        def close(self):
            return None

    for name in ("EMBEDDING_MODEL_TYPE", "EMBEDDING_API_TIMEOUT_SECONDS", "EMBEDDING_API_URL", "EMBEDDING_API_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATENT_EMBEDDING_MODEL_TYPE", "local")
    monkeypatch.setenv("PATENT_EMBEDDING_API_TIMEOUT_SECONDS", "44")
    monkeypatch.setenv("PATENT_EMBEDDING_API_URL", "https://legacy.example/v1/embeddings")
    monkeypatch.setenv("PATENT_EMBEDDING_API_MODEL", "legacy-embedding")
    monkeypatch.setattr("server.patent.runtime.httpx.Client", _FakeHttpClient)

    client = PatentEmbeddingClient()

    assert client._mode == "remote"
    assert client._http.timeout == 120.0
    assert client._api_url == "http://127.0.0.1:8001/v1/embeddings"
    assert client._api_model == "bge-local"


def test_build_default_patent_runtime_passes_injected_http_client_to_llm_wrappers(monkeypatch, tmp_path: Path):
    from server.patent.resource_registry import PatentResourceRegistry
    from server.patent.runtime import build_default_patent_runtime

    resource_root = tmp_path / "resource" / "patentQA"
    archive_dir = resource_root / "__archive__"
    patent_dir = archive_dir / "CN115132975B"
    patent_dir.mkdir(parents=True)
    (patent_dir / "著录项目.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "pn": "CN115132975B",
                        "bibliographic_data": {
                            "publication_reference": {"country": "CN", "kind": "B", "doc_number": "115132975", "date": "2022-10-01"},
                            "application_reference": {"doc_number": "CN202110320984.1"},
                            "invention_title": [{"text": "一种锂离子电池及动力车辆"}],
                            "abstracts": [{"text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"}],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (patent_dir / "权利要求.json").write_text(
        json.dumps({"data": [{"claims": [{"claim_text": '<div num="1">一种锂离子电池。</div>'}]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (patent_dir / "说明书.json").write_text(
        json.dumps({"data": [{"description": [{"text": '<b class="d_n">[0001]</b>该电池能够改善高 SOC 充电安全性。'}]}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}
    shared_http_client = object()

    class _AnswerBuilder:
        def __init__(self, http_client=None):
            captured["answer_http_client"] = http_client

        def close(self):
            captured["answer_closed"] = True

    class _PlannerClient:
        def __init__(self, http_client=None):
            captured["planner_http_client"] = http_client

        def close(self):
            captured["planner_closed"] = True

    monkeypatch.setattr(
        "server.patent.runtime.PatentResourceRegistry.discover",
        lambda: PatentResourceRegistry(
            repo_root=tmp_path,
            abstract_db_path=resource_root / "vector_db_patent_abstracts",
            chunk_db_path=resource_root / "vector_db_patent_chunks",
            archive_root=archive_dir,
        ),
    )
    monkeypatch.setattr("server.patent.runtime.PatentAnswerBuilder.from_env", lambda http_client=None: _AnswerBuilder(http_client=http_client))
    monkeypatch.setattr(
        "server.patent.runtime._build_patent_planning_runtime_inputs",
        lambda http_client=None: (_PlannerClient(http_client=http_client), "planner-model"),
    )

    runtime = build_default_patent_runtime(http_client=shared_http_client)

    assert runtime is not None
    assert captured["answer_http_client"] is shared_http_client
    assert captured["planner_http_client"] is shared_http_client
    runtime.close()
    assert captured["answer_closed"] is True
    assert captured["planner_closed"] is True


def test_build_default_patent_runtime_closes_private_wrappers_when_retrieval_bootstrap_fails(monkeypatch, tmp_path: Path):
    from server.patent.resource_registry import PatentResourceRegistry
    from server.patent.runtime import build_default_patent_runtime

    resource_root = tmp_path / "resource" / "patentQA"
    archive_dir = resource_root / "__archive__"
    patent_dir = archive_dir / "CN115132975B"
    patent_dir.mkdir(parents=True)
    (patent_dir / "著录项目.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "pn": "CN115132975B",
                        "bibliographic_data": {
                            "publication_reference": {"country": "CN", "kind": "B", "doc_number": "115132975", "date": "2022-10-01"},
                            "application_reference": {"doc_number": "CN202110320984.1"},
                            "invention_title": [{"text": "一种锂离子电池及动力车辆"}],
                            "abstracts": [{"text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。"}],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    captured = {"answer_closed": 0, "planner_closed": 0}

    class _AnswerBuilder:
        def close(self):
            captured["answer_closed"] += 1

    class _PlannerClient:
        def close(self):
            captured["planner_closed"] += 1

    monkeypatch.setattr(
        "server.patent.runtime.PatentResourceRegistry.discover",
        lambda: PatentResourceRegistry(
            repo_root=tmp_path,
            abstract_db_path=resource_root / "vector_db_patent_abstracts",
            chunk_db_path=resource_root / "vector_db_patent_chunks",
            archive_root=archive_dir,
        ),
    )
    monkeypatch.setattr("server.patent.runtime.PatentAnswerBuilder.from_env", lambda http_client=None: _AnswerBuilder())
    monkeypatch.setattr(
        "server.patent.runtime._build_patent_planning_runtime_inputs",
        lambda http_client=None: (_PlannerClient(), "planner-model"),
    )
    monkeypatch.setattr(
        "server.patent.runtime.PatentRetrievalService",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("retrieval bootstrap boom")),
    )

    with pytest.raises(RuntimeError, match="retrieval bootstrap boom"):
        build_default_patent_runtime()

    assert captured["answer_closed"] == 1
    assert captured["planner_closed"] == 1
