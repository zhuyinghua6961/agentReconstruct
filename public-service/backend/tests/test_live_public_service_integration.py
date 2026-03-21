from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None

from app.core import runtime as runtime_module
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.deps import AuthContext
from app.core.errors import DatabaseUnavailableError
from app.integrations.redis import build_redis_bindings
from app.integrations.storage.minio import MinIOStorageBackend
from app.main import create_app
from app.modules.auth.deps import get_optional_auth_context, require_auth_context
from app.modules.auth.repository import AuthRepository
from app.modules.auth.service import _hash_password
from app.modules.conversation.cache import (
    build_conversation_detail_cache_key,
    build_conversation_list_recent_pages_key,
)
from app.modules.conversation.outbox_worker import ChatJsonOutboxWorker


@dataclass
class _LiveContext:
    client: TestClient
    runtime: object
    settings: Settings
    database: Database
    user_id: int
    username: str
    redis_prefix: str
    tmp_path: Path


def _load_live_env() -> dict[str, str]:
    if dotenv_values is None:
        pytest.skip("python-dotenv unavailable; cannot load live env for integration tests")

    raw = str(os.getenv("PUBLIC_SERVICE_TEST_ENV_FILES", "") or "").strip()
    if not raw:
        pytest.skip("PUBLIC_SERVICE_TEST_ENV_FILES not set; skip live integration tests")

    env_files = [Path(item).expanduser().resolve() for item in raw.split(os.pathsep) if item.strip()]
    merged: dict[str, str] = {}
    for env_file in env_files:
        if not env_file.exists():
            continue
        for key, value in dotenv_values(env_file).items():
            if not key or value is None:
                continue
            merged[str(key)] = str(value)
    if not merged:
        pytest.skip("PUBLIC_SERVICE_TEST_ENV_FILES yielded no env values; cannot run live integration tests")
    return merged


def _configure_live_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Settings, str]:
    merged = _load_live_env()
    for key, value in merged.items():
        monkeypatch.setenv(key, value)

    redis_prefix = f"public-service-it-{uuid4().hex[:10]}"
    monkeypatch.setenv("REDIS_KEY_PREFIX", redis_prefix)
    monkeypatch.setenv("CHAT_JSON_BASE_DIR", str((tmp_path / "chat-json").resolve()))

    get_settings.cache_clear()
    import app.integrations.storage.factory as storage_factory_module

    storage_factory_module._backend_instance = None
    settings = get_settings()

    database = Database(settings=settings)
    try:
        if not database.ping():
            pytest.skip("live mysql ping returned false")
    except Exception as exc:
        pytest.skip(f"live mysql unavailable: {exc}")

    bindings = build_redis_bindings(settings=settings)
    if not bindings.available:
        pytest.skip(f"live redis unavailable: {bindings.detail or bindings.error}")

    if not (settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key):
        pytest.skip("live minio config missing")
    try:
        backend = MinIOStorageBackend(
            endpoint=str(settings.minio_endpoint),
            access_key=str(settings.minio_access_key),
            secret_key=str(settings.minio_secret_key),
            bucket=str(settings.minio_bucket),
            secure=bool(settings.minio_secure),
            region=settings.minio_region,
        )
        if not backend._client.bucket_exists(settings.minio_bucket):
            pytest.skip("live minio bucket unavailable")
    except Exception as exc:
        pytest.skip(f"live minio unavailable: {exc}")

    return settings, redis_prefix


def _collect_storage_refs(database: Database, *, user_id: int) -> list[str]:
    refs: list[str] = []
    with database.connection() as conn:
        with conn.cursor() as cursor:
            statements = [
                (
                    """
                    SELECT storage_ref
                    FROM conversation_files
                    WHERE user_id = %s AND storage_ref IS NOT NULL AND storage_ref <> ''
                    """,
                    (int(user_id),),
                ),
                (
                    """
                    SELECT chat_json_storage_ref AS storage_ref
                    FROM conversations
                    WHERE user_id = %s AND chat_json_storage_ref IS NOT NULL AND chat_json_storage_ref <> ''
                    """,
                    (int(user_id),),
                ),
            ]
            for sql, params in statements:
                try:
                    cursor.execute(sql, params)
                except Exception:
                    continue
                rows = cursor.fetchall() or []
                for row in rows:
                    ref = str((row or {}).get("storage_ref") or "").strip()
                    if ref:
                        refs.append(ref)
    return refs


