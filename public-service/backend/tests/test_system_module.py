import os
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.errors import DatabaseUnavailableError
from app.core.runtime import create_runtime
from app.core import runtime as runtime_module
from app.integrations.redis import RedisService
from app.main import app
from app.modules.auth.deps import require_admin_context
from app.modules.retrieval.models import ChromaBootstrapResult, RetrievalBindings, RetrievalRuntimeConfig
from app.modules.conversation.cache import (
    build_conversation_detail_cache_key,
    build_conversation_list_cache_key,
    build_conversation_list_recent_pages_key,
)
from app.modules.qa_cache.metrics import increment_cache_metric, reset_cache_metrics
from app.modules.system.service import system_service


def test_system_service_background_status_contract(tmp_path):
    reset_cache_metrics()
    runtime = create_runtime(get_settings())
    runtime.current_answer_context = "context-body"
    runtime.component_status["upload_processing"] = {"status": "ok", "enabled": True}
    runtime.conversation_outbox_status = {
        "state": "running",
        "thread_alive": False,
        "loops": 3,
        "last_summary": {"done": 1},
        "last_error": "",
        "last_run_at": "2026-03-14T00:00:00+08:00",
    }
    runtime.conversation_outbox_thread = SimpleNamespace(is_alive=lambda: True)
    runtime.upload_processing_worker = SimpleNamespace(enabled=True, _active_keys={(1, 2), (2, 3)})
    runtime.authority_assistant_inbox_status = {
        "state": "running",
        "thread_alive": False,
        "loops": 2,
        "last_summary": {"done": 1},
        "last_error": "",
        "last_run_at": "2026-03-14T00:00:01+08:00",
        "backlog": 3,
        "processing": 1,
        "failed": 0,
        "enabled": True,
    }
    runtime.authority_assistant_inbox_thread = SimpleNamespace(is_alive=lambda: True)
    runtime.logs_dir = tmp_path / "logs"
    runtime.logs_dir.mkdir()
    older = runtime.logs_dir / "background_programmatic_insert_older.json"
    newer = runtime.logs_dir / "background_programmatic_insert_newer.json"
    older.write_text("{}", encoding="utf-8")
    newer.write_text("{}", encoding="utf-8")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    increment_cache_metric("stage1", "cache_hit")

    payload, status_code = system_service.build_background_status(runtime)

    assert status_code == 200
    assert payload["success"] is True
    assert payload["status"]["has_current_answer_context"] is True
    assert payload["status"]["current_answer_preview"] == "context-body..."
    assert payload["status"]["conversation_outbox"]["thread_alive"] is True
    assert payload["status"]["authority_assistant_inbox"]["thread_alive"] is True
    assert payload["status"]["authority_assistant_inbox"]["backlog"] == 3
    assert payload["status"]["upload_processing"]["active_tasks"] == 2
    assert payload["status"]["latest_background_file"].endswith("background_programmatic_insert_newer.json")
    assert payload["status"]["qa_cache"]["metrics"]["stage1"]["cache_hit"] == 1


def test_runtime_marks_upload_processing_degraded_when_pdf_extractor_missing(monkeypatch):
    monkeypatch.setattr("app.core.runtime._build_pdf_text_extractor", lambda: (None, {"pdf_extract_available": False, "pdf_extract_error": "missing"}))

    runtime = create_runtime(get_settings())

    assert runtime.component_status["upload_processing"]["status"] == "degraded"
    assert runtime.component_status["upload_processing"]["pdf_extract_available"] is False


def test_system_service_clear_cache_contract():
    runtime = create_runtime(get_settings())
    runtime.answer_cache = {"cached": "value"}

    payload, status_code = system_service.clear_cache(runtime)

    assert status_code == 200
    assert payload == {
        "success": True,
        "message": "当前实例答案缓存已清空",
        "scope": "instance_local",
        "cluster_consistency": "not_coordinated",
    }
    assert runtime.answer_cache == {}


def test_system_service_kb_info_without_agent():
    runtime = create_runtime(get_settings())
    runtime.agent = None
    runtime.vector_db_client = None
    runtime.vector_collection = None
    runtime.neo4j_client = None

    payload, status_code = system_service.build_kb_info(runtime)

    assert status_code == 200
    assert payload["success"] is False
    assert payload["message"] == "知识库运行时未初始化"
    assert payload["kb_size"] == 0
    assert payload["chromadb_size"] == 0


