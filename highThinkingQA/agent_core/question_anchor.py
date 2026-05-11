"""Prepend a visible user-question anchor to LLM prompts to reduce topic drift."""

from __future__ import annotations

# Bumped when anchor wording changes; mixed into stage_cache prompt hashes.
ANCHOR_PROMPT_SALT = "|ht_question_anchor=v1|"


def prepend_question_anchor(body: str, question: str) -> str:
    """
    Prefix *body* with the user's question so every generation step re-grounds on intent.

    When *question* is empty, *body* is returned unchanged.
    """
    q = str(question or "").strip()
    text = str(body or "")
    if not q:
        return text
    prefix = (
        "=== USER QUESTION ANCHOR (read first; stay on-topic; answer THIS question only; "
        "do not substitute a different or broader question; tie all substantive content to this intent; "
        "mark uncertainty instead of inventing specific numbers or experimental details) ===\n"
        f"{q}\n"
        "=== END ANCHOR ===\n\n"
    )
    return prefix + text


__all__ = ["ANCHOR_PROMPT_SALT", "prepend_question_anchor"]
