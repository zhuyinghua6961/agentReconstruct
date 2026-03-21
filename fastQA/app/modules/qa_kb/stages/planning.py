from __future__ import annotations

from typing import Any

from app.modules.qa_kb.models import GenerationRuntime


class Stage1Planner:
    def run(self, *, runtime: GenerationRuntime, user_question: str) -> dict[str, Any]:
        return runtime.stage1_pre_answer_and_planning(user_question)
