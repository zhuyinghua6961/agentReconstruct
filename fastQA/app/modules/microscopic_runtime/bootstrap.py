from __future__ import annotations

from typing import Any

from app.modules.microscopic_runtime.embedding_client import RemoteEmbeddingClient
from app.modules.microscopic_runtime.path_utils import resolve_project_path


def init_embedding_model(
    *,
    embedding_model_type: str,
    model_path: str,
    embedding_api_url: str,
    project_root: str,
    flag_available: bool,
    requests_available: bool,
    flag_model_cls: Any,
    requests_module: Any,
) -> Any:
    if embedding_model_type == "remote":
        if not requests_available:
            raise ImportError("requests is required for remote embedding")
        if not embedding_api_url:
            raise ValueError("EMBEDDING_API_URL is required for remote embedding")
        return RemoteEmbeddingClient(embedding_api_url, requests_module)

    if not flag_available:
        raise ImportError("FlagEmbedding is required for local embedding")

    resolved_model_path = resolve_project_path(model_path, project_root)
    return flag_model_cls(
        resolved_model_path,
        query_instruction_for_retrieval="Represent this sentence for searching relevant passages:",
        use_fp16=False,
        trust_remote_code=True,
    )


def init_vector_collection(
    *,
    db_path: str,
    project_root: str,
    chromadb_persistent_client_cls: Any,
    collection_name: str = "lfp_papers",
):
    resolved_db_path = resolve_project_path(db_path, project_root)
    client = chromadb_persistent_client_cls(path=resolved_db_path)
    collection = client.get_collection(collection_name)
    return client, collection
