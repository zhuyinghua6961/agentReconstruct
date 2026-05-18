from __future__ import annotations

import json
import re
import time
from typing import Any

from server.patent.models import PatentRetrievalClaim, PatentRetrievalPlan
from server.patent.prompt_loader import load_patent_prompt_template


DEFAULT_PATENT_STAGE1_PROMPT = load_patent_prompt_template("stage1_planning.txt")

_OUTER_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*)\s*```\s*$", re.IGNORECASE | re.DOTALL)
_PATENT_ID_RE = re.compile(r"\b(?=[A-Z0-9/.,-]*\d)[A-Z]{2}[A-Z0-9][A-Z0-9/.,-]{4,}[A-Z0-9]\b")
_PATENT_ID_NORMALIZE_RE = re.compile(r"[^A-Z0-9]")


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
            temperature=0.3,
            max_tokens=1800,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        if not _is_response_format_capability_error(exc):
            raise
        logger.warning("patent stage1 response_format unsupported; retrying without it: %s", exc)
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=1800,
        )


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

    graph_kb = conversation_context.get("graph_kb")
    if isinstance(graph_kb, dict):
        graph_lines: list[str] = []
        mode = " ".join(str(graph_kb.get("mode") or "").split()).strip()
        if mode:
            graph_lines.append(f"图谱模式：{mode}")
        patent_candidates = _normalize_text_list(graph_kb.get("stage2_patent_candidates"))
        if patent_candidates:
            graph_lines.append(f"图谱候选专利：{'；'.join(patent_candidates)}")
        entity_hints = dict(graph_kb.get("stage2_entity_hints") or {})
        rendered_hints: list[str] = []
        for key, values in entity_hints.items():
            hint_values = _normalize_text_list(values)
            if hint_values:
                rendered_hints.append(f"{str(key).strip()}={'；'.join(hint_values)}")
        if rendered_hints:
            graph_lines.append(f"图谱实体提示：{'；'.join(rendered_hints)}")
        rendered_constraints: list[str] = []
        for item in list(graph_kb.get("stage2_constraints") or []):
            if not isinstance(item, dict):
                continue
            field = " ".join(str(item.get("field") or "").split()).strip()
            operator = " ".join(str(item.get("operator") or "").split()).strip()
            value = " ".join(str(item.get("value") or "").split()).strip()
            if field and operator and value:
                rendered_constraints.append(f"{field} {operator} {value}")
        if rendered_constraints:
            graph_lines.append(f"图谱约束：{'；'.join(rendered_constraints)}")
        fact_block = " ".join(str(graph_kb.get("stage4_fact_block") or "").split()).strip()
        if fact_block:
            graph_lines.append(f"图谱事实：{fact_block}")
        if graph_lines:
            parts.append("图谱辅助：\n" + "\n".join(graph_lines))
    return "\n\n".join(parts).strip()


def _seed_retrieval_claims_from_graph(
    *,
    question: str,
    conversation_context: dict[str, Any] | None,
) -> list[PatentRetrievalClaim]:
    context = dict(conversation_context or {})
    graph_kb = dict(context.get("graph_kb") or {})
    if not graph_kb:
        return []

    patent_candidates = _normalize_text_list(graph_kb.get("stage2_patent_candidates"))
    entity_hints = dict(graph_kb.get("stage2_entity_hints") or {})
    ipc_codes = _normalize_text_list(entity_hints.get("ipc_codes"))
    organizations = _normalize_text_list(entity_hints.get("organizations"))
    inventors = _normalize_text_list(entity_hints.get("inventors"))
    fact_lines = _normalize_text_list(str(graph_kb.get("stage4_fact_block") or "").splitlines())
    keywords = patent_candidates + ipc_codes + organizations + inventors
    if not (keywords or fact_lines):
        return []

    claim_parts = ["优先核验图谱候选专利与结构化实体线索"]
    if fact_lines:
        claim_parts.append(fact_lines[0].lstrip("- ").strip())
    claim_text = "；".join(part for part in claim_parts if part)
    return [
        PatentRetrievalClaim(
            claim=claim_text,
            keywords=keywords[:10],
            preferred_sections=["claims", "description", "tables"],
            filters={"graph_seeded": True},
        )
    ]


def _normalize_text_list(values: Any) -> list[str]:
    if isinstance(values, str):
        iterable = [values]
    else:
        iterable = list(values or [])
    normalized: list[str] = []
    for item in iterable:
        text = " ".join(str(item or "").split()).strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_claims(raw_claims: Any, *, question: str) -> list[PatentRetrievalClaim]:
    if isinstance(raw_claims, dict):
        iterable = [raw_claims]
    elif isinstance(raw_claims, str):
        iterable = [raw_claims]
    else:
        iterable = list(raw_claims or [])
    normalized: list[PatentRetrievalClaim] = []
    for item in iterable:
        if isinstance(item, dict):
            claim_text = " ".join(str(item.get("claim") or "").split()).strip()
            keywords = _normalize_text_list(item.get("keywords") or item.get("keyword") or [])
            preferred_sections = _normalize_text_list(
                item.get("preferred_sections") or item.get("sections") or item.get("preferred") or []
            )
            filters = dict(item.get("filters") or {}) if isinstance(item.get("filters"), dict) else {}
        else:
            claim_text = " ".join(str(item or "").split()).strip()
            keywords = []
            preferred_sections = []
            filters = {}
        if not claim_text:
            continue
        if not preferred_sections:
            preferred_sections = _infer_preferred_sections(f"{question} {claim_text}".strip())
        normalized.append(
            PatentRetrievalClaim(
                claim=claim_text,
                keywords=keywords,
                preferred_sections=preferred_sections,
                filters=filters,
            )
        )
    return normalized


def _extract_patent_ids(text: str) -> list[str]:
    normalized: list[str] = []
    for match in _PATENT_ID_RE.findall(str(text or "").upper()):
        patent_id = _PATENT_ID_NORMALIZE_RE.sub("", match)
        if patent_id and patent_id not in normalized:
            normalized.append(patent_id)
    return normalized


def _merge_claim_filters(claims: list[PatentRetrievalClaim]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for claim in claims:
        for key, value in dict(claim.filters or {}).items():
            if key not in merged:
                merged[key] = value
                continue
            existing = merged[key]
            if isinstance(existing, list) and isinstance(value, list):
                merged[key] = list(dict.fromkeys([*existing, *value]))
                continue
            if isinstance(existing, dict) and isinstance(value, dict):
                merged[key] = {**existing, **value}
    return merged


def _retrieval_plan_from_claims(claims: list[PatentRetrievalClaim], *, question: str) -> PatentRetrievalPlan:
    explicit_patent_ids = list(dict.fromkeys(
        _extract_patent_ids(question)
        + _extract_patent_ids(" ".join(
            " ".join([claim.claim, *claim.keywords]).strip()
            for claim in claims
        ))
    ))
    candidate_recall_queries: list[str] = []
    evidence_localization_queries: list[str] = []
    preferred_sections: list[str] = []
    for claim in claims:
        query_parts = [claim.claim, *claim.keywords]
        query = " ".join(part.strip() for part in query_parts if str(part).strip()).strip()
        if query and query not in candidate_recall_queries:
            candidate_recall_queries.append(query)
        if query and query not in evidence_localization_queries:
            evidence_localization_queries.append(query)
        for section in claim.preferred_sections:
            normalized_section = str(section).strip()
            if normalized_section and normalized_section not in preferred_sections:
                preferred_sections.append(normalized_section)
    return PatentRetrievalPlan(
        question_type=_infer_question_type(question, explicit_patent_ids),
        analysis_axes=_infer_analysis_axes(
            " ".join([question, *[" ".join([claim.claim, *claim.keywords]).strip() for claim in claims]]).strip()
        ),
        explicit_patent_ids=explicit_patent_ids,
        candidate_recall_queries=candidate_recall_queries,
        evidence_localization_queries=evidence_localization_queries,
        preferred_sections=preferred_sections or _infer_preferred_sections(question),
        filters=_merge_claim_filters(claims),
    )


def _claims_from_legacy_retrieval_plan(raw_plan: Any, *, question: str) -> list[PatentRetrievalClaim]:
    payload = raw_plan if isinstance(raw_plan, dict) else {}
    query_candidates = _normalize_text_list(payload.get("candidate_recall_queries"))
    localization_queries = _normalize_text_list(payload.get("evidence_localization_queries"))
    preferred_sections = _normalize_text_list(payload.get("preferred_sections")) or _infer_preferred_sections(question)
    filters = dict(payload.get("filters") or {}) if isinstance(payload.get("filters"), dict) else {}
    claims = []
    for text in localization_queries or query_candidates:
        claims.append(
            PatentRetrievalClaim(
                claim=text,
                keywords=[],
                preferred_sections=preferred_sections,
                filters=filters,
            )
        )
    return claims


def _infer_question_type(question: str, explicit_patent_ids: list[str]) -> str:
    text = str(question or "").lower()
    if any(token in text for token in ("替代", "substitution", "replace")):
        return "technology_substitution"
    if any(token in text for token in ("风险", "risk")) and any(token in text for token in ("时间", "窗口", "window", "timeline")):
        return "risk_timing_window"
    if any(token in text for token in ("对比", "比较", "compare", "versus", "vs")):
        return "comparison"
    if any(token in text for token in ("自由实施", "fto", "侵权", "绕开")):
        return "freedom_to_operate"
    if explicit_patent_ids:
        return "patent_lookup"
    return "patent_analysis"


def _infer_analysis_axes(question: str) -> list[str]:
    text = str(question or "").lower()
    axes: list[str] = []
    if any(token in text for token in ("替代", "substitution", "replace")):
        axes.append("substitution_risk")
    if any(token in text for token in ("风险", "risk")):
        axes.append("risk")
    if any(token in text for token in ("时间", "窗口", "window", "timeline")):
        axes.append("time_window")
    if any(token in text for token in ("对比", "比较", "compare", "versus", "vs")):
        axes.append("technical_route_comparison")
    if any(token in text for token in ("自由实施", "fto", "侵权", "绕开")):
        axes.append("freedom_to_operate")
    if any(token in text for token in ("性能", "参数", "table", "表格", "倍率", "循环")):
        axes.append("performance_evidence")
    return axes or ["core_patent_evidence"]


def _infer_preferred_sections(question: str) -> list[str]:
    text = str(question or "").lower()
    sections: list[str] = []
    if any(token in text for token in ("权利要求", "claim")):
        sections.append("claims")
    if any(token in text for token in ("说明书", "实施例", "description", "embodiment")):
        sections.append("description")
    if any(token in text for token in ("表格", "table", "性能", "参数", "倍率", "循环")):
        sections.append("tables")
    defaults = ["claims", "description", "tables"]
    for item in defaults:
        if item not in sections:
            sections.append(item)
    return sections


def _fallback_deep_answer(question: str, retrieval_plan: PatentRetrievalPlan) -> str:
    del question
    if retrieval_plan.question_type == "technology_substitution":
        return "初步判断需要围绕候选专利集合、关键性能指标与时间窗口信号来评估技术替代可能性。"
    if retrieval_plan.question_type == "risk_timing_window":
        return "初步判断需要围绕风险来源、时间窗口和关键证据段落组织后续专利检索。"
    if retrieval_plan.question_type == "freedom_to_operate":
        return "初步判断需要围绕权利要求边界、规避空间与同路线专利分布组织后续专利检索。"
    return "初步判断需要先召回相关专利，再在候选专利内定位权利要求、说明书和表格证据。"


def _empty_retrieval_plan(question: str) -> PatentRetrievalPlan:
    explicit_patent_ids = _extract_patent_ids(question)
    return PatentRetrievalPlan(
        question_type=_infer_question_type(question, explicit_patent_ids),
        analysis_axes=_infer_analysis_axes(question),
        explicit_patent_ids=explicit_patent_ids,
        preferred_sections=_infer_preferred_sections(question),
    )


def run_stage1_pre_answer_and_planning(
    *,
    user_question: str,
    client: Any | None = None,
    model: str = "",
    logger: Any,
    conversation_context: dict[str, Any] | None = None,
    stage1_prompt: str = DEFAULT_PATENT_STAGE1_PROMPT,
) -> dict[str, Any]:
    question = str(user_question or "").strip()
    context_block = _format_conversation_context(conversation_context)
    stage_started = time.perf_counter()
    logger.info(
        "patent stage1 planning start question_chars=%s context_chars=%s planner_ready=%s model=%s",
        len(question),
        len(context_block),
        client is not None and bool(str(model or "").strip()),
        str(model or "").strip(),
    )
    if client is None or not str(model or "").strip():
        retrieval_claims = _seed_retrieval_claims_from_graph(question=question, conversation_context=conversation_context)
        retrieval_plan = _retrieval_plan_from_claims(retrieval_claims, question=question) if retrieval_claims else _empty_retrieval_plan(question)
        logger.warning(
            "patent stage1 planning using fallback because planner is unavailable question_type=%s explicit_patent_ids=%s",
            retrieval_plan.question_type,
            list(retrieval_plan.explicit_patent_ids or []),
        )
        return {
            "success": True,
            "deep_answer": _fallback_deep_answer(question, retrieval_plan),
            "retrieval_claims": retrieval_claims,
            "retrieval_plan": retrieval_plan,
            "fallback": "planner_unavailable",
        }

    user_content = f"{context_block}\n\n用户问题：{question}" if context_block else f"用户问题：{question}"
    system_content = str(stage1_prompt or "").strip()
    logger.info(
        "patent stage1 planning prompt prepared prompt_chars=%s user_content_chars=%s elapsed_ms=%.3f",
        len(system_content),
        len(user_content),
        (time.perf_counter() - stage_started) * 1000,
    )
    try:
        llm_started = time.perf_counter()
        logger.info("patent stage1 planning llm request start model=%s", str(model).strip())
        response = _create_stage1_completion(
            client=client,
            model=str(model).strip(),
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            logger=logger,
        )
        result_text = str(response.choices[0].message.content or "").strip()
        logger.info(
            "patent stage1 planning llm response received response_chars=%s elapsed_ms=%.3f",
            len(result_text),
            (time.perf_counter() - llm_started) * 1000,
        )
        payload, cleaned_text = _parse_stage1_json_payload(result_text)
        if payload is None:
            retrieval_claims = _seed_retrieval_claims_from_graph(question=question, conversation_context=conversation_context)
            retrieval_plan = _retrieval_plan_from_claims(retrieval_claims, question=question) if retrieval_claims else _empty_retrieval_plan(question)
            logger.warning(
                "patent stage1 planning json parse failed response_chars=%s question_type=%s",
                len(result_text),
                retrieval_plan.question_type,
            )
            return {
                "success": True,
                "deep_answer": result_text,
                "retrieval_claims": retrieval_claims,
                "retrieval_plan": retrieval_plan,
                "raw_response": result_text,
                "fallback": "json_parse_failed",
            }

        retrieval_claims = _normalize_claims(payload.get("retrieval_claims"), question=question)
        if not retrieval_claims:
            retrieval_claims = _claims_from_legacy_retrieval_plan(payload.get("retrieval_plan"), question=question)
        retrieval_plan = _retrieval_plan_from_claims(retrieval_claims, question=question) if retrieval_claims else _empty_retrieval_plan(question)
        deep_answer = " ".join(str(payload.get("deep_answer") or "").split()).strip()
        if not deep_answer:
            deep_answer = _fallback_deep_answer(question, retrieval_plan)
            logger.warning(
                "patent stage1 planning missing deep_answer; using fallback question_type=%s",
                retrieval_plan.question_type,
            )
        logger.info(
            "patent stage1 planning parsed claims=%s explicit_patent_ids=%s preferred_sections=%s deep_answer_chars=%s",
            len(retrieval_claims),
            list(retrieval_plan.explicit_patent_ids or []),
            list(retrieval_plan.preferred_sections or []),
            len(deep_answer),
        )
        return {
            "success": True,
            "deep_answer": deep_answer,
            "retrieval_claims": retrieval_claims,
            "retrieval_plan": retrieval_plan,
            "raw_response": cleaned_text or result_text,
        }
    except Exception as exc:
        logger.error("patent stage1 planning failed: %s", exc)
        retrieval_claims = _seed_retrieval_claims_from_graph(question=question, conversation_context=conversation_context)
        retrieval_plan = _retrieval_plan_from_claims(retrieval_claims, question=question) if retrieval_claims else _empty_retrieval_plan(question)
        return {
            "success": True,
            "deep_answer": _fallback_deep_answer(question, retrieval_plan),
            "retrieval_claims": retrieval_claims,
            "retrieval_plan": retrieval_plan,
            "fallback": "planner_error",
            "error": str(exc),
        }
