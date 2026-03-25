"""System/info APIs used by the frontend."""

# Deprecated: retained only for the retired highThinkingQA system/info HTTP surface.


from __future__ import annotations

from typing import Any

from ingest.vector_store import get_collection_count


class SystemService:
    def build_kb_info(self) -> tuple[dict[str, Any], int]:
        try:
            chromadb_count = int(get_collection_count())
        except Exception:
            chromadb_count = 0

        payload = {
            "success": True,
            "kb_size": 0,
            "chromadb_size": chromadb_count,
            "source_stats": {
                "neo4j": 0,
                "neo4j_connected": False,
                "chromadb": chromadb_count,
            },
        }
        return payload, 200

    def refresh_kb(self) -> tuple[dict[str, Any], int]:
        return {"success": False, "message": "refresh_kb_not_supported"}, 200

    def clear_cache(self) -> tuple[dict[str, Any], int]:
        return {"success": True, "message": "cache_cleared"}, 200


system_service = SystemService()
