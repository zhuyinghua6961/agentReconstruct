"""Gateway route decision service."""

from __future__ import annotations

from typing import Any

from app.models.routing import FileContextDecision, RouteDecision, RouteName, SourceScope

_TABLE_FILE_TYPES = {"csv", "excel", "xls", "xlsx"}


class RouteDecisionService:
    def decide(self, *, requested_mode: str, file_context: FileContextDecision) -> RouteDecision:
        route = self._normalized_route(file_context)
        actual_mode = self._actual_mode(requested_mode=requested_mode, turn_mode=file_context.turn_mode)
        source_scope = self._source_scope(route=route, file_context=file_context)
        kb_enabled = bool(source_scope and "kb" in source_scope)
        if route == "kb_qa" and not file_context.needs_clarification:
            selected_file_ids = []
            execution_files: list[dict[str, Any]] = []
            primary_file_id = None
        else:
            selected_file_ids = list(file_context.selected_file_ids)
            execution_files = list(file_context.execution_files)
            primary_file_id = selected_file_ids[0] if len(selected_file_ids) == 1 else None
        strategy = self._canonical_strategy(file_context.strategy)

        return RouteDecision(
            requested_mode=requested_mode,
            actual_mode=actual_mode,
            route=route,
            turn_mode=file_context.turn_mode,
            allow_kb_verification=file_context.allow_kb_verification,
            needs_clarification=file_context.needs_clarification,
            clarification_message=file_context.clarification_message,
            status_code=file_context.status_code,
            status_error=file_context.status_error,
            status_message=file_context.status_message,
            status_retriable=file_context.status_retriable,
            status_detail=dict(file_context.status_detail),
            clarify_candidates=list(file_context.clarify_candidates),
            source_scope=source_scope,
            kb_enabled=kb_enabled,
            selected_file_ids=selected_file_ids,
            execution_files=execution_files,
            strategy=strategy,
            primary_file_id=primary_file_id,
            file_selection=self._file_selection(
                route=route,
                file_context=file_context,
                strategy=strategy,
                source_scope=source_scope,
                kb_enabled=kb_enabled,
                selected_file_ids=selected_file_ids,
            ),
            route_reasons=self._route_reasons(
                file_context=file_context,
                route=route,
                requested_mode=requested_mode,
                actual_mode=actual_mode,
                source_scope=source_scope,
            ),
            route_confidence=self._route_confidence(file_context=file_context),
            classifier_used=bool(file_context.classifier_used),
        )

    def _actual_mode(self, *, requested_mode: str, turn_mode: str) -> str:
        if turn_mode not in {"file_only", "mixed"}:
            return requested_mode
        if requested_mode == "patent":
            return "patent"
        return "fast"

    def _normalized_route(self, file_context: FileContextDecision) -> RouteName:
        families = self._selected_families(file_context)
        if file_context.route == "hybrid_qa" and file_context.turn_mode == "file_only":
            if families == {"pdf"}:
                return "pdf_qa"
            if families == {"table"}:
                return "tabular_qa"
            if families == {"pdf", "table"}:
                return "hybrid_qa"
        if file_context.turn_mode == "mixed" and file_context.route in {"pdf_qa", "tabular_qa", "hybrid_qa"}:
            return "hybrid_qa"
        return file_context.route

    def _source_scope(self, *, route: RouteName, file_context: FileContextDecision) -> SourceScope:
        if route == "kb_qa":
            return "kb"
        if route == "pdf_qa":
            return "pdf"
        if route == "tabular_qa":
            return "table"
        if route != "hybrid_qa":
            return "kb"

        selected_families = self._selected_families(file_context)
        if file_context.turn_mode == "mixed":
            if route == "pdf_qa" or selected_families == {"pdf"}:
                return "pdf+kb"
            if route == "tabular_qa" or selected_families == {"table"}:
                return "table+kb"
            if selected_families == {"pdf", "table"}:
                return "pdf+table+kb"
            return "kb"

        if file_context.turn_mode == "file_only":
            if route == "pdf_qa" or selected_families == {"pdf"}:
                return "pdf"
            if route == "tabular_qa" or selected_families == {"table"}:
                return "table"
            if selected_families == {"pdf", "table"}:
                return "pdf+table"
        return "kb"

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
        route: RouteName,
        file_context: FileContextDecision,
        strategy: str,
        source_scope: SourceScope | None,
        kb_enabled: bool,
        selected_file_ids: list[int],
    ) -> dict[str, Any]:
        if route == "kb_qa" and not file_context.needs_clarification:
            return {}
        if not selected_file_ids and strategy == "none":
            return {}

        payload: dict[str, Any] = {
            "strategy": strategy,
            "selected_file_ids": selected_file_ids,
            "turn_mode": file_context.turn_mode,
            "kb_enabled": kb_enabled,
        }
        if file_context.clarify_candidates:
            payload["clarify_candidates"] = list(file_context.clarify_candidates)
        if source_scope is not None:
            payload["source_scope"] = source_scope
        return payload

    def _route_reasons(
        self,
        *,
        file_context: FileContextDecision,
        route: RouteName,
        requested_mode: str,
        actual_mode: str,
        source_scope: SourceScope,
    ) -> list[str]:
        reasons: list[str] = []
        raw_strategy = str(file_context.strategy or "").strip().lower()
        canonical_strategy = self._canonical_strategy(raw_strategy)
        selected_families = self._selected_families(file_context)
        if file_context.needs_clarification:
            reasons.append("MULTIPLE_FILES_NEED_CLARIFICATION")
        elif file_context.classifier_used and file_context.classifier_reason_codes:
            reasons.extend(list(file_context.classifier_reason_codes))
        elif route == "kb_qa":
            reasons.extend(["NO_FILE_INTENT", "FALLBACK_TO_KB"])
        elif raw_strategy == "metadata_focus_scope":
            if selected_families == {"table"} or route == "tabular_qa":
                reasons.append("EXPLICIT_TABLE_REF")
            elif selected_families == {"pdf"} or route == "pdf_qa":
                reasons.append("EXPLICIT_PDF_REF")
            else:
                reasons.append("EXPLICIT_SELECTED_FILES")
        elif raw_strategy in {"selected_scope", "selected_single"} or canonical_strategy == "explicit_selection":
            reasons.append("EXPLICIT_SELECTED_FILES")
        elif raw_strategy == "explicit_ref" or canonical_strategy == "explicit_ref":
            reasons.append("EXPLICIT_FILE_REF")
        elif raw_strategy == "last_focus" or canonical_strategy == "last_focus":
            reasons.append("LAST_FOCUS_REUSE")
        elif raw_strategy == "latest_new_upload" or canonical_strategy == "latest_upload":
            reasons.append("LATEST_UPLOAD_REUSE")
        elif raw_strategy == "single_candidate" or canonical_strategy == "single_candidate":
            reasons.append("ONLY_ONE_READY_FILE")
        elif raw_strategy in {"ordinal_ref", "deictic_count_scope", "plural_scope"} or canonical_strategy in {"ordinal_ref", "plural_scope"}:
            reasons.append("EXPLICIT_FILE_REF")

        if file_context.allow_kb_verification:
            reasons.append("EXPLICIT_MIXED_INTENT")

        return reasons

    def _route_confidence(self, *, file_context: FileContextDecision) -> float:
        if file_context.needs_clarification:
            return 0.0
        if file_context.classifier_used:
            return float(file_context.classifier_confidence or 0.0)
        return 1.0

    def _canonical_strategy(self, strategy: str) -> str:
        normalized = str(strategy or "").strip().lower()
        mapping = {
            "none": "none",
            "selected_ids_no_file_intent": "none",
            "explicit_selection": "explicit_selection",
            "selected_scope": "explicit_selection",
            "selected_single": "explicit_selection",
            "metadata_focus_scope": "explicit_selection",
            "explicit_ref": "explicit_ref",
            "ordinal_ref": "ordinal_ref",
            "deictic_count_scope": "plural_scope",
            "plural_scope": "plural_scope",
            "latest_upload": "latest_upload",
            "latest_new_upload": "latest_upload",
            "single_candidate": "single_candidate",
            "last_focus": "last_focus",
            "clarify_required": "clarify_required",
            "classifier_resolved": "classifier_resolved",
        }
        return mapping.get(normalized, "none")
