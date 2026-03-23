from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from threading import Event, Thread
from typing import Any, Callable

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import DatabaseUnavailableError
from app.core.timezone import now_beijing_iso
from app.integrations.redis import RedisService, build_redis_bindings
from app.integrations.storage.factory import get_storage_backend
from app.integrations.storage.local import LocalStorageBackend
from app.integrations.storage.minio import MinIOStorageBackend
from app.modules.auth.repository import AuthRepository
from app.modules.auth.service import AuthService, set_auth_service
from app.modules.conversation.assistant_inbox import AuthorityAssistantInboxWorker
from app.modules.conversation.outbox import ConversationOutboxRepository
from app.modules.conversation.outbox_worker import ChatJsonOutboxWorker
from app.modules.conversation.upload_processing_worker import UploadProcessingWorker
from app.modules.conversation.repository import ConversationRepository
from app.modules.conversation.service import ConversationService, set_conversation_service
from app.modules.quota.repository import QuotaRepository
from app.modules.quota.service import QuotaService, set_quota_service
from app.modules.retrieval.service import retrieval_service


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return now_beijing_iso()


def _storage_backend_name(storage_backend: Any | None) -> str:
    if storage_backend is None:
        return "missing"
    name = storage_backend.__class__.__name__.lower()
    if "minio" in name:
        return "minio"
    if "local" in name:
        return "local"
    return storage_backend.__class__.__name__


@dataclass
class PublicServiceRuntime:
    settings: Settings
    started_at: str = field(default_factory=_now_iso)
    component_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    health_flags: dict[str, str] = field(default_factory=dict)
    db: Database | None = None
    redis_service: RedisService | None = None
    auth_repository: Any | None = None
    auth_service: Any | None = None
    quota_repository: Any | None = None
    quota_service: Any | None = None
    conversation_repository: Any | None = None
    conversation_outbox_repository: Any | None = None
    conversation_service: Any | None = None
    agent: Any | None = None
    generation_runtime: Any | None = None
    vector_db_client: Any | None = None
    vector_collection: Any | None = None
    neo4j_client: Any | None = None
    answer_cache: dict[str, Any] = field(default_factory=dict)
    current_answer_context: str = ""
    logs_dir: Path = field(default_factory=lambda: Path("/tmp/public-service-logs"))
    conversation_outbox_thread: Any | None = None
    conversation_outbox_status: dict[str, Any] = field(default_factory=dict)
    current_pdf_path: str | None = None
    upload_folder: Path = field(default_factory=lambda: Path("uploads"))
    upload_processing_worker: Any | None = None
    init_agent: Callable[[], bool] | None = None
    storage_backend: Any | None = None
    conversation_outbox_worker: Any | None = None
    conversation_outbox_stop_event: Any | None = None
    authority_assistant_inbox_worker: Any | None = None
    authority_assistant_inbox_thread: Any | None = None
    authority_assistant_inbox_stop_event: Any | None = None
    authority_assistant_inbox_status: dict[str, Any] = field(default_factory=dict)


def _set_component_status(
    runtime: PublicServiceRuntime,
    component: str,
    *,
    status: str,
    detail: str = "",
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": str(status or "unknown"),
        "detail": str(detail or ""),
        "error": str(error or ""),
        "updated_at": _now_iso(),
    }
    if extra:
        payload.update(extra)
    runtime.component_status[component] = payload
    runtime.health_flags[component] = payload["status"]


def _bootstrap_database(runtime: PublicServiceRuntime) -> None:
    runtime.db = Database(settings=runtime.settings)
    try:
        ok = bool(runtime.db.ping())
        _set_component_status(
            runtime,
            "database",
            status="ok" if ok else "degraded",
            detail="mysql connected" if ok else "mysql ping returned false",
        )
    except Exception as exc:
        _set_component_status(
            runtime,
            "database",
            status="degraded",
            detail="mysql unavailable",
            error=str(exc),
        )


