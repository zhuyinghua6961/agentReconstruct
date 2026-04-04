from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_upload_module():
    fake_minio = types.ModuleType("minio")
    fake_minio_error = types.ModuleType("minio.error")

    class _FakeMinio:
        def __init__(self, *args, **kwargs) -> None:
            _ = args, kwargs

    class _FakeS3Error(Exception):
        pass

    fake_minio.Minio = _FakeMinio
    fake_minio_error.S3Error = _FakeS3Error
    sys.modules.setdefault("minio", fake_minio)
    sys.modules.setdefault("minio.error", fake_minio_error)

    spec = importlib.util.spec_from_file_location("upload_module_for_test", ROOT / "upload.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SinglePassIterable:
    def __init__(self, items):
        self._items = list(items)
        self.iter_calls = 0

    def __iter__(self):
        self.iter_calls += 1
        if self.iter_calls > 1:
            raise AssertionError("file iterable should not be consumed more than once")
        yield from self._items


class _DummyLogger:
    def info(self, *args, **kwargs) -> None:
        _ = args, kwargs

    def debug(self, *args, **kwargs) -> None:
        _ = args, kwargs

    def warning(self, *args, **kwargs) -> None:
        _ = args, kwargs

    def error(self, *args, **kwargs) -> None:
        _ = args, kwargs


class _DummyProgress:
    def __init__(self, iterable, **kwargs) -> None:
        self._iterable = iterable
        self.kwargs = kwargs

    def __iter__(self):
        return iter(self._iterable)

    def set_postfix_str(self, value: str) -> None:
        _ = value


class _DummyStateDB:
    def __init__(self) -> None:
        self.saved: list[dict[str, object]] = []

    def save_upload_state(self, object_name: str, file_path: str, file_size: int, file_mtime: float, file_hash: str, etag: str) -> None:
        self.saved.append(
            {
                "object_name": object_name,
                "file_path": file_path,
                "file_size": file_size,
                "file_mtime": file_mtime,
                "file_hash": file_hash,
                "etag": etag,
            }
        )

    def get_stats(self):
        total_size = sum(int(item["file_size"]) for item in self.saved)
        return {"total_files": len(self.saved), "total_size": total_size}


class _DummyClient:
    def __init__(self) -> None:
        self.uploaded: list[tuple[str, str, str]] = []
        self._sizes: dict[str, int] = {}

    def fput_object(self, bucket: str, object_name: str, file_path: str) -> None:
        self.uploaded.append((bucket, object_name, file_path))
        self._sizes[object_name] = os.path.getsize(file_path)

    def stat_object(self, bucket: str, object_name: str):
        _ = bucket
        return SimpleNamespace(etag='"etag-1"', size=self._sizes[object_name])


def test_upload_folder_consumes_file_stream_only_once(tmp_path, monkeypatch) -> None:
    module = _load_upload_module()

    sample = tmp_path / "demo.txt"
    sample.write_text("hello patent", encoding="utf-8")
    stat = sample.stat()
    files = _SinglePassIterable(
        [
            (
                str(sample),
                sample.name,
                stat.st_size,
                stat.st_mtime,
                "hash-demo",
            )
        ]
    )

    uploader = module.MinioFolderUploader.__new__(module.MinioFolderUploader)
    uploader.bucket = "agentcode"
    uploader.client = _DummyClient()
    uploader.state_db = _DummyStateDB()
    uploader.logger = _DummyLogger()

    monkeypatch.setattr(module, "tqdm", _DummyProgress)
    monkeypatch.setattr(
        uploader,
        "_get_all_files",
        lambda local_folder, ignore_patterns, include_top_level_dirs=None: files,
    )

    uploader.upload_folder(
        local_folder=str(tmp_path),
        prefix="patents/",
        ignore_patterns=[],
        show_progress=False,
        force_upload=False,
        resume=False,
    )

    assert files.iter_calls == 1
    assert uploader.client.uploaded == [("agentcode", "patents/demo.txt", str(sample))]
    assert uploader.state_db.get_stats()["total_files"] == 1


def test_get_all_files_filters_by_top_level_patent_ids(tmp_path) -> None:
    module = _load_upload_module()

    keep_dir = tmp_path / "CNKEEP123A"
    skip_dir = tmp_path / "CNSKIP456A"
    keep_dir.mkdir()
    skip_dir.mkdir()
    keep_file = keep_dir / "CNKEEP123A.pdf"
    skip_file = skip_dir / "CNSKIP456A.pdf"
    keep_file.write_text("keep", encoding="utf-8")
    skip_file.write_text("skip", encoding="utf-8")

    uploader = module.MinioFolderUploader.__new__(module.MinioFolderUploader)
    uploader.logger = _DummyLogger()

    files = list(
        uploader._get_all_files(
            str(tmp_path),
            [],
            include_top_level_dirs={"CNKEEP123A"},
        )
    )

    relative_paths = [item[1] for item in files]
    assert relative_paths == [f"CNKEEP123A/{keep_file.name}"]


def test_create_storage_client_falls_back_to_botocore(monkeypatch) -> None:
    module = _load_upload_module()

    class _FakeS3Client:
        def head_bucket(self, Bucket):
            _ = Bucket
            return {}

    class _FakeSession:
        def create_client(self, *args, **kwargs):
            _ = args, kwargs
            return _FakeS3Client()

    monkeypatch.setattr(module, "_load_minio_client_class", lambda: None)
    monkeypatch.setattr(module.botocore.session, "get_session", lambda: _FakeSession())

    client = module._create_object_storage_client(
        endpoint="127.0.0.1:9000",
        access_key="admin",
        secret_key="12345678",
        secure=False,
    )

    assert hasattr(client, "bucket_exists")
    assert client.bucket_exists("agentcode") is True


def test_botocore_fput_object_uses_put_object_with_file_stream(tmp_path) -> None:
    module = _load_upload_module()

    sample = tmp_path / "demo.pdf"
    sample.write_bytes(b"%PDF-1.4 demo")

    calls = []

    class _FakeS3Client:
        def put_object(self, **kwargs):
            body = kwargs["Body"]
            calls.append(
                {
                    "Bucket": kwargs["Bucket"],
                    "Key": kwargs["Key"],
                    "ContentLength": kwargs["ContentLength"],
                    "ContentType": kwargs.get("ContentType"),
                    "BodyBytes": body.read(),
                }
            )

    client = module._BotocoreS3CompatClient.__new__(module._BotocoreS3CompatClient)
    client._client = _FakeS3Client()

    client.fput_object("agentcode", "patent/originals/CN1/fulltext/original.pdf", str(sample), content_type="application/pdf")

    assert calls == [
        {
            "Bucket": "agentcode",
            "Key": "patent/originals/CN1/fulltext/original.pdf",
            "ContentLength": sample.stat().st_size,
            "ContentType": "application/pdf",
            "BodyBytes": b"%PDF-1.4 demo",
        }
    ]
