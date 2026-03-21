from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import pymysql

from app.core.config import Settings
from app.core.errors import DatabaseUnavailableError


@dataclass
class Database:
    settings: Settings

    def connect(self):
        try:
            return pymysql.connect(
                host=self.settings.mysql_host,
                port=self.settings.mysql_port,
                user=self.settings.mysql_user,
                password=self.settings.mysql_password,
                database=self.settings.mysql_database,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
            )
        except Exception as exc:  # pragma: no cover
            raise DatabaseUnavailableError(f"Failed to connect MySQL: {exc}") from exc

    @contextmanager
    def connection(self) -> Iterator:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def ping(self) -> bool:
        with self.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                row = cursor.fetchone() or {}
                return int(row.get("ok") or 0) == 1