def _bootstrap_redis(runtime: PublicServiceRuntime) -> None:
    bindings = build_redis_bindings(settings=runtime.settings)
    runtime.redis_service = RedisService.from_prefix(
        client=bindings.client,
        key_prefix=str(runtime.settings.redis_key_prefix or "agentcode"),
    )
    status = "ok"
    if not bindings.enabled:
        status = "skipped"
    elif not bindings.available:
        status = "degraded"
    _set_component_status(
        runtime,
        "redis",
        status=status,
        detail=bindings.detail,
        error=bindings.error,
        extra={
            "enabled": bindings.enabled,
            "available": bindings.available,
            "library_available": bindings.library_available,
            "url": bindings.url,
            "key_prefix": bindings.key_prefix,
        },
    )


def _bootstrap_storage(runtime: PublicServiceRuntime) -> None:
    try:
        backend = runtime.storage_backend or get_storage_backend(
            project_root=str(runtime.settings.local_storage_root),
            force_new=True,
        )
        runtime.storage_backend = backend
        backend_name = _storage_backend_name(backend)
        status = "ok"
        detail = f"{backend_name} backend ready"
        if isinstance(backend, MinIOStorageBackend):
            try:
                client = getattr(backend, "_client", None)
                bucket = str(getattr(backend, "_bucket", "") or "")
                if client is not None and bucket:
                    client.bucket_exists(bucket)
                    detail = f"minio bucket ready: {bucket}"
            except Exception as exc:
                _set_component_status(
                    runtime,
                    "storage",
                    status="degraded",
                    detail="minio unavailable, backend constructed but health probe failed",
                    error=str(exc),
                    extra={"backend": backend_name},
                )
                return
        elif isinstance(backend, LocalStorageBackend):
            detail = "local backend active"
        _set_component_status(runtime, "storage", status=status, detail=detail, extra={"backend": backend_name})
    except Exception as exc:
        _set_component_status(
            runtime,
            "storage",
            status="degraded",
            detail="storage bootstrap failed",
            error=str(exc),
            extra={"backend": ""},
        )


