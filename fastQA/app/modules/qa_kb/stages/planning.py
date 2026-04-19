from __future__ import annotations

import inspect
from typing import Any

from app.modules.qa_kb.models import GenerationRuntime


class Stage1Planner:
    def run(
        self,
        *,
        runtime: GenerationRuntime,
        user_question: str,
        conversation_context: dict[str, Any] | None = None,
        graph_evidence=None,
    ) -> dict[str, Any]:
        stage1_fn = runtime.stage1_pre_answer_and_planning
        graph_context = getattr(graph_evidence, "stage1_context_block", "") if graph_evidence is not None else ""
        try:
            signature = inspect.signature(stage1_fn)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            parameters = signature.parameters
            supports_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
            kwargs: dict[str, Any] = {}
            if "conversation_context" in parameters or supports_kwargs:
                kwargs["conversation_context"] = conversation_context
            if "graph_context" in parameters or supports_kwargs:
                kwargs["graph_context"] = graph_context
            if kwargs:
                return stage1_fn(user_question, **kwargs)
        return stage1_fn(user_question)
