from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Literal

from app.integrations.llm import raise_if_upstream_pool_timeout
from app.utils.upstream_errors import UpstreamCallError
from app.integrations.llm.thinking import LLM_STAGE_CONTROL, merge_extra_body, resolve_thinking_controls
from app.modules.generation_pipeline.intent_detect import (
    apply_intent_tag_to_question_focus,
    format_intent_hint_for_stage1_user_block,
    intent_detect_enabled,
    run_intent_detect_quick_tag,
)
from app.modules.generation_pipeline.text_processing import _clean_retrieval_token


def _env_bool_truthy(raw: str | None, *, default: bool = False) -> bool:
    value = str(raw or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _stage1_log_retrieval_claims_enabled() -> bool:
    """When true, INFO-log each normalized retrieval claim (truncate long text). Enabled via QA_STAGE1_LOG_RETRIEVAL_CLAIMS."""

    return _env_bool_truthy(os.getenv("QA_STAGE1_LOG_RETRIEVAL_CLAIMS"))


def _stage1_claim_log_max_chars() -> int:
    try:
        return max(80, min(int(str(os.getenv("QA_STAGE1_LOG_CLAIM_MAX_CHARS", "360")).strip()), 8000))
    except ValueError:
        return 360


def _stage1_claim_log_preview(text: str, *, max_chars: int) -> str:
    t = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 1] + "…"


def _stage1_bool_text(value: bool) -> str:
    return "true" if value else "false"


def _stage1_response_log_max_chars() -> int:
    try:
        return max(0, min(int(str(os.getenv("QA_STAGE1_LOG_RESPONSE_MAX_CHARS", "4000")).strip()), 50000))
    except ValueError:
        return 4000


def _stage1_response_log_text(raw_response: str) -> tuple[str, bool]:
    text = str(raw_response or "")
    if _env_bool_truthy(os.getenv("QA_STAGE1_LOG_FULL_RESPONSE")):
        return text, False
    max_chars = _stage1_response_log_max_chars()
    if max_chars <= 0:
        return "", len(text) > 0
    if len(text) <= max_chars:
        return text, False
    return text[: max_chars - 1] + "…", True


def _log_stage1_structured_quality(
    *,
    logger: Any,
    raw_response: str,
    json_parsed: bool,
    schema_valid: bool,
    deep_answer: str,
    retrieval_claims_count: int,
    valid_claims_count: int,
    raw_claims_count: int,
    query_focus_terms_count: int,
    question_focus_present: bool,
    answer_plan_present: bool,
    fallback: str,
) -> None:
    stage2_eligible = valid_claims_count > 0
    logger.info(
        "阶段一结构化质量检查: json_parsed=%s schema_valid=%s deep_answer_chars=%s "
        "retrieval_claims_count=%s valid_claims_count=%s raw_claims_count=%s "
        "query_focus_terms_count=%s question_focus_present=%s answer_plan_present=%s "
        "fallback=%s stage2_eligible=%s raw_response_chars=%s",
        _stage1_bool_text(json_parsed),
        _stage1_bool_text(schema_valid),
        len(str(deep_answer or "")),
        retrieval_claims_count,
        valid_claims_count,
        raw_claims_count,
        query_focus_terms_count,
        _stage1_bool_text(question_focus_present),
        _stage1_bool_text(answer_plan_present),
        fallback or "none",
        _stage1_bool_text(stage2_eligible),
        len(str(raw_response or "")),
    )
    response_preview, truncated = _stage1_response_log_text(raw_response)
    if response_preview:
        logger.info(
            "阶段一原始回答预览: raw_response_chars=%s preview_chars=%s truncated=%s content=%s",
            len(str(raw_response or "")),
            len(response_preview),
            _stage1_bool_text(truncated),
            response_preview,
        )


def _stage1_count_raw_claims(raw_claims: Any) -> int:
    if isinstance(raw_claims, dict):
        return 1
    if isinstance(raw_claims, str):
        return 1 if raw_claims.strip() else 0
    if isinstance(raw_claims, list):
        return len(raw_claims)
    return 0


