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
    ) -> dict[str, Any]:
        stage1_fn = runtime.stage1_pre_answer_and_planning
        try:
            signature = inspect.signature(stage1_fn)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            parameters = signature.parameters
            if "conversation_context" in parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
            ):
                return stage1_fn(user_question, conversation_context=conversation_context)
        return stage1_fn(user_question)
