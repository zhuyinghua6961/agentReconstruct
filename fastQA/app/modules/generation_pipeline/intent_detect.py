"""Pre-Stage1 intent classification via a lightweight chat model (fixed tag list).

默认模型 ID **`qwen3-8b`**（可用统一的 ``INTENT_MODEL`` 覆盖；旧
``QA_INTENT_DETECT_MODEL`` 仍兼容）。

使用简短 **system prompt + 纯用户原文**：模型从固定 snake_case **键名** 中选恰好一个输出；
也可用环境变量换用 `tongyi-intent-detect-v3` 等任意 `chat.completions` 可调模型。
"""

from __future__ import annotations

import json
import os
import re
import time
from types import SimpleNamespace
from typing import Any

import httpx

from app.integrations.llm import raise_if_upstream_pool_timeout
from app.integrations.llm.thinking import auth_headers

# DashScope/OpenAI-compat 登记的轻量意图分类模型 ID（可自行 env 覆盖）。
DEFAULT_INTENT_DETECT_MODEL = "qwen3-8b"

# Tag keys align with Stage1 `question_focus.focus_type` where possible.
_INTENT_TAG_DESCRIPTIONS: dict[str, str] = {
    "mechanism_analysis": "问题主轴为反应机理、反应路径、动力学、中间相、价态/化学步骤等",
    "comparative_tradeoff": "问题主轴为对比多种路线/材料/方案：差异、优劣、适用场景、选型",
    "synthesis_preparation": "问题主轴为合成与制备工艺、条件、原料与烧结路线（机理不是唯一主轴时也可选）",
    "electrochemical_performance": "问题主轴为电化学性能：容量、倍率、循环、阻抗等",
    "characterization": "问题主轴为表征/结构：物相、形貌、谱学、晶体结构等",
    "recycling_sustainability": "问题主轴为回收、再生、废弃物、可持续等",
    "generic": "以上类别都不突出，或泛化的材料/电池文献问答",
}


def _env_bool(raw: str | None, *, default: bool = False) -> bool:
    value = str(raw or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        raw = str(os.getenv(name) or "").strip()
        if raw:
            return raw
    return default


def intent_detect_enabled() -> bool:
    return _env_bool(os.getenv("INTENT_MODEL_ENABLED")) or _env_bool(os.getenv("QA_INTENT_DETECT_ENABLED"))


def intent_override_focus_enabled() -> bool:
    return _env_bool(os.getenv("QA_INTENT_DETECT_OVERRIDE_FOCUS"))


def intent_detect_model() -> str:
    return (
        _env_first("INTENT_MODEL", "QA_INTENT_DETECT_MODEL", default=DEFAULT_INTENT_DETECT_MODEL)
        or DEFAULT_INTENT_DETECT_MODEL
    )


def _intent_model_api_key() -> str:
    return _env_first("INTENT_MODEL_API_KEY")


def _intent_model_base_url() -> str:
    return _env_first(
        "INTENT_MODEL_BASE_URL",
        "LLM_BASE_URL",
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )


def _intent_model_timeout_seconds() -> float:
    raw = _env_first("INTENT_MODEL_TIMEOUT_SECONDS", "LLM_READ_TIMEOUT_SECONDS", default="30")
    try:
        return max(float(raw), 1.0)
    except Exception:
        return 30.0


def _create_dedicated_intent_completion(*, model: str, messages: list[dict[str, Any]]) -> Any:
    base_url = _intent_model_base_url().rstrip("/")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 64,
        "stream": False,
        "enable_thinking": False,
    }
    response = httpx.post(
        f"{base_url}/chat/completions",
        headers=auth_headers(_intent_model_api_key()),
        json=payload,
        timeout=_intent_model_timeout_seconds(),
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=str(content or "")))])


def _create_intent_completion(*, client: Any, model: str, messages: list[dict[str, Any]]) -> Any:
    if _intent_model_api_key():
        return _create_dedicated_intent_completion(model=model, messages=messages)
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.0,
        max_tokens=64,
        extra_body={"enable_thinking": False},
        stream=False,
    )


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
    # Model may echo description snippet; try substring match
    for key in _INTENT_TAG_DESCRIPTIONS:
        if key in text:
            return key
    return "generic"


def build_intent_detect_system_prompt() -> str:
    """System message for lightweight tag classification (user message = raw question only)."""
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
    """Calls intent classifier; returns intent_tag, timing, raw text. Never raises—failures return generic."""
    model = intent_detect_model()
    started = time.perf_counter()
    system_prompt = build_intent_detect_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": str(user_question or "").strip()},
    ]
    try:
        response = _create_intent_completion(client=client, model=model, messages=messages)
        raw = str(response.choices[0].message.content or "").strip()
    except Exception as exc:
        raise_if_upstream_pool_timeout(exc)
        if logger is not None:
            try:
                logger.warning("intent-detect failed, using generic: %s", exc)
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
                "intent-detect ok model=%s intent_tag=%s raw=%r elapsed_ms=%s",
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


def format_intent_hint_for_stage1_user_block(*, intent_result: dict[str, Any]) -> str:
    """Short block injected before the user question for Stage1."""
    if not intent_result.get("ok"):
        return ""
    tag = str(intent_result.get("intent_tag") or "generic").strip()
    label = _INTENT_TAG_DESCRIPTIONS.get(tag, tag)
    return (
        f"【快速意图识别（供 question_focus 与检索主张参考）】\n"
        f"- 建议 focus_type 优先对齐：`{tag}`（含义：{label}）\n"
        f"- 若与用户原句显性意图冲突，以用户原句为准。\n"
    )


def apply_intent_tag_to_question_focus(
    *,
    intent_tag: str,
    question_focus: dict[str, Any],
) -> dict[str, Any]:
    """Optional post-normalize override when intent says mechanism but Stage1 chose synthesis/generic."""
    if not intent_override_focus_enabled():
        return question_focus
    tag = str(intent_tag or "").strip()
    if tag != "mechanism_analysis":
        return question_focus
    ft = str(question_focus.get("focus_type") or "").strip()
    if ft == "mechanism_analysis":
        return question_focus
    if ft not in {"synthesis_preparation", "generic", ""}:
        return question_focus

    out = dict(question_focus)
    out["focus_type"] = "mechanism_analysis"
    summary = str(out.get("focus_summary") or "").strip()
    hint = "【意图快筛】问句侧重反应机理；evidence_axes 宜包含反应路径/中间相/动力学等相关轴。"
    out["focus_summary"] = f"{summary}\n{hint}".strip() if summary else hint
    return out


__all__ = [
    "DEFAULT_INTENT_DETECT_MODEL",
    "build_intent_detect_system_prompt",
    "apply_intent_tag_to_question_focus",
    "format_intent_hint_for_stage1_user_block",
    "intent_detect_enabled",
    "intent_override_focus_enabled",
    "intent_detect_model",
    "run_intent_detect_quick_tag",
    "_INTENT_TAG_DESCRIPTIONS",
]