_DensityBucket = Literal["neutral", "tap_only", "compaction_only", "both", "ambiguous_dense"]


_DENSITY_TERM_TAP_FULL = frozenset(
    {
        "振实密度",
        "敲击密度",
        "tap density",
        "tap-density",
        "tapping density",
    }
)


_DENSITY_TERM_COMP_FULL = frozenset(
    {
        "压实密度",
        "压片密度",
        "极片压实密度",
        "电极压实密度",
        "电极片压实密度",
        "compaction density",
    }
)


def _density_axis_bucket(user_question: str) -> _DensityBucket:
    """Classify user wording for deterministic tap vs compaction vs vague high-compaction."""

    raw = str(user_question or "")
    qc = raw.lower()

    tap = bool(
        re.search(
            r"(振实密度|敲击密度|粉末振实|tap[-\s]*density|tapping\s+density)",
            raw,
            flags=re.I,
        )
    )
    compaction = bool(
        re.search(
            r"(压实密度|压片密度|极片压实|电极压实|电极片压实|compaction\s+density)",
            raw,
            flags=re.I,
        )
    )
    compaction = compaction or ("calendering" in qc and re.search(r"(辊压|极片)", raw))
    compaction = compaction or ("calender" in qc and re.search(r"(辊压|极片)", raw))

    if tap and compaction:
        return "both"
    if tap:
        return "tap_only"
    if compaction:
        return "compaction_only"
    if re.search(r"(高压实型|高压实|粉末致密|致密化)", raw):
        return "ambiguous_dense"
    return "neutral"


def _is_pure_density_metric_token(term: str) -> bool:
    t = _clean_retrieval_token(str(term or "").strip(), max_len=96)
    if not t:
        return False
    tl = t.lower()
    return t in _DENSITY_TERM_TAP_FULL.union(_DENSITY_TERM_COMP_FULL) or tl in {"tap density", "tap-density"}


def _filter_focus_term_list(items: List[str], bucket: _DensityBucket) -> List[str]:
    if bucket == "neutral" or bucket == "both":
        return items

    out: list[str] = []
    for item in items:
        ck = _clean_retrieval_token(str(item or "").strip(), max_len=96)
        if not ck:
            continue
        lower_ck = ck.lower()

        # Vague 「高压实型」questions: strip LLM-inserted singleton metric anchors so Stage2 won't
        # must-include a single polarity (typically tap-density) exclusively.
        if bucket == "ambiguous_dense":
            if _is_pure_density_metric_token(ck):
                continue
            out.append(ck)
            continue

        tap_hit = ck in _DENSITY_TERM_TAP_FULL or ("tap density" in lower_ck) or ("tap-density" in lower_ck)
        comp_hit = ck in _DENSITY_TERM_COMP_FULL or ("compaction density" in lower_ck)
        tapish = tap_hit or re.search(r"(振实|敲击密度|tap\s*density|tapping\s+density)", ck, flags=re.I)
        compish = comp_hit or re.search(r"(压实密度|压片密度|极片压实|电极压实)", ck)

        if bucket == "compaction_only" and tapish and not compish:
            continue
        if bucket == "tap_only" and compish and not tapish:
            continue
        out.append(ck)
    return out


