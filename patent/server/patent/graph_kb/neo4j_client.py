from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _is_timeout_error(exc: BaseException) -> bool:
    code = str(getattr(exc, "code", "") or "").lower()
    message = str(getattr(exc, "message", "") or str(exc) or "").lower()
    return "timeout" in code or "timed out" in message or "timeout" in message


def _probe_database(driver: Any, *, database: str) -> None:
    driver.execute_query("RETURN 1 AS ok", database_=database, parameters_={})


@dataclass
class PatentNeo4jClient:
    driver: Any | None
    available: bool
    degraded: bool
    error: str = ""
    database: str = "neo4j"

    def close(self) -> None:
        driver = self.driver
        self.driver = None
        if driver is None:
            return
        close = getattr(driver, "close", None)
        if callable(close):
            close()

    def query(self, cypher: str, params: dict[str, Any], *, timeout_ms: int) -> list[dict[str, Any]]:
        driver = self.driver
        if driver is None or not self.available:
            return []

        from neo4j import Query

        query = Query(text=cypher, timeout=float(max(0, int(timeout_ms or 0))) / 1000.0)
        try:
            rows, _, _ = driver.execute_query(
                query,
                database_=self.database,
                parameters_=dict(params or {}),
            )
        except Exception as exc:
            if _is_timeout_error(exc):
                raise TimeoutError(str(getattr(exc, "message", "") or str(exc) or "graph query timed out")) from exc
            raise

        normalized: list[dict[str, Any]] = []
        for item in list(rows or []):
            if isinstance(item, dict):
                normalized.append(dict(item))
                continue
            data = getattr(item, "data", None)
            if callable(data):
                payload = data()
                if isinstance(payload, dict):
                    normalized.append(dict(payload))
        return normalized


def bootstrap_patent_neo4j_client(
    *,
    url: str,
    username: str,
    password: str,
    database: str,
    logger: Any | None = None,
) -> PatentNeo4jClient:
    try:
        from neo4j import GraphDatabase
    except Exception as exc:
        return PatentNeo4jClient(
            driver=None,
            available=False,
            degraded=True,
            error=str(exc),
            database=database,
        )

    try:
        driver = GraphDatabase.driver(url, auth=(username, password))
        driver.verify_connectivity()
        _probe_database(driver, database=database)
        if logger is not None:
            logger.info("Patent graph neo4j connectivity verified url=%s database=%s", url, database)
        return PatentNeo4jClient(
            driver=driver,
            available=True,
            degraded=False,
            error="",
            database=database,
        )
    except Exception as exc:
        close = getattr(locals().get("driver"), "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        if logger is not None:
            logger.warning("Patent graph neo4j bootstrap degraded url=%s database=%s error=%s", url, database, exc)
        return PatentNeo4jClient(
            driver=None,
            available=False,
            degraded=True,
            error=str(exc),
            database=database,
        )
