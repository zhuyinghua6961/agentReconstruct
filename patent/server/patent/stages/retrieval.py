from __future__ import annotations

import json
import re
from typing import Any, Callable

from server.patent.models import PatentRetrievalClaim
from server.patent.prompt_loader import load_patent_prompt_template
from server.patent.retrieval_guardrails import apply_patent_stage2_query_guardrails
from server.patent.retrieval_service import PatentRetrievalService
from server.patent.stage2_controls import resolve_stage2_runtime_toggles


DEFAULT_PATENT_STAGE2_QUERY_PROMPT = load_patent_prompt_template("stage2_query_generation.txt")
DEFAULT_PATENT_STAGE2_QUERY_SYSTEM_PROMPT = load_patent_prompt_template("stage2_query_system.txt")

_OUTER_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*)\s*```\s*$", re.IGNORECASE | re.DOTALL)


class _NullLogger:
    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None


def _create_stage2_completion(*, client: Any, model: str, messages: list[dict[str, Any]], logger: Any) -> Any:
    del logger
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=150,
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


class _PromptFormatDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _render_stage2_query_prompt(
    *,
    template: str,
    core_question: str,
    claim_text: str,
    keywords_text: str,
) -> str:
    return str(template or "").format_map(
        _PromptFormatDict(
            core_question=str(core_question or ""),
            claim_text=str(claim_text or ""),
            keywords_text=str(keywords_text or ""),
        )
    )


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
    stage2_system_prompt: str = DEFAULT_PATENT_STAGE2_QUERY_SYSTEM_PROMPT,
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

    keyword_text = ", ".join(
        " ".join(str(item or "").split()).strip()
        for item in list(retrieval_claim.keywords or [])
        if " ".join(str(item or "").split()).strip()
    )
    user_content = _render_stage2_query_prompt(
        template=stage2_prompt,
        core_question=str(user_question or "").strip() or f"关于{claim_text[:50]}的问题",
        claim_text=claim_text,
        keywords_text=keyword_text or "无",
    ).strip()
    try:
        response = _create_stage2_completion(
            client=client,
            model=str(model).strip(),
            messages=[
                {"role": "system", "content": str(stage2_system_prompt or "").strip()},
                {"role": "user", "content": user_content},
            ],
            logger=logger,
        )
        result_text = str(response.choices[0].message.content or "").strip()
        payload = _parse_json_payload(result_text)
        if payload is None:
            plain_queries = _normalize_query_list(result_text)
            if plain_queries:
                logger.info(
                    "patent stage2 query generation succeeded claim=%s query_count=%s queries=%s",
                    claim_text[:120],
                    len(plain_queries),
                    plain_queries,
                )
                return plain_queries
            fallback_queries = _fallback_claim_query(retrieval_claim=retrieval_claim, user_question=user_question)
            logger.warning(
                "patent stage2 query generation returned empty non-json response claim=%s response_chars=%s fallback_queries=%s",
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
    should_cancel: Any | None = None,
    active_stream_count: int | None = None,
    parallel_workers: int = 1,
    context: dict[str, Any] | None = None,
    rerank_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    log = logger
    planning_log = logger or _NullLogger()
    toggles = resolve_stage2_runtime_toggles()
    frozen_claim_queries: list[list[str]] = []
    stage2_query_diagnostics: list[dict[str, Any]] = []
    graph_context = dict((context or {}).get("graph_kb") or {}) if isinstance(context, dict) else {}
    for claim_index, claim in enumerate(list(retrieval_claims or []), start=1):
        claim_queries = build_stage2_queries_for_claim(
            user_question=user_question,
            retrieval_claim=claim,
            client=query_client,
            model=query_model,
            logger=planning_log,
        )
        guarded = apply_patent_stage2_query_guardrails(
            user_question=user_question,
            retrieval_claim=claim,
            queries=claim_queries,
            toggles=toggles,
            graph_context=graph_context,
        )
        frozen_claim_queries.append(list(guarded.queries))
        stage2_query_diagnostics.append(dict(guarded.diagnostics))
        if log is not None:
            log.info(
                "patent stage2 query guardrail claim_index=%s enabled=%s original_queries=%s final_queries=%s "
                "injected_entities=%s injected_metrics=%s",
                claim_index,
                bool(guarded.diagnostics.get("enabled")),
                len(claim_queries),
                len(guarded.queries),
                len(list(guarded.diagnostics.get("injected_entities") or [])),
                len(list(guarded.diagnostics.get("injected_metrics") or [])),
            )
    if log is not None:
        log.info(
            "patent stage2 targeted retrieval start claim_count=%s query_model=%s parallel_workers=%s convergence=%s rerank=%s validation=%s c_scoring=%s",
            len(list(retrieval_claims or [])),
            str(query_model or "").strip(),
            max(1, int(parallel_workers or 1)),
            bool(toggles.convergence_enabled),
            bool(toggles.rerank_enabled),
            bool(toggles.validation_enabled),
            bool(toggles.c_patent_scoring_enabled),
        )
    result = retrieval_service.targeted_retrieve(
        retrieval_claims=list(retrieval_claims or []),
        user_question=user_question,
        query_generation_fn=None,
        frozen_claim_queries=frozen_claim_queries,
        parallel_workers=parallel_workers,
        should_cancel=should_cancel,
        active_stream_count=active_stream_count,
        context=context,
        rerank_fn=rerank_fn,
        stage2_query_diagnostics=stage2_query_diagnostics,
    )
    if log is not None:
        metadata = dict(result.get("metadata") or {})
        log.info(
            "patent stage2 targeted retrieval completed source_ids=%s references=%s retrieval_plan_queries=%s raw_candidates=%s "
            "rerank=%s validation=%s graph_behavior=%s",
            list(result.get("source_ids") or []),
            len(list(result.get("references") or [])),
            list(metadata.get("retrieval_plan_queries") or []),
            metadata.get("stage2_raw_candidate_count"),
            dict(metadata.get("stage2_rerank") or {}),
            dict(metadata.get("stage2_validation") or {}),
            str(metadata.get("graph_stage2_behavior") or "none"),
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