def _cleanup_live_minio_objects(settings: Settings, *, storage_refs: list[str]) -> None:
    if not storage_refs:
        return
    try:
        backend = MinIOStorageBackend(
            endpoint=str(settings.minio_endpoint),
            access_key=str(settings.minio_access_key),
            secret_key=str(settings.minio_secret_key),
            bucket=str(settings.minio_bucket),
            secure=bool(settings.minio_secure),
            region=settings.minio_region,
        )
    except Exception:
        return

    for storage_ref in storage_refs:
        raw = str(storage_ref or "").strip()
        if not raw.startswith("minio://"):
            continue
        value = raw[len("minio://") :]
        if "/" not in value:
            continue
        bucket, object_name = value.split("/", 1)
        try:
            backend.delete_object(object_name=object_name, bucket=bucket or None)
        except Exception:
            continue


def _find_minio_objects_with_suffix(settings: Settings, *, prefix: str, suffix: str) -> list[str]:
    try:
        backend = MinIOStorageBackend(
            endpoint=str(settings.minio_endpoint),
            access_key=str(settings.minio_access_key),
            secret_key=str(settings.minio_secret_key),
            bucket=str(settings.minio_bucket),
            secure=bool(settings.minio_secure),
            region=settings.minio_region,
        )
    except Exception:
        return []

    matches: list[str] = []
    try:
        for item in backend._client.list_objects(str(settings.minio_bucket), prefix=prefix, recursive=True):
            object_name = str(getattr(item, "object_name", "") or "")
            if object_name.endswith(suffix):
                matches.append(object_name)
    except Exception:
        return []
    return matches


def _cleanup_live_rows(database: Database, *, user_id: int) -> None:
    statements = [
        ("DELETE FROM conversation_json_outbox WHERE user_id = %s", (int(user_id),)),
        ("DELETE FROM conversation_files WHERE user_id = %s", (int(user_id),)),
        ("DELETE FROM conversation_messages WHERE user_id = %s", (int(user_id),)),
        ("DELETE FROM conversations WHERE user_id = %s", (int(user_id),)),
        ("DELETE FROM user_quota_usage WHERE user_id = %s", (int(user_id),)),
        ("DELETE FROM user_quota_overrides WHERE user_id = %s", (int(user_id),)),
        ("DELETE FROM users WHERE id = %s", (int(user_id),)),
    ]
    with database.connection() as conn:
        with conn.cursor() as cursor:
            for sql, params in statements:
                try:
                    cursor.execute(sql, params)
                except Exception:
                    continue


def _cleanup_live_redis(settings: Settings, *, redis_prefix: str) -> None:
    bindings = build_redis_bindings(settings=settings)
    client = bindings.client
    if not bindings.available or client is None:
        return
    try:
        keys = list(client.scan_iter(match=f"{redis_prefix}*"))
    except Exception:
        return
    if not keys:
        return
    try:
        client.delete(*keys)
    except Exception:
        return


@pytest.fixture()
def live_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _LiveContext:
    settings, redis_prefix = _configure_live_settings(monkeypatch, tmp_path)
    database = Database(settings=settings)
    auth_repo = AuthRepository(database=database)

    username = f"public_service_it_{uuid4().hex[:12]}"
    user_id = auth_repo.create_user(
        username=username,
        password_hash=_hash_password("AgentPassw0rd!!"),
        role="user",
        user_type=3,
    )
    context = AuthContext(user_id=int(user_id), role="user", username=username)

    app = create_app(settings=settings)
    app.dependency_overrides[require_auth_context] = lambda: context
    app.dependency_overrides[get_optional_auth_context] = lambda: context

    try:
        with TestClient(app) as client:
            runtime = client.app.state.runtime
            runtime_module._stop_conversation_outbox_worker(runtime)
            runtime.upload_folder = (tmp_path / "uploads").resolve()
            runtime.upload_folder.mkdir(parents=True, exist_ok=True)
            yield _LiveContext(
                client=client,
                runtime=runtime,
                settings=settings,
                database=database,
                user_id=int(user_id),
                username=username,
                redis_prefix=redis_prefix,
                tmp_path=tmp_path,
            )
    finally:
        app.dependency_overrides.clear()
        storage_refs = _collect_storage_refs(database, user_id=int(user_id))
        _cleanup_live_minio_objects(settings, storage_refs=storage_refs)
        _cleanup_live_rows(database, user_id=int(user_id))
        _cleanup_live_redis(settings, redis_prefix=redis_prefix)
        get_settings.cache_clear()
        import app.integrations.storage.factory as storage_factory_module

        storage_factory_module._backend_instance = None


