"""Pre-Stage1 lightweight intent classification (fixed-tag chat prompt).

与 fastQA `generation_pipeline/intent_detect.py` 对齐。默认模型 ID **`qwen3-8b`**；
优先使用统一 `INTENT_MODEL` 配置，旧 `PATENT_INTENT_DETECT_MODEL` /
`QA_INTENT_DETECT_MODEL` 仍兼容。

实现方式：`build_intent_detect_system_prompt()` 作为 system，`user` 仅为当前用户原始问题全文。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from types import SimpleNamespace
from typing import Any

import httpx

from server.patent.thinking import (
    auth_headers,
)
from server.patent.upstream_transport import is_patent_pool_timeout

_LOGGER = logging.getLogger(__name__)

# 百炼/兼容网关登记的轻量意图分类模型 ID（可用 env 覆盖）。
DEFAULT_INTENT_DETECT_MODEL = "qwen3-8b"


def _truthy_env(raw: str | None, *, default: bool = False) -> bool:
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


_INTENT_TAG_DESCRIPTIONS: dict[str, str] = {
    "mechanism_analysis": "问题主轴为反应机理、反应路径、动力学、中间相、价态/化学步骤等",
    "comparative_tradeoff": "问题主轴为对比多种路线/材料/方案：差异、优劣、适用场景、选型",
    "synthesis_preparation": "问题主轴为合成与制备工艺、条件、原料与烧结路线（机理不是唯一主轴时也可选）",
    "electrochemical_performance": "问题主轴为电化学性能：容量、倍率、循环、阻抗等",
    "characterization": "问题主轴为表征/结构：物相、形貌、谱学、晶体结构等",
    "recycling_sustainability": "问题主轴为回收、再生、废弃物、可持续等",
    "generic": "以上类别都不突出，或泛化的材料/电池/专利文献问答",
}


def intent_detect_enabled() -> bool:
    return (
        _truthy_env(os.getenv("INTENT_MODEL_ENABLED"))
        or _truthy_env(os.getenv("PATENT_INTENT_DETECT_ENABLED"))
        or _truthy_env(
        os.getenv("QA_INTENT_DETECT_ENABLED"),
    )
    )


def intent_detect_model() -> str:
    raw = (
        _env_first(
            "INTENT_MODEL",
            "PATENT_INTENT_DETECT_MODEL",
            "QA_INTENT_DETECT_MODEL",
            default=DEFAULT_INTENT_DETECT_MODEL,
        )
        or DEFAULT_INTENT_DETECT_MODEL
    )
    return raw or DEFAULT_INTENT_DETECT_MODEL


def _intent_model_api_key() -> str:
    return _env_first("INTENT_MODEL_API_KEY")


def _intent_model_base_url() -> str:
    return _env_first(
        "INTENT_MODEL_BASE_URL",
        "LLM_BASE_URL",
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )


def _chat_completions_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    if not value.endswith("/v1"):
        value = value.rstrip("/") + "/v1"
    return value.rstrip("/") + "/chat/completions"


def _intent_model_timeout_seconds() -> float:
    raw = _env_first("INTENT_MODEL_TIMEOUT_SECONDS", "LLM_READ_TIMEOUT_SECONDS", default="30")
    try:
        return max(float(raw), 1.0)
    except Exception:
        return 30.0


def _message_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(item.get("content") or "")) for item in messages if isinstance(item, dict))


def _intent_max_tokens(*, include_anchors: bool) -> int:
    if not include_anchors:
        return 64
    try:
        return max(64, min(int(str(os.getenv("PATENT_INTENT_ANCHOR_MAX_TOKENS", "256")).strip()), 512))
    except Exception:
        return 256


def _create_dedicated_intent_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    include_anchors: bool = False,
) -> Any:
    endpoint_url = _chat_completions_url(_intent_model_base_url())
    timeout_seconds = _intent_model_timeout_seconds()
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": _intent_max_tokens(include_anchors=include_anchors),
        "stream": False,
        "enable_thinking": False,
    }
    auth_mode = str(os.getenv("INTENT_MODEL_AUTH_MODE") or os.getenv("LLM_AUTH_MODE") or "bearer").strip() or "bearer"
    started_at = time.perf_counter()
    _LOGGER.info(
        "model_call start service=patent component=llm_intent model=%s endpoint=%s auth_mode=%s "
        "stream=false message_count=%s message_chars=%s timeout_seconds=%s key_present=%s",
        model,
        endpoint_url,
        auth_mode,
        len(messages),
        _message_chars(messages),
        timeout_seconds,
        bool(_intent_model_api_key()),
    )
    response = None
    try:
        response = httpx.post(
            endpoint_url,
            headers=auth_headers(_intent_model_api_key(), auth_mode_env="INTENT_MODEL_AUTH_MODE"),
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except Exception as exc:
        _LOGGER.warning(
            "model_call failed service=patent component=llm_intent model=%s endpoint=%s auth_mode=%s "
            "status_code=%s stream=false elapsed_ms=%.2f error_type=%s",
            model,
            endpoint_url,
            auth_mode,
            getattr(response, "status_code", None),
            (time.perf_counter() - started_at) * 1000.0,
            type(exc).__name__,
        )
        raise
    _LOGGER.info(
        "model_call success service=patent component=llm_intent model=%s endpoint=%s auth_mode=%s "
        "status_code=%s stream=false answer_chars=%s elapsed_ms=%.2f",
        model,
        endpoint_url,
        auth_mode,
        getattr(response, "status_code", None),
        len(str(content or "")),
        (time.perf_counter() - started_at) * 1000.0,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=str(content or "")))])


def _create_intent_completion(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    include_anchors: bool = False,
) -> Any:
    if _intent_model_api_key():
        return _create_dedicated_intent_completion(model=model, messages=messages, include_anchors=include_anchors)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": _intent_max_tokens(include_anchors=include_anchors),
        "stream": False,
        "extra_body": {"enable_thinking": False},
    }
    return client.chat.completions.create(**kwargs)


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


def patent_intent_detect_cache_signature() -> dict[str, Any]:
    """Fields merged into Stage1 Redis cache fingerprint when intent routing is configured."""
    if not intent_detect_enabled():
        return {"patent_intent_detect": False}
    from server.patent.question_anchors import intent_anchor_extract_enabled

    return {
        "patent_intent_detect": True,
        "patent_intent_detect_model": intent_detect_model(),
        "patent_intent_anchor_extract": intent_anchor_extract_enabled(),
    }


def _normalize_intent_tag(raw: str) -> str:
    text = _strip_model_noise(raw).lower().replace(" ", "_")
    if text in _INTENT_TAG_DESCRIPTIONS:
        return text
    for key in _INTENT_TAG_DESCRIPTIONS:
        if key in text:
            return key
    return "generic"


def build_intent_detect_system_prompt(*, include_anchors: bool = False) -> str:
    """System message for tag classification（user role = raw question）。与 fastQA 文案保持一致。"""
    intent_dict = {k: v for k, v in _INTENT_TAG_DESCRIPTIONS.items()}
    intent_string = json.dumps(intent_dict, ensure_ascii=False)
    keys_line = ", ".join(sorted(k for k in _INTENT_TAG_DESCRIPTIONS))
    if include_anchors:
        return (
            "You classify a user's question for materials/battery/electrochemistry patent QA and extract retrieval anchor terms.\n"
            f"Valid intent_tag values (copy ONE verbatim): {keys_line}\n\n"
            f"Tag meanings:\n{intent_string}\n\n"
            "Reply with ONLY one JSON object, no markdown fences or extra text:\n"
            '{"intent_tag":"<one valid tag>","anchor_terms":["term1","term2"]}\n\n'
            "Rules for anchor_terms:\n"
            "- Extract 3-10 terms that MUST appear in patent retrieval queries.\n"
            "- Prefer the user's exact wording for materials, methods, metrics, ratios, and patent IDs.\n"
            "- Include explicit chemical/material names (e.g. 铁红, 葡萄糖, PEG) and numeric thresholds if present.\n"
            "- Do NOT invent terms absent from the user question.\n"
            "- Do NOT include generic stopwords like 如何, 什么, 专利, 制备.\n"
        )
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


def _unwrap_outer_json_fence(text: str) -> str | None:
    match = re.match(r"^\s*```(?:json)?\s*(.*)\s*```\s*$", str(text or ""), flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    candidate = str(match.group(1) or "").strip()
    return candidate or None


def _parse_intent_payload(raw: str) -> dict[str, Any] | None:
    for candidate in (
        str(raw or "").strip(),
        _unwrap_outer_json_fence(raw),
    ):
        normalized = str(candidate or "").strip()
        if not normalized or not normalized.startswith("{"):
            continue
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def run_intent_detect_quick_tag(
    *,
    client: Any,
    user_question: str,
    logger: Any,
) -> dict[str, Any]:
    """Call classifier; returns tag + anchor_terms + timing. Failures degrade to generic (non-pool-timeout)."""
    from server.patent.question_anchors import (
        extract_rule_based_anchor_terms,
        intent_anchor_extract_enabled,
        normalize_anchor_term_list,
        resolve_question_anchor_terms,
    )

    model = intent_detect_model()
    started = time.perf_counter()
    include_anchors = intent_anchor_extract_enabled()
    system_prompt = build_intent_detect_system_prompt(include_anchors=include_anchors)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": str(user_question or "").strip()},
    ]
    try:
        response = _create_intent_completion(
            client=client,
            model=model,
            messages=messages,
            include_anchors=include_anchors,
        )
        raw = str(response.choices[0].message.content or "").strip()
    except Exception as exc:
        if is_patent_pool_timeout(exc):
            raise
        if logger is not None:
            try:
                logger.warning("patent intent-detect failed, using generic: %s", exc)
            except Exception:
                pass
        fallback_anchors = extract_rule_based_anchor_terms(user_question)
        return {
            "intent_tag": "generic",
            "anchor_terms": fallback_anchors,
            "raw_response": "",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "model": model,
            "ok": False,
            "error": str(exc),
        }

    tag = "generic"
    llm_anchor_terms: list[str] = []
    if include_anchors:
        payload = _parse_intent_payload(raw)
        if payload is not None:
            tag = _normalize_intent_tag(str(payload.get("intent_tag") or raw))
            llm_anchor_terms = normalize_anchor_term_list(payload.get("anchor_terms"))
        else:
            tag = _normalize_intent_tag(raw)
    else:
        tag = _normalize_intent_tag(raw)
    partial_result = {
        "intent_tag": tag,
        "anchor_terms": llm_anchor_terms,
        "raw_response": raw,
        "ok": True,
        "error": "",
    }
    anchor_terms = resolve_question_anchor_terms(user_question=user_question, intent_result=partial_result)
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    if logger is not None:
        try:
            logger.info(
                "patent intent-detect ok model=%s intent_tag=%s anchor_terms=%s raw=%r elapsed_ms=%s",
                model,
                tag,
                anchor_terms,
                raw[:200],
                elapsed,
            )
        except Exception:
            pass
    return {
        "intent_tag": tag,
        "anchor_terms": anchor_terms,
        "raw_response": raw,
        "elapsed_ms": elapsed,
        "model": model,
        "ok": True,
        "error": "",
    }


def format_intent_hint_for_stage1_user_block(*, intent_result: dict[str, Any]) -> str:
    """Short prefix injected ahead of Stage1 user content (conversation + question)."""
    if not intent_result.get("ok"):
        return ""
    tag = str(intent_result.get("intent_tag") or "generic").strip()
    label = _INTENT_TAG_DESCRIPTIONS.get(tag, tag)
    anchor_terms = [
        str(item).strip()
        for item in list(intent_result.get("anchor_terms") or [])
        if str(item).strip()
    ]
    lines = [
        "【快速意图识别（供深度预回答与 retrieval_claims 锚定参考）】",
        f"- 主轴类型建议对齐：`{tag}`（含义：{label}）",
    ]
    if anchor_terms:
        lines.append(f"- 检索锚词（每条 retrieval_claims.keywords 必须包含）：{', '.join(anchor_terms)}")
    lines.append("- 每条检索主张与用户显性关键词必须与该主轴和用户原句一致；冲突时以用户原句为准。")
    return "\n".join(lines) + "\n"


__all__ = [
    "DEFAULT_INTENT_DETECT_MODEL",
    "build_intent_detect_system_prompt",
    "_INTENT_TAG_DESCRIPTIONS",
    "format_intent_hint_for_stage1_user_block",
    "intent_detect_enabled",
    "intent_detect_model",
    "patent_intent_detect_cache_signature",
    "run_intent_detect_quick_tag",
]
