from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.runtime import PublicServiceRuntime
from app.modules.documents.reference_preview import (
    build_reference_preview_entry,
    query_chroma_reference_metadata,
    query_graph_reference_metadata,
)
from app.modules.literature_search.cache import (
    build_literature_search_cache_key,
    build_literature_search_lock_key,
    cache_literature_search,
    get_cached_literature_search,
    resolve_literature_search_redis_service,
    run_literature_search_singleflight,
)
from app.modules.literature_search.doi_search import search_by_doi
from app.modules.literature_search.doi_utils import resolve_query_type
from app.modules.literature_search.rerank_hits import apply_literature_rerank
from app.modules.literature_search.title_search import search_by_title
from app.modules.retrieval.service import retrieval_service


class LiteratureSearchService:
    def _resolve_papers_dir(self) -> Path:
        return Path(get_settings().papers_dir).resolve()

    def _resolve_sources(self, sources: str) -> set[str]:
        value = str(sources or "both").strip().lower()
        if value == "fastqa":
            return {"fastqa"}
        if value == "fastqa_md":
            return {"fastqa_md"}
        if value == "highthinking":
            return {"highthinking"}
        return {"fastqa", "fastqa_md", "highthinking"}

    def _runtime_collections(
        self,
        runtime: PublicServiceRuntime | None,
    ) -> tuple[Any, Any, Any, Any, Any]:
        agent = getattr(runtime, "agent", None) if runtime is not None else None
        graph = getattr(agent, "graph", None) if agent is not None else None
        semantic_expert = getattr(agent, "semantic_expert", None) if agent is not None else None
        fastqa_collection = getattr(semantic_expert, "collection", None) if semantic_expert is not None else None
        if fastqa_collection is None and runtime is not None:
            fastqa_collection = getattr(runtime, "vector_collection", None)

        fastqa_md_collection = getattr(runtime, "fastqa_md_chroma", None) if runtime is not None else None
        if fastqa_md_collection is None and runtime is not None:
            bindings = retrieval_service.build_fastqa_md_chroma(project_root=str(runtime.settings.data_root))
            fastqa_md_collection = bindings.collection
            if bindings.collection is not None:
                runtime.fastqa_md_chroma = bindings.collection

        highthinking_collection = getattr(runtime, "highthinking_chroma", None) if runtime is not None else None
        if highthinking_collection is None and runtime is not None:
            bindings = retrieval_service.build_highthinking_chroma(project_root=str(runtime.settings.data_root))
            highthinking_collection = bindings.collection
            if bindings.collection is not None:
                runtime.highthinking_chroma = bindings.collection

        config = retrieval_service.build_runtime_config(project_root=str(get_settings().data_root))
        return graph, fastqa_collection, fastqa_md_collection, highthinking_collection, config

    def _enrich_hits(
        self,
        *,
        hits: list[dict[str, Any]],
        agent: Any,
        papers_dir: Path,
        logger: Any,
        match_mode: str,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for hit in hits:
            doi = str(hit.get("doi") or "").strip()
            if not doi:
                continue
            preview = build_reference_preview_entry(doi=doi, agent=agent, papers_dir=papers_dir, logger=logger)
            graph_meta = query_graph_reference_metadata(agent, doi, logger)
            chroma_meta = query_chroma_reference_metadata(agent, doi, logger)
            abstract = ""
            authors = ""
            items.append(
                {
                    **preview,
                    "authors": authors,
                    "abstract": abstract,
                    "match_source": hit.get("match_source") or preview.get("source") or "unknown",
                    "match_score": float(hit.get("match_score") or 0.0),
                    "match_mode": match_mode,
                    "title": preview.get("title") or hit.get("title") or graph_meta.get("title") or chroma_meta.get("title") or "",
                }
            )
        return items

    def _search_uncached(
        self,
        *,
        clean_query: str,
        query_type: str,
        match_mode: str,
        sources: str,
        limit: int,
        agent: Any,
        logger: Any,
        runtime: PublicServiceRuntime | None = None,
    ) -> dict[str, Any]:
        resolved_type = resolve_query_type(query=clean_query, query_type=query_type)
        source_set = self._resolve_sources(sources)
        graph, fastqa_collection, fastqa_md_collection, highthinking_collection, config = self._runtime_collections(
            runtime
        )

        warning_code: str | None = None
        if resolved_type == "doi":
            hits = search_by_doi(
                query=clean_query,
                limit=limit,
                fastqa_collection=fastqa_collection,
                fastqa_md_collection=fastqa_md_collection,
                highthinking_collection=highthinking_collection,
                sources=source_set,
                graph=graph,
                logger=logger,
            )
            effective_match_mode = "exact"
            rerank_meta = {"enabled": False, "applied": False, "fallback": False}
        else:
            hits, warning_code = search_by_title(
                query=clean_query,
                match_mode=match_mode,
                limit=limit,
                fastqa_collection=fastqa_collection,
                fastqa_md_collection=fastqa_md_collection,
                highthinking_collection=highthinking_collection,
                fastqa_db_path=config.vector_db_path,
                fastqa_collection_name=config.vector_collection_name,
                fastqa_md_db_path=config.fastqa_md_vector_db_path,
                fastqa_md_collection_name=config.fastqa_md_vector_collection_name,
                highthinking_db_path=config.highthinking_vector_db_path,
                highthinking_collection_name=config.highthinking_vector_collection_name,
                sources=source_set,
                graph=graph,
                logger=logger,
            )
            effective_match_mode = match_mode
            hits, rerank_meta = apply_literature_rerank(
                query=clean_query,
                hits=hits,
                limit=limit,
                logger=logger,
            )

        items = self._enrich_hits(
            hits=hits,
            agent=agent,
            papers_dir=self._resolve_papers_dir(),
            logger=logger,
            match_mode=effective_match_mode,
        )
        payload: dict[str, Any] = {
            "items": items,
            "count": len(items),
            "query_type_detected": resolved_type,
            "query": clean_query,
            "sources": sorted(source_set),
            "rerank": rerank_meta,
        }
        if warning_code and not items:
            payload["code"] = warning_code
            payload["error"] = "语义检索依赖的 embedding 服务不可用"
        return payload

    def search(
        self,
        *,
        query: str,
        query_type: str,
        match_mode: str,
        sources: str,
        limit: int,
        agent: Any,
        logger: Any,
        runtime: PublicServiceRuntime | None = None,
    ) -> tuple[dict[str, Any], int]:
        clean_query = str(query or "").strip()
        if not clean_query:
            return {"items": [], "count": 0, "query_type_detected": "title", "error": "缺少查询参数"}, 200

        resolved_type = resolve_query_type(query=clean_query, query_type=query_type)
        effective_match_mode = "exact" if resolved_type == "doi" else match_mode
        redis_service = resolve_literature_search_redis_service(runtime)
        cache_key = None
        if redis_service is not None:
            cache_key = build_literature_search_cache_key(
                redis_service=redis_service,
                query=clean_query,
                query_type=resolved_type,
                match_mode=effective_match_mode,
                sources=sources,
                limit=limit,
            )
            cached = get_cached_literature_search(redis_service=redis_service, cache_key=cache_key)
            if cached is not None:
                return cached, 200

        lock_key = None
        if redis_service is not None and cache_key is not None:
            lock_key = build_literature_search_lock_key(
                redis_service=redis_service,
                query=clean_query,
                query_type=resolved_type,
                match_mode=effective_match_mode,
                sources=sources,
                limit=limit,
            )

        def _compute() -> dict[str, Any]:
            payload = self._search_uncached(
                clean_query=clean_query,
                query_type=query_type,
                match_mode=match_mode,
                sources=sources,
                limit=limit,
                agent=agent,
                logger=logger,
                runtime=runtime,
            )
            if redis_service is not None and cache_key is not None:
                cache_literature_search(
                    redis_service=redis_service,
                    cache_key=cache_key,
                    payload=payload,
                )
            return payload

        if redis_service is not None and lock_key is not None:
            payload = run_literature_search_singleflight(
                redis_service=redis_service,
                lock_key=lock_key,
                read_cached_fn=lambda: get_cached_literature_search(
                    redis_service=redis_service,
                    cache_key=cache_key,
                )
                if cache_key is not None
                else lambda: None,
                compute_fn=_compute,
            )
            return payload, 200

        return _compute(), 200


literature_search_service = LiteratureSearchService()
