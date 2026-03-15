from __future__ import annotations

from contextlib import contextmanager

from server.services.conversation.conversation_service import ConversationService


class FakeConversationRepo:
    def __init__(self) -> None:
        self.row = {
            "id": 11,
            "user_id": 7,
            "title": "demo",
            "created_at": "2026-03-15T10:00:00+08:00",
            "updated_at": "2026-03-15T10:00:00+08:00",
            "chat_json_storage_ref": "",
            "chat_json_version": 0,
        }
        self.db_messages: list[dict] = []
        self.db_files: list[dict] = []
        self.add_message_calls: list[dict] = []
        self.add_message_with_created_at_calls: list[dict] = []
        self.add_uploaded_file_with_created_at_calls: list[dict] = []
        self.delete_message_calls: list[dict] = []
        self.delete_uploaded_file_calls: list[dict] = []
        self.set_message_count_calls: list[dict] = []
        self.update_chat_json_index_calls: list[dict] = []

    def get_conversation(self, *, conversation_id: int, user_id: int):
        if conversation_id == int(self.row["id"]) and user_id == int(self.row["user_id"]):
            return dict(self.row)
        return None

    def list_messages(self, *, conversation_id: int, user_id: int):
        if conversation_id != int(self.row["id"]) or user_id != int(self.row["user_id"]):
            return []
        return [dict(item) for item in self.db_messages]

    def list_uploaded_files(self, *, conversation_id: int, user_id: int):
        if conversation_id != int(self.row["id"]) or user_id != int(self.row["user_id"]):
            return []
        return [dict(item) for item in self.db_files]

    def add_message(self, *, conversation_id: int, user_id: int, role: str, content: str, metadata: dict | None):
        self.add_message_calls.append(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": role,
                "content": content,
                "metadata": metadata,
            }
        )
        return 101

    def add_message_with_created_at(
        self,
        *,
        conversation_id: int,
        user_id: int,
        role: str,
        content: str,
        metadata: dict | None,
        created_at,
    ):
        next_id = 200 + len(self.add_message_with_created_at_calls) + 1
        payload = {
            "id": next_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "created_at": created_at,
        }
        self.add_message_with_created_at_calls.append(dict(payload))
        self.db_messages.append(dict(payload))
        return next_id

    def add_uploaded_file(
        self,
        *,
        conversation_id: int,
        user_id: int,
        file_type: str,
        file_name: str,
        local_path: str | None,
        storage_ref: str | None,
        content_type: str | None,
        size_bytes: int | None,
    ):
        next_id = 400 + len(self.db_files) + 1
        payload = {
            "id": next_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "file_type": file_type,
            "file_name": file_name,
            "local_path": local_path,
            "storage_ref": storage_ref,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "created_at": "2026-03-15T10:10:00+08:00",
        }
        self.db_files.append(dict(payload))
        return next_id

    def delete_message(self, *, message_id: int, conversation_id: int, user_id: int):
        self.delete_message_calls.append(
            {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
            }
        )
        return 1

    def set_message_count(self, *, conversation_id: int, user_id: int, message_count: int):
        self.set_message_count_calls.append(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "message_count": message_count,
            }
        )
        return 1

    def update_chat_json_index(self, **kwargs):
        self.update_chat_json_index_calls.append(dict(kwargs))
        self.row["chat_json_version"] = int(kwargs["version"])
        self.row["updated_at"] = kwargs["updated_at"]
        return 1

    def get_uploaded_file(self, *, conversation_id: int, user_id: int, file_id: int):
        if conversation_id != int(self.row["id"]) or user_id != int(self.row["user_id"]):
            return None
        for item in self.db_files:
            if int(item.get("id") or 0) == int(file_id):
                return dict(item)
        return None

    def delete_uploaded_file(self, *, conversation_id: int, user_id: int, file_id: int):
        self.delete_uploaded_file_calls.append(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "file_id": file_id,
            }
        )
        before = len(self.db_files)
        self.db_files = [item for item in self.db_files if int(item.get("id") or 0) != int(file_id)]
        return 1 if len(self.db_files) != before else 0

    def add_uploaded_file_with_created_at(
        self,
        *,
        conversation_id: int,
        user_id: int,
        file_type: str,
        file_name: str,
        local_path: str | None,
        storage_ref: str | None,
        content_type: str | None,
        size_bytes: int | None,
        created_at,
    ):
        next_id = 300 + len(self.add_uploaded_file_with_created_at_calls) + 1
        payload = {
            "id": next_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "file_type": file_type,
            "file_name": file_name,
            "local_path": local_path,
            "storage_ref": storage_ref,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "created_at": created_at,
        }
        self.add_uploaded_file_with_created_at_calls.append(dict(payload))
        self.db_files.append(dict(payload))
        return next_id


