import argparse
import os
import json
import hashlib
import logging
import sqlite3
import sys
from datetime import datetime
from itertools import chain
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Iterator, List, Optional, Dict, Set, Tuple

import botocore.session
from tqdm import tqdm


REPO_ROOT = Path(__file__).resolve().parent
PATENT_ROOT = REPO_ROOT / "patent"
if str(PATENT_ROOT) not in sys.path:
    sys.path.insert(0, str(PATENT_ROOT))

from server.patent.original_assets_tooling import (  # noqa: E402
    build_patent_original_backfill_plan,
    upload_patent_original_backfill_plan,
)

class UploadStateDB:
    """管理上传状态的 SQLite 数据库"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS upload_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_name TEXT UNIQUE NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_mtime REAL NOT NULL,
                file_hash TEXT,
                etag TEXT,
                upload_time TEXT NOT NULL,
                status TEXT DEFAULT 'completed'
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_object_name ON upload_state(object_name)')
        conn.commit()
        conn.close()
    
    def get_uploaded_file(self, object_name: str) -> Optional[Dict]:
        """查询已上传文件信息"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM upload_state WHERE object_name = ?', (object_name,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def save_upload_state(self, object_name: str, file_path: str, file_size: int, 
                          file_mtime: float, file_hash: str, etag: str):
        """保存上传状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO upload_state 
            (object_name, file_path, file_size, file_mtime, file_hash, etag, upload_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'completed')
        ''', (object_name, file_path, file_size, file_mtime, file_hash, etag, 
              datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def clear_all(self):
        """清空所有上传记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM upload_state')
        conn.commit()
        conn.close()
    
    def get_stats(self) -> Dict:
        """获取上传统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as total, SUM(file_size) as total_size FROM upload_state')
        row = cursor.fetchone()
        conn.close()
        return {
            'total_files': row[0] or 0,
            'total_size': row[1] or 0
        }


def _load_minio_client_class():
    try:
        from minio import Minio  # type: ignore
    except Exception:
        return None
    return Minio


class _BotocoreStreamingResponse:
    def __init__(self, body) -> None:
        self._body = body

    def read(self):
        return self._body.read()

    def close(self) -> None:
        self._body.close()

    def release_conn(self) -> None:
        return None


class _BotocoreS3CompatClient:
    def __init__(self, *, endpoint: str, access_key: str, secret_key: str, secure: bool) -> None:
        endpoint_url = endpoint
        if not str(endpoint_url).startswith("http://") and not str(endpoint_url).startswith("https://"):
            scheme = "https" if secure else "http"
            endpoint_url = f"{scheme}://{endpoint}"
        session = botocore.session.get_session()
        self._client = session.create_client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            use_ssl=bool(secure),
            verify=False,
        )

    def bucket_exists(self, bucket_name: str) -> bool:
        try:
            self._client.head_bucket(Bucket=bucket_name)
            return True
        except Exception:
            return False

    def make_bucket(self, bucket_name: str) -> None:
        self._client.create_bucket(Bucket=bucket_name)

    def fput_object(self, bucket_name: str, object_name: str, file_path: str, content_type: str | None = None) -> None:
        path = Path(file_path)
        with path.open("rb") as handle:
            self._client.put_object(
                Bucket=bucket_name,
                Key=object_name,
                Body=handle,
                ContentLength=path.stat().st_size,
                **({"ContentType": content_type} if content_type else {}),
            )

    def put_object(self, bucket_name: str, object_name: str, data, length: int, content_type: str | None = None) -> None:
        self._client.put_object(
            Bucket=bucket_name,
            Key=object_name,
            Body=data.read() if hasattr(data, "read") else data,
            ContentLength=length,
            **({"ContentType": content_type} if content_type else {}),
        )

    def stat_object(self, bucket_name: str, object_name: str):
        response = self._client.head_object(Bucket=bucket_name, Key=object_name)
        return SimpleNamespace(
            etag=str(response.get("ETag") or "").strip('"'),
            size=int(response.get("ContentLength") or 0),
        )

    def get_object(self, bucket_name: str, object_name: str):
        response = self._client.get_object(Bucket=bucket_name, Key=object_name)
        return _BotocoreStreamingResponse(response["Body"])


def _create_object_storage_client(*, endpoint: str, access_key: str, secret_key: str, secure: bool):
    minio_client_class = _load_minio_client_class()
    if minio_client_class is not None:
        return minio_client_class(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    return _BotocoreS3CompatClient(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


class MinioFolderUploader:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        log_file: Optional[str] = None,
        log_level: str = "INFO",
        state_db_path: str = "upload_state.db"
    ):
        self.client = _create_object_storage_client(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self.bucket = bucket
        self.state_db = UploadStateDB(state_db_path)
        self._setup_logging(log_file, log_level)
        self._ensure_bucket_exists()

    def _setup_logging(self, log_file: Optional[str], log_level: str):
        self.logger = logging.getLogger("MinioUploader")
        self.logger.setLevel(getattr(logging, log_level.upper()))
        self.logger.handlers.clear()
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        if log_file:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            self.logger.info(f"日志文件：{log_file}")

    def _ensure_bucket_exists(self):
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                self.logger.info(f"✓ 创建 bucket: {self.bucket}")
            else:
                self.logger.info(f"✓ 使用已有 bucket: {self.bucket}")
        except Exception as e:
            self.logger.error(f"✗ Bucket 操作失败：{e}")
            raise

    def _calculate_file_hash(self, file_path: str, chunk_size: int = 8192) -> str:
        """计算文件 MD5 哈希"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def _get_file_metadata(self, file_path: str) -> Tuple[int, float, str]:
        """获取文件元数据"""
        size = os.path.getsize(file_path)
        mtime = os.path.getmtime(file_path)
        file_hash = self._calculate_file_hash(file_path)
        return size, mtime, file_hash

    def _should_skip_file(self, file_path: str, ignore_patterns: List[str]) -> bool:
        file_name = os.path.basename(file_path)
        for pattern in ignore_patterns:
            if pattern in file_path or pattern in file_name:
                return True
        return False

    def _check_file_changed(self, object_name: str, file_path: str, file_size: int, 
                            file_mtime: float, file_hash: str) -> bool:
        """检查文件是否已变化"""
        cached = self.state_db.get_uploaded_file(object_name)
        if not cached:
            self.logger.debug(f"🆕 新文件：{object_name}")
            return True
        if cached['file_size'] != file_size:
            self.logger.debug(f"📝 大小变化：{object_name} ({cached['file_size']} -> {file_size})")
            return True
        if cached['file_hash'] != file_hash:
            self.logger.debug(f"📝 内容变化：{object_name}")
            return True
        try:
            obj = self.client.stat_object(self.bucket, object_name)
            if obj.size != file_size:
                self.logger.debug(f"📝 远程大小不匹配：{object_name}")
                return True
            self.logger.debug(f"⏭ 跳过（未变化）：{object_name}")
            return False
        except Exception:
            self.logger.debug(f"🆕 远程不存在：{object_name}")
            return True

    def _get_all_files(
        self,
        local_folder: str,
        ignore_patterns: List[str],
        include_top_level_dirs: Optional[Set[str]] = None,
    ) -> Iterator[tuple[str, str, int, float, str]]:
        local_folder = os.path.abspath(local_folder)
        normalized_include_dirs = {
            str(item or "").strip().upper()
            for item in list(include_top_level_dirs or set())
            if str(item or "").strip()
        }
        for root, dirnames, filenames in os.walk(local_folder, topdown=True):
            dirnames[:] = [
                name for name in dirnames
                if not self._should_skip_file(os.path.join(root, name), ignore_patterns)
            ]
            if normalized_include_dirs and os.path.abspath(root) == local_folder:
                dirnames[:] = [
                    name for name in dirnames
                    if str(name or "").strip().upper() in normalized_include_dirs
                ]
            for filename in filenames:
                file_path = os.path.join(root, filename)
                if self._should_skip_file(file_path, ignore_patterns):
                    self.logger.debug(f"跳过：{file_path}")
                    continue
                try:
                    size, mtime, file_hash = self._get_file_metadata(file_path)
                    relative_path = os.path.relpath(file_path, local_folder)
                    yield (file_path, relative_path, size, mtime, file_hash)
                except Exception as e:
                    self.logger.warning(f"无法获取文件信息 {file_path}: {e}")

    @staticmethod
    def _prime_file_iterable(files: Iterable[tuple[str, str, int, float, str]]) -> Iterator[tuple[str, str, int, float, str]]:
        iterator = iter(files)
        first_item = next(iterator, None)
        if first_item is None:
            return iter(())
        return chain((first_item,), iterator)

    def upload_folder(
        self,
        local_folder: str,
        prefix: str = "",
        ignore_patterns: Optional[List[str]] = None,
        show_progress: bool = True,
        force_upload: bool = False,
        resume: bool = True,
        include_top_level_dirs: Optional[Set[str]] = None,
    ):
        if not os.path.isdir(local_folder):
            raise ValueError(f"路径无效：{local_folder}")

        ignore_patterns = ignore_patterns or ['.git', '__pycache__', '.DS_Store', '.idea', '.vscode']
        normalized_include_dirs = {
            str(item or "").strip().upper()
            for item in list(include_top_level_dirs or set())
            if str(item or "").strip()
        }
        files = self._prime_file_iterable(
            self._get_all_files(
                local_folder,
                ignore_patterns,
                include_top_level_dirs=normalized_include_dirs,
            )
        )
        first_file = next(files, None)
        if first_file is None:
            self.logger.warning("⚠ 没有找到可上传的文件")
            return
        files = chain((first_file,), files)
        if normalized_include_dirs:
            self.logger.info(f"🎯 专利白名单过滤：{len(normalized_include_dirs)} 个目录")
        self.logger.info("📁 待处理文件数：流式扫描中，最终统计见结束汇总")
        self.logger.info("📊 总大小：流式扫描中，最终统计见结束汇总")

        uploaded = 0
        failed = 0
        skipped = 0
        scanned = 0
        scanned_total_size = 0
        total_uploaded_size = 0
        start_time = datetime.now()

        progress_bar = tqdm(files, desc="上传中", unit="file", disable=not show_progress, ncols=100)

        for file_path, relative_path, file_size, file_mtime, file_hash in progress_bar:
            scanned += 1
            scanned_total_size += file_size
            object_name = os.path.join(prefix, relative_path).replace("\\", "/")
            progress_bar.set_postfix_str(
                f"当前：{os.path.basename(file_path)} | 已扫描：{scanned} | 已上传：{uploaded} | 已跳过：{skipped}"
            )

            if resume and not force_upload:
                if not self._check_file_changed(object_name, file_path, file_size, file_mtime, file_hash):
                    skipped += 1
                    continue

            try:
                self.client.fput_object(self.bucket, object_name, file_path)
                obj = self.client.stat_object(self.bucket, object_name)
                etag = obj.etag.strip('"')
                self.state_db.save_upload_state(object_name, file_path, file_size, file_mtime, file_hash, etag)
                uploaded += 1
                total_uploaded_size += file_size
                self.logger.debug(f"✓ 上传成功：{object_name} ({self._format_size(file_size)})")
            except Exception as e:
                failed += 1
                self.logger.error(f"✗ 上传失败：{object_name} - {e}")

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        avg_speed = total_uploaded_size / duration if duration > 0 else 0
        db_stats = self.state_db.get_stats()

        self.logger.info("=" * 60)
        self.logger.info("📈 上传统计")
        self.logger.info("=" * 60)
        self.logger.info(f"🧾 本次扫描：{scanned} 个文件")
        self.logger.info(f"📊 本次扫描总大小：{self._format_size(scanned_total_size)}")
        self.logger.info(f"✅ 本次成功：{uploaded} 个文件")
        self.logger.info(f"❌ 本次失败：{failed} 个文件")
        self.logger.info(f"⏭ 本次跳过：{skipped} 个文件")
        self.logger.info(f"📦 本次上传大小：{self._format_size(total_uploaded_size)}")
        self.logger.info(f"🗄 累计上传文件：{db_stats['total_files']} 个")
        self.logger.info(f"🗄 累计上传大小：{self._format_size(db_stats['total_size'])}")
        self.logger.info(f"⏱ 耗时：{duration:.2f} 秒")
        self.logger.info(f"🚀 平均速度：{self._format_size(avg_speed)}/s")
        self.logger.info("=" * 60)

    def _format_size(self, size: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"

    def reset_upload_state(self):
        """清空上传状态记录"""
        self.state_db.clear_all()
        self.logger.info("✓ 已清空上传状态记录")


def _load_patent_ids_from_file(path: str | Path | None) -> Set[str]:
    if path is None:
        return set()
    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        raise ValueError(f"专利清单文件不存在：{file_path}")
    patent_ids: Set[str] = set()
    for line in file_path.read_text(encoding="utf-8").splitlines():
        text = str(line or "").strip().upper()
        if not text or text.startswith("#"):
            continue
        patent_ids.add(text)
    return patent_ids


def _build_arg_parser() -> argparse.ArgumentParser:
    repo_root = REPO_ROOT
    default_patent_source_dir = repo_root / "resource" / "patentQA" / "__磷酸铁锂__AND__制备___NOT__废旧__已提取归档_"
    default_runtime_dir = repo_root / "patent" / ".tmp"

    parser = argparse.ArgumentParser(description="增量上传专利原文目录到 MinIO")
    parser.add_argument(
        "--mode",
        choices=("patent-originals", "raw-folder"),
        default="patent-originals",
        help="上传模式：patent-originals 会生成 patent/originals 结构；raw-folder 保留原始目录上传",
    )
    parser.add_argument("--local-folder", default=str(default_patent_source_dir), help="本地专利原文目录")
    parser.add_argument("--prefix", default="patents/", help="上传前缀")
    parser.add_argument("--log-file", default=str(default_runtime_dir / "minio_upload.log"), help="日志文件路径")
    parser.add_argument("--state-db-path", default=str(default_runtime_dir / "upload_state.db"), help="上传状态 SQLite 路径")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    parser.add_argument("--force-upload", action="store_true", help="忽略增量判断，强制重传")
    parser.add_argument("--no-resume", action="store_true", help="不读取历史上传状态")
    parser.add_argument(
        "--only-patent-list",
        default=None,
        help="只上传清单文件中列出的专利目录；文件每行一个专利号",
    )
    return parser


class _MinioPatentOriginalTarget:
    def __init__(self, uploader: MinioFolderUploader) -> None:
        self._uploader = uploader

    def object_exists(self, *, object_name: str) -> bool:
        try:
            self._uploader.client.stat_object(self._uploader.bucket, object_name)
            return True
        except Exception:
            return False

    def upload_bytes(self, *, object_name: str, payload: bytes, content_type: str) -> None:
        import io

        self._uploader.client.put_object(
            self._uploader.bucket,
            object_name,
            io.BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )

    def upload_file(self, *, object_name: str, source_path: str, content_type: str) -> None:
        self._uploader.client.fput_object(
            self._uploader.bucket,
            object_name,
            source_path,
            content_type=content_type,
        )

    def read_object_bytes(self, *, object_name: str) -> bytes | None:
        try:
            response = self._uploader.client.get_object(self._uploader.bucket, object_name)
        except Exception:
            return None
        try:
            return response.read()
        finally:
            try:
                response.close()
            except Exception:
                pass
            try:
                response.release_conn()
            except Exception:
                pass


def _resolve_patent_source_dirs(local_folder: str, patent_id_filter: Set[str]) -> list[Path]:
    root = Path(local_folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"路径无效：{root}")
    if patent_id_filter:
        selected = []
        for patent_id in sorted(patent_id_filter):
            candidate = root / patent_id
            if candidate.is_dir():
                selected.append(candidate)
        return selected
    return sorted(
        path for path in root.iterdir()
        if path.is_dir()
    )


def _upload_patent_originals(
    *,
    uploader: MinioFolderUploader,
    local_folder: str,
    patent_id_filter: Set[str],
    force_upload: bool,
    show_progress: bool,
) -> None:
    source_dirs = _resolve_patent_source_dirs(local_folder, patent_id_filter)
    if not source_dirs:
        uploader.logger.warning("⚠ 没有找到可上传的专利目录")
        return

    uploader.logger.info(f"📁 待处理专利数：{len(source_dirs)}")
    target = _MinioPatentOriginalTarget(uploader)

    uploaded_plans = 0
    failed_plans = 0
    uploaded_objects = 0
    skipped_objects = 0

    progress_bar = tqdm(source_dirs, desc="专利原文补传中", unit="patent", disable=not show_progress, ncols=120)
    for source_dir in progress_bar:
        canonical_patent_id = source_dir.name.strip().upper()
        progress_bar.set_postfix_str(
            f"当前：{canonical_patent_id} | 专利成功：{uploaded_plans} | 专利失败：{failed_plans} | 对象上传：{uploaded_objects}"
        )
        try:
            plan = build_patent_original_backfill_plan(source_dir, provider="patent_source_x")
            result = upload_patent_original_backfill_plan(
                plan,
                target=target,
                dry_run=False,
                skip_existing=not force_upload,
            )
            uploaded_plans += 1
            uploaded_objects += int(result.get("uploaded_count") or 0)
            skipped_objects += int(result.get("skipped_count") or 0)
            uploader.logger.info(
                f"✓ 专利补传成功：{canonical_patent_id} uploaded={result.get('uploaded_count') or 0} skipped={result.get('skipped_count') or 0}"
            )
        except Exception as e:
            failed_plans += 1
            uploader.logger.error(f"✗ 专利补传失败：{canonical_patent_id} - {e}")

    uploader.logger.info("=" * 60)
    uploader.logger.info("📈 专利原文补传统计")
    uploader.logger.info("=" * 60)
    uploader.logger.info(f"✅ 专利成功：{uploaded_plans} 个")
    uploader.logger.info(f"❌ 专利失败：{failed_plans} 个")
    uploader.logger.info(f"📦 对象上传：{uploaded_objects} 个")
    uploader.logger.info(f"⏭ 对象跳过：{skipped_objects} 个")
    uploader.logger.info("=" * 60)

# 使用示例
if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    runtime_dir = Path(args.state_db_path).expanduser().resolve().parent
    runtime_dir.mkdir(parents=True, exist_ok=True)
    patent_id_filter = _load_patent_ids_from_file(args.only_patent_list)

    uploader = MinioFolderUploader(
        endpoint="127.0.0.1:9000",
        access_key="admin",
        secret_key="12345678",
        bucket="agentcode",
        secure=False,
        log_file=args.log_file,
        log_level=args.log_level,
        state_db_path=args.state_db_path,
    )

    if args.mode == "patent-originals":
        _upload_patent_originals(
            uploader=uploader,
            local_folder=args.local_folder,
            patent_id_filter=patent_id_filter,
            force_upload=bool(args.force_upload),
            show_progress=True,
        )
    else:
        uploader.upload_folder(
            local_folder=args.local_folder,
            prefix=args.prefix,
            ignore_patterns=['.git', '__pycache__', '.log', 'tmp', '.DS_Store'],
            show_progress=True,
            force_upload=bool(args.force_upload),
            resume=not bool(args.no_resume),
            include_top_level_dirs=patent_id_filter or None,
        )
