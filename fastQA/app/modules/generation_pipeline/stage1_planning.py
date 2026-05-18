from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List

from app.integrations.llm import raise_if_upstream_pool_timeout
from app.modules.generation_pipeline.text_processing import _clean_retrieval_token


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


def _effective_focus_max_n() -> int:
    """Merged `query_focus_terms` + evidence axes cap for Stage2 / DOI gating."""
    try:
        return max(4, min(int(str(os.getenv("QA_STAGE1_EFFECTIVE_FOCUS_MAX_TERMS", "12")).strip()), 24))
    except ValueError:
        return 12


_ALLOWED_QUESTION_FOCUS_TYPES = frozenset(
    {
        "generic",
        "synthesis_preparation",
        "powder_dense_morphology",
        "electrode_compaction_process",
        "carbon_coating_conductivity",
        "doping_structure",
        "electrochemical_performance",
        "characterization",
        "mechanism_analysis",
        "comparative_tradeoff",
        "density_metric_ambiguity",
        "safety_reliability",
        "cost_scaleup",
        "recycling_sustainability",
        "other",
    }
)


def _normalize_axes_list(raw: Any, *, max_n: int) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        ck = _clean_retrieval_token(str(item or "").strip(), max_len=48)
        if not ck or ck in seen:
            continue
        seen.add(ck)
        out.append(ck)
        if len(out) >= max_n:
            break
    return out


def _normalize_question_focus(raw: Any) -> dict[str, Any]:
    """Stage1 JSON `question_focus`: LLM-derived focus routing for retrieval / ranking."""
    axes_max = 8
    secondary_max = 4
    try:
        axes_max = max(1, min(int(str(os.getenv("QA_STAGE1_EVIDENCE_AXES_MAX_TERMS", "8")).strip()), 16))
    except ValueError:
        axes_max = 8
    try:
        secondary_max = max(0, min(int(str(os.getenv("QA_STAGE1_SECONDARY_AXES_MAX_TERMS", "4")).strip()), 12))
    except ValueError:
        secondary_max = 4

    if not isinstance(raw, dict):
        return {
            "focus_type": "generic",
            "focus_summary": "",
            "evidence_axes": [],
            "secondary_axes": [],
            "confidence": "medium",
        }

    focus_raw = str(raw.get("focus_type") or "").strip().lower().replace(" ", "_")
    focus_type = focus_raw if focus_raw in _ALLOWED_QUESTION_FOCUS_TYPES else "generic"
    summary = str(raw.get("focus_summary") or "").strip()
    if len(summary) > 280:
        summary = summary[:280].rsplit(maxsplit=1)[0].strip()

    confidence_raw = str(raw.get("confidence") or "").strip().lower()
    if confidence_raw not in {"high", "medium", "low"}:
        confidence_raw = "medium"

    evidence_axes = _normalize_axes_list(raw.get("evidence_axes"), max_n=axes_max)
    secondary_axes = _normalize_axes_list(raw.get("secondary_axes"), max_n=secondary_max)

    return {
        "focus_type": focus_type,
        "focus_summary": summary,
        "evidence_axes": evidence_axes,
        "secondary_axes": secondary_axes,
        "confidence": confidence_raw,
    }


def effective_query_focus_terms_for_stage2(stage1_result: dict[str, Any] | None) -> List[str]:
    """Merge narrow `query_focus_terms` with `question_focus` axes for Stage2 reordering / DOI selection."""
    if not isinstance(stage1_result, dict):
        return []

    merged: list[str] = []

    raw_qf = stage1_result.get("query_focus_terms")
    if isinstance(raw_qf, list):
        for item in raw_qf:
            t = _clean_retrieval_token(str(item or "").strip(), max_len=48)
            if t:
                merged.append(t)

    qf_block = stage1_result.get("question_focus")
    if isinstance(qf_block, dict):
        for key in ("evidence_axes", "secondary_axes"):
            inner = qf_block.get(key)
            if isinstance(inner, list):
                for item in inner:
                    t = _clean_retrieval_token(str(item or "").strip(), max_len=48)
                    if t:
                        merged.append(t)

    cap = _effective_focus_max_n()
    seen: set[str] = set()
    out: list[str] = []
    for t in merged:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= cap:
            break
    return out


def _normalize_query_focus_terms(raw: Any) -> List[str]:
    """Stage1 JSON `query_focus_terms`: short phrases for Stage2 must-include (deduped, length-capped)."""
    max_n = 8
    try:
        max_n = max(1, min(int(str(os.getenv("QA_STAGE1_QUERY_FOCUS_MAX_TERMS", "6")).strip()), 12))
    except ValueError:
        max_n = 6
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        ck = _clean_retrieval_token(str(item or "").strip(), max_len=48)
        if not ck or ck in seen:
            continue
        seen.add(ck)
        out.append(ck)
        if len(out) >= max_n:
            break
    return out


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
        question_focus = _normalize_question_focus(stage1_result.get("question_focus"))
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
        query_focus_terms = _normalize_query_focus_terms(stage1_result.get("query_focus_terms"))
        merged_focus = effective_query_focus_terms_for_stage2(
            {
                "query_focus_terms": query_focus_terms,
                "question_focus": question_focus,
            }
        )
        logger.info(
            "阶段一结果归一化完成: deep_answer_chars=%s retrieval_claims=%s query_focus_terms=%s "
            "question_focus=%s effective_focus_terms=%s",
            len(deep_answer),
            len(retrieval_claims),
            query_focus_terms,
            {"type": question_focus.get("focus_type"), "axes": len(question_focus.get("evidence_axes") or [])},
            merged_focus,
        )
        return {
            "success": True,
            "deep_answer": deep_answer,
            "answer_plan": answer_plan,
            "retrieval_claims": retrieval_claims,
            "query_focus_terms": query_focus_terms,
            "question_focus": question_focus,
            "effective_query_focus_terms": merged_focus,
            "raw_response": cleaned_text,
        }
    except Exception as exc:
        raise_if_upstream_pool_timeout(exc)
        logger.error("阶段一执行失败: %s", exc)
        return {"success": False, "error": str(exc)}
