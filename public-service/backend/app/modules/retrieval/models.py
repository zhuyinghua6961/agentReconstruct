from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RetrievalRuntimeConfig:
    vector_db_path: Path
    vector_collection_name: str
    fastqa_md_vector_db_path: Path
    fastqa_md_vector_collection_name: str
    highthinking_vector_db_path: Path
    highthinking_vector_collection_name: str
    neo4j_url: str
    neo4j_username: str
    neo4j_password: str


@dataclass(frozen=True)
class VectorCountSnapshot:
    count: int
    source: str
    collection_name: str
    sqlite_path: Path


@dataclass(frozen=True)
class Neo4jBootstrapResult:
    graph: Any | None
    available: bool
    degraded: bool
    connectivity_verified: bool
    attempted_modes: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True)
class ChromaBootstrapResult:
    client: Any | None
    collection: Any | None
    available: bool
    error: str | None = None


@dataclass(frozen=True)
class RetrievalBindings:
    runtime: RetrievalRuntimeConfig
    vector_db_client: Any
    chroma: ChromaBootstrapResult
    neo4j_client: Neo4jBootstrapResult | None = None
