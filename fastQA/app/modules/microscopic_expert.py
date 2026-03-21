from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import chromadb

    CHROMADB_AVAILABLE = True
except Exception:
    CHROMADB_AVAILABLE = False
    chromadb = None

try:
    from FlagEmbedding import FlagModel

    FLAG_EMBEDDING_AVAILABLE = True
except Exception:
    FLAG_EMBEDDING_AVAILABLE = False
    FlagModel = None

try:
    import requests

    REQUESTS_AVAILABLE = True
except Exception:
    REQUESTS_AVAILABLE = False
    requests = None

from app.modules.generation_pipeline.rerank_service import rerank_documents as rerank_documents_impl
from app.modules.microscopic_runtime import init_embedding_model, init_vector_collection
from app.modules.microscopic_search import run_semantic_search


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class MicroscopicSemanticExpert:
    def __init__(
        self,
        model_path: str | None = None,
        db_path: str | None = None,
        embedding_model_type: str | None = None,
        embedding_api_url: str | None = None,
        enable_translation: bool = False,
        **_kwargs: Any,
    ) -> None:
        self.available = False
        self.availability_detail = ""
        self.embedding_model = None
        self.client = None
        self.collection = None
        self.translator = None

        if not CHROMADB_AVAILABLE:
            self.availability_detail = "chromadb unavailable"
            return

        if embedding_model_type is None:
            embedding_model_type = str(os.getenv("EMBEDDING_MODEL_TYPE", "local") or "local").strip()
        if model_path is None:
            model_path = str(os.getenv("EMBEDDING_MODEL_PATH", "models/bge_model") or "models/bge_model").strip()
        if embedding_api_url is None:
            embedding_api_url = str(os.getenv("EMBEDDING_API_URL", "") or "").strip()
        if db_path is None:
            db_path = str(os.getenv("VECTOR_DB_PATH", "vector_database") or "vector_database").strip()

        try:
            self.embedding_model = init_embedding_model(
                embedding_model_type=embedding_model_type,
                model_path=model_path,
                embedding_api_url=embedding_api_url,
                project_root=str(PROJECT_ROOT),
                flag_available=FLAG_EMBEDDING_AVAILABLE,
                requests_available=REQUESTS_AVAILABLE,
                flag_model_cls=FlagModel,
                requests_module=requests,
            )
            self.client, self.collection = init_vector_collection(
                db_path=db_path,
                project_root=str(PROJECT_ROOT),
                chromadb_persistent_client_cls=chromadb.PersistentClient,
                collection_name=str(os.getenv("VECTOR_COLLECTION_NAME", "lfp_papers") or "lfp_papers").strip(),
            )
            self.available = self.embedding_model is not None and self.collection is not None
            self.availability_detail = "ok" if self.available else "collection unavailable"
        except Exception as exc:
            self.available = False
            self.availability_detail = str(exc)

        if enable_translation:
            self.availability_detail = self.availability_detail or "translation disabled in fastQA minimal migration"

    def _rerank_documents(
        self,
        *,
        query: str,
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        top_n: int = 20,
    ) -> dict[str, Any]:
        provider = str(os.getenv("QA_RETRIEVAL_RERANK_PROVIDER", "dashscope") or "dashscope").strip()
        api_key = (
            str(os.getenv("QA_RETRIEVAL_RERANK_API_KEY", "") or "").strip()
            or str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()
        )
        base_url = str(
            os.getenv("QA_RETRIEVAL_RERANK_BASE_URL", "https://dashscope.aliyuncs.com")
            or "https://dashscope.aliyuncs.com"
        ).strip()
        model = str(os.getenv("QA_RETRIEVAL_RERANK_MODEL", "qwen3-vl-rerank") or "qwen3-vl-rerank").strip()
        try:
            timeout_seconds = float(str(os.getenv("QA_RETRIEVAL_RERANK_TIMEOUT", "20") or "20").strip())
        except Exception:
            timeout_seconds = 20.0

        return rerank_documents_impl(
            query=query,
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )

    def search(self, user_question, n_results=5, translate=False, use_rerank=False, rerank_candidates=50):
        if not self.available or self.embedding_model is None or self.collection is None:
            return {
                "documents": [],
                "metadatas": [],
                "distances": [],
                "ids": [],
                "rerank": {
                    "enabled": bool(use_rerank),
                    "applied": False,
                    "fallback": True,
                    "reason": self.availability_detail,
                },
            }
        return run_semantic_search(
            user_question=user_question,
            n_results=n_results,
            embedding_model=self.embedding_model,
            collection=self.collection,
            translator=self.translator,
            translate=translate,
            use_rerank=use_rerank,
            rerank_candidates=rerank_candidates,
            rerank_fn=self._rerank_documents,
        )

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