class FakeJsonStore:
    def __init__(self, *, document: dict | None = None, fail_write: bool = False) -> None:
        self.document = document
        self.fail_write = fail_write
        self.saved_documents: list[dict] = []

    @contextmanager
    def conversation_lock(self, *, user_id: int, conversation_id: int):
        yield

    def load_document(self, *, user_id: int, conversation_id: int):
        return self.document

    def build_default_document(self, **kwargs):
        return {
            "meta": {
                "conversation_id": kwargs["conversation_id"],
                "user_id": kwargs["user_id"],
                "title": kwargs["title"],
                "created_at": kwargs["created_at"],
                "updated_at": kwargs["updated_at"],
                "message_count": kwargs.get("message_count", 0),
                "last_message_at": kwargs["updated_at"] if kwargs.get("message_count", 0) else None,
            },
            "messages": list(kwargs.get("messages") or []),
            "files": list(kwargs.get("files") or []),
            "runtime": {},
        }

    def write_document(self, *, user_id: int, conversation_id: int, document: dict, storage_ref_hint: str | None = None):
        if self.fail_write:
            raise RuntimeError("write failed")
        self.document = document
        self.saved_documents.append(document)
        return {
            "local_path": f"/tmp/{user_id}_{conversation_id}.json",
            "storage_ref": storage_ref_hint or None,
            "content_hash": "hash",
            "size_bytes": 123,
            "sync_status": "ok",
        }

    def conversation_object_name(self, *, user_id: int, conversation_id: int) -> str:
        return f"conversations/{user_id}/{conversation_id}.json"


class FakeOutboxRepo:
    def enqueue_task(self, **kwargs):
        return 1


def _base_document() -> dict:
    return {
        "meta": {
            "conversation_id": 11,
            "user_id": 7,
            "title": "demo",
            "created_at": "2026-03-15T10:00:00+08:00",
            "updated_at": "2026-03-15T10:00:00+08:00",
            "message_count": 0,
            "last_message_at": None,
        },
        "messages": [],
        "files": [],
        "runtime": {},
    }


def test_add_message_persists_mysql_row_and_json():
    repo = FakeConversationRepo()
    json_store = FakeJsonStore(document=_base_document())
    service = ConversationService(repo=repo, json_store=json_store, outbox_repo=FakeOutboxRepo())

    result = service.add_message(
        user_id=7,
        conversation_id=11,
        role="assistant",
        content="final answer",
        metadata={"query_mode": "thinking", "references": [{"title": "paper"}]},
    )

    assert result["success"] is True
    assert result["data"]["message_id"] == 101
    assert repo.add_message_calls[0]["content"] == "final answer"
    assert repo.add_message_calls[0]["metadata"]["query_mode"] == "thinking"
    saved = json_store.saved_documents[-1]
    assert saved["messages"][-1]["message_id"] == "m_000101"
    assert saved["messages"][-1]["query_mode"] == "thinking"
    assert repo.delete_message_calls == []


def test_get_conversation_detail_heals_json_when_mysql_has_more_messages():
    repo = FakeConversationRepo()
    repo.db_messages = [
        {
            "id": 10,
            "role": "user",
            "content": "q1",
            "metadata": {"source": "ask_stream"},
            "created_at": "2026-03-15T10:01:00+08:00",
        },
        {
            "id": 11,
            "role": "assistant",
            "content": "a1",
            "metadata": {"query_mode": "thinking"},
            "created_at": "2026-03-15T10:02:00+08:00",
        },
    ]
    stale_document = _base_document()
    stale_document["messages"] = [
        {
            "message_id": "m_000010",
            "role": "user",
            "content": "q1",
            "created_at": "2026-03-15T10:01:00+08:00",
            "status": "done",
            "metadata": {"source": "ask_stream"},
        }
    ]
    stale_document["meta"]["message_count"] = 1
    stale_document["meta"]["last_message_at"] = "2026-03-15T10:01:00+08:00"
    json_store = FakeJsonStore(document=stale_document)
    service = ConversationService(repo=repo, json_store=json_store, outbox_repo=FakeOutboxRepo())

    result = service.get_conversation_detail(user_id=7, conversation_id=11)

    assert result["success"] is True
    assert result["data"]["message_count"] == 2
    assert [item["id"] for item in result["data"]["messages"]] == [10, 11]
    healed_doc = json_store.saved_documents[-1]
    assert len(healed_doc["messages"]) == 2
    assert repo.set_message_count_calls[-1]["message_count"] == 2


