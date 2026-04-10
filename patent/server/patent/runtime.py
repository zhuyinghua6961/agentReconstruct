from __future__ import annotations

import logging
import os
from types import SimpleNamespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from server.patent.answering import PatentAnswerBuilder
from server.patent.archive_loader import PatentArchiveLoader
from server.patent.models import PatentRetrievalClaim, PatentRetrievalPlan
from server.patent.resource_registry import PatentResourceRegistry
from server.patent.retrieval_service import PatentRetrievalService
from server.patent.stages.evidence_loading import run_stage3_load_patent_evidence
from server.patent.stages.planning import DEFAULT_PATENT_STAGE1_PROMPT, run_stage1_pre_answer_and_planning
from server.patent.stages.retrieval import (
    extract_patent_source_ids_from_results,
    run_stage2_targeted_retrieval,
    run_stage25_patent_evidence_expansion,
)
from server.patent.stages.synthesis import run_stage4_synthesis_with_patent_evidence

try:
    import chromadb
except Exception:  # pragma: no cover - dependency guard
    chromadb = None

try:
    from FlagEmbedding import FlagModel
except Exception:  # pragma: no cover - dependency guard
    FlagModel = None


_LOGGER = logging.getLogger("patent.runtime")


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return str(default or "").strip()


def _positive_int_env(*names: str, default: int) -> int:
    raw = _first_env(*names, default=str(default))
    try:
        value = int(str(raw).strip())
    except Exception:
        return int(default)
    return value if value >= 1 else int(default)


def _resolve_local_embedding_model_path(repo_root: Path) -> str:
    configured = _first_env("PATENT_EMBEDDING_MODEL_PATH", "EMBEDDING_MODEL_PATH")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            repo_candidate = repo_root / candidate
            if repo_candidate.exists():
                return str(repo_candidate.resolve())
            return configured
        return str(candidate.resolve())
    bundled = repo_root / "resource" / "fastqa" / "models" / "bge_model"
    if bundled.exists():
        return str(bundled.resolve())
    return "BAAI/bge-large-zh-v1.5"


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


class PatentPlanningClient:
    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: float) -> None:
        self._api_key = str(api_key or "").strip()
        self._base_url = str(base_url or "").strip()
        self._http = httpx.Client(timeout=float(timeout_seconds))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def close(self) -> None:
        self._http.close()

    def _create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "model": str(model or "").strip(),
            "messages": list(messages or []),
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        if response_format is not None:
            payload["response_format"] = dict(response_format)
        response = self._http.post(
            f"{self._base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        choices = list(body.get("choices") or [])
        message = dict((choices[0] or {}).get("message") or {}) if choices else {}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=str(message.get("content") or "")))]
        )