def test_live_upload_pdf_updates_mysql_and_redis_and_only_counts_bound_quota(live_context: _LiveContext):
    runtime = live_context.runtime
    client = live_context.client
    runtime.upload_processing_worker = None

    before = runtime.quota_service.check_quota(user_id=live_context.user_id, quota_type="file_upload")
    assert before["success"] is True
    before_current = int(before.get("current") or 0)

    create_response = client.post("/api/v1/conversations", json={"title": "live integration upload"})
    assert create_response.status_code == 201
    conversation_id = int(create_response.json()["data"]["conversation_id"])

    list_response = client.get("/api/v1/conversations", params={"page": 1, "page_size": 20})
    assert list_response.status_code == 200

    recent_pages_key = build_conversation_list_recent_pages_key(
        redis_service=runtime.redis_service,
        user_id=live_context.user_id,
    )
    recent_pages_payload = runtime.redis_service.get_json(recent_pages_key, default=None)
    assert recent_pages_payload["pages"][0] == {"page": 1, "page_size": 20}

    upload_response = client.post(
        "/api/v1/upload_pdf",
        files={"file": ("live.pdf", b"%PDF-1.4\nlive integration", "application/pdf")},
        data={"conversation_id": str(conversation_id)},
    )
    assert upload_response.status_code == 200
    upload_payload = upload_response.json()
    assert upload_payload["success"] is True
    assert upload_payload["conversation_bound"] is True
    assert Path(upload_payload["filepath"]).exists()
    assert str(upload_payload.get("storage_ref") or "").startswith("minio://")

    minio_backend = MinIOStorageBackend(
        endpoint=str(live_context.settings.minio_endpoint),
        access_key=str(live_context.settings.minio_access_key),
        secret_key=str(live_context.settings.minio_secret_key),
        bucket=str(live_context.settings.minio_bucket),
        secure=bool(live_context.settings.minio_secure),
        region=live_context.settings.minio_region,
    )
    upload_storage_ref = str(upload_payload["storage_ref"])
    upload_object_name = upload_storage_ref[len(f"minio://{live_context.settings.minio_bucket}/") :]
    assert minio_backend.object_exists(object_name=upload_object_name) is True

    files_response = client.get(f"/api/v1/conversations/{conversation_id}/files")
    assert files_response.status_code == 200
    files_payload = files_response.json()
    assert len(files_payload["data"]["files"]) == 1
    assert int(files_payload["data"]["files"][0]["id"]) == int(upload_payload["file_id"])

    after_bound = runtime.quota_service.check_quota(user_id=live_context.user_id, quota_type="file_upload")
    assert after_bound["success"] is True
    assert int(after_bound.get("current") or 0) == before_current + 1

    detail_response = client.get(f"/api/v1/conversations/{conversation_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert len(detail_payload["data"]["uploaded_files"]) == 1

    detail_cache_key = build_conversation_detail_cache_key(
        redis_service=runtime.redis_service,
        user_id=live_context.user_id,
        conversation_id=conversation_id,
    )
    cached_detail = runtime.redis_service.get_json(detail_cache_key, default=None)
    assert cached_detail["data"]["uploaded_files"][0]["id"] == int(upload_payload["file_id"])

    orphan_filename = f"orphan-{uuid4().hex}.pdf"
    before_orphan_objects = _find_minio_objects_with_suffix(
        live_context.settings,
        prefix="uploads/pdf/",
        suffix=orphan_filename,
    )
    unbound_upload_response = client.post(
        "/api/v1/upload_pdf",
        files={"file": (orphan_filename, b"%PDF-1.4\nlegacy", "application/pdf")},
    )
    assert unbound_upload_response.status_code == 200
    assert unbound_upload_response.json() == {"error": "缺少会话上下文，无法关联上传文件"}
    assert list(runtime.upload_folder.iterdir()) == [Path(upload_payload["filepath"])]

    after_orphan_objects = _find_minio_objects_with_suffix(
        live_context.settings,
        prefix="uploads/pdf/",
        suffix=orphan_filename,
    )
    assert after_orphan_objects == before_orphan_objects

    after_unbound = runtime.quota_service.check_quota(user_id=live_context.user_id, quota_type="file_upload")
    assert after_unbound["success"] is True
    assert int(after_unbound.get("current") or 0) == int(after_bound.get("current") or 0)


class _FailingStorageBackend:
    def upload_file(self, *, local_path: str, object_name: str, content_type: str | None = None) -> str:
        _ = local_path, object_name, content_type
        raise RuntimeError("forced_live_sync_failure")


class _StopAfterRecovery:
    def __init__(self, *, max_wait_calls: int = 4) -> None:
        self._wait_calls = 0
        self._stopped = False
        self._max_wait_calls = max(2, int(max_wait_calls))

    def is_set(self) -> bool:
        return self._stopped

    def set(self) -> None:
        self._stopped = True

    def wait(self, _seconds: float) -> None:
        self._wait_calls += 1
        if self._wait_calls >= self._max_wait_calls:
            self._stopped = True


class _RecoveringWorker:
    def __init__(self, *, delegate: ChatJsonOutboxWorker) -> None:
        self._delegate = delegate
        self.config = delegate.config
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def run_once(self) -> dict[str, int]:
        self._calls += 1
        if self._calls == 1:
            raise DatabaseUnavailableError("db_unavailable")
        return self._delegate.run_once()


def test_live_outbox_loop_recovers_and_flushes_real_mysql_task(live_context: _LiveContext):
    runtime = runtime_module.create_runtime(live_context.settings)
    service = runtime.conversation_service
    original_storage_backend = getattr(service._json_store, "_storage_backend", None)
    service._json_store._storage_backend = _FailingStorageBackend()

    try:
        created = service.create_conversation(user_id=live_context.user_id, title="live outbox recovery")
    finally:
        service._json_store._storage_backend = original_storage_backend

    assert created["success"] is True
    conversation_id = int(created["data"]["conversation_id"])

    row_before = runtime.conversation_repository.get_conversation(
        conversation_id=conversation_id,
        user_id=live_context.user_id,
    )
    assert row_before is not None
    assert str(row_before.get("chat_json_sync_status") or "") == "sync_failed"

    with live_context.database.connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, status, last_error
                FROM conversation_json_outbox
                WHERE conversation_id = %s AND user_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (conversation_id, live_context.user_id),
            )
            outbox_row = cursor.fetchone() or {}
    assert outbox_row
    assert str(outbox_row.get("status") or "") == "pending"

    real_worker = ChatJsonOutboxWorker(
        outbox_repo=runtime.conversation_outbox_repository,
        conversation_repo=runtime.conversation_repository,
        storage_backend=MinIOStorageBackend(
            endpoint=str(live_context.settings.minio_endpoint),
            access_key=str(live_context.settings.minio_access_key),
            secret_key=str(live_context.settings.minio_secret_key),
            bucket=str(live_context.settings.minio_bucket),
            secure=bool(live_context.settings.minio_secure),
            region=live_context.settings.minio_region,
        ),
    )
    runtime.conversation_outbox_worker = _RecoveringWorker(delegate=real_worker)
    runtime.conversation_outbox_stop_event = _StopAfterRecovery(max_wait_calls=5)

    runtime_module._run_conversation_outbox_loop(runtime)

    row_after = runtime.conversation_repository.get_conversation(
        conversation_id=conversation_id,
        user_id=live_context.user_id,
    )
    assert row_after is not None
    assert str(row_after.get("chat_json_sync_status") or "") == "ok"
    assert str(row_after.get("chat_json_storage_ref") or "").startswith("minio://")

    minio_backend = MinIOStorageBackend(
        endpoint=str(live_context.settings.minio_endpoint),
        access_key=str(live_context.settings.minio_access_key),
        secret_key=str(live_context.settings.minio_secret_key),
        bucket=str(live_context.settings.minio_bucket),
        secure=bool(live_context.settings.minio_secure),
        region=live_context.settings.minio_region,
    )
    chat_json_ref = str(row_after.get("chat_json_storage_ref") or "")
    chat_json_object = chat_json_ref[len(f"minio://{live_context.settings.minio_bucket}/") :]
    assert minio_backend.object_exists(object_name=chat_json_object) is True

    with live_context.database.connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, last_error, attempt_count
                FROM conversation_json_outbox
                WHERE id = %s
                """,
                (int(outbox_row["id"]),),
            )
            outbox_after = cursor.fetchone() or {}
    assert str(outbox_after.get("status") or "") == "done"
    assert str(outbox_after.get("last_error") or "") == "ok"
    assert int(outbox_after.get("attempt_count") or 0) == 0

    assert runtime.conversation_outbox_status["last_error"] == ""
    assert int(runtime.conversation_outbox_status["loops"] or 0) >= 1
    assert isinstance(runtime.conversation_outbox_status.get("last_summary"), dict)