def test_add_message_rolls_back_mysql_row_when_json_write_fails():
    repo = FakeConversationRepo()
    json_store = FakeJsonStore(document=_base_document(), fail_write=True)
    service = ConversationService(repo=repo, json_store=json_store, outbox_repo=FakeOutboxRepo())

    result = service.add_message(
        user_id=7,
        conversation_id=11,
        role="user",
        content="hello",
        metadata={"source": "ask_stream"},
    )

    assert result["success"] is False
    assert result["code"] == "MESSAGE_ADD_ERROR"
    assert repo.delete_message_calls == [
        {
            "message_id": 101,
            "conversation_id": 11,
            "user_id": 7,
        }
    ]


def test_get_conversation_detail_backfills_mysql_from_legacy_json_messages():
    repo = FakeConversationRepo()
    legacy_document = _base_document()
    legacy_document["messages"] = [
        {
            "message_id": "m_000001",
            "role": "user",
            "content": "legacy q",
            "created_at": "2026-03-15T10:01:00+08:00",
            "status": "done",
            "metadata": {"source": "legacy"},
        },
        {
            "message_id": "m_000002",
            "role": "assistant",
            "content": "legacy a",
            "created_at": "2026-03-15T10:02:00+08:00",
            "status": "done",
            "metadata": {"query_mode": "thinking"},
            "query_mode": "thinking",
        },
    ]
    legacy_document["meta"]["message_count"] = 2
    legacy_document["meta"]["last_message_at"] = "2026-03-15T10:02:00+08:00"
    json_store = FakeJsonStore(document=legacy_document)
    service = ConversationService(repo=repo, json_store=json_store, outbox_repo=FakeOutboxRepo())

    result = service.get_conversation_detail(user_id=7, conversation_id=11)

    assert result["success"] is True
    assert len(repo.add_message_with_created_at_calls) == 2
    assert [item["content"] for item in repo.add_message_with_created_at_calls] == ["legacy q", "legacy a"]
    assert [item["id"] for item in result["data"]["messages"]] == [201, 202]
    healed_doc = json_store.saved_documents[-1]
    assert [item["message_id"] for item in healed_doc["messages"]] == ["m_000201", "m_000202"]


def test_remove_uploaded_file_marks_json_deleted_and_removes_db_row():
    repo = FakeConversationRepo()
    repo.db_files = [
        {
            "id": 31,
            "conversation_id": 11,
            "user_id": 7,
            "file_type": "pdf",
            "file_name": "demo.pdf",
            "local_path": "/tmp/demo.pdf",
            "storage_ref": "minio://bucket/demo.pdf",
            "content_type": "application/pdf",
            "size_bytes": 12,
            "created_at": "2026-03-15T10:01:00+08:00",
        }
    ]
    document = _base_document()
    document["files"] = [
        {
            "file_no": 1,
            "file_id": 31,
            "file_type": "pdf",
            "file_name": "demo.pdf",
            "local_path": "/tmp/demo.pdf",
            "storage_ref": "minio://bucket/demo.pdf",
            "content_type": "application/pdf",
            "size_bytes": 12,
            "uploaded_at": "2026-03-15T10:01:00+08:00",
            "file_status": "active",
            "deleted_at": None,
            "deleted_by": None,
        }
    ]
    json_store = FakeJsonStore(document=document)
    service = ConversationService(repo=repo, json_store=json_store, outbox_repo=FakeOutboxRepo())

    result = service.remove_uploaded_file(user_id=7, conversation_id=11, file_id=31)

    assert result["success"] is True
    assert repo.delete_uploaded_file_calls == [{"conversation_id": 11, "user_id": 7, "file_id": 31}]
    assert repo.db_files == []
    saved = json_store.saved_documents[-1]
    assert saved["files"][0]["file_status"] == "deleted"
    assert saved["files"][0]["deleted_by"] == 7


