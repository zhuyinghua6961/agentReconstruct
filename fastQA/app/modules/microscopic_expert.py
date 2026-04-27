from __future__ import annotations

import os
from pathlib import Path
from threading import Event, Thread
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
from app.integrations.llm.upstream_gate import Stage2UpstreamGateCancelled


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _run_cancelable_upstream_call(
    *,
    call,
    should_cancel,
    abort=None,
    cancel_message: str,
):
    if should_cancel is None:
        return call()

    done = Event()
    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result_box["value"] = call()
        except BaseException as exc:  # pragma: no cover - propagated below
            error_box["error"] = exc
        finally:
            done.set()

    worker = Thread(target=_runner, daemon=True)
    worker.start()
    while not done.wait(0.05):
        try:
            cancelled = bool(should_cancel())
        except Exception:
            cancelled = False
        if not cancelled:
            continue
        if abort is not None:
            try:
                abort()
            except Exception:
                pass
        done.wait(0.2)
        raise Stage2UpstreamGateCancelled(cancel_message)

    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


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
        self.rerank_session_pool = _kwargs.get("rerank_session_pool")

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
        rerank_gate: Any | None = None,
        rerank_gate_limit: int | None = None,
        trace_label: str | None = None,
        logger: Any | None = None,
        should_cancel: Any | None = None,
    ) -> dict[str, Any]:
        provider = str(os.getenv("QA_RETRIEVAL_RERANK_PROVIDER", "dashscope") or "dashscope").strip()
        provider_norm = provider.lower()
        raw_api_key = str(os.getenv("QA_RETRIEVAL_RERANK_API_KEY", "") or "").strip()
        if provider_norm == "local":
            api_key = raw_api_key
            default_base_url = "http://localhost:8084"
        else:
            api_key = raw_api_key or str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()
            default_base_url = "https://dashscope.aliyuncs.com"
        base_url = str(
            os.getenv("QA_RETRIEVAL_RERANK_BASE_URL", default_base_url) or default_base_url
        ).strip()
        model = str(os.getenv("QA_RETRIEVAL_RERANK_MODEL", "qwen3-vl-rerank") or "qwen3-vl-rerank").strip()
        try:
            timeout_seconds = float(str(os.getenv("QA_RETRIEVAL_RERANK_TIMEOUT", "20") or "20").strip())
        except Exception:
            timeout_seconds = 20.0

        def _call_rerank(*, session: Any | None = None) -> dict[str, Any]:
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
                logger=logger,
                session=session,
            )

        rerank_session_pool = getattr(self, "rerank_session_pool", None)

        def _run() -> dict[str, Any]:
            if rerank_session_pool is not None:
                with rerank_session_pool.lease_lane(trace_label=trace_label or "rerank") as lane:
                    if lane is not None and getattr(lane, "session", None) is not None:
                        leased_session = lane.session
                        leased_lane_id = int(getattr(lane, "lane_id", -1))
                        if logger is not None:
                            logger.info(
                                "stage2 rerank lane lease trace_label=%s lane=%s ready=true",
                                str(trace_label or ""),
                                leased_lane_id,
                            )
                        return _run_cancelable_upstream_call(
                            call=lambda: _call_rerank(session=leased_session),
                            should_cancel=should_cancel,
                            abort=(
                                (lambda: rerank_session_pool.abort_lane(leased_lane_id, error_summary="cancelled"))
                                if hasattr(rerank_session_pool, "abort_lane")
                                else None
                            ),
                            cancel_message="stage2 rerank upstream call cancelled",
                        )
            return _call_rerank()

        if rerank_gate is not None:
            try:
                gate_ctx = rerank_gate.enter(
                    trace_label=trace_label,
                    request_limit=rerank_gate_limit,
                    should_cancel=should_cancel,
                )
            except TypeError:
                gate_ctx = rerank_gate.enter(trace_label=trace_label)
            with gate_ctx:
                return _run()
        return _run()

    def search(
        self,
        user_question,
        n_results=5,
        translate=False,
        use_rerank=False,
        rerank_candidates=50,
        logger: Any | None = None,
        trace_label: str | None = None,
        rerank_gate: Any | None = None,
        rerank_gate_limit: int | None = None,
        should_cancel: Any | None = None,
    ):
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
            rerank_fn=lambda **kwargs: self._rerank_documents(
                **kwargs,
                rerank_gate=rerank_gate,
                rerank_gate_limit=rerank_gate_limit,
                trace_label=trace_label,
                logger=logger,
                should_cancel=should_cancel,
            ),
            logger=logger,
            trace_label=trace_label,
        )

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