def _build_patent_planning_runtime_inputs() -> tuple[Any | None, str]:
    use_shared_env = _env_flag(
        "PATENT_STAGE1_OPENAI_USE_SHARED_ENV",
        default=_env_flag("PATENT_OPENAI_USE_SHARED_ENV", default=False),
    )
    shared_api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "") if use_shared_env else ""
    shared_base_url = (os.getenv("OPENAI_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or "") if use_shared_env else ""
    shared_model = (os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL") or "") if use_shared_env else ""
    api_key = str(
        os.getenv("PATENT_STAGE1_OPENAI_API_KEY")
        or os.getenv("PATENT_OPENAI_API_KEY")
        or shared_api_key
        or ""
    ).strip()
    base_url = str(
        os.getenv("PATENT_STAGE1_OPENAI_BASE_URL")
        or os.getenv("PATENT_OPENAI_BASE_URL")
        or shared_base_url
        or ""
    ).strip()
    model = str(
        os.getenv("PATENT_STAGE1_OPENAI_MODEL")
        or os.getenv("PATENT_OPENAI_MODEL")
        or shared_model
        or ""
    ).strip()
    timeout_seconds = float(
        str(
            os.getenv("PATENT_STAGE1_OPENAI_TIMEOUT_SECONDS")
            or os.getenv("PATENT_OPENAI_TIMEOUT_SECONDS")
            or "30"
        ).strip()
    )
    if not api_key or not base_url or not model:
        _LOGGER.warning(
            "Patent planning client disabled use_shared_env=%s api_key_set=%s base_url_set=%s model=%s",
            use_shared_env,
            bool(api_key),
            bool(base_url),
            model,
        )
        return None, ""
    _LOGGER.info(
        "Patent planning client enabled use_shared_env=%s model=%s base_url=%s timeout_seconds=%s",
        use_shared_env,
        model,
        base_url,
        timeout_seconds,
    )
    return PatentPlanningClient(api_key=api_key, base_url=base_url, timeout_seconds=timeout_seconds), model


class PatentEmbeddingClient:
    def __init__(self) -> None:
        self._repo_root = Path(__file__).resolve().parents[3]
        self._mode = _first_env("PATENT_EMBEDDING_MODEL_TYPE", "EMBEDDING_MODEL_TYPE", default="remote").lower()
        self._http = httpx.Client(timeout=float(str(os.getenv("PATENT_EMBEDDING_API_TIMEOUT_SECONDS") or os.getenv("EMBEDDING_API_TIMEOUT_SECONDS") or "20").strip()))
        self._api_url = _first_env("PATENT_EMBEDDING_API_URL", "EMBEDDING_API_URL", default="http://127.0.0.1:8001/v1/embeddings")
        self._api_model = _first_env("PATENT_EMBEDDING_API_MODEL", "EMBEDDING_API_MODEL", default="bge-local")
        self._local_model_path = _resolve_local_embedding_model_path(self._repo_root)
        self._local_model = None

    def close(self) -> None:
        self._http.close()

    def encode(self, texts: list[str]) -> list[list[float]]:
        if self._mode == "local":
            self._prime_local_model()
            if self._local_model is not None:
                return self._local_model.encode(texts).tolist()
        response = self._http.post(
            self._api_url,
            json={"input": texts, "model": self._api_model},
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        data = list(payload.get("data") or [])
        embeddings = [list(item.get("embedding") or []) for item in data if isinstance(item, dict)]
        if embeddings:
            return embeddings
        single = list(payload.get("embedding") or [])
        return [single] if single else []

    def _prime_local_model(self) -> None:
        if self._local_model is not None:
            return
        if FlagModel is None:
            if self._api_url:
                self._mode = "remote"
                _LOGGER.warning("FlagEmbedding unavailable; falling back to remote patent embedding endpoint")
                return
            raise RuntimeError("FlagEmbedding is unavailable and no remote embedding endpoint is configured")
        try:
            self._local_model = FlagModel(
                self._local_model_path,
                query_instruction_for_retrieval="Represent this sentence for searching relevant passages:",
                use_fp16=False,
                trust_remote_code=True,
            )
        except Exception:
            if self._api_url:
                self._mode = "remote"
                _LOGGER.warning(
                    "Patent local embedding model failed to load from %s; falling back to remote endpoint",
                    self._local_model_path,
                    exc_info=True,
                )
                return
            raise


class ChromaPatentSearch:
    def __init__(self, *, db_path: str, collection_name: str, embedding_client: PatentEmbeddingClient) -> None:
        if chromadb is None:
            raise RuntimeError("chromadb is unavailable")
        self._client = chromadb.PersistentClient(path=db_path)
        self._collection = self._client.get_collection(collection_name)
        self._embedding_client = embedding_client

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def search(self, *, question: str, top_k: int, patent_ids: list[str] | None = None) -> list[dict[str, Any]]:
        embeddings = self._embedding_client.encode([question])
        if not embeddings:
            return []
        where: dict[str, Any] | None = None
        normalized_patent_ids = [str(item).strip().upper() for item in list(patent_ids or []) if str(item).strip()]
        if len(normalized_patent_ids) == 1:
            where = {"patent_id": normalized_patent_ids[0]}
        elif len(normalized_patent_ids) > 1:
            where = {"patent_id": {"$in": normalized_patent_ids}}
        results = self._collection.query(
            query_embeddings=embeddings,
            n_results=max(int(top_k), 1),
            include=["documents", "metadatas", "distances"],
            where=where,
        )
        documents = list((results.get("documents") or [[]])[0])
        metadatas = list((results.get("metadatas") or [[]])[0])
        distances = list((results.get("distances") or [[]])[0])
        ids = list((results.get("ids") or [[]])[0])
        hits: list[dict[str, Any]] = []
        for index, metadata in enumerate(metadatas):
            item = dict(metadata or {})
            item["document"] = documents[index] if index < len(documents) else ""
            item["distance"] = distances[index] if index < len(distances) else None
            item["id"] = ids[index] if index < len(ids) else None
            hits.append(item)
        return hits


@dataclass
class PatentRuntime:
    retrieval_service: PatentRetrievalService
    resources: list[Any]
    archive_loader: PatentArchiveLoader | None = None
    answer_builder: Any | None = None
    planning_client: Any | None = None
    planning_model: str = ""
    stage1_prompt: str = DEFAULT_PATENT_STAGE1_PROMPT
    stage25_is_noop: bool = True
    stage25_skip_reason: str = "patent_mode_no_md_expansion"
    stage3_force_pdf: bool = False
    stage2_parallel_workers: int = 4
    stage3_parallel_workers: int = 4

    def stage1_pre_answer_and_planning(
        self,
        user_question: str,
        conversation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return run_stage1_pre_answer_and_planning(
            user_question=user_question,
            client=self.planning_client,
            model=self.planning_model,
            logger=_LOGGER,
            conversation_context=conversation_context,
            stage1_prompt=self.stage1_prompt,
        )

    def stage2_targeted_retrieval(
        self,
        retrieval_claims: Any,
        *,
        user_question: str,
        should_cancel: Any | None = None,
        active_stream_count: int | None = None,
    ) -> dict[str, Any]:
        if isinstance(retrieval_claims, PatentRetrievalPlan):
            queries = list(retrieval_claims.evidence_localization_queries or retrieval_claims.candidate_recall_queries or [])
            retrieval_claims = [
                PatentRetrievalClaim(
                    claim=query,
                    keywords=[],
                    preferred_sections=list(retrieval_claims.preferred_sections or []),
                    filters=dict(retrieval_claims.filters or {}),
                )
                for query in queries
            ] or [
                PatentRetrievalClaim(
                    claim=" ".join(str(item).strip() for item in retrieval_claims.explicit_patent_ids if str(item).strip()),
                    keywords=[],
                    preferred_sections=list(retrieval_claims.preferred_sections or []),
                    filters=dict(retrieval_claims.filters or {}),
                )
            ]
        return run_stage2_targeted_retrieval(
            retrieval_service=self.retrieval_service,
            retrieval_claims=list(retrieval_claims or []),
            user_question=user_question,
            query_client=self.planning_client,
            query_model=self.planning_model,
            logger=_LOGGER,
            should_cancel=should_cancel,
            active_stream_count=active_stream_count,
            parallel_workers=self.stage2_parallel_workers,
        )

    def _extract_patent_ids_from_results(self, retrieval_results: dict[str, Any]) -> list[str]:
        return extract_patent_source_ids_from_results(
            retrieval_service=self.retrieval_service,
            retrieval_results=retrieval_results,
        )

    def stage25_patent_evidence_expansion(
        self,
        *,
        retrieval_results: dict[str, Any],
        user_question: str,
        source_ids: list[str],
    ) -> dict[str, Any]:
        return run_stage25_patent_evidence_expansion(
            retrieval_results=retrieval_results,
            skipped=self.stage25_is_noop,
            skip_reason=self.stage25_skip_reason,
        )

    def stage3_load_patent_evidence(
        self,
        *,
        retrieval_results: dict[str, Any],
        source_ids: list[str],
        should_cancel: Any | None = None,
    ) -> dict[str, Any]:
        return run_stage3_load_patent_evidence(
            retrieval_results=retrieval_results,
            source_ids=source_ids,
            catalog_loader=self.archive_loader.load_catalog_record if self.archive_loader is not None else None,
            table_loader=self.archive_loader.load_tables if self.archive_loader is not None else None,
            pdf_loader=self.archive_loader.load_pdf_document if self.archive_loader is not None else None,
            force_pdf=self.stage3_force_pdf,
            parallel_workers=self.stage3_parallel_workers,
            should_cancel=should_cancel,
        )

    def stage4_synthesis_with_patent_evidence(
        self,
        *,
        user_question: str,
        deep_answer: str,
        patent_evidence_bundle: dict[str, Any],
        retrieval_results: dict[str, Any] | None = None,
        should_cancel: Any | None = None,
        content_callback: Any | None = None,
        conversation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del should_cancel
        return run_stage4_synthesis_with_patent_evidence(
            user_question=user_question,
            deep_answer=deep_answer,
            patent_evidence_bundle=patent_evidence_bundle,
            retrieval_results=retrieval_results,
            answer_builder=self.answer_builder,
            content_callback=content_callback,
            conversation_context=conversation_context,
        )

    def close(self) -> None:
        for resource in self.resources:
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    continue


def build_default_patent_runtime() -> PatentRuntime | None:
    registry = PatentResourceRegistry.discover()
    if not registry.archive_available():
        _LOGGER.warning("Patent runtime bootstrap skipped because archive root is unavailable")
        return None

    archive_loader = PatentArchiveLoader(registry.archive_root)
    answer_builder = PatentAnswerBuilder.from_env()
    resources: list[Any] = [answer_builder]
    planning_client, planning_model = _build_patent_planning_runtime_inputs()
    if planning_client is not None:
        resources.append(planning_client)
    abstract_search = None
    chunk_search = None

    if registry.vector_resources_available() and chromadb is not None:
        vector_resources: list[Any] = []
        try:
            embedding_client = PatentEmbeddingClient()
            vector_resources.append(embedding_client)
            abstract_runtime = ChromaPatentSearch(
                db_path=str(registry.abstract_db_path),
                collection_name=registry.abstract_collection,
                embedding_client=embedding_client,
            )
            vector_resources.append(abstract_runtime)
            chunk_runtime = ChromaPatentSearch(
                db_path=str(registry.chunk_db_path),
                collection_name=registry.chunk_collection,
                embedding_client=embedding_client,
            )
            vector_resources.append(chunk_runtime)
        except Exception:
            _LOGGER.warning("Patent vector runtime bootstrap failed; degrading to no-vector retrieval", exc_info=True)
            for resource in reversed(vector_resources):
                close = getattr(resource, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        continue
        else:
            resources.extend(vector_resources)
            abstract_search = lambda question, top_k: abstract_runtime.search(question=question, top_k=top_k)
            chunk_search = lambda question, candidate_patent_ids, top_k: chunk_runtime.search(
                question=question,
                top_k=top_k,
                patent_ids=candidate_patent_ids,
            )

    retrieval_service = PatentRetrievalService(
        identity_registry=archive_loader.build_identity_registry(),
        catalog_records=archive_loader.build_catalog_records(),
        retrieval_version="retrieval-v2",
        catalog_index_version="catalog-v2",
        abstract_vector_search=abstract_search,
        chunk_vector_search=chunk_search,
        table_loader=archive_loader.load_tables,
        answer_builder=answer_builder,
        archive_loader=archive_loader,
    )
    _LOGGER.info(
        "Patent runtime bootstrap complete archive_root=%s abstract_db=%s chunk_db=%s planner_ready=%s planning_model=%s answer_builder_ready=%s answer_model=%s vector_enabled=%s",
        registry.archive_root,
        registry.abstract_db_path,
        registry.chunk_db_path,
        planning_client is not None,
        planning_model,
        bool(getattr(answer_builder, "api_key", "")),
        getattr(answer_builder, "model", ""),
        bool(abstract_search and chunk_search),
    )
    return PatentRuntime(
        retrieval_service=retrieval_service,
        resources=resources,
        archive_loader=archive_loader,
        answer_builder=answer_builder,
        planning_client=planning_client,
        planning_model=planning_model,
        stage3_force_pdf=_first_env("PATENT_STAGE3_FORCE_PDF", default="false").lower() in {"1", "true", "yes", "on"},
        stage2_parallel_workers=_positive_int_env("PATENT_STAGE2_PARALLEL_WORKERS", default=4),
        stage3_parallel_workers=_positive_int_env("PATENT_STAGE3_PARALLEL_WORKERS", default=4),
    )
