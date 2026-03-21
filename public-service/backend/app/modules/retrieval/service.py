from __future__ import annotations

import os
from pathlib import Path

from app.core.config import get_settings
from app.integrations.neo4j import bootstrap_neo4j
from app.integrations.vector_db import VectorDbClient, bootstrap_chroma_collection
from app.modules.retrieval.models import RetrievalBindings, RetrievalRuntimeConfig


def _resolve_project_root(project_root: str | Path | None) -> Path:
    if project_root is None:
        return get_settings().data_root
    return Path(project_root).resolve()


def _resolve_path(raw: str, *, project_root: Path) -> Path:
    path = Path(str(raw or "").strip() or ".")
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


class RetrievalService:
    def build_runtime_config(
        self,
        *,
        config: dict[str, object] | None = None,
        project_root: str | Path | None = None,
    ) -> RetrievalRuntimeConfig:
        project_dir = _resolve_project_root(project_root)
        values = dict(config or {})
        return RetrievalRuntimeConfig(
            vector_db_path=_resolve_path(
                str(values.get("chroma_db_path") or values.get("vector_db_path") or os.getenv("VECTOR_DB_PATH", "vector_database")),
                project_root=project_dir,
            ),
            vector_collection_name=str(
                values.get("vector_collection_name") or os.getenv("VECTOR_COLLECTION_NAME", "lfp_papers")
            ).strip()
            or "lfp_papers",
            neo4j_url=str(values.get("neo4j_url") or os.getenv("NEO4J_URL", "")).strip(),
            neo4j_username=str(values.get("neo4j_username") or os.getenv("NEO4J_USERNAME", "neo4j")).strip(),
            neo4j_password=str(values.get("neo4j_password") or os.getenv("NEO4J_PASSWORD", "password")).strip(),
        )

    def build_bindings(
        self,
        *,
        config: dict[str, object] | None = None,
        project_root: str | Path | None = None,
        include_neo4j: bool = False,
        logger=None,
        graph_factory=None,
        base_driver_factory=None,
    ) -> RetrievalBindings:
        runtime = self.build_runtime_config(config=config, project_root=project_root)
        neo4j_client = None
        if include_neo4j and runtime.neo4j_url:
            neo4j_client = bootstrap_neo4j(
                url=runtime.neo4j_url,
                username=runtime.neo4j_username,
                password=runtime.neo4j_password,
                logger=logger,
                graph_factory=graph_factory,
                base_driver_factory=base_driver_factory,
            )
        return RetrievalBindings(
            runtime=runtime,
            vector_db_client=VectorDbClient(
                db_path=runtime.vector_db_path,
                collection_name=runtime.vector_collection_name,
            ),
            chroma=bootstrap_chroma_collection(
                db_path=runtime.vector_db_path,
                collection_name=runtime.vector_collection_name,
            ),
            neo4j_client=neo4j_client,
        )


retrieval_service = RetrievalService()
