"""Gateway route decision service."""

from __future__ import annotations

from typing import Any

from app.models.routing import FileContextDecision, RouteDecision, RouteName, SourceScope

_TABLE_FILE_TYPES = {"csv", "excel", "xls", "xlsx"}


class RouteDecisionService:
    def decide(self, *, requested_mode: str, file_context: FileContextDecision) -> RouteDecision:
        actual_mode = requested_mode
        if file_context.turn_mode in {"file_only", "mixed"}:
            actual_mode = "fast"

        route = self._normalized_route(file_context)
        source_scope = self._source_scope(route=route, file_context=file_context)
        kb_enabled = bool(source_scope and "kb" in source_scope)
        selected_file_ids = list(file_context.selected_file_ids)
        primary_file_id = selected_file_ids[0] if len(selected_file_ids) == 1 else None

        return RouteDecision(
            requested_mode=requested_mode,
            actual_mode=actual_mode,
            route=route,
            turn_mode=file_context.turn_mode,
            allow_kb_verification=file_context.allow_kb_verification,
            needs_clarification=file_context.needs_clarification,
            clarification_message=file_context.clarification_message,
            source_scope=source_scope,
            kb_enabled=kb_enabled,
            selected_file_ids=selected_file_ids,
            primary_file_id=primary_file_id,
            file_selection=self._file_selection(
                file_context=file_context,
                source_scope=source_scope,
                kb_enabled=kb_enabled,
                selected_file_ids=selected_file_ids,
            ),
        )

    def _normalized_route(self, file_context: FileContextDecision) -> RouteName:
        if file_context.turn_mode == "mixed" and file_context.route in {"pdf_qa", "tabular_qa", "hybrid_qa"}:
            return "hybrid_qa"
        return file_context.route

    def _source_scope(self, *, route: RouteName, file_context: FileContextDecision) -> SourceScope | None:
        if route == "pdf_qa":
            return "pdf"
        if route == "tabular_qa":
            return "table"
        if route != "hybrid_qa":
            return None

        selected_families = self._selected_families(file_context)
        if file_context.turn_mode == "mixed":
            if file_context.route == "pdf_qa" or selected_families == {"pdf"}:
                return "pdf+kb"
            if file_context.route == "tabular_qa" or selected_families == {"table"}:
                return "table+kb"
            if selected_families == {"pdf", "table"}:
                return "pdf+table+kb"
            return None

        if file_context.turn_mode == "file_only" and selected_families == {"pdf", "table"}:
            return "pdf+table"
        return None

    def _selected_families(self, file_context: FileContextDecision) -> set[str]:
        families: set[str] = set()
        for payload in file_context.execution_files or file_context.used_files:
            family = self._file_family(payload)
            if family:
                families.add(family)
        return families

    def _file_family(self, payload: dict[str, Any]) -> str | None:
        file_type = str((payload or {}).get("file_type") or "").strip().lower()
        if file_type == "pdf":
            return "pdf"
        if file_type in _TABLE_FILE_TYPES:
            return "table"
        return None

    def _file_selection(
        self,
        *,
        file_context: FileContextDecision,
        source_scope: SourceScope | None,
        kb_enabled: bool,
        selected_file_ids: list[int],
    ) -> dict[str, Any]:
        if not selected_file_ids and file_context.strategy == "none":
            return {}

        payload: dict[str, Any] = {
            "strategy": file_context.strategy,
            "selected_file_ids": selected_file_ids,
            "turn_mode": file_context.turn_mode,
            "kb_enabled": kb_enabled,
        }
        if source_scope is not None:
            payload["source_scope"] = source_scope
        return payload