def _ensure_runtime_directories(runtime: PublicServiceRuntime) -> None:
    runtime.logs_dir = runtime.settings.logs_dir
    runtime.upload_folder = runtime.settings.uploads_dir
    for path in (
        runtime.settings.data_root,
        runtime.settings.logs_dir,
        runtime.settings.uploads_dir,
        runtime.settings.papers_dir,
        runtime.settings.chat_json_base_dir,
        runtime.settings.vector_db_path,
        runtime.settings.translation_cache_dir,
        runtime.settings.local_storage_root,
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.warning("runtime directory init failed: %s", path, exc_info=True)


def _bootstrap_services(runtime: PublicServiceRuntime) -> None:
    database_status = str(((runtime.component_status or {}).get("database") or {}).get("status") or "").strip().lower()
    status = "ok" if database_status == "ok" else "degraded"
    detail = "service wired" if status == "ok" else "service wired but blocked by database"

    runtime.auth_repository = AuthRepository(database=runtime.db)
    runtime.auth_service = AuthService(repo=runtime.auth_repository)
    set_auth_service(runtime.auth_service)
    _set_component_status(
        runtime,
        "auth",
        status=status,
        detail=f"auth {detail}",
    )

    runtime.quota_repository = QuotaRepository(database=runtime.db)
    runtime.quota_service = QuotaService(repo=runtime.quota_repository, redis_service=runtime.redis_service)
    set_quota_service(runtime.quota_service)
    _set_component_status(
        runtime,
        "quota",
        status=status,
        detail=f"quota {detail}",
    )

    runtime.conversation_repository = ConversationRepository(database=runtime.db)
    runtime.conversation_outbox_repository = ConversationOutboxRepository(database=runtime.db)
    runtime.conversation_service = ConversationService(
        repo=runtime.conversation_repository,
        outbox_repo=runtime.conversation_outbox_repository,
        workspace_root=runtime.settings.data_root,
        redis_service=runtime.redis_service,
    )
    set_conversation_service(runtime.conversation_service)
    _set_component_status(
        runtime,
        "conversation",
        status=status,
        detail=f"conversation {detail}",
    )


def _build_agent_adapter(*, graph: Any | None, collection: Any | None) -> Any | None:
    if graph is None and collection is None:
        return None
    semantic_expert = SimpleNamespace(collection=collection) if collection is not None else None
    return SimpleNamespace(graph=graph, semantic_expert=semantic_expert)


def _build_init_agent(runtime: PublicServiceRuntime) -> Callable[[], bool]:
    def _init_agent() -> bool:
        include_neo4j = bool(str(os.getenv("NEO4J_URL", "") or "").strip())
        bindings = retrieval_service.build_bindings(
            project_root=str(runtime.settings.data_root),
            include_neo4j=include_neo4j,
            logger=logger,
        )
        runtime.vector_db_client = bindings.vector_db_client
        runtime.vector_collection = bindings.chroma.collection
        runtime.neo4j_client = bindings.neo4j_client
        graph = None
        if bindings.neo4j_client is not None and bool(getattr(bindings.neo4j_client, "available", False)):
            graph = getattr(bindings.neo4j_client, "graph", None)
        runtime.agent = _build_agent_adapter(graph=graph, collection=bindings.chroma.collection)
        runtime.generation_runtime = None
        return runtime.agent is not None or runtime.vector_db_client is not None

    return _init_agent


def _bootstrap_retrieval(runtime: PublicServiceRuntime) -> None:
    runtime.init_agent = _build_init_agent(runtime)
    try:
        success = bool(runtime.init_agent())
    except Exception as exc:
        runtime.agent = None
        runtime.vector_db_client = None
        runtime.vector_collection = None
        runtime.neo4j_client = None
        _set_component_status(
            runtime,
            "retrieval",
            status="degraded",
            detail="retrieval bootstrap failed",
            error=str(exc),
        )
        _set_component_status(
            runtime,
            "agent",
            status="degraded",
            detail="knowledge runtime bootstrap failed",
            error=str(exc),
        )
        return

    neo4j_status = "skipped"
    neo4j_error = ""
    if runtime.neo4j_client is not None:
        if runtime.neo4j_client.available and not runtime.neo4j_client.degraded:
            neo4j_status = "ok"
        elif runtime.neo4j_client.available:
            neo4j_status = "degraded"
        else:
            neo4j_status = "degraded"
        neo4j_error = str(runtime.neo4j_client.error or "")

    chroma_available = runtime.vector_collection is not None
    retrieval_status = "ok" if (success or chroma_available or neo4j_status == "ok") else "degraded"
    _set_component_status(
        runtime,
        "retrieval",
        status=retrieval_status,
        detail="knowledge retrieval runtime ready" if retrieval_status == "ok" else "knowledge retrieval runtime degraded",
        extra={
            "vector_db_path": str(getattr(runtime.vector_db_client, "db_path", "") or ""),
            "vector_collection_name": str(getattr(runtime.vector_db_client, "collection_name", "") or ""),
            "chroma_available": chroma_available,
            "neo4j_status": neo4j_status,
            "neo4j_error": neo4j_error,
        },
    )
    _set_component_status(
        runtime,
        "agent",
        status="ok" if runtime.agent is not None else "degraded",
        detail="knowledge compatibility adapter ready" if runtime.agent is not None else "knowledge compatibility adapter unavailable",
    )


def _build_pdf_text_extractor() -> tuple[Any | None, dict[str, Any]]:
    try:
        import fitz  # type: ignore
    except Exception as exc:
        return None, {"pdf_extract_available": False, "pdf_extract_error": str(exc)}

    def _extract_pdf_text(local_path: str, *, max_pages: int = 20, exclude_references: bool = False) -> str:
        _ = exclude_references
        doc = fitz.open(local_path)
        try:
            parts: list[str] = []
            page_limit = max(1, int(max_pages or 20))
            for page_index, page in enumerate(doc):
                if page_index >= page_limit:
                    break
                text = page.get_text("text")
                if text:
                    parts.append(str(text))
            return "\n".join(parts)
        finally:
            doc.close()

    return _extract_pdf_text, {"pdf_extract_available": True, "pdf_extract_error": ""}


def _bootstrap_upload_processing(runtime: PublicServiceRuntime) -> None:
    extractor, extra = _build_pdf_text_extractor()
    runtime.upload_processing_worker = UploadProcessingWorker(
        conversation_service=runtime.conversation_service,
        extract_pdf_text_fn=extractor,
        redis_service=runtime.redis_service,
        logger=logger,
    )
    worker = runtime.upload_processing_worker
    enabled = bool(getattr(worker, "enabled", True))
    max_workers = getattr(getattr(worker, "_config", None), "max_workers", None)
    pdf_extract_available = bool(extra.get("pdf_extract_available"))
    detail = "upload processing worker ready" if enabled else "upload processing disabled"
    status = "ok" if enabled else "skipped"
    if enabled and not pdf_extract_available:
        detail = "upload processing worker degraded; pdf extractor unavailable"
        status = "degraded"
    recovery_summary: dict[str, Any] | None = None
    if runtime.conversation_service is not None:
        try:
            recovery_summary = runtime.conversation_service.recover_pending_upload_processing_tasks(worker=worker)
        except Exception as exc:
            logger.warning("upload processing recovery bootstrap failed: %s", exc)
            recovery_summary = {"success": False, "error": str(exc)}
    _set_component_status(
        runtime,
        "upload_processing",
        status=status,
        detail=detail,
        extra={
            "enabled": enabled,
            "max_workers": int(max_workers) if max_workers is not None else None,
            "recovery": recovery_summary,
            **extra,
        },
    )


def create_runtime(settings: Settings | None = None) -> PublicServiceRuntime:
    runtime = PublicServiceRuntime(settings=settings or get_settings())
    runtime.component_status = {}
    runtime.health_flags = {}
    _ensure_runtime_directories(runtime)
    runtime.storage_backend = get_storage_backend(project_root=str(runtime.settings.local_storage_root), force_new=True)
    runtime.conversation_outbox_status = {
        "state": "uninitialized",
        "thread_alive": False,
        "loops": 0,
        "last_summary": None,
        "last_error": "",
        "last_run_at": None,
    }
    runtime.authority_assistant_inbox_status = {
        "state": "uninitialized",
        "thread_alive": False,
        "loops": 0,
        "last_summary": None,
        "last_error": "",
        "last_run_at": None,
        "backlog": 0,
        "processing": 0,
        "failed": 0,
        "enabled": True,
    }
    _set_component_status(runtime, "public_modules", status="ok", detail="public service modules wired")
    _bootstrap_database(runtime)
    _bootstrap_redis(runtime)
    _bootstrap_storage(runtime)
    _bootstrap_services(runtime)
    _bootstrap_retrieval(runtime)
    _bootstrap_upload_processing(runtime)
    runtime.logs_dir.mkdir(parents=True, exist_ok=True)
    return runtime


def _outbox_wait_seconds(worker: Any) -> float:
    try:
        return max(0.05, float(getattr(getattr(worker, "config", None), "poll_interval_ms", 1000)) / 1000.0)
    except Exception:
        return 1.0


def _authority_assistant_inbox_wait_seconds(worker: Any) -> float:
    try:
        return max(0.05, float(getattr(getattr(worker, "config", None), "poll_interval_ms", 1000)) / 1000.0)
    except Exception:
        return 1.0


def _authority_assistant_inbox_probe(runtime: PublicServiceRuntime) -> dict[str, Any]:
    repo = getattr(runtime, "conversation_repository", None)
    if repo is None or not hasattr(repo, "authority_assistant_inbox_status"):
        return {}
    try:
        status = repo.authority_assistant_inbox_status()
    except Exception as exc:
        logger.warning("authority assistant inbox probe failed: %s", exc)
        return {"probe_error": str(exc)}
    return dict(status or {})


def _run_conversation_outbox_loop(runtime: PublicServiceRuntime) -> None:
    worker = runtime.conversation_outbox_worker
    stop_event = runtime.conversation_outbox_stop_event
    if worker is None or stop_event is None:
        return

    runtime.health_flags["conversation_outbox"] = "running"
    runtime.conversation_outbox_status.update({"state": "running", "thread_alive": True})
    _set_component_status(
        runtime,
        "conversation_outbox",
        status="ok",
        detail="conversation outbox worker running",
        extra={"thread_alive": True},
    )

    while not stop_event.is_set():
        try:
            summary = worker.run_once()
            loops = int(runtime.conversation_outbox_status.get("loops") or 0) + 1
            runtime.health_flags["conversation_outbox"] = "ok"
            runtime.conversation_outbox_status.update(
                {
                    "state": "running",
                    "thread_alive": True,
                    "loops": loops,
                    "last_summary": dict(summary),
                    "last_error": "",
                    "last_run_at": _now_iso(),
                }
            )
            current_component_status = str(((runtime.component_status or {}).get("conversation_outbox") or {}).get("status") or "").strip().lower()
            if current_component_status != "ok":
                _set_component_status(
                    runtime,
                    "conversation_outbox",
                    status="ok",
                    detail="conversation outbox worker running",
                    extra={"thread_alive": True},
                )
        except DatabaseUnavailableError as exc:
            runtime.health_flags["conversation_outbox"] = "degraded"
            runtime.conversation_outbox_status.update(
                {
                    "state": "degraded",
                    "thread_alive": True,
                    "last_error": str(exc),
                    "last_run_at": _now_iso(),
                }
            )
            current_component = (runtime.component_status or {}).get("conversation_outbox") or {}
            current_error = str(current_component.get("error") or "")
            if current_error != str(exc):
                logger.warning("conversation outbox worker waiting for database: %s", exc)
            _set_component_status(
                runtime,
                "conversation_outbox",
                status="degraded",
                detail="conversation outbox worker waiting for database",
                error=str(exc),
                extra={"thread_alive": True},
            )
        except Exception as exc:
            logger.exception("conversation outbox worker loop failed")
            runtime.health_flags["conversation_outbox"] = "degraded"
            runtime.conversation_outbox_status.update(
                {
                    "state": "degraded",
                    "thread_alive": True,
                    "last_error": str(exc),
                    "last_run_at": _now_iso(),
                }
            )
            _set_component_status(
                runtime,
                "conversation_outbox",
                status="degraded",
                detail="conversation outbox worker loop failed",
                error=str(exc),
                extra={"thread_alive": True},
            )
        stop_event.wait(_outbox_wait_seconds(worker))

    runtime.health_flags["conversation_outbox"] = "stopped"
    runtime.conversation_outbox_status.update({"state": "stopped", "thread_alive": False})
    _set_component_status(
        runtime,
        "conversation_outbox",
        status="stopped",
        detail="conversation outbox worker stopped",
        extra={"thread_alive": False},
    )


def _run_authority_assistant_inbox_loop(runtime: PublicServiceRuntime) -> None:
    worker = runtime.authority_assistant_inbox_worker
    stop_event = runtime.authority_assistant_inbox_stop_event
    if worker is None or stop_event is None:
        return

    snapshot = _authority_assistant_inbox_probe(runtime)
    runtime.health_flags["authority_assistant_inbox"] = "running"
    runtime.authority_assistant_inbox_status.update({"state": "running", "thread_alive": True, **snapshot})
    _set_component_status(
        runtime,
        "authority_assistant_inbox",
        status="ok",
        detail="authority assistant inbox worker running",
        extra={"thread_alive": True, **snapshot},
    )

    while not stop_event.is_set():
        try:
            summary = worker.run_once()
            loops = int(runtime.authority_assistant_inbox_status.get("loops") or 0) + 1
            snapshot = _authority_assistant_inbox_probe(runtime)
            runtime.health_flags["authority_assistant_inbox"] = "ok"
            runtime.authority_assistant_inbox_status.update(
                {
                    "state": "running",
                    "thread_alive": True,
                    "loops": loops,
                    "last_summary": dict(summary),
                    "last_error": "",
                    "last_run_at": _now_iso(),
                    **snapshot,
                }
            )
            current_status = str(((runtime.component_status or {}).get("authority_assistant_inbox") or {}).get("status") or "").strip().lower()
            if current_status != "ok":
                _set_component_status(
                    runtime,
                    "authority_assistant_inbox",
                    status="ok",
                    detail="authority assistant inbox worker running",
                    extra={"thread_alive": True, **snapshot},
                )
        except DatabaseUnavailableError as exc:
            snapshot = _authority_assistant_inbox_probe(runtime)
            runtime.health_flags["authority_assistant_inbox"] = "degraded"
            runtime.authority_assistant_inbox_status.update(
                {
                    "state": "degraded",
                    "thread_alive": True,
                    "last_error": str(exc),
                    "last_run_at": _now_iso(),
                    **snapshot,
                }
            )
            _set_component_status(
                runtime,
                "authority_assistant_inbox",
                status="degraded",
                detail="authority assistant inbox worker waiting for database",
                error=str(exc),
                extra={"thread_alive": True, **snapshot},
            )
        except Exception as exc:
            logger.exception("authority assistant inbox worker loop failed")
            snapshot = _authority_assistant_inbox_probe(runtime)
            runtime.health_flags["authority_assistant_inbox"] = "degraded"
            runtime.authority_assistant_inbox_status.update(
                {
                    "state": "degraded",
                    "thread_alive": True,
                    "last_error": str(exc),
                    "last_run_at": _now_iso(),
                    **snapshot,
                }
            )
            _set_component_status(
                runtime,
                "authority_assistant_inbox",
                status="degraded",
                detail="authority assistant inbox worker loop failed",
                error=str(exc),
                extra={"thread_alive": True, **snapshot},
            )
        stop_event.wait(_authority_assistant_inbox_wait_seconds(worker))

    snapshot = _authority_assistant_inbox_probe(runtime)
    runtime.health_flags["authority_assistant_inbox"] = "stopped"
    runtime.authority_assistant_inbox_status.update({"state": "stopped", "thread_alive": False, **snapshot})
    _set_component_status(
        runtime,
        "authority_assistant_inbox",
        status="stopped",
        detail="authority assistant inbox worker stopped",
        extra={"thread_alive": False, **snapshot},
    )


def _start_authority_assistant_inbox_worker(
    runtime: PublicServiceRuntime,
    *,
    worker_cls: type[AuthorityAssistantInboxWorker] = AuthorityAssistantInboxWorker,
    thread_cls: type[Thread] = Thread,
    event_cls: type[Event] = Event,
) -> None:
    stop_event = event_cls()
    worker = worker_cls(
        repository=runtime.conversation_repository,
        conversation_service=runtime.conversation_service,
    )
    thread = thread_cls(
        target=_run_authority_assistant_inbox_loop,
        args=(runtime,),
        name="authority-assistant-inbox-worker",
        daemon=True,
    )
    runtime.authority_assistant_inbox_worker = worker
    runtime.authority_assistant_inbox_stop_event = stop_event
    runtime.authority_assistant_inbox_thread = thread
    snapshot = _authority_assistant_inbox_probe(runtime)
    runtime.authority_assistant_inbox_status.update({"state": "starting", "thread_alive": False, "last_error": "", **snapshot})
    _set_component_status(
        runtime,
        "authority_assistant_inbox",
        status="ok",
        detail="authority assistant inbox worker starting",
        extra={"thread_alive": False, **snapshot},
    )
    thread.start()


def _stop_authority_assistant_inbox_worker(runtime: PublicServiceRuntime, *, join_timeout: float = 2.0) -> None:
    stop_event = runtime.authority_assistant_inbox_stop_event
    thread = runtime.authority_assistant_inbox_thread
    if stop_event is not None:
        stop_event.set()
    if thread is not None and hasattr(thread, "join"):
        try:
            thread.join(timeout=join_timeout)
        except Exception:
            logger.warning("authority assistant inbox worker join failed", exc_info=True)
    snapshot = _authority_assistant_inbox_probe(runtime)
    thread_alive = bool(thread.is_alive()) if thread is not None and hasattr(thread, "is_alive") else False
    runtime.authority_assistant_inbox_status.update({"state": "stopped", "thread_alive": thread_alive, **snapshot})
    _set_component_status(
        runtime,
        "authority_assistant_inbox",
        status="stopped",
        detail="authority assistant inbox worker stopped",
        extra={"thread_alive": thread_alive, **snapshot},
    )


def _start_conversation_outbox_worker(
    runtime: PublicServiceRuntime,
    *,
    worker_cls: type[ChatJsonOutboxWorker] = ChatJsonOutboxWorker,
    thread_cls: type[Thread] = Thread,
    event_cls: type[Event] = Event,
) -> None:
    support: dict[str, Any] | None = None
    repo = runtime.conversation_outbox_repository
    if repo is not None and hasattr(repo, "support_status"):
        try:
            support = repo.support_status()
        except Exception as exc:
            logger.warning("conversation outbox support probe failed: %s", exc)
    if support is not None and not bool(support.get("enabled", True)):
        runtime.conversation_outbox_worker = None
        runtime.conversation_outbox_stop_event = None
        runtime.conversation_outbox_thread = None
        runtime.conversation_outbox_status.update(
            {
                "state": "disabled",
                "thread_alive": False,
                "loops": 0,
                "last_summary": None,
                "last_error": "conversation_json_outbox table missing",
                "last_run_at": None,
                **support,
            }
        )
        _set_component_status(
            runtime,
            "conversation_outbox",
            status="degraded",
            detail="conversation outbox disabled; required table missing",
            extra={"thread_alive": False, **support},
        )
        return

    stop_event = event_cls()
    worker = worker_cls(
        outbox_repo=runtime.conversation_outbox_repository,
        conversation_repo=runtime.conversation_repository,
        json_store=getattr(runtime.conversation_service, "_json_store", None),
        storage_backend=runtime.storage_backend,
    )
    thread = thread_cls(
        target=_run_conversation_outbox_loop,
        args=(runtime,),
        name="conversation-outbox-worker",
        daemon=True,
    )
    runtime.conversation_outbox_worker = worker
    runtime.conversation_outbox_stop_event = stop_event
    runtime.conversation_outbox_thread = thread
    runtime.conversation_outbox_status.update({"state": "starting", "thread_alive": False, "last_error": ""})
    _set_component_status(
        runtime,
        "conversation_outbox",
        status="ok",
        detail="conversation outbox worker starting",
        extra={"thread_alive": False},
    )
    thread.start()


def _stop_conversation_outbox_worker(runtime: PublicServiceRuntime, *, join_timeout: float = 2.0) -> None:
    stop_event = runtime.conversation_outbox_stop_event
    thread = runtime.conversation_outbox_thread
    if stop_event is not None:
        stop_event.set()
    if thread is not None and hasattr(thread, "join"):
        try:
            thread.join(timeout=join_timeout)
        except Exception:
            logger.warning("conversation outbox worker join failed", exc_info=True)
    runtime.conversation_outbox_status.update(
        {
            "state": "stopped",
            "thread_alive": bool(thread.is_alive()) if thread is not None and hasattr(thread, "is_alive") else False,
        }
    )
    _set_component_status(
        runtime,
        "conversation_outbox",
        status="stopped",
        detail="conversation outbox worker stopped",
        extra={
            "thread_alive": bool(thread.is_alive()) if thread is not None and hasattr(thread, "is_alive") else False,
        },
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = create_runtime(getattr(app.state, "settings", None))
    app.state.runtime = runtime
    app.state.auth_service = runtime.auth_service
    app.state.quota_service = runtime.quota_service
    app.state.conversation_service = runtime.conversation_service
    app.state.redis_service = runtime.redis_service
    runtime.health_flags["startup"] = "ok"
    _start_conversation_outbox_worker(runtime)
    _start_authority_assistant_inbox_worker(runtime)
    try:
        yield
    finally:
        worker = getattr(runtime, "upload_processing_worker", None)
        if worker is not None:
            try:
                worker.shutdown(wait=False)
            except Exception:
                logger.warning("upload processing worker shutdown failed", exc_info=True)
        _stop_authority_assistant_inbox_worker(runtime)
        _stop_conversation_outbox_worker(runtime)
        runtime.health_flags["shutdown"] = "ok"
