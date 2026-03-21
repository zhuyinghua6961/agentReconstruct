from __future__ import annotations

from dataclasses import dataclass

from app.core.db import Database


class DatabaseLeaseLostError(RuntimeError):
    def __init__(self, *, key: str, label: str) -> None:
        super().__init__(f"{str(label or 'db_lock')}_lease_lost:{str(key or '')}")
        self.key = str(key or "")
        self.label = str(label or "db_lock")


@dataclass
class MySQLNamedLockLease:
    database: Database
    key: str
    wait_seconds: int
    label: str = "mysql_lock"

    def __post_init__(self) -> None:
        self._conn = None
        self._released = False

    @classmethod
    def acquire(
        cls,
        *,
        database: Database,
        key: str,
        wait_seconds: int,
        label: str = "mysql_lock",
    ) -> "MySQLNamedLockLease | None":
        lease = cls(
            database=database,
            key=str(key),
            wait_seconds=max(0, int(wait_seconds)),
            label=str(label or "mysql_lock"),
        )
        lease._conn = database.connect()
        try:
            with lease._conn.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, %s) AS acquired", (lease.key, lease.wait_seconds))
                row = cursor.fetchone() or {}
            if int((row or {}).get("acquired") or 0) == 1:
                return lease
        except Exception:
            lease.release()
            raise
        lease.release()
        return None

    def ensure_healthy(self) -> None:
        if self._released or self._conn is None:
            raise DatabaseLeaseLostError(key=self.key, label=self.label)
        try:
            self._conn.ping(reconnect=False)
        except Exception as exc:
            raise DatabaseLeaseLostError(key=self.key, label=self.label) from exc

    def release(self) -> bool:
        if self._released:
            return False
        released = False
        try:
            if self._conn is not None:
                try:
                    with self._conn.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK(%s) AS released", (self.key,))
                        row = cursor.fetchone() or {}
                    released = int((row or {}).get("released") or 0) == 1
                finally:
                    self._conn.close()
        finally:
            self._released = True
            self._conn = None
        return released


__all__ = ["DatabaseLeaseLostError", "MySQLNamedLockLease"]
