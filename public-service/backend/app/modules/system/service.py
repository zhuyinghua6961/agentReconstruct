from __future__ import annotations

import logging
import os
from datetime import datetime

from app.core.timezone import BEIJING_TIMEZONE, now_beijing_iso
from typing import Any

from app.core.runtime import PublicServiceRuntime
from app.modules.conversation.cache import (
    build_conversation_detail_cache_key,
    build_conversation_list_cache_key,
    build_conversation_list_recent_pages_key,
    get_conversation_detail_cache_version,
    get_conversation_list_cache_version,
    get_recent_conversation_list_pages,
)
from app.modules.qa_cache.metrics import snapshot_cache_metrics


logger = logging.getLogger(__name__)


class SystemService:
    @staticmethod
    def _ttl_or_none(runtime: PublicServiceRuntime, key: str) -> int | None:
        redis_service = runtime.redis_service
        if redis_service is None:
            return None
        ttl = redis_service.ttl(key)
        return ttl if isinstance(ttl, int) else None

    @staticmethod
    def _cache_status() -> dict[str, Any]:
        return {
            "metrics": snapshot_cache_metrics(),
            "config": {
                "lock_enabled": str(os.getenv("QA_CACHE_LOCK_ENABLED", "1") or "1").strip(),
                "wait_ms": str(os.getenv("QA_CACHE_WAIT_MS", "400") or "400").strip(),
                "lock_ttl_seconds": str(os.getenv("QA_CACHE_LOCK_TTL_SECONDS", "30") or "30").strip(),
                "stage1_ttl_seconds": str(os.getenv("QA_STAGE1_CACHE_TTL_SECONDS", "3600") or "3600").strip(),
                "stage2_ttl_seconds": str(os.getenv("QA_STAGE2_CACHE_TTL_SECONDS", "1800") or "1800").strip(),
                "pdf_text_ttl_seconds": str(os.getenv("PDF_TEXT_CACHE_TTL_SECONDS", "86400") or "86400").strip(),
                "conversation_list_ttl_seconds": str(os.getenv("CONVERSATION_LIST_CACHE_TTL_SECONDS", "60") or "60").strip(),
                "conversation_detail_ttl_seconds": str(os.getenv("CONVERSATION_DETAIL_CACHE_TTL_SECONDS", "30") or "30").strip(),
                "conversation_detail_touch_on_hit": str(os.getenv("CONVERSATION_DETAIL_CACHE_TOUCH_ON_HIT", "1") or "1").strip(),
                "conversation_list_recent_pages_ttl_seconds": str(os.getenv("CONVERSATION_LIST_RECENT_PAGES_TTL_SECONDS", "900") or "900").strip(),
                "conversation_list_recent_pages_limit": str(os.getenv("CONVERSATION_LIST_RECENT_PAGES_LIMIT", "8") or "8").strip(),
            },
        }

    def build_health(self, runtime: PublicServiceRuntime) -> dict[str, Any]:
        component_status = dict(runtime.component_status or {})
        component_states = [str((item or {}).get("status") or "").strip().lower() for item in component_status.values()]
        overall_status = "healthy"
        if any(state == "degraded" for state in component_states):
            overall_status = "degraded"
        elif any(state in {"pending", "skeleton"} for state in component_states):
            overall_status = "starting"
        return {
            "status": overall_status,
            "agent_initialized": (
                runtime.agent is not None
                or runtime.vector_collection is not None
                or bool(getattr(runtime.neo4j_client, "available", False))
            ),
            "generation_runtime_initialized": runtime.generation_runtime is not None,
            "vector_db_initialized": runtime.vector_db_client is not None,
            "storage_backend": str(((runtime.component_status or {}).get("storage") or {}).get("backend") or ""),
            "components": component_status,
            "qa_cache": self._cache_status(),
            "timestamp": now_beijing_iso(),
        }

    def build_background_status(self, runtime: PublicServiceRuntime) -> tuple[dict[str, Any], int]:
        try:
            outbox_thread = runtime.conversation_outbox_thread
            outbox_status = dict(runtime.conversation_outbox_status or {})
            outbox_status["thread_alive"] = bool(outbox_thread.is_alive()) if outbox_thread is not None and hasattr(outbox_thread, "is_alive") else bool(outbox_status.get("thread_alive"))
            if not outbox_status:
                outbox_status = {
                    "state": "uninitialized",
                    "thread_alive": False,
                    "loops": 0,
                    "last_summary": None,
                    "last_error": "",
                    "last_run_at": None,
                }

            upload_status = dict(((runtime.component_status or {}).get("upload_processing") or {}))
            upload_worker = getattr(runtime, "upload_processing_worker", None)
            if upload_worker is not None:
                upload_status.setdefault("enabled", bool(getattr(upload_worker, "enabled", True)))
                active_keys = getattr(upload_worker, "_active_keys", None)
                if isinstance(active_keys, set):
                    upload_status["active_tasks"] = len(active_keys)

            assistant_inbox_status = dict(getattr(runtime, "authority_assistant_inbox_status", {}) or {})
            assistant_inbox_thread = getattr(runtime, "authority_assistant_inbox_thread", None)
            assistant_inbox_status["thread_alive"] = bool(assistant_inbox_thread.is_alive()) if assistant_inbox_thread is not None and hasattr(assistant_inbox_thread, "is_alive") else bool(assistant_inbox_status.get("thread_alive"))
            if not assistant_inbox_status:
                assistant_inbox_status = {
                    "state": "uninitialized",
                    "thread_alive": False,
                    "loops": 0,
                    "last_summary": None,
                    "last_error": "",
                    "last_run_at": None,
                    "backlog": 0,
                    "processing": 0,
                    "failed": 0,
                    "enabled": True,
                }

            status = {
                "has_current_answer_context": bool(runtime.current_answer_context and runtime.current_answer_context.strip()),
                "current_answer_preview": (runtime.current_answer_context[:500] + "...") if runtime.current_answer_context else "",
                "latest_background_file": None,
                "latest_background_file_mtime": None,
                "conversation_outbox": outbox_status,
                "authority_assistant_inbox": assistant_inbox_status,
                "upload_processing": upload_status,
                "qa_cache": self._cache_status(),
            }

            logs_dir = runtime.logs_dir
            if logs_dir.exists() and logs_dir.is_dir():
                files = sorted(
                    logs_dir.glob("background_programmatic_insert_*.json"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                if files:
                    latest = files[0]
                    status["latest_background_file"] = str(latest)
                    status["latest_background_file_mtime"] = datetime.fromtimestamp(latest.stat().st_mtime, tz=BEIJING_TIMEZONE).isoformat(timespec="seconds")

            return {"success": True, "status": status}, 200
        except Exception as exc:
            logger.warning("Failed to read background status: %s", exc)
            return {"success": False, "error": str(exc)}, 500

    def build_kb_info(self, runtime: PublicServiceRuntime) -> tuple[dict[str, Any], int]:
        try:
            chromadb_count = self._chromadb_count(runtime)
            graph = self._graph(runtime)
            kb_ready = (
                graph is not None
                or runtime.vector_collection is not None
                or chromadb_count > 0
            )
            if not kb_ready:
                return {
                    "success": False,
                    "message": "知识库运行时未初始化",
                    "kb_size": 0,
                    "chromadb_size": chromadb_count,
                    "source_stats": {
                        "neo4j": 0,
                        "neo4j_connected": False,
                        "chromadb": chromadb_count,
                    },
                }, 200

            neo4j_connected = True
            try:
                if graph is None:
                    raise RuntimeError("neo4j_graph_unavailable")
                query_result = graph.query("MATCH (n) RETURN count(n) as count")
                node_count = int(query_result[0]["count"] or 0) if query_result else 0
            except Exception as exc:
                logger.warning("Failed to query Neo4j node count: %s", exc)
                node_count = 0
                neo4j_connected = False

            return {
                "success": True,
                "kb_size": node_count,
                "chromadb_size": chromadb_count,
                "source_stats": {
                    "neo4j": node_count,
                    "neo4j_connected": neo4j_connected,
                    "chromadb": chromadb_count,
                },
            }, 200
        except Exception as exc:
            logger.error("Failed to get KB info: %s", exc)
            return {
                "success": False,
                "message": str(exc),
                "kb_size": 0,
                "chromadb_size": 0,
                "source_stats": {
                    "neo4j": 0,
                    "neo4j_connected": False,
                    "chromadb": 0,
                },
            }, 200

    def refresh_kb(self, runtime: PublicServiceRuntime) -> tuple[dict[str, Any], int]:
        try:
            payload_base = {"scope": "instance_local", "cluster_consistency": "not_coordinated"}
            if runtime.init_agent is None:
                return {**payload_base, "success": False, "message": "知识库运行时未配置"}, 200
            if runtime.init_agent():
                return {**payload_base, "success": True, "message": "当前实例知识库已刷新"}, 200
            return {**payload_base, "success": False, "message": "当前实例知识库刷新失败"}, 200
        except Exception as exc:
            logger.error("Failed to refresh KB: %s", exc)
            return {"success": False, "message": str(exc), "scope": "instance_local", "cluster_consistency": "not_coordinated"}, 200

    def clear_cache(self, runtime: PublicServiceRuntime) -> tuple[dict[str, Any], int]:
        try:
            runtime.answer_cache.clear()
            logger.info("Answer cache cleared")
            return {
                "success": True,
                "message": "当前实例答案缓存已清空",
                "scope": "instance_local",
                "cluster_consistency": "not_coordinated",
            }, 200
        except Exception as exc:
            logger.error("Failed to clear answer cache: %s", exc)
            return {"success": False, "message": str(exc), "scope": "instance_local", "cluster_consistency": "not_coordinated"}, 200

    def build_conversation_cache_debug(
        self,
        runtime: PublicServiceRuntime,
        *,
        user_id: int,
        conversation_id: int | None = None,
    ) -> tuple[dict[str, Any], int]:
        try:
            redis_service = runtime.redis_service
            if redis_service is None:
                return {
                    "success": True,
                    "data": {
                        "redis_available": False,
                        "key_prefix": str(getattr(runtime.settings, "redis_key_prefix", "agentcode") or "agentcode"),
                        "conversation_cache": {
                            "user_id": int(user_id),
                            "list": {"version": "0", "recent_pages_key": "", "recent_pages_ttl_seconds": None, "recent_pages": [], "pages": []},
                            "detail": {},
                        },
                    },
                }, 200

            recent_pages = get_recent_conversation_list_pages(redis_service=redis_service, user_id=user_id)
            pages_to_check: list[tuple[int, int]] = [(1, 20)]
            for item in recent_pages:
                candidate = (int(item.get("page") or 0), int(item.get("page_size") or 0))
                if candidate[0] <= 0 or candidate[1] <= 0 or candidate in pages_to_check:
                    continue
                pages_to_check.append(candidate)

            list_version = get_conversation_list_cache_version(redis_service=redis_service, user_id=user_id)
            list_pages: list[dict[str, Any]] = []
            for page, page_size in pages_to_check:
                key = build_conversation_list_cache_key(redis_service=redis_service, user_id=user_id, page=page, page_size=page_size)
                payload = redis_service.get_json(key, default=None)
                data = payload.get("data") if isinstance(payload, dict) else {}
                conversations = data.get("conversations") if isinstance(data, dict) else []
                preview: list[dict[str, Any]] = []
                if isinstance(conversations, list):
                    for item in conversations[:5]:
                        if not isinstance(item, dict):
                            continue
                        preview.append(
                            {
                                "conversation_id": int(item.get("conversation_id") or 0),
                                "title": str(item.get("title") or ""),
                                "message_count": int(item.get("message_count") or 0),
                            }
                        )
                list_pages.append(
                    {
                        "page": page,
                        "page_size": page_size,
                        "key": key,
                        "present": isinstance(payload, dict) and payload.get("success") is True,
                        "ttl_seconds": self._ttl_or_none(runtime, key),
                        "conversation_count": len(conversations) if isinstance(conversations, list) else 0,
                        "total_count": int((data or {}).get("total_count") or 0) if isinstance(data, dict) else 0,
                        "preview": preview,
                    }
                )

            detail_section: dict[str, Any] = {}
            if conversation_id is not None and int(conversation_id) > 0:
                detail_key = build_conversation_detail_cache_key(
                    redis_service=redis_service,
                    user_id=user_id,
                    conversation_id=int(conversation_id),
                )
                detail_payload = redis_service.get_json(detail_key, default=None)
                detail_data = detail_payload.get("data") if isinstance(detail_payload, dict) else {}
                messages = detail_data.get("messages") if isinstance(detail_data, dict) else []
                uploaded_files = detail_data.get("uploaded_files") if isinstance(detail_data, dict) else []
                last_message = messages[-1] if isinstance(messages, list) and messages else {}
                detail_section = {
                    "conversation_id": int(conversation_id),
                    "version": get_conversation_detail_cache_version(
                        redis_service=redis_service,
                        user_id=user_id,
                        conversation_id=int(conversation_id),
                    ),
                    "key": detail_key,
                    "present": isinstance(detail_payload, dict) and detail_payload.get("success") is True,
                    "ttl_seconds": self._ttl_or_none(runtime, detail_key),
                    "message_count": len(messages) if isinstance(messages, list) else 0,
                    "uploaded_files_count": len(uploaded_files) if isinstance(uploaded_files, list) else 0,
                    "title": str((detail_data or {}).get("title") or "") if isinstance(detail_data, dict) else "",
                    "updated_at": (detail_data or {}).get("updated_at") if isinstance(detail_data, dict) else None,
                    "last_message_preview": {
                        "role": str((last_message or {}).get("role") or "") if isinstance(last_message, dict) else "",
                        "content": str((last_message or {}).get("content") or "")[:120] if isinstance(last_message, dict) else "",
                    },
                }

            recent_pages_key = build_conversation_list_recent_pages_key(redis_service=redis_service, user_id=user_id)
            return {
                "success": True,
                "data": {
                    "redis_available": bool(redis_service.available),
                    "key_prefix": str(getattr(runtime.settings, "redis_key_prefix", "agentcode") or "agentcode"),
                    "conversation_cache": {
                        "user_id": int(user_id),
                        "list": {
                            "version": list_version,
                            "recent_pages_key": recent_pages_key,
                            "recent_pages_ttl_seconds": self._ttl_or_none(runtime, recent_pages_key),
                            "recent_pages": recent_pages,
                            "pages": list_pages,
                        },
                        "detail": detail_section,
                    },
                },
            }, 200
        except Exception as exc:
            logger.warning("Failed to read conversation cache debug: %s", exc)
            return {"success": False, "error": str(exc)}, 500

    @staticmethod
    def _chromadb_count(runtime: PublicServiceRuntime) -> int:
        vector_client = runtime.vector_db_client
        collection = runtime.vector_collection or SystemService._get_semantic_collection(runtime.agent)
        if vector_client is not None and hasattr(vector_client, "count"):
            try:
                result = vector_client.count(collection=collection)
                return int(getattr(result, "count", 0) or 0)
            except Exception as exc:
                logger.warning("Failed to query runtime vector DB client: %s", exc)
        if collection is not None and hasattr(collection, "count"):
            try:
                return int(collection.count() or 0)
            except Exception as exc:
                logger.warning("Failed to query semantic collection: %s", exc)
        return 0

    @staticmethod
    def _get_semantic_collection(agent: Any) -> Any | None:
        semantic_expert = getattr(agent, "semantic_expert", None)
        if semantic_expert is None:
            return None
        return getattr(semantic_expert, "collection", None)

    @staticmethod
    def _graph(runtime: PublicServiceRuntime) -> Any | None:
        agent_graph = getattr(getattr(runtime, "agent", None), "graph", None)
        if agent_graph is not None:
            return agent_graph
        neo4j_client = getattr(runtime, "neo4j_client", None)
        if neo4j_client is not None and bool(getattr(neo4j_client, "available", False)):
            return getattr(neo4j_client, "graph", None)
        return None


system_service = SystemService()
