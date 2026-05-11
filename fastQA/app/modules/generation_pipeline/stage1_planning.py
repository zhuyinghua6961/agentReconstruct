from __future__ import annotations

import json
import re
import time
from typing import Any, Dict

from app.integrations.llm import raise_if_upstream_pool_timeout


def _is_response_format_capability_error(exc: Exception) -> bool:
    message = " ".join(str(exc or "").split()).lower()
    if not message:
        return False
    if "response_format" not in message and "json_object" not in message:
        return False
    capability_hints = ("not supported", "unsupported", "unknown parameter", "invalid parameter", "not implemented")
    return any(hint in message for hint in capability_hints)


def _create_stage1_completion(*, client: Any, model: str, messages: list[dict[str, Any]], logger: Any) -> Any:
    try:
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.5,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        if not _is_response_format_capability_error(exc):
            raise
        logger.warning("阶段一 response_format 不可用，回退到普通 completion: %s", exc)
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.5,
            max_tokens=3000,
        )


_OUTER_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*)\s*```\s*$", re.IGNORECASE | re.DOTALL)


def _unwrap_outer_json_fence(text: str) -> str | None:
    match = _OUTER_JSON_FENCE_RE.match(str(text or ""))
    if not match:
        return None
    candidate = str(match.group(1) or "").strip()
    return candidate or None


def _extract_balanced_json_object(text: str) -> str | None:
    source = str(text or "")
    start = source.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(source)):
        ch = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                candidate = source[start : index + 1].strip()
                return candidate or None
    return None


def _candidate_json_payloads(result_text: str) -> list[str]:
    candidates: list[str] = []
    for candidate in (
        str(result_text or "").strip(),
        _unwrap_outer_json_fence(result_text),
        _extract_balanced_json_object(result_text),
        _extract_balanced_json_object(_unwrap_outer_json_fence(result_text) or ""),
    ):
        normalized = str(candidate or "").strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _parse_stage1_json_payload(result_text: str) -> tuple[dict[str, Any] | None, str | None]:
    for candidate in _candidate_json_payloads(result_text):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload, candidate
    return None, None


def _format_conversation_context(conversation_context: dict[str, Any] | None) -> str:
    if not isinstance(conversation_context, dict):
        return ""

    parts: list[str] = []

    summary = conversation_context.get("summary_for_llm")
    if isinstance(summary, dict):
        short_summary = " ".join(str(summary.get("short_summary") or "").split()).strip()
        if short_summary:
            parts.append(f"会话摘要：{short_summary}")
        open_threads = [str(item).strip() for item in list(summary.get("open_threads") or []) if str(item).strip()]
        if open_threads:
            parts.append(f"待继续话题：{'；'.join(open_threads)}")
        memory_facts = [str(item).strip() for item in list(summary.get("memory_facts") or []) if str(item).strip()]
        if memory_facts:
            parts.append(f"已知事实：{'；'.join(memory_facts)}")

    turns = conversation_context.get("recent_turns_for_llm")
    if isinstance(turns, list):
        rendered_turns: list[str] = []
        for item in turns:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = " ".join(str(item.get("content") or "").split()).strip()
            if not content:
                continue
            role_label = "用户" if role == "user" else "助手"
            rendered_turns.append(f"{role_label}: {content}")
        if rendered_turns:
            parts.append("最近对话：\n" + "\n".join(rendered_turns))

    return "\n\n".join(parts).strip()


def run_stage1_pre_answer_and_planning(
    *,
    user_question: str,
    stage1_prompt: str,
    vector_db_context: str,
    client: Any,
    model: str,
    logger: Any,
    conversation_context: dict[str, Any] | None = None,
    graph_context: str | None = None,
    should_cancel: Any | None = None,
) -> Dict[str, Any]:
    logger.info("阶段一：LLM预回答与检索规划")
    logger.info("用户问题: %s", user_question)
    stage_started = time.perf_counter()

    try:
        if callable(should_cancel) and should_cancel():
            return {
                "success": False,
                "deep_answer": "",
                "retrieval_claims": [],
                "error": "cancelled",
                "metadata": {"cancelled": True},
            }
        full_system_prompt = stage1_prompt + (("\n\n" + vector_db_context) if vector_db_context else "")
        context_block = _format_conversation_context(conversation_context)
        user_content = f"{context_block}\n\n用户问题：{user_question}" if context_block else f"用户问题：{user_question}"
        if graph_context:
            user_content = f"图谱结构化线索：\n{graph_context}\n\n{user_content}"
        logger.info(
            "阶段一提示词拼装完成: prompt_chars=%s user_content_chars=%s context_chars=%s elapsed_ms=%.3f",
            len(
                full_system_prompt
                + "\n\n你必须严格按照 JSON 模板输出，返回值只能是一个 JSON 对象，不能包含任何解释性文字。"
            ),
            len(user_content),
            len(context_block),
            (time.perf_counter() - stage_started) * 1000,
        )
        llm_started = time.perf_counter()
        logger.info("阶段一 LLM 请求发起: model=%s", model)
        response = _create_stage1_completion(
            client=client,
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": full_system_prompt
                    + "\n\n你必须严格按照 JSON 模板输出，返回值只能是一个 JSON 对象，不能包含任何解释性文字。",
                },
                {"role": "user", "content": user_content},
            ],
            logger=logger,
        )
        if callable(should_cancel) and should_cancel():
            return {
                "success": False,
                "deep_answer": "",
                "retrieval_claims": [],
                "error": "cancelled",
                "metadata": {"cancelled": True},
            }

        result_text = str(response.choices[0].message.content or "").strip()
        logger.info(
            "阶段一 LLM 响应已接收: model=%s response_chars=%s elapsed_ms=%.3f",
            model,
            len(result_text),
            (time.perf_counter() - llm_started) * 1000,
        )
        stage1_result, cleaned_text = _parse_stage1_json_payload(result_text)
        if stage1_result is None or cleaned_text is None:
            preview = result_text[:500].replace("\n", "\\n")
            logger.error("阶段一 JSON 解析失败，降级为仅预回答")
            logger.error("阶段一原始响应前500字符: %s", preview)
            return {
                "success": True,
                "deep_answer": result_text,
                "retrieval_claims": [],
                "raw_response": result_text,
                "fallback": "json_parse_failed",
            }

        deep_answer = str(stage1_result.get("deep_answer") or "").strip()
        answer_plan = stage1_result.get("answer_plan")
        if not isinstance(answer_plan, dict):
            answer_plan = {}
        raw_claims = stage1_result.get("retrieval_claims") or []

        retrieval_claims = []
        for item in raw_claims:
            if isinstance(item, dict):
                claim_text = str(item.get("claim") or "").strip()
                retrieval_claims.append(
                    {
                        "claim": claim_text,
                        "keywords": list(item.get("keywords") or []),
                        "preferred_sections": list(item.get("preferred_sections") or item.get("preferred") or []),
                        "filters": item.get("filters") if isinstance(item.get("filters"), dict) else {},
                    }
                )
            else:
                retrieval_claims.append(
                    {
                        "claim": str(item or "").strip(),
                        "keywords": [],
                        "preferred_sections": [],
                        "filters": {},
                    }
                )

        retrieval_claims = [item for item in retrieval_claims if str(item.get("claim") or "").strip()]
        logger.info(
            "阶段一结果归一化完成: deep_answer_chars=%s retrieval_claims=%s",
            len(deep_answer),
            len(retrieval_claims),
        )
        return {
            "success": True,
            "deep_answer": deep_answer,
            "answer_plan": answer_plan,
            "retrieval_claims": retrieval_claims,
            "raw_response": cleaned_text,
        }
    except Exception as exc:
        raise_if_upstream_pool_timeout(exc)
        logger.error("阶段一执行失败: %s", exc)
        return {"success": False, "error": str(exc)}