def _apply_stage1_density_disambiguation(
    *,
    user_question: str,
    query_focus_terms: List[str],
    question_focus: dict[str, Any],
) -> tuple[List[str], dict[str, Any]]:
    """Strip conflicting density-metric anchors and annotate ambiguous 「高压实」questions."""

    bucket = _density_axis_bucket(user_question)
    if bucket == "neutral":
        return query_focus_terms, question_focus

    qf_filtered = _filter_focus_term_list(query_focus_terms, bucket)
    q_focus = dict(question_focus or {})

    for axis_key in ("evidence_axes", "secondary_axes"):
        inner = q_focus.get(axis_key)
        if isinstance(inner, list):
            q_focus[axis_key] = _filter_focus_term_list([str(x) for x in inner], bucket)

    if bucket == "ambiguous_dense":
        clar = (
            "【指标辨析】题干偏重高压实/致密但未点明表征口径：粉体侧常用振实密度与球形／堆积；"
            "电极侧用压实密度及辊压。二者物理含义不同，检索与行文不应混为一谈。"
        )
        summary = str(q_focus.get("focus_summary") or "").strip()
        if clar not in summary:
            sep = "\n" if summary else ""
            q_focus["focus_summary"] = f"{summary}{sep}{clar}".strip()

        prev_type = str(q_focus.get("focus_type") or "").strip().lower()
        if prev_type in {
            "",
            "generic",
            "powder_dense_morphology",
            "synthesis_preparation",
            "electrode_compaction_process",
        }:
            q_focus["focus_type"] = "density_metric_ambiguity"

    return qf_filtered, q_focus


def _is_response_format_capability_error(exc: Exception) -> bool:
    message = " ".join(str(exc or "").split()).lower()
    if not message:
        return False
    if "response_format" not in message and "json_object" not in message:
        return False
    capability_hints = ("not supported", "unsupported", "unknown parameter", "invalid parameter", "not implemented")
    return any(hint in message for hint in capability_hints)


