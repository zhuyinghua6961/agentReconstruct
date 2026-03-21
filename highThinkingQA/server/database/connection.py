"""MySQL connection helpers for server-side persistence."""

from __future__ import annotations

import os
import time
from typing import Any


class DatabaseConfigError(RuntimeError):
    """Raised when required MySQL config is missing."""


class DatabaseConnectionError(RuntimeError):
    """Raised when MySQL connection/query repeatedly fails."""


def _int_env(key: str, default: int, *, minimum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(key, str(default))).strip())
    except Exception:
        value = int(default)
    if minimum is not None and value < minimum:
        value = minimum
    return value


def _float_env(key: str, default: float, *, minimum: float | None = None) -> float:
    try:
        value = float(str(os.getenv(key, str(default))).strip())
    except Exception:
        value = float(default)
    if minimum is not None and value < minimum:
        value = minimum
    return value


def _mysql_config() -> dict[str, Any]:
    host = str(os.getenv("MYSQL_HOST", "")).strip() or "127.0.0.1"
    port = _int_env("MYSQL_PORT", 3306, minimum=1)
    user = str(os.getenv("MYSQL_USER", "")).strip()
    password = str(os.getenv("MYSQL_PASSWORD", "")).strip()
    database = str(os.getenv("MYSQL_DATABASE", "")).strip()
    unix_socket = str(os.getenv("MYSQL_UNIX_SOCKET", "")).strip()
    connect_timeout = _float_env("MYSQL_CONNECT_TIMEOUT_SECONDS", 2.0, minimum=0.1)

    if not user:
        raise DatabaseConfigError("MYSQL_USER is required")
    if not database:
        raise DatabaseConfigError("MYSQL_DATABASE is required")

    config = {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "database": database,
        "connect_timeout": float(connect_timeout),
        "charset": "utf8mb4",
        "autocommit": True,
        "cursorclass": None,
    }
    if unix_socket:
        # Prefer unix socket when provided (useful for local deployments/sandboxed envs).
        config["unix_socket"] = unix_socket
        config.pop("host", None)
        config.pop("port", None)
    return config


def _connect():
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except Exception as exc:  # pragma: no cover - dependency guard
        raise DatabaseConnectionError("pymysql is required for mysql persistence") from exc

    cfg = _mysql_config()
    cfg["cursorclass"] = DictCursor

    retries = _int_env("MYSQL_CONNECT_RETRIES", 2, minimum=0)
    retry_delay = _float_env("MYSQL_CONNECT_RETRY_DELAY_SECONDS", 0.15, minimum=0.0)
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        try:
            return pymysql.connect(**cfg)
        except Exception as exc:  # pragma: no cover - runtime env specific
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(retry_delay)

    raise DatabaseConnectionError(f"mysql connect failed: {last_exc}")


def execute_query(sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[dict[str, Any]]:
    """Execute SELECT-like SQL and return rows as dict list."""
    query_retries = _int_env("MYSQL_QUERY_RETRIES", 2, minimum=0)
    query_retry_delay = _float_env("MYSQL_QUERY_RETRY_DELAY_SECONDS", 0.05, minimum=0.0)

    last_exc: Exception | None = None
    for attempt in range(query_retries + 1):
        conn = None
        try:
            conn = _connect()
            with conn.cursor() as cursor:
                cursor.execute(sql, tuple(params or ()))
                rows = cursor.fetchall() or []
            return [dict(row) for row in rows]
        except Exception as exc:  # pragma: no cover - runtime env specific
            last_exc = exc
            if attempt >= query_retries:
                break
            time.sleep(query_retry_delay)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    raise DatabaseConnectionError(f"mysql query failed: {last_exc}")


def execute_update(sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> int:
    """Execute INSERT/UPDATE/DELETE and return lastrowid or affected rows."""
    query_retries = _int_env("MYSQL_QUERY_RETRIES", 2, minimum=0)
    query_retry_delay = _float_env("MYSQL_QUERY_RETRY_DELAY_SECONDS", 0.05, minimum=0.0)

    last_exc: Exception | None = None
    for attempt in range(query_retries + 1):
        conn = None
        try:
            conn = _connect()
            with conn.cursor() as cursor:
                affected = cursor.execute(sql, tuple(params or ()))
                conn.commit()
                lastrowid = int(getattr(cursor, "lastrowid", 0) or 0)
                if lastrowid > 0:
                    return lastrowid
                return int(affected or 0)
        except Exception as exc:  # pragma: no cover - runtime env specific
            last_exc = exc
            if attempt >= query_retries:
                break
            time.sleep(query_retry_delay)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    raise DatabaseConnectionError(f"mysql update failed: {last_exc}")
