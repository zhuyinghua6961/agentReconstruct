"""Pre–Step1 lightweight intent classification (fixed-tag chat prompt).

与 fastQA / patent 对齐：默认模型 ID **`qwen3-8b`**（可用 ``QA_INTENT_DETECT_MODEL``
或 ``HT_QA_INTENT_DETECT_MODEL`` 覆盖）。开关：``QA_INTENT_DETECT_ENABLED`` 或
``HT_QA_INTENT_DETECT_ENABLED``（与 patent 并行支持共享 env）。

调用方式：`build_intent_detect_system_prompt()` 为 system，user 仅为当前问题的有效正文
（通常为 effective_question）。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


DEFAULT_INTENT_DETECT_MODEL = "qwen3-8b"

_INTENT_TAG_DESCRIPTIONS: dict[str, str] = {
    "mechanism_analysis": "问题主轴为反应机理、反应路径、动力学、中间相、价态/化学步骤等",
    "comparative_tradeoff": "问题主轴为对比多种路线/材料/方案：差异、优劣、适用场景、选型",
    "synthesis_preparation": "问题主轴为合成与制备工艺、条件、原料与烧结路线（机理不是唯一主轴时也可选）",
    "electrochemical_performance": "问题主轴为电化学性能：容量、倍率、循环、阻抗等",
    "characterization": "问题主轴为表征/结构：物相、形貌、谱学、晶体结构等",
    "recycling_sustainability": "问题主轴为回收、再生、废弃物、可持续等",
    "generic": "以上类别都不突出，或泛化的材料/电池文献问答",
}


def _truthy_env(raw: str | None, *, default: bool = False) -> bool:
    value = str(raw or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def intent_detect_enabled() -> bool:
    return _truthy_env(os.getenv("HT_QA_INTENT_DETECT_ENABLED")) or _truthy_env(
        os.getenv("QA_INTENT_DETECT_ENABLED"),
    )


def intent_detect_model() -> str:
    raw = (
        str(os.getenv("HT_QA_INTENT_DETECT_MODEL") or "").strip()
        or str(os.getenv("QA_INTENT_DETECT_MODEL") or "").strip()
        or DEFAULT_INTENT_DETECT_MODEL
    )
    return raw or DEFAULT_INTENT_DETECT_MODEL


def is_upstream_pool_timeout(exc: Exception) -> bool:
    pool_timeout_cls = getattr(httpx, "PoolTimeout", None) if httpx else None
    if pool_timeout_cls is not None and isinstance(exc, pool_timeout_cls):
        return True
    return exc.__class__.__name__ == "PoolTimeout"


_FENCE_STRIP_RE = re.compile(r"^\s*```(?:[a-zA-Z0-9_-]+)?\s*", re.MULTILINE)


def _strip_model_noise(raw: str) -> str:
    text = str(raw or "").strip()
    text = _FENCE_STRIP_RE.sub("", text, count=1)
    text = text.removesuffix("```").strip()
    line = text.splitlines()[0] if text else ""
    line = line.strip().strip("\"'").strip()
    for sep in ("：", ":"):
        if sep in line:
            tail = line.split(sep, 1)[-1].strip()
            if tail:
                line = tail
                break
    return line.strip()


def _normalize_intent_tag(raw: str) -> str:
    text = _strip_model_noise(raw).lower().replace(" ", "_")
    if text in _INTENT_TAG_DESCRIPTIONS:
        return text
    for key in _INTENT_TAG_DESCRIPTIONS:
        if key in text:
            return key
    return "generic"


def build_intent_detect_system_prompt() -> str:
    """System message for tag classification（user = raw effective question）。与 fastQA 文案一致。"""
    intent_dict = {k: v for k, v in _INTENT_TAG_DESCRIPTIONS.items()}
    intent_string = json.dumps(intent_dict, ensure_ascii=False)
    keys_line = ", ".join(sorted(k for k in _INTENT_TAG_DESCRIPTIONS))
    return (
        "You classify a user's question for materials/battery/electrochemistry QA.\n"
        "Pick exactly ONE tag whose key best matches the user's MAIN information need (what evidence to prioritize).\n"
        f"Valid outputs — copy ONE of these literals verbatim (ASCII lowercase snake_case): {keys_line}\n\n"
        f"Tag meanings — keys below are exactly the strings you may output:\n{intent_string}\n\n"
        "Rules:\n"
        "- Reply with ONLY that key string on a single token/line — no punctuation, quotes, markdown, spaces, reasoning, "
        "or any other characters.\n"
        "- User text may be Chinese or English; your answer must still be exactly one English key from the valid list.\n"
        "- If ambiguous or overlapping, prefer the retrieval-shaping lens; if nothing fits better than generic, output generic.\n"
    )


def run_intent_detect_quick_tag(
    *,
    client: Any,
    user_question: str,
    logger: Any,
) -> dict[str, Any]:
    """Calls classifier; returns tag + timing. Fail-open to generic unless connection pool timed out."""
    model = intent_detect_model()
    started = time.perf_counter()
    system_prompt = build_intent_detect_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": str(user_question or "").strip()},
    ]
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=64,
            stream=False,
        )
        raw = str(response.choices[0].message.content or "").strip()
    except Exception as exc:
        if is_upstream_pool_timeout(exc):
            raise
        if logger is not None:
            try:
                logger.warning("highThinkingQA intent-detect failed, using generic: %s", exc)
            except Exception:
                pass
        return {
            "intent_tag": "generic",
            "raw_response": "",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "model": model,
            "ok": False,
            "error": str(exc),
        }

    tag = _normalize_intent_tag(raw)
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    if logger is not None:
        try:
            logger.info(
                "highThinkingQA intent-detect ok model=%s intent_tag=%s raw=%r elapsed_ms=%s",
                model,
                tag,
                raw[:200],
                elapsed,
            )
        except Exception:
            pass
    return {
        "intent_tag": tag,
        "raw_response": raw,
        "elapsed_ms": elapsed,
        "model": model,
        "ok": True,
        "error": "",
    }


def format_intent_hint_for_thinking_user_block(*, intent_result: dict[str, Any]) -> str:
    """Short prefix prefixed to the effective question text for downstream LLM stages."""
    if not intent_result.get("ok"):
        return ""
    tag = str(intent_result.get("intent_tag") or "generic").strip()
    label = _INTENT_TAG_DESCRIPTIONS.get(tag, tag)
    return (
        "【快速意图识别（供子问题拆解、直接回答与检索主轴对齐参考）】\n"
        f"- 主轴类型建议对齐：`{tag}`（含义：{label}）\n"
        "- 若与用户原句显性意图冲突，以用户原句为准。\n"
    )


__all__ = [
    "DEFAULT_INTENT_DETECT_MODEL",
    "build_intent_detect_system_prompt",
    "_INTENT_TAG_DESCRIPTIONS",
    "format_intent_hint_for_thinking_user_block",
    "intent_detect_enabled",
    "intent_detect_model",
    "is_upstream_pool_timeout",
    "run_intent_detect_quick_tag",
]