def _create_stage1_completion(*, client: Any, model: str, messages: list[dict[str, Any]], logger: Any) -> Any:
    controls = resolve_thinking_controls(
        stage=LLM_STAGE_CONTROL,
        max_tokens=3000,
        stream=False,
    )
    extra_body = merge_extra_body(None, controls)
    try:
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.5,
            max_tokens=controls.max_tokens,
            extra_body=extra_body,
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
            max_tokens=controls.max_tokens,
            extra_body=extra_body,
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
        intent_result: dict[str, Any] | None = None
        if intent_detect_enabled():
            intent_result = run_intent_detect_quick_tag(
                client=client,
                user_question=user_question,
                logger=logger,
            )
        intent_hint = format_intent_hint_for_stage1_user_block(intent_result=intent_result or {})

        full_system_prompt = stage1_prompt + (("\n\n" + vector_db_context) if vector_db_context else "")
        context_block = _format_conversation_context(conversation_context)
        user_content = f"{context_block}\n\n用户问题：{user_question}" if context_block else f"用户问题：{user_question}"
        if graph_context:
            user_content = f"图谱结构化线索：\n{graph_context}\n\n{user_content}"
        if intent_hint:
            user_content = f"{intent_hint}\n{user_content}"
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
            upstream = UpstreamCallError.stage1_json_invalid()
            logger.error("阶段一 JSON 解析失败，终止流程")
            logger.error("阶段一原始响应前500字符: %s", preview)
            _log_stage1_structured_quality(
                logger=logger,
                raw_response=result_text,
                json_parsed=False,
                schema_valid=False,
                deep_answer="",
                retrieval_claims_count=0,
                valid_claims_count=0,
                raw_claims_count=0,
                query_focus_terms_count=0,
                question_focus_present=False,
                answer_plan_present=False,
                fallback="json_parse_failed",
            )
            return {
                "success": False,
                "deep_answer": "",
                "retrieval_claims": [],
                "raw_response": result_text,
                "error": upstream.error,
                "upstream_error": upstream.to_dict(),
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
        query_focus_terms, question_focus = _apply_stage1_density_disambiguation(
            user_question=user_question,
            query_focus_terms=query_focus_terms,
            question_focus=question_focus,
        )
        if intent_result and intent_result.get("ok"):
            question_focus = apply_intent_tag_to_question_focus(
                intent_tag=str(intent_result.get("intent_tag") or ""),
                question_focus=question_focus,
            )
        merged_focus = effective_query_focus_terms_for_stage2(
            {
                "query_focus_terms": query_focus_terms,
                "question_focus": question_focus,
            }
        )
        raw_claims_field = stage1_result.get("retrieval_claims")
        raw_query_focus_terms = stage1_result.get("query_focus_terms")
        raw_question_focus = stage1_result.get("question_focus")
        valid_claims_count = len(retrieval_claims)
        schema_valid = bool(
            deep_answer
            and isinstance(raw_claims_field, list)
            and valid_claims_count > 0
            and isinstance(raw_query_focus_terms, list)
            and isinstance(raw_question_focus, dict)
        )
        _log_stage1_structured_quality(
            logger=logger,
            raw_response=result_text,
            json_parsed=True,
            schema_valid=schema_valid,
            deep_answer=deep_answer,
            retrieval_claims_count=len(retrieval_claims),
            valid_claims_count=valid_claims_count,
            raw_claims_count=_stage1_count_raw_claims(raw_claims),
            query_focus_terms_count=len(query_focus_terms),
            question_focus_present=isinstance(raw_question_focus, dict),
            answer_plan_present=isinstance(stage1_result.get("answer_plan"), dict),
            fallback=str(stage1_result.get("fallback") or "none"),
        )
        logger.info(
            "阶段一结果归一化完成: deep_answer_chars=%s retrieval_claims=%s query_focus_terms=%s "
            "question_focus=%s effective_focus_terms=%s raw_claims_type=%s raw_claims_count=%s answer_plan_keys=%s",
            len(deep_answer),
            len(retrieval_claims),
            query_focus_terms,
            {"type": question_focus.get("focus_type"), "axes": len(question_focus.get("evidence_axes") or [])},
            merged_focus,
            type(raw_claims).__name__,
            _stage1_count_raw_claims(raw_claims),
            sorted(answer_plan.keys()) if isinstance(answer_plan, dict) else [],
        )
        if not retrieval_claims:
            logger.warning(
                "阶段一 retrieval_claims 为空: raw_claims_type=%s raw_claims_count=%s answer_plan_keys=%s "
                "fallback=%s raw_response_preview=%s",
                type(raw_claims).__name__,
                _stage1_count_raw_claims(raw_claims),
                sorted(answer_plan.keys()) if isinstance(answer_plan, dict) else [],
                str(stage1_result.get("fallback") or ""),
                _stage1_claim_log_preview(result_text, max_chars=500),
            )

        if _stage1_log_retrieval_claims_enabled() and retrieval_claims:
            cap = _stage1_claim_log_max_chars()
            logger.info(
                "阶段一 retrieval_claims 明细 (%s 条，单条预览上限约 %s 字符，keywords 最多展示 12 项)",
                len(retrieval_claims),
                cap,
            )
            for idx, item in enumerate(retrieval_claims, start=1):
                claim_txt = _stage1_claim_log_preview(str(item.get("claim") or ""), max_chars=cap)
                keywords = item.get("keywords") or []
                kw_line = ""
                if isinstance(keywords, list):
                    flat = [str(x).strip() for x in keywords if str(x).strip()]
                    kw_line = "; ".join(flat[:12])
                secs = item.get("preferred_sections") or []
                sec_line = ""
                if isinstance(secs, list):
                    sec_line = "; ".join(str(x).strip() for x in secs if str(x).strip())[:240]
                logger.info(
                    "  [%s/%s] claim=%s keywords=%s sections=%s",
                    idx,
                    len(retrieval_claims),
                    claim_txt,
                    kw_line or "(none)",
                    sec_line or "(none)",
                )

        out: Dict[str, Any] = {
            "success": True,
            "deep_answer": deep_answer,
            "answer_plan": answer_plan,
            "retrieval_claims": retrieval_claims,
            "query_focus_terms": query_focus_terms,
            "question_focus": question_focus,
            "effective_query_focus_terms": merged_focus,
            "raw_response": cleaned_text,
        }
        if intent_result is not None:
            out["intent_detect"] = intent_result
        return out
    except Exception as exc:
        raise_if_upstream_pool_timeout(exc)
        logger.error("阶段一执行失败: %s", exc)
        upstream = UpstreamCallError.from_exception(
            exc,
            code="LLM_UNAVAILABLE",
            component="llm",
            stage="stage1",
            error="llm_unavailable",
        )
        return {"success": False, "error": str(exc), "upstream_error": upstream.to_dict()}
