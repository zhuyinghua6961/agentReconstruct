from __future__ import annotations

import json
import re
from typing import Any, Callable

from server.patent.models import PatentRetrievalClaim
from server.patent.retrieval_service import PatentRetrievalService


DEFAULT_PATENT_STAGE2_QUERY_PROMPT = """
你是专利问答系统的阶段二检索查询生成器。你的任务是基于用户问题和当前待验证 claim，输出最适合向量检索的专利检索 query。

请严格输出一个 JSON 对象，字段包括：
- query: 主检索 query，必须适合专利摘要和正文 chunk 检索
- query_expansions: 可选数组，给出最多 2 条补充检索 query

要求：
1. query 必须保留 claim 中的技术对象、性能指标、材料体系和关键比较维度。
2. 如果 claim 或关键词里出现专利号、公开号、IPC/CPC、材料体系、倍率、温度窗口、SOC 等信息，要显式保留。
3. 不要输出解释文字。
""".strip()

_OUTER_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*)\s*```\s*$", re.IGNORECASE | re.DOTALL)


def _is_response_format_capability_error(exc: Exception) -> bool:
    message = " ".join(str(exc or "").split()).lower()
    if not message:
        return False
    if "response_format" not in message and "json_object" not in message:
        return False
    capability_hints = ("not supported", "unsupported", "unknown parameter", "invalid parameter", "not implemented")
    return any(hint in message for hint in capability_hints)


def _create_stage2_completion(*, client: Any, model: str, messages: list[dict[str, Any]], logger: Any) -> Any:
    try:
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        if not _is_response_format_capability_error(exc):
            raise
        logger.warning("patent stage2 response_format unsupported; retrying without it: %s", exc)
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=600,
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


def _parse_json_payload(result_text: str) -> dict[str, Any] | None:
    for candidate in (
        str(result_text or "").strip(),
        _unwrap_outer_json_fence(result_text),
        _extract_balanced_json_object(result_text),
        _extract_balanced_json_object(_unwrap_outer_json_fence(result_text) or ""),
    ):
        normalized = str(candidate or "").strip()
        if not normalized:
            continue
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _normalize_query_list(values: Any) -> list[str]:
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


def _fallback_claim_query(*, retrieval_claim: PatentRetrievalClaim, user_question: str) -> list[str]:
    parts = [
        " ".join(str(retrieval_claim.claim or "").split()).strip(),
        *[" ".join(str(item or "").split()).strip() for item in list(retrieval_claim.keywords or [])],
    ]
    query = " ".join(part for part in parts if part).strip()
    if query:
        return [query]
    normalized_question = " ".join(str(user_question or "").split()).strip()
    return [normalized_question] if normalized_question else []


def build_stage2_queries_for_claim(
    *,
    user_question: str,
    retrieval_claim: PatentRetrievalClaim,
    client: Any | None,
    model: str,
    logger: Any,
    stage2_prompt: str = DEFAULT_PATENT_STAGE2_QUERY_PROMPT,
) -> list[str]:
    claim_text = " ".join(str(retrieval_claim.claim or "").split()).strip()
    if client is None or not str(model or "").strip():
        fallback_queries = _fallback_claim_query(retrieval_claim=retrieval_claim, user_question=user_question)
        logger.warning(
            "patent stage2 query generation using fallback because query planner is unavailable claim=%s fallback_queries=%s",
            claim_text[:120],
            fallback_queries,
        )
        return fallback_queries

    keyword_text = "；".join(
        " ".join(str(item or "").split()).strip()
        for item in list(retrieval_claim.keywords or [])
        if " ".join(str(item or "").split()).strip()
    )
    preferred_sections = "；".join(
        " ".join(str(item or "").split()).strip()
        for item in list(retrieval_claim.preferred_sections or [])
        if " ".join(str(item or "").split()).strip()
    )
    filters = dict(retrieval_claim.filters or {})
    user_content = "\n".join(
        [
            f"用户问题：{user_question}",
            f"当前 claim：{claim_text}",
            f"关键词：{keyword_text}",
            f"偏好 sections：{preferred_sections}",
            f"过滤条件：{json.dumps(filters, ensure_ascii=False, sort_keys=True)}",
        ]
    ).strip()
    try:
        response = _create_stage2_completion(
            client=client,
            model=str(model).strip(),
            messages=[
                {
                    "role": "system",
                    "content": stage2_prompt + "\n\n返回值只能是一个 JSON 对象，不能包含 JSON 以外的解释性文字。",
                },
                {"role": "user", "content": user_content},
            ],
            logger=logger,
        )
        result_text = str(response.choices[0].message.content or "").strip()
        payload = _parse_json_payload(result_text)
        if payload is None:
            fallback_queries = _fallback_claim_query(retrieval_claim=retrieval_claim, user_question=user_question)
            logger.warning(
                "patent stage2 query generation json parse failed claim=%s response_chars=%s fallback_queries=%s",
                claim_text[:120],
                len(result_text),
                fallback_queries,
            )
            return fallback_queries
        queries = _normalize_query_list([payload.get("query"), *list(payload.get("query_expansions") or [])])
        if queries:
            logger.info(
                "patent stage2 query generation succeeded claim=%s query_count=%s queries=%s",
                claim_text[:120],
                len(queries),
                queries,
            )
            return queries
        fallback_queries = _fallback_claim_query(retrieval_claim=retrieval_claim, user_question=user_question)
        logger.warning(
            "patent stage2 query generation returned empty queries claim=%s fallback_queries=%s",
            claim_text[:120],
            fallback_queries,
        )
        return fallback_queries
    except Exception as exc:
        logger.warning("patent stage2 query generation failed; using fallback query: %s", exc)
        return _fallback_claim_query(retrieval_claim=retrieval_claim, user_question=user_question)


def run_stage2_targeted_retrieval(
    *,
    retrieval_service: PatentRetrievalService,
    retrieval_claims: list[PatentRetrievalClaim],
    user_question: str,
    query_client: Any | None = None,
    query_model: str = "",
    logger: Any | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    log = logger
    if log is not None:
        log.info(
            "patent stage2 targeted retrieval start claim_count=%s query_model=%s",
            len(list(retrieval_claims or [])),
            str(query_model or "").strip(),
        )
    result = retrieval_service.targeted_retrieve(
        retrieval_claims=list(retrieval_claims or []),
        user_question=user_question,
        query_generation_fn=(
            None
            if log is None
            else lambda *, user_question, retrieval_claim: build_stage2_queries_for_claim(
                user_question=user_question,
                retrieval_claim=retrieval_claim,
                client=query_client,
                model=query_model,
                logger=log,
            )
        ),
        context=context,
    )
    if log is not None:
        metadata = dict(result.get("metadata") or {})
        log.info(
            "patent stage2 targeted retrieval completed source_ids=%s references=%s retrieval_plan_queries=%s",
            list(result.get("source_ids") or []),
            len(list(result.get("references") or [])),
            list(metadata.get("retrieval_plan_queries") or []),
        )
    return result


def extract_patent_source_ids_from_results(
    *,
    retrieval_service: PatentRetrievalService,
    retrieval_results: dict[str, Any],
) -> list[str]:
    return retrieval_service.extract_source_ids(retrieval_results)


def run_stage25_patent_evidence_expansion(
    *,
    retrieval_results: dict[str, Any],
    skipped: bool = True,
    skip_reason: str = "patent_mode_no_md_expansion",
) -> dict[str, Any]:
    return {
        "skipped": bool(skipped),
        "skip_reason": str(skip_reason or "").strip(),
        "retrieval_results": dict(retrieval_results or {}),
    }