def test_list_uploaded_files_backfills_mysql_from_legacy_json_active_files():
    repo = FakeConversationRepo()
    document = _base_document()
    document["files"] = [
        {
            "file_no": 1,
            "file_id": 1,
            "file_type": "pdf",
            "file_name": "legacy.pdf",
            "local_path": "/tmp/legacy.pdf",
            "storage_ref": "minio://bucket/legacy.pdf",
            "content_type": "application/pdf",
            "size_bytes": 88,
            "uploaded_at": "2026-03-15T10:05:00+08:00",
            "file_status": "active",
            "deleted_at": None,
            "deleted_by": None,
        },
        {
            "file_no": 2,
            "file_id": 2,
            "file_type": "excel",
            "file_name": "gone.xlsx",
            "local_path": "/tmp/gone.xlsx",
            "storage_ref": "",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "size_bytes": 16,
            "uploaded_at": "2026-03-15T10:06:00+08:00",
            "file_status": "deleted",
            "deleted_at": "2026-03-15T10:07:00+08:00",
            "deleted_by": 7,
        },
    ]
    json_store = FakeJsonStore(document=document)
    service = ConversationService(repo=repo, json_store=json_store, outbox_repo=FakeOutboxRepo())

    result = service.list_uploaded_files(user_id=7, conversation_id=11, include_deleted=True)

    assert result["success"] is True
    assert len(repo.add_uploaded_file_with_created_at_calls) == 1
    assert repo.add_uploaded_file_with_created_at_calls[0]["file_name"] == "legacy.pdf"
    assert [item["id"] for item in result["data"]["files"]] == [301, 2]
    saved = json_store.saved_documents[-1]
    assert [item["file_id"] for item in saved["files"]] == [301, 2]
    assert saved["files"][1]["file_status"] == "deleted"


def test_add_uploaded_file_reconciles_legacy_json_files_before_append():
    repo = FakeConversationRepo()
    document = _base_document()
    document["files"] = [
        {
            "file_no": 1,
            "file_id": 1,
            "file_type": "pdf",
            "file_name": "legacy.pdf",
            "local_path": "/tmp/legacy.pdf",
            "storage_ref": "minio://bucket/legacy.pdf",
            "content_type": "application/pdf",
            "size_bytes": 88,
            "uploaded_at": "2026-03-15T10:05:00+08:00",
            "file_status": "active",
            "deleted_at": None,
            "deleted_by": None,
        }
    ]
    json_store = FakeJsonStore(document=document)
    service = ConversationService(repo=repo, json_store=json_store, outbox_repo=FakeOutboxRepo())

    original_add_uploaded_file = repo.add_uploaded_file

    def fake_add_uploaded_file(**kwargs):
        repo.db_files.append(
            {
                "id": 401,
                "conversation_id": kwargs["conversation_id"],
                "user_id": kwargs["user_id"],
                "file_type": kwargs["file_type"],
                "file_name": kwargs["file_name"],
                "local_path": kwargs["local_path"],
                "storage_ref": kwargs["storage_ref"],
                "content_type": kwargs["content_type"],
                "size_bytes": kwargs["size_bytes"],
                "created_at": "2026-03-15T10:10:00+08:00",
            }
        )
        return 401

    repo.add_uploaded_file = fake_add_uploaded_file
    try:
        result = service.add_uploaded_file(
            user_id=7,
            conversation_id=11,
            file_type="excel",
            file_name="new.xlsx",
            local_path="/tmp/new.xlsx",
            storage_ref="minio://bucket/new.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=16,
        )
    finally:
        repo.add_uploaded_file = original_add_uploaded_file

    assert result["success"] is True
    assert len(repo.add_uploaded_file_with_created_at_calls) == 1
    saved = json_store.saved_documents[-1]
    assert [item["file_id"] for item in saved["files"]] == [301, 401]
    assert [item["file_no"] for item in saved["files"]] == [1, 2]
