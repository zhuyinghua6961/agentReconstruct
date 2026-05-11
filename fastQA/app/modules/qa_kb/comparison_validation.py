from __future__ import annotations

from typing import Any


def _comparison_groups(retrieval_results: dict[str, Any] | None) -> list[dict[str, Any]]:
    groups = (retrieval_results or {}).get("comparison_groups") if isinstance(retrieval_results, dict) else None
    if not isinstance(groups, list):
        return []
    return [dict(group) for group in groups if isinstance(group, dict) and str(group.get("label") or "").strip()]


def validate_comparison_answer(answer: str, *, retrieval_results: dict[str, Any] | None) -> dict[str, Any]:
    groups = _comparison_groups(retrieval_results)
    if not groups:
        return {"answer": str(answer or ""), "changed": False, "missing_objects": [], "insufficient_objects": []}

    final_answer = str(answer or "").strip()
    missing_objects: list[str] = []
    insufficient_objects: list[dict[str, str]] = []
    for group in groups:
        label = str(group.get("label") or "").strip()
        if not label:
            continue
        if label not in final_answer:
            missing_objects.append(label)
        if str(group.get("evidence_status") or "") != "sufficient":
            insufficient_objects.append(
                {
                    "label": label,
                    "reason": str(group.get("missing_evidence_reason") or "evidence_below_threshold"),
                }
            )

    notes: list[str] = []
    if missing_objects:
        notes.append("以下对比对象未在正文中充分展开：" + "、".join(missing_objects) + "。")
    if insufficient_objects:
        details = "；".join(f"{item['label']}：证据不足（{item['reason']}）" for item in insufficient_objects)
        notes.append("证据覆盖提示：" + details + "。")

    if notes:
        final_answer = f"{final_answer}\n\n### 证据覆盖提示\n" + "\n".join(f"- {note}" for note in notes)

    return {
        "answer": final_answer,
        "changed": bool(notes),
        "missing_objects": missing_objects,
        "insufficient_objects": [item["label"] for item in insufficient_objects],
    }


__all__ = ["validate_comparison_answer"]
