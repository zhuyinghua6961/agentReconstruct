"""Gateway route decision service."""

from __future__ import annotations

from app.models.routing import FileContextDecision, RouteDecision


class RouteDecisionService:
    def decide(self, *, requested_mode: str, file_context: FileContextDecision) -> RouteDecision:
        actual_mode = requested_mode
        if file_context.turn_mode in {"file_only", "mixed"}:
            actual_mode = "fast"

        return RouteDecision(
            requested_mode=requested_mode,
            actual_mode=actual_mode,
            route=file_context.route,
            turn_mode=file_context.turn_mode,
            allow_kb_verification=file_context.allow_kb_verification,
            needs_clarification=file_context.needs_clarification,
            clarification_message=file_context.clarification_message,
        )