def test_system_service_kb_info_works_with_lightweight_runtime(monkeypatch):
    class _FakeCollection:
        def count(self):
            return 5

    class _FakeVectorClient:
        def __init__(self):
            self.db_path = "/tmp/vector-db"
            self.collection_name = "lfp_papers"

        def count(self, *, collection=None):
            _ = collection
            return SimpleNamespace(count=5)

    monkeypatch.setattr(
        runtime_module.retrieval_service,
        "build_bindings",
        lambda **kwargs: RetrievalBindings(
            runtime=RetrievalRuntimeConfig(
                vector_db_path=runtime_module.Path("/tmp/vector-db"),
                vector_collection_name="lfp_papers",
                neo4j_url="",
                neo4j_username="neo4j",
                neo4j_password="password",
            ),
            vector_db_client=_FakeVectorClient(),
            chroma=ChromaBootstrapResult(client=object(), collection=_FakeCollection(), available=True, error=None),
            neo4j_client=None,
        ),
    )

    runtime = create_runtime(get_settings())
    payload, status_code = system_service.build_kb_info(runtime)

    assert status_code == 200
    assert payload["success"] is True
    assert payload["kb_size"] == 0
    assert payload["chromadb_size"] == 5


def test_system_service_refresh_kb_uses_runtime_initializer_without_existing_agent():
    runtime = create_runtime(get_settings())
    runtime.agent = None
    runtime.init_agent = lambda: True

    payload, status_code = system_service.refresh_kb(runtime)

    assert status_code == 200
    assert payload == {
        "success": True,
        "message": "当前实例知识库已刷新",
        "scope": "instance_local",
        "cluster_consistency": "not_coordinated",
    }


def test_system_http_routes_use_runtime(tmp_path):
    reset_cache_metrics()
    with TestClient(app) as client:
        client.app.dependency_overrides[require_admin_context] = lambda: SimpleNamespace(user_id=1, role="admin", username="root")
        runtime = client.app.state.runtime
        runtime.component_status["database"] = {"status": "ok", "detail": "mock"}
        runtime.component_status["storage"] = {"status": "ok", "backend": "local"}
        runtime.current_answer_context = "ctx"
        runtime.logs_dir = tmp_path / "logs"
        runtime.logs_dir.mkdir()
        runtime.conversation_outbox_status = {"state": "running"}
        runtime.conversation_outbox_thread = SimpleNamespace(is_alive=lambda: True)
        runtime.upload_processing_worker = SimpleNamespace(enabled=True, _active_keys={(1, 2, 3)})

        health_resp = client.get("/api/v1/health")
        background_resp = client.get("/api/v1/background_status")
        clear_resp = client.post("/api/v1/clear_cache")
        client.app.dependency_overrides.clear()

    assert health_resp.status_code == 200
    assert health_resp.json()["components"]["database"]["status"] == "ok"
    assert health_resp.json()["storage_backend"] == "local"
    assert background_resp.status_code == 200
    assert background_resp.json()["success"] is True
    assert clear_resp.status_code == 200
    assert clear_resp.json() == {
        "success": True,
        "message": "当前实例答案缓存已清空",
        "scope": "instance_local",
        "cluster_consistency": "not_coordinated",
    }


