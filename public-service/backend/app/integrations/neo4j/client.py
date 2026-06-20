from __future__ import annotations

from typing import Any

from app.modules.retrieval.models import Neo4jBootstrapResult


def run_graph_query(graph: Any, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Execute Cypher against LangChain Neo4jGraph or legacy py2neo-style graphs."""
    if graph is None:
        return []
    bound = dict(params or {})
    if hasattr(graph, "query"):
        rows = graph.query(query, bound)
        return [dict(item) for item in list(rows or []) if isinstance(item, dict)]
    if hasattr(graph, "run"):
        result = graph.run(query, **bound)
        data = result.data() if hasattr(result, "data") else result
        return [dict(item) for item in list(data or []) if isinstance(item, dict)]
    raise AttributeError(f"unsupported neo4j graph type: {type(graph)!r}")


def _apoc_error(text: str) -> bool:
    lowered = str(text or "").lower()
    return "apoc" in lowered


def bootstrap_neo4j(
    *,
    url: str,
    username: str,
    password: str,
    logger: Any,
    graph_factory: Any | None = None,
    base_driver_factory: Any | None = None,
) -> Neo4jBootstrapResult:
    attempted_modes: list[str] = []

    factory = graph_factory
    if factory is None:  # pragma: no cover
        from langchain_community.graphs import Neo4jGraph

        factory = Neo4jGraph

    common_kwargs = {"url": url, "username": username, "password": password}
    variants = (
        ("refresh_schema_false_sanitize", {"sanitize": True, "refresh_schema": False}),
        ("sanitize", {"sanitize": True}),
        ("basic", {}),
    )

    try:
        for mode, extra in variants:
            attempted_modes.append(mode)
            try:
                graph = factory(**common_kwargs, **extra)
                if logger is not None:
                    logger.info("Neo4j bootstrap succeeded with mode %s", mode)
                return Neo4jBootstrapResult(
                    graph=graph,
                    available=True,
                    degraded=False,
                    connectivity_verified=False,
                    attempted_modes=tuple(attempted_modes),
                    error=None,
                )
            except (TypeError, ValueError) as exc:
                if mode != "basic":
                    if logger is not None:
                        logger.warning("Neo4j bootstrap mode %s failed: %s", mode, exc)
                    continue
                raise
    except Exception as exc:
        error_text = str(exc)
        if _apoc_error(error_text):
            if logger is not None:
                logger.warning("Neo4j APOC unavailable, entering degraded mode: %s", error_text)
            if base_driver_factory is not None:
                try:
                    driver = base_driver_factory(url, auth=(username, password))
                    try:
                        driver.verify_connectivity()
                    finally:
                        driver.close()
                    return Neo4jBootstrapResult(
                        graph=None,
                        available=True,
                        degraded=True,
                        connectivity_verified=True,
                        attempted_modes=tuple(attempted_modes),
                        error=error_text,
                    )
                except Exception as connectivity_exc:
                    error_text = f"{error_text}; fallback_connectivity_failed: {connectivity_exc}"
            return Neo4jBootstrapResult(
                graph=None,
                available=False,
                degraded=True,
                connectivity_verified=False,
                attempted_modes=tuple(attempted_modes),
                error=error_text,
            )
        if logger is not None:
            logger.warning("Neo4j bootstrap failed, continuing in degraded mode: %s", error_text)
        return Neo4jBootstrapResult(
            graph=None,
            available=False,
            degraded=True,
            connectivity_verified=False,
            attempted_modes=tuple(attempted_modes),
            error=error_text,
        )

    return Neo4jBootstrapResult(
        graph=None,
        available=False,
        degraded=True,
        connectivity_verified=False,
        attempted_modes=tuple(attempted_modes),
        error="neo4j_bootstrap_failed",
    )