def test_system_kb_info_and_refresh_routes_use_admin_contract(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[require_admin_context] = lambda: SimpleNamespace(user_id=1, role="admin", username="root")
        monkeypatch.setattr(system_service, "build_kb_info", lambda runtime: ({"success": True, "kb_size": 5, "chromadb_size": 8}, 200))
        monkeypatch.setattr(system_service, "refresh_kb", lambda runtime: ({"success": True, "message": "知识库已刷新"}, 200))

        kb_resp = client.get("/api/v1/kb_info")
        refresh_resp = client.post("/api/v1/refresh_kb")
        client.app.dependency_overrides.clear()

    assert kb_resp.status_code == 200
    assert kb_resp.json()["kb_size"] == 5
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["message"] == "知识库已刷新"


def test_system_http_routes_require_admin():
    with TestClient(app) as client:
        response = client.get("/api/v1/background_status")

    assert response.status_code == 401


def test_runtime_starts_and_stops_conversation_outbox_worker():
    runtime = create_runtime(get_settings())
    runtime.component_status["database"] = {"status": "ok"}
    runtime.storage_backend = object()
    runtime.conversation_repository = object()
    runtime.conversation_outbox_repository = object()

    class _FakeWorker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.config = SimpleNamespace(poll_interval_ms=10)

    class _FakeEvent:
        def __init__(self):
            self.set_called = False

        def is_set(self):
            return self.set_called

        def set(self):
            self.set_called = True

        def wait(self, _seconds):
            return None

    class _FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            self.started = False
            self.joined = False
            self._alive = False

        def start(self):
            self.started = True
            self._alive = True

        def is_alive(self):
            return self._alive

        def join(self, timeout=None):
            _ = timeout
            self.joined = True
            self._alive = False

    runtime_module._start_conversation_outbox_worker(
        runtime,
        worker_cls=_FakeWorker,
        thread_cls=_FakeThread,
        event_cls=_FakeEvent,
    )

    assert isinstance(runtime.conversation_outbox_worker, _FakeWorker)
    assert runtime.conversation_outbox_worker.kwargs["storage_backend"] is runtime.storage_backend
    assert runtime.conversation_outbox_thread.started is True
    assert runtime.component_status["conversation_outbox"]["detail"] == "conversation outbox worker starting"

    runtime_module._stop_conversation_outbox_worker(runtime)

    assert runtime.conversation_outbox_stop_event.set_called is True
    assert runtime.conversation_outbox_thread.joined is True
    assert runtime.component_status["conversation_outbox"]["status"] == "stopped"


def test_runtime_starts_and_stops_authority_assistant_inbox_worker():
    runtime = create_runtime(get_settings())
    runtime.component_status["database"] = {"status": "ok"}
    runtime.conversation_repository = object()
    runtime.conversation_service = object()

    class _FakeWorker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.config = SimpleNamespace(poll_interval_ms=10)

    class _FakeEvent:
        def __init__(self):
            self.set_called = False

        def is_set(self):
            return self.set_called

        def set(self):
            self.set_called = True

        def wait(self, _seconds):
            return None

    class _FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            self.started = False
            self.joined = False
            self._alive = False

        def start(self):
            self.started = True
            self._alive = True

        def is_alive(self):
            return self._alive

        def join(self, timeout=None):
            _ = timeout
            self.joined = True
            self._alive = False

    runtime_module._start_authority_assistant_inbox_worker(
        runtime,
        worker_cls=_FakeWorker,
        thread_cls=_FakeThread,
        event_cls=_FakeEvent,
    )

    assert isinstance(runtime.authority_assistant_inbox_worker, _FakeWorker)
    assert runtime.authority_assistant_inbox_worker.kwargs["repository"] is runtime.conversation_repository
    assert runtime.authority_assistant_inbox_worker.kwargs["conversation_service"] is runtime.conversation_service
    assert runtime.authority_assistant_inbox_thread.started is True
    assert runtime.component_status["authority_assistant_inbox"]["detail"] == "authority assistant inbox worker starting"

    runtime_module._stop_authority_assistant_inbox_worker(runtime)

    assert runtime.authority_assistant_inbox_stop_event.set_called is True
    assert runtime.authority_assistant_inbox_thread.joined is True
    assert runtime.component_status["authority_assistant_inbox"]["status"] == "stopped"


def test_runtime_starts_conversation_outbox_worker_even_when_database_is_degraded():
    runtime = create_runtime(get_settings())
    runtime.component_status["database"] = {"status": "degraded"}
    runtime.storage_backend = object()
    runtime.conversation_repository = object()
    runtime.conversation_outbox_repository = object()

    class _FakeWorker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.config = SimpleNamespace(poll_interval_ms=10)

    class _FakeEvent:
        def __init__(self):
            self.set_called = False

        def is_set(self):
            return self.set_called

        def set(self):
            self.set_called = True

        def wait(self, _seconds):
            return None

    class _FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

        def join(self, timeout=None):
            _ = timeout
            self.started = False

    runtime_module._start_conversation_outbox_worker(
        runtime,
        worker_cls=_FakeWorker,
        thread_cls=_FakeThread,
        event_cls=_FakeEvent,
    )

    assert isinstance(runtime.conversation_outbox_worker, _FakeWorker)
    assert runtime.conversation_outbox_thread.started is True
    assert runtime.component_status["conversation_outbox"]["detail"] == "conversation outbox worker starting"


def test_runtime_conversation_outbox_loop_recovers_after_database_returns(monkeypatch):
    runtime = create_runtime(get_settings())
    recorded_statuses: list[str] = []
    original_set_component_status = runtime_module._set_component_status

    def _recording_set_component_status(*args, **kwargs):
        if len(args) >= 2 and args[1] == "conversation_outbox":
            recorded_statuses.append(str(kwargs.get("status") or ""))
        return original_set_component_status(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "_set_component_status", _recording_set_component_status)

    class _FakeStopEvent:
        def __init__(self):
            self.wait_calls = 0
            self.stop = False

        def is_set(self):
            return self.stop

        def set(self):
            self.stop = True

        def wait(self, _seconds):
            self.wait_calls += 1
            if self.wait_calls >= 2:
                self.stop = True
            return None

    class _RecoveringWorker:
        def __init__(self):
            self.calls = 0
            self.config = SimpleNamespace(poll_interval_ms=1)

        def run_once(self):
            self.calls += 1
            if self.calls == 1:
                raise DatabaseUnavailableError("db_unavailable")
            return {"done": 1}

    worker = _RecoveringWorker()
    stop_event = _FakeStopEvent()
    runtime.conversation_outbox_worker = worker
    runtime.conversation_outbox_stop_event = stop_event

    runtime_module._run_conversation_outbox_loop(runtime)

    assert worker.calls == 2
    assert runtime.conversation_outbox_status["loops"] == 1
    assert runtime.conversation_outbox_status["last_summary"] == {"done": 1}
    assert runtime.conversation_outbox_status["last_error"] == ""
    assert "degraded" in recorded_statuses
    assert recorded_statuses[-2:] == ["ok", "stopped"]


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.expirations: dict[str, int] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = int(ex)
        return True

    def delete(self, *keys: str):
        deleted = 0
        for key in keys:
            if key in self.values:
                deleted += 1
                self.values.pop(key, None)
                self.expirations.pop(key, None)
        return deleted

    def expire(self, key: str, seconds: int):
        if key not in self.values:
            return False
        self.expirations[key] = int(seconds)
        return True

    def ttl(self, key: str):
        return self.expirations.get(key)


def test_system_service_conversation_cache_debug_contract():
    runtime = create_runtime(get_settings())
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime.redis_service = redis_service

    recent_pages_key = build_conversation_list_recent_pages_key(redis_service=redis_service, user_id=7)
    redis_service.set_json(recent_pages_key, {"pages": [{"page": 2, "page_size": 10}]}, ttl_seconds=300)

    list_key = build_conversation_list_cache_key(redis_service=redis_service, user_id=7, page=2, page_size=10)
    redis_service.set_json(
        list_key,
        {
            "success": True,
            "data": {
                "total_count": 1,
                "conversations": [{"conversation_id": 42, "title": "Title", "message_count": 3}],
            },
        },
        ttl_seconds=120,
    )

    detail_key = build_conversation_detail_cache_key(redis_service=redis_service, user_id=7, conversation_id=42)
    redis_service.set_json(
        detail_key,
        {
            "success": True,
            "data": {
                "title": "Title",
                "updated_at": "2026-03-16T00:00:00+08:00",
                "messages": [{"role": "user", "content": "hello world"}],
                "uploaded_files": [{"id": 1}],
            },
        },
        ttl_seconds=90,
    )

    payload, status_code = system_service.build_conversation_cache_debug(runtime, user_id=7, conversation_id=42)

    assert status_code == 200
    assert payload["success"] is True
    assert payload["data"]["redis_available"] is True
    assert payload["data"]["conversation_cache"]["list"]["recent_pages"][0]["page"] == 2
    assert payload["data"]["conversation_cache"]["list"]["pages"][1]["present"] is True
    assert payload["data"]["conversation_cache"]["detail"]["present"] is True
    assert payload["data"]["conversation_cache"]["detail"]["message_count"] == 1


def test_system_service_background_status_includes_disabled_outbox_support():
    runtime = create_runtime(get_settings())
    runtime.conversation_outbox_status = {
        "state": "disabled",
        "thread_alive": False,
        "loops": 0,
        "last_summary": None,
        "last_error": "conversation_json_outbox table missing",
        "last_run_at": None,
        "table_name": "conversation_json_outbox",
        "table_exists": False,
        "enabled": False,
        "reason": "missing_table",
    }

    payload, status_code = system_service.build_background_status(runtime)

    assert status_code == 200
    assert payload["status"]["conversation_outbox"]["enabled"] is False
    assert payload["status"]["conversation_outbox"]["table_exists"] is False
    assert payload["status"]["conversation_outbox"]["reason"] == "missing_table"


def test_runtime_does_not_start_outbox_worker_when_table_missing():
    runtime = create_runtime(get_settings())
    runtime.conversation_outbox_status = {
        "state": "uninitialized",
        "thread_alive": False,
        "loops": 0,
        "last_summary": None,
        "last_error": "",
        "last_run_at": None,
    }

    class _Repo:
        def support_status(self):
            return {
                "table_name": "conversation_json_outbox",
                "table_exists": False,
                "enabled": False,
                "reason": "missing_table",
            }

    class _FakeWorker:
        def __init__(self, **kwargs):
            raise AssertionError("worker should not be constructed when outbox table is missing")

    runtime.conversation_outbox_repository = _Repo()
    runtime.conversation_repository = object()
    runtime.storage_backend = object()

    runtime_module._start_conversation_outbox_worker(runtime, worker_cls=_FakeWorker)

    assert runtime.conversation_outbox_worker is None
    assert runtime.conversation_outbox_thread is None
    assert runtime.conversation_outbox_status["state"] == "disabled"
    assert runtime.component_status["conversation_outbox"]["status"] == "degraded"
    assert runtime.component_status["conversation_outbox"]["table_exists"] is False
