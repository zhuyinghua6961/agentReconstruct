from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
import re
from threading import Event, Thread
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app.integrations.llm import Stage2UpstreamGateCancelled, raise_if_upstream_pool_timeout
from app.modules.graph_kb.models import GraphRagPayload
from app.modules.generation_pipeline.feature_flags import env_bool, env_int
from app.modules.generation_pipeline.retrieval_validation import validate_retrieval_relevance
from app.modules.generation_pipeline.text_processing import (
    extract_question_keywords,
    finalize_retrieval_keywords_for_embedding,
    preprocess_retrieval_query,
)
from app.modules.qa_kb.comparison_intent import build_retrieval_claims_from_comparison_plan


ELEMENT_SYNONYM_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Ti", ("ti", "钛")),
    ("Mg", ("mg", "镁")),
    ("Mn", ("mn", "锰")),
    ("Zn", ("zn", "锌")),
    ("V", ("v", "钒")),
    ("F", ("f", "氟")),
    ("Cu", ("cu", "铜")),
    ("Al", ("al", "铝")),
)

QUESTION_CONTEXT_MARKERS: Tuple[str, ...] = (
    "当前需要正式回答的问题是：",
    "当前需要正式回答的问题是:",
)

NOISE_KEYWORD_PATTERNS: Tuple[str, ...] = (
    "以下是最近几轮对话背景",
    "保持当前问题语气和重点不变",
    "仅将其视为语义补充信息",
    "当前需要正式回答的问题",
    "对话背景",
    "语义补充信息",
)


@dataclass
class Stage2RuntimeToggles:
    force_keyword_injection_enabled: bool
    entity_lock_enabled: bool
    use_rerank: bool
    rerank_candidates: int


def resolve_stage2_runtime_toggles(
    *,
    force_keyword_injection_enabled: Optional[bool],
    entity_lock_enabled: Optional[bool],
    use_rerank: Optional[bool],
    rerank_candidates: Optional[int],
) -> Stage2RuntimeToggles:
    return Stage2RuntimeToggles(
        force_keyword_injection_enabled=(
            env_bool("QA_STAGE2_FORCE_KEYWORD_INJECTION", True)
            if force_keyword_injection_enabled is None
            else bool(force_keyword_injection_enabled)
        ),
        entity_lock_enabled=(
            env_bool("QA_STAGE2_ENTITY_LOCK_ENABLED", True)
            if entity_lock_enabled is None
            else bool(entity_lock_enabled)
        ),
        use_rerank=(
            True
            if use_rerank is None
            else bool(use_rerank)
        ),
        rerank_candidates=(
            env_int("QA_RETRIEVAL_RERANK_CANDIDATES", 50, minimum=5, maximum=100)
            if rerank_candidates is None
            else max(5, min(int(rerank_candidates), 100))
        ),
    )


def resolve_stage2_parallel_workers(
    *,
    base_workers: int,
    active_stream_count: Optional[int],
) -> tuple[int, Dict[str, Any]]:
    base = max(1, int(base_workers))
    dynamic_enabled = env_bool("QA_STAGE2_DYNAMIC_WORKERS_ENABLED", False)
    if not dynamic_enabled:
        return base, {
            "dynamic_enabled": False,
            "active_stream_count": int(active_stream_count or 0),
            "trigger_active": None,
            "min_workers": None,
            "step": None,
            "effective_workers": base,
        }

    trigger_active = env_int("QA_STAGE2_DYNAMIC_WORKERS_TRIGGER_ACTIVE", 4, minimum=1, maximum=128)
    min_workers = env_int("QA_STAGE2_DYNAMIC_WORKERS_MIN", 3, minimum=1, maximum=32)
    step = env_int("QA_STAGE2_DYNAMIC_WORKERS_STEP", 1, minimum=1, maximum=8)

    if min_workers > base:
        min_workers = base

    active = max(0, int(active_stream_count or 0))
    overload_units = max(0, active - trigger_active + 1)
    reduced = base - overload_units * step
    effective = max(min_workers, reduced)
    effective = min(base, max(1, effective))
    return effective, {
        "dynamic_enabled": True,
        "active_stream_count": active,
        "trigger_active": trigger_active,
        "min_workers": min_workers,
        "step": step,
        "effective_workers": effective,
    }


def resolve_stage2_upstream_gate_limit(
    *,
    configured_limit: int,
    ready_lanes: int,
    effective_parallel_workers: int,
) -> int | None:
    configured = max(0, int(configured_limit or 0))
    ready = max(0, int(ready_lanes or 0))
    effective = max(0, int(effective_parallel_workers or 0))
    if configured <= 0 or ready <= 0 or effective <= 0:
        return None
    return min(configured, ready, effective)


@contextmanager
def _noop_context() -> Any:
    yield


def _gate_context(
    gate: Any | None,
    *,
    trace_label: str | None,
    request_limit: int | None,
    should_cancel: Callable[[], bool] | None,
):
    if gate is None:
        return _noop_context()
    try:
        return gate.enter(
            trace_label=trace_label,
            request_limit=request_limit,
            should_cancel=should_cancel,
        )
    except TypeError:
        return gate.enter(trace_label=trace_label)


def _run_cancelable_upstream_call(
    *,
    call: Callable[[], Any],
    should_cancel: Callable[[], bool] | None,
    abort: Callable[[], None] | None = None,
    cancel_message: str,
) -> Any:
    if should_cancel is None:
        return call()

    done = Event()
    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result_box["value"] = call()
        except BaseException as exc:  # pragma: no cover - propagated below
            error_box["error"] = exc
        finally:
            done.set()

    worker = Thread(target=_runner, daemon=True)
    worker.start()
    while not done.wait(0.05):
        try:
            cancelled = bool(should_cancel())
        except Exception:
            cancelled = False
        if not cancelled:
            continue
        if abort is not None:
            try:
                abort()
            except Exception:
                pass
        done.wait(0.2)
        raise Stage2UpstreamGateCancelled(cancel_message)

    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


def _contains_keyword(text: str, keyword: str) -> bool:
    source = str(text or "")
    token = str(keyword or "").strip()
    if not source or not token:
        return False
    if re.search(r"[\u4e00-\u9fff]", token):
        return token in source
    return bool(re.search(r"\b" + re.escape(token) + r"\b", source, flags=re.IGNORECASE))


def extract_critical_entity_groups(question: str) -> List[Tuple[str, Tuple[str, ...]]]:
    text = str(question or "")
    lowered = text.lower()
    groups: List[Tuple[str, Tuple[str, ...]]] = []

    def _alias_present(alias: str) -> bool:
        token = str(alias or "").strip().lower()
        if not token:
            return False
        if re.search(r"[\u4e00-\u9fff]", token):
            return token in lowered
        return bool(re.search(r"\b" + re.escape(token) + r"\b", lowered, flags=re.IGNORECASE))

    for canonical, aliases in ELEMENT_SYNONYM_GROUPS:
        if any(_alias_present(alias) for alias in aliases):
            groups.append((canonical, aliases))
    return groups


def normalize_user_question_for_stage2(question: str) -> str:
    text = str(question or "").strip()
    if not text:
        return text

    for marker in QUESTION_CONTEXT_MARKERS:
        if marker in text:
            candidate = text.split(marker, 1)[-1].strip()
            if candidate:
                text = candidate
            break

    text = re.sub(r"^以下是最近几轮对话背景[^\n]*\n?", "", text, flags=re.IGNORECASE).strip()
    if "\n" in text:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            text = lines[-1]
    return text


def _is_noise_keyword(keyword: str) -> bool:
    token = str(keyword or "").strip()
    if not token:
        return True
    return any(pattern in token for pattern in NOISE_KEYWORD_PATTERNS)


def select_force_keywords(
    *,
    question: str,
    claim_keywords: Iterable[Any],
    extract_question_keywords_fn: Optional[Callable[[str], List[str]]],
    limit: int = 5,
) -> List[str]:
    selected: List[str] = []
    seen: set[str] = set()

    if extract_question_keywords_fn is not None and question:
        try:
            for item in extract_question_keywords_fn(question):
                kw = str(item or "").strip()
                if not kw or kw in seen or _is_noise_keyword(kw):
                    continue
                selected.append(kw)
                seen.add(kw)
                if len(selected) >= limit:
                    return selected
        except Exception:
            pass

    raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-_+/]*|[\u4e00-\u9fff]{2,8}|\d+[:/]\d+", str(question or ""))
    for token in raw_tokens:
        kw = str(token).strip()
        if len(kw) < 2 or kw in seen or _is_noise_keyword(kw):
            continue
        selected.append(kw)
        seen.add(kw)
        if len(selected) >= limit:
            return selected

    for item in claim_keywords:
        kw = str(item or "").strip()
        if not kw or kw in seen:
            continue
        selected.append(kw)
        seen.add(kw)
        if len(selected) >= limit:
            break
    return selected


def apply_stage2_query_constraints(
    *,
    query: str,
    user_question: str,
    claim_keywords: Iterable[Any],
    preprocess_retrieval_query_fn: Callable[[str], str],
    toggles: Stage2RuntimeToggles,
    extract_question_keywords_fn: Optional[Callable[[str], List[str]]],
) -> Tuple[str, Dict[str, Any]]:
    normalized_question = normalize_user_question_for_stage2(user_question)
    merged_prefix: List[str] = []
    details: Dict[str, Any] = {
        "injected_keywords": [],
        "injected_entities": [],
    }

    if toggles.force_keyword_injection_enabled:
        top_keywords = select_force_keywords(
            question=normalized_question,
            claim_keywords=claim_keywords,
            extract_question_keywords_fn=extract_question_keywords_fn,
            limit=5,
        )
        missing_keywords = [kw for kw in top_keywords if not _contains_keyword(query, kw)]
        if missing_keywords:
            details["injected_keywords"] = missing_keywords
            merged_prefix.extend(missing_keywords)

    if toggles.entity_lock_enabled:
        missing_entities: List[str] = []
        for canonical, aliases in extract_critical_entity_groups(normalized_question):
            if any(_contains_keyword(query, alias) for alias in aliases):
                continue
            missing_entities.append(canonical)
        if missing_entities:
            details["injected_entities"] = missing_entities
            merged_prefix.extend(missing_entities)

    constrained = query
    if merged_prefix:
        # Core retrieval tokens first; finalize_embedding will still prioritize must_include.
        constrained = f"{query} {' '.join(merged_prefix)}".strip()
    return constrained, details


def merge_graph_hints_into_retrieval(
    *,
    query: str,
    preprocess_retrieval_query_fn: Callable[[str], str],
    graph_evidence: GraphRagPayload | None,
) -> str:
    """Append optional graph **entity** hints after the core query (no DOI merge; no preprocess).

    DOI strings must not enter the dense retrieval query. ``preprocess_retrieval_query_fn`` is
    kept for API compatibility but unused here; embedding truncation is handled by
    ``finalize_retrieval_keywords_for_embedding`` at search time.
    """
    _ = preprocess_retrieval_query_fn
    if graph_evidence is None:
        return query
    if not env_bool("QA_STAGE2_GRAPH_QUERY_HINT_MERGE_ENABLED", False):
        return query

    prefixes: list[str] = []
    for values in dict(graph_evidence.stage2_entity_hints or {}).values():
        for item in list(values or [])[:5]:
            text = str(item or "").strip()
            if text and text not in prefixes and text not in query:
                prefixes.append(text)

    if not prefixes:
        return query
    return f"{query} {' '.join(prefixes)}".strip()


def _ensure_comparison_object_lock(
    *,
    query: str,
    must_include_any: Iterable[Any],
    preprocess_retrieval_query_fn: Callable[[str], str],
) -> tuple[str, list[str]]:
    tokens = [str(item or "").strip() for item in list(must_include_any or []) if str(item or "").strip()]
    if not tokens:
        return query, []
    if any(_contains_keyword(query, token) for token in tokens):
        return query, []
    locked = f"{query} {tokens[0]}".strip()
    return locked, [tokens[0]]


def _search_with_optional_rerank(
    *,
    literature_expert: Any,
    combined_query: str,
    n_results: int,
    toggles: Stage2RuntimeToggles,
    logger: Any | None = None,
    trace_label: str | None = None,
    rerank_gate: Any | None = None,
    rerank_gate_limit: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Dict[str, Any]:
    search_kwargs = {
        "n_results": n_results,
        "translate": False,
        "use_rerank": toggles.use_rerank,
        "rerank_candidates": toggles.rerank_candidates,
        "logger": logger,
        "trace_label": trace_label,
        "rerank_gate": rerank_gate,
        "rerank_gate_limit": rerank_gate_limit,
        "should_cancel": should_cancel,
    }
    try:
        return literature_expert.search(combined_query, **search_kwargs)
    except TypeError:
        reduced_kwargs = dict(search_kwargs)
        reduced_kwargs.pop("logger", None)
        reduced_kwargs.pop("trace_label", None)
        try:
            return literature_expert.search(combined_query, **reduced_kwargs)
        except TypeError:
            reduced_kwargs.pop("rerank_gate_limit", None)
            reduced_kwargs.pop("should_cancel", None)
            reduced_kwargs.pop("rerank_gate", None)
            reduced_kwargs.pop("use_rerank", None)
            reduced_kwargs.pop("rerank_candidates", None)
            return literature_expert.search(combined_query, **reduced_kwargs)


def _extract_dois_from_metadatas(metadatas: Iterable[Any]) -> list[str]:
    dois: list[str] = []
    seen: set[str] = set()
    for meta in metadatas:
        if not isinstance(meta, dict):
            continue
        doi = str(meta.get("doi") or meta.get("DOI") or meta.get("source_doi") or "").strip()
        if not doi or doi in seen:
            continue
        seen.add(doi)
        dois.append(doi)
    return dois


def _build_comparison_groups(
    *,
    comparison_plan: dict[str, Any] | None,
    claim_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(comparison_plan, dict) or not comparison_plan.get("enabled"):
        return []
    min_docs = int(comparison_plan.get("min_docs_per_object") or 1)
    groups: list[dict[str, Any]] = []
    for item in list(comparison_plan.get("objects") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        outputs = [output for output in claim_outputs if str(output.get("comparison_object") or "") == label and output.get("ok")]
        documents: list[Any] = []
        metadatas: list[Any] = []
        distances: list[Any] = []
        queries: list[str] = []
        for output in outputs:
            documents.extend(list(output.get("documents") or []))
            metadatas.extend(list(output.get("metadatas") or []))
            distances.extend(list(output.get("distances") or []))
            q_use = str(output.get("embedding_query") or output.get("query") or "").strip()
            if q_use:
                queries.append(q_use)
        evidence_status = "sufficient" if len(documents) >= min_docs else "insufficient"
        groups.append(
            {
                "label": label,
                "aliases": list(item.get("aliases") or []),
                "queries": queries,
                "abstract_hits": [
                    {"document": documents[idx], "metadata": metadatas[idx] if idx < len(metadatas) else {}, "distance": distances[idx] if idx < len(distances) else None}
                    for idx in range(len(documents))
                ],
                "md_hits": [],
                "doi_candidates": _extract_dois_from_metadatas(metadatas),
                "evidence_status": evidence_status,
                "missing_evidence_reason": "" if evidence_status == "sufficient" else "abstract_hits_below_threshold",
            }
        )
    return groups


def _generate_ai_query(
    *,
    client: Any | None,
    model: str | None,
    chat_lane_pool: Any | None,
    chat_gate: Any | None,
    chat_gate_limit: int | None,
    trace_label: str | None,
    logger: Any | None,
    should_cancel: Callable[[], bool] | None,
    normalized_user_question: str,
    claim_text: str,
    keywords: list[str],
    preferred_sections: list[str],
    filters: dict[str, Any],
    entity_lock_enabled: bool,
) -> str:
    if client is None or not model:
        return ""

    entity_guardrail_block = ""
    if entity_lock_enabled:
        entity_guardrail_block = """
【⚠️ 关键约束 - 必须遵守】：
- 如果用户问题中提到了具体的化学元素（如 Ti/钛、Mg/镁、F/氟 等），查询中必须保留该元素名称或符号
- 禁止省略用户问题中明确提到的元素，否则会检索到不相关文献
"""

    core_question = normalized_user_question if normalized_user_question else f"关于{claim_text[:50]}的问题"
    _ = preferred_sections
    _ = filters
    prompt = f"""
你是一个学术检索专家，擅长根据研究问题生成精准的文献检索查询。

【原始用户问题】（最重要！查询必须紧密围绕这个问题生成）：
{core_question}

【检索主张】（这个主张需要文献验证）：
{claim_text}

【关键词参考】（必须包含在查询中）：
{', '.join(keywords) if keywords else '无'}

【检索目标】：
- 数据库包含学术论文的结构化摘要，主要为中文内容
- 需要查找直接回答原始用户问题的文献
- 重点关注：具体的实验参数、数值、比例、条件等事实信息
- 如果用户问"最佳比例"，查询应包含具体的比例数值或搜索策略
{entity_guardrail_block}

【格式要求】：
- 用空格分隔的关键字列表（40-60字）
- 保留核心关键词和具体数值
- 不要包含与原始问题无关的背景信息或宽泛概念

请根据【原始用户问题】生成查询（必须紧密围绕问题核心）：
"""

    active_client = client
    if chat_lane_pool is not None:
        gate_ctx = _gate_context(
            chat_gate,
            trace_label=trace_label,
            request_limit=chat_gate_limit,
            should_cancel=should_cancel,
        )
        with gate_ctx:
            with chat_lane_pool.lease_lane(trace_label=trace_label) as leased_lane:
                if leased_lane is not None and getattr(leased_lane, "client", None) is not None:
                    active_client = leased_lane.client
                    if logger is not None:
                        logger.info(
                            "stage2 chat lane lease trace_label=%s lane=%s ready=true",
                            str(trace_label or ""),
                            int(getattr(leased_lane, "lane_id", -1)),
                        )
                response = _run_cancelable_upstream_call(
                    call=lambda: active_client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": "你是一个学术检索专家，擅长根据研究内容生成精准的文献检索查询。"},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0.3,
                        max_tokens=150,
                    ),
                    should_cancel=should_cancel,
                    abort=(
                        (lambda: chat_lane_pool.abort_lane(int(getattr(leased_lane, "lane_id", -1)), error_summary="cancelled"))
                        if leased_lane is not None and hasattr(chat_lane_pool, "abort_lane")
                        else None
                    ),
                    cancel_message="stage2 chat upstream call cancelled",
                )
                return str(response.choices[0].message.content or "").strip()

    response = active_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是一个学术检索专家，擅长根据研究内容生成精准的文献检索查询。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=150,
    )
    return str(response.choices[0].message.content or "").strip()


def run_single_claim_retrieval(
    *,
    claim: Any,
    n_results: int = 3,
    claim_index: int = 0,
    literature_expert: Any,
    logger: Any,
    use_rerank: Optional[bool] = None,
    rerank_candidates: Optional[int] = None,
) -> Dict[str, Any]:
    toggles = resolve_stage2_runtime_toggles(
        force_keyword_injection_enabled=False,
        entity_lock_enabled=False,
        use_rerank=use_rerank,
        rerank_candidates=rerank_candidates,
    )
    try:
        if isinstance(claim, dict):
            claim_text = claim.get("claim", "") or ""
            keywords = claim.get("keywords", []) or []
        else:
            claim_text = str(claim or "")
            keywords = []

        query_parts = []
        if claim_text:
            query_parts.append(claim_text)
        if keywords:
            query_parts.extend(keywords)
        final_query = " ".join(query_parts) if query_parts else "磷酸铁锂 Fe2P"

        logger.info("🔍 [并行检索 %s] 查询: %s...", claim_index, final_query[:100])
        max_kw = env_int("QA_STAGE2_EMBEDDING_QUERY_MAX_KEYWORDS", 15, minimum=4, maximum=48)
        max_inj = env_int("QA_STAGE2_EMBEDDING_QUERY_MAX_INJECTION_SLOTS", 0, minimum=0, maximum=64)
        embedding_query = finalize_retrieval_keywords_for_embedding(
            final_query,
            [],
            max_keywords=max_kw,
            max_injection_slots=max_inj if max_inj > 0 else None,
            logger=logger,
        )
        if literature_expert:
            results = _search_with_optional_rerank(
                literature_expert=literature_expert,
                combined_query=embedding_query,
                n_results=n_results,
                toggles=toggles,
            )
            doc_count = len(results.get("documents", []))
            logger.info("✅ [并行检索 %s] 完成: %s 个文档", claim_index, doc_count)
        else:
            logger.warning("⚠️ [并行检索 %s] literature_expert不可用", claim_index)
            results = {"documents": [], "metadatas": [], "distances": []}

        return {
            "documents": results.get("documents", []),
            "metadatas": results.get("metadatas", []),
            "distances": results.get("distances", []),
            "query": final_query,
            "embedding_query": embedding_query,
            "claim_index": claim_index,
        }
    except Exception as exc:
        raise_if_upstream_pool_timeout(exc)
        logger.error("❌ [并行检索 %s] 失败: %s", claim_index, exc)
        return {
            "documents": [],
            "metadatas": [],
            "distances": [],
            "query": str(claim)[:50] if claim else "",
            "embedding_query": "",
            "claim_index": claim_index,
            "error": str(exc),
        }


def run_stage2_targeted_retrieval(
    *,
    retrieval_claims: List[Any],
    n_results_per_claim: int = 3,
    user_question: Optional[str] = None,
    literature_expert: Any,
    logger: Any,
    client: Any | None = None,
    model: str | None = None,
    chat_lane_pool: Any | None = None,
    chat_gate: Any | None = None,
    rerank_gate: Any | None = None,
    preprocess_retrieval_query_fn: Callable[[str], str] | None = None,
    validate_retrieval_relevance_fn: Callable[[Dict[str, Any], str, str], Dict[str, Any]] | None = None,
    current_answer_context: Optional[str] = None,
    extract_question_keywords_fn: Optional[Callable[[str], List[str]]] = None,
    expand_query_fn: Optional[Callable[[str], str]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    active_stream_count: Optional[int] = None,
    force_keyword_injection_enabled: Optional[bool] = None,
    entity_lock_enabled: Optional[bool] = None,
    use_rerank: Optional[bool] = None,
    rerank_candidates: Optional[int] = None,
    graph_evidence: GraphRagPayload | None = None,
    comparison_plan: dict[str, Any] | None = None,
    query_focus_terms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    def _cancelled() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            return False

    cancel_check = _cancelled if should_cancel is not None else None

    preprocess_fn = preprocess_retrieval_query_fn or (lambda query: preprocess_retrieval_query(query, logger=logger))
    validate_fn = validate_retrieval_relevance_fn or (
        lambda results, query, claim_text: validate_retrieval_relevance(results, query, claim_text, logger)
    )
    extract_keywords_fn = extract_question_keywords_fn or extract_question_keywords

    toggles = resolve_stage2_runtime_toggles(
        force_keyword_injection_enabled=force_keyword_injection_enabled,
        entity_lock_enabled=entity_lock_enabled,
        use_rerank=use_rerank,
        rerank_candidates=rerank_candidates,
    )

    logger.info("\n%s", "=" * 60)
    logger.info("🔍 阶段二：基于检索指令的精准检索")
    logger.info("%s", "=" * 60)
    comparison_enabled = bool(isinstance(comparison_plan, dict) and comparison_plan.get("enabled"))
    logger.info("检索指令数量: %s", len(retrieval_claims))
    logger.info("每个指令检索数量: %s", n_results_per_claim)
    logger.info(
        "Stage2 开关: force_keywords=%s, entity_lock=%s, rerank=%s(%s)",
        int(toggles.force_keyword_injection_enabled),
        int(toggles.entity_lock_enabled),
        int(toggles.use_rerank),
        toggles.rerank_candidates,
    )

    configured_parallel_workers = env_int("QA_STAGE2_PARALLEL_WORKERS", 5, minimum=1, maximum=16)
    parallel_workers, worker_policy = resolve_stage2_parallel_workers(
        base_workers=configured_parallel_workers,
        active_stream_count=active_stream_count,
    )
    query_expansion_enabled = env_bool("QA_STAGE2_QUERY_EXPANSION_ENABLED", False)
    chat_ready_lanes = int(dict(getattr(chat_lane_pool, "snapshot", lambda: {})() or {}).get("ready_lanes") or 0)
    rerank_session_pool = getattr(literature_expert, "rerank_session_pool", None)
    rerank_ready_lanes = int(dict(getattr(rerank_session_pool, "snapshot", lambda: {})() or {}).get("ready_lanes") or 0)
    chat_gate_limit = resolve_stage2_upstream_gate_limit(
        configured_limit=env_int("FASTQA_STAGE2_CHAT_GATE_MAX_IN_FLIGHT", 3, minimum=0, maximum=16),
        ready_lanes=chat_ready_lanes,
        effective_parallel_workers=parallel_workers,
    )
    rerank_gate_limit = resolve_stage2_upstream_gate_limit(
        configured_limit=env_int("FASTQA_STAGE2_RERANK_GATE_MAX_IN_FLIGHT", 3, minimum=0, maximum=16),
        ready_lanes=rerank_ready_lanes,
        effective_parallel_workers=parallel_workers,
    )
    logger.info(
        "Stage2 并发: workers=%s (configured=%s, dynamic=%s)",
        parallel_workers,
        configured_parallel_workers,
        int(bool(worker_policy.get("dynamic_enabled"))),
    )
    if worker_policy.get("dynamic_enabled"):
        logger.info(
            "Stage2 动态并发参数: active=%s, trigger=%s, min=%s, step=%s",
            worker_policy.get("active_stream_count"),
            worker_policy.get("trigger_active"),
            worker_policy.get("min_workers"),
            worker_policy.get("step"),
        )
    logger.info("Stage2 查询扩展: enabled=%s", int(query_expansion_enabled))

    if _cancelled():
        logger.info("🛑 Stage2 在开始前已取消")
        return {
            "success": False,
            "cancelled": True,
            "documents": [],
            "metadatas": [],
            "distances": [],
            "claim_to_results": {},
            "unique_count": 0,
            "total_count": 0,
        }

    claims = list(retrieval_claims or [])
    if comparison_enabled:
        comparison_claims = build_retrieval_claims_from_comparison_plan(comparison_plan or {})
        if comparison_claims:
            claims = comparison_claims
    if not claims:
        return {
            "success": True,
            "documents": [],
            "metadatas": [],
            "distances": [],
            "claim_to_results": {},
            "comparison_plan": comparison_plan if comparison_enabled else None,
            "comparison_groups": [],
            "unique_count": 0,
            "total_count": 0,
        }

    normalized_user_question = normalize_user_question_for_stage2(str(user_question or ""))
    if normalized_user_question and normalized_user_question != str(user_question or "").strip():
        logger.info("🧹 Stage2 已移除对话背景包装，仅使用当前问题进行检索约束")

    focus_for_injection: List[str] = []
    if env_bool("QA_STAGE2_QUERY_FOCUS_INJECTION_ENABLED", True):
        focus_for_injection = [str(t).strip() for t in list(query_focus_terms or []) if str(t or "").strip()]
    if focus_for_injection:
        logger.info("Stage2 query_focus_terms (must-include)=%s", focus_for_injection)

    def _process_claim(index: int, claim: Any) -> Dict[str, Any]:
        claim_started_at = time.monotonic()
        if _cancelled():
            return {
                "index": index,
                "claim_key": f"claim_{index}",
                "documents": [],
                "metadatas": [],
                "distances": [],
                "query": "",
                "query_guardrail": {
                    "injected_keywords": [],
                    "injected_entities": [],
                    "query_focus_terms": list(focus_for_injection),
                },
                "rerank": {},
                "relevance_validation": {"before": 0, "after": 0},
                "timing": {
                    "ai_query_ms": 0.0,
                    "query_expansion_ms": 0.0,
                    "search_total_ms": 0.0,
                    "relevance_validation_ms": 0.0,
                    "claim_total_ms": (time.monotonic() - claim_started_at) * 1000.0,
                },
                "ok": False,
                "cancelled": True,
            }

        if isinstance(claim, dict):
            claim_text = str(claim.get("claim") or "").strip()
            profile_query = str(claim.get("query") or "").strip()
            keywords = [str(item).strip() for item in list(claim.get("keywords") or []) if str(item or "").strip()]
            preferred_sections = [str(item).strip() for item in list(claim.get("preferred_sections") or claim.get("preferred") or []) if str(item or "").strip()]
            filters = dict(claim.get("filters") or {}) if isinstance(claim.get("filters"), dict) else {}
            comparison_group = bool(claim.get("comparison_group"))
            comparison_object = str(claim.get("comparison_object") or "").strip()
            comparison_aliases = [str(item).strip() for item in list(claim.get("comparison_aliases") or []) if str(item or "").strip()]
            must_include_any = [str(item).strip() for item in list(claim.get("must_include_any") or []) if str(item or "").strip()]
            avoid_confusions = [str(item).strip() for item in list(claim.get("avoid_confusions") or []) if str(item or "").strip()]
            positive_context_terms = [str(item).strip() for item in list(claim.get("positive_context_terms") or []) if str(item or "").strip()]
            negative_context_terms = [str(item).strip() for item in list(claim.get("negative_context_terms") or []) if str(item or "").strip()]
        else:
            claim_text = str(claim or "").strip()
            profile_query = ""
            keywords = []
            preferred_sections = []
            filters = {}
            comparison_group = False
            comparison_object = ""
            comparison_aliases = []
            must_include_any = []
            avoid_confusions = []
            positive_context_terms = []
            negative_context_terms = []

        claim_key = claim_text or f"claim_{index}"
        query_guardrail_details = {
            "injected_keywords": [],
            "injected_entities": [],
            "query_focus_terms": list(focus_for_injection),
        }
        combined_query = ""
        embedding_query = ""
        ai_query_ms = 0.0
        query_expansion_ms = 0.0
        search_total_ms = 0.0
        relevance_validation_ms = 0.0

        try:
            ai_query_started_at = time.monotonic()
            ai_generated_query = _generate_ai_query(
                client=client,
                model=model,
                chat_lane_pool=chat_lane_pool,
                chat_gate=chat_gate,
                chat_gate_limit=chat_gate_limit,
                trace_label=f"claim_{index}",
                logger=logger,
                should_cancel=cancel_check,
                normalized_user_question=normalized_user_question,
                claim_text=claim_text,
                keywords=keywords,
                preferred_sections=preferred_sections,
                filters=filters,
                entity_lock_enabled=toggles.entity_lock_enabled,
            )
            ai_query_ms = (time.monotonic() - ai_query_started_at) * 1000.0
            if ai_generated_query:
                combined_query = preprocess_fn(ai_generated_query)
                logger.info("[%s/%s] AI生成检索查询: %s...", index, len(claims), combined_query[:200])
        except Stage2UpstreamGateCancelled:
            return {
                "index": index,
                "claim_key": claim_key,
                "documents": [],
                "metadatas": [],
                "distances": [],
                "query": combined_query,
                "embedding_query": embedding_query,
                "query_guardrail": query_guardrail_details,
                "rerank": {},
                "relevance_validation": {"before": 0, "after": 0},
                "timing": {
                    "ai_query_ms": ai_query_ms,
                    "query_expansion_ms": query_expansion_ms,
                    "search_total_ms": search_total_ms,
                    "relevance_validation_ms": relevance_validation_ms,
                    "claim_total_ms": (time.monotonic() - claim_started_at) * 1000.0,
                },
                "ok": False,
                "cancelled": True,
            }
        except Exception as exc:
            raise_if_upstream_pool_timeout(exc)
            logger.warning("AI查询生成失败，使用传统方法: %s", exc)

        if not combined_query:
            if profile_query:
                combined_query = preprocess_fn(profile_query)
            elif keywords:
                combined_query = preprocess_fn(f"{' '.join(keywords)} {claim_text}".strip())
            else:
                combined_query = preprocess_fn(claim_text)
            logger.info("[%s/%s] 回退到传统查询: %s...", index, len(claims), combined_query[:200])

        if query_expansion_enabled and expand_query_fn is not None:
            try:
                query_expansion_started_at = time.monotonic()
                expanded_query = str(expand_query_fn(combined_query) or "").strip()
                query_expansion_ms = (time.monotonic() - query_expansion_started_at) * 1000.0
                if expanded_query:
                    combined_query = preprocess_fn(expanded_query)
                    if comparison_group:
                        combined_query, locked_tokens = _ensure_comparison_object_lock(
                            query=combined_query,
                            must_include_any=must_include_any,
                            preprocess_retrieval_query_fn=preprocess_fn,
                        )
                        if locked_tokens:
                            query_guardrail_details["comparison_object_lock"] = locked_tokens
                    logger.info("[%s/%s] 查询扩展后: %s...", index, len(claims), combined_query[:200])
            except Exception as exc:
                raise_if_upstream_pool_timeout(exc)
                logger.warning("查询扩展失败，保持原查询: %s", exc)

        combined_query, query_guardrail_details = apply_stage2_query_constraints(
            query=combined_query,
            user_question=normalized_user_question,
            claim_keywords=keywords,
            preprocess_retrieval_query_fn=preprocess_fn,
            toggles=toggles,
            extract_question_keywords_fn=extract_keywords_fn,
        )
        query_guardrail_details["query_focus_terms"] = list(focus_for_injection)
        combined_query = merge_graph_hints_into_retrieval(
            query=combined_query,
            preprocess_retrieval_query_fn=preprocess_fn,
            graph_evidence=graph_evidence,
        )
        if comparison_group:
            combined_query, locked_tokens = _ensure_comparison_object_lock(
                query=combined_query,
                must_include_any=must_include_any,
                preprocess_retrieval_query_fn=preprocess_fn,
            )
            if locked_tokens:
                query_guardrail_details["comparison_object_lock"] = locked_tokens
        if (
            query_guardrail_details["injected_keywords"]
            or query_guardrail_details["injected_entities"]
            or query_guardrail_details.get("query_focus_terms")
        ):
            logger.info(
                "[%s/%s] 查询约束生效: injected_keywords=%s injected_entities=%s query_focus_terms=%s",
                index,
                len(claims),
                query_guardrail_details["injected_keywords"],
                query_guardrail_details["injected_entities"],
                query_guardrail_details.get("query_focus_terms") or [],
            )

        must_include: List[str] = []
        must_include.extend(str(t) for t in focus_for_injection if str(t or "").strip())
        must_include.extend(
            str(x) for x in (query_guardrail_details.get("injected_keywords") or []) if str(x or "").strip()
        )
        must_include.extend(
            str(x) for x in (query_guardrail_details.get("injected_entities") or []) if str(x or "").strip()
        )
        lock_extra = query_guardrail_details.get("comparison_object_lock")
        if isinstance(lock_extra, list):
            must_include.extend(str(x) for x in lock_extra if str(x or "").strip())

        max_kw = env_int("QA_STAGE2_EMBEDDING_QUERY_MAX_KEYWORDS", 15, minimum=4, maximum=48)
        max_inj = env_int("QA_STAGE2_EMBEDDING_QUERY_MAX_INJECTION_SLOTS", 0, minimum=0, maximum=64)
        embedding_query = finalize_retrieval_keywords_for_embedding(
            combined_query,
            must_include,
            max_keywords=max_kw,
            max_injection_slots=max_inj if max_inj > 0 else None,
            logger=logger,
        )
        if env_bool("QA_STAGE2_EMBEDDING_QUERY_LLM_REFINE_ENABLED", False):
            # Hook for optional LLM-based compression; disabled by default for determinism.
            pass

        try:
            search_started_at = time.monotonic()
            raw_results = _search_with_optional_rerank(
                literature_expert=literature_expert,
                combined_query=embedding_query,
                n_results=max(n_results_per_claim * 3, 8),
                toggles=toggles,
                logger=logger,
                trace_label=f"claim_{index}",
                rerank_gate=rerank_gate,
                rerank_gate_limit=rerank_gate_limit,
                should_cancel=cancel_check,
            )
            search_total_ms = (time.monotonic() - search_started_at) * 1000.0
            before_count = len(list(raw_results.get("documents") or []))
            rerank_meta = dict(raw_results.get("rerank") or {})
            relevance_started_at = time.monotonic()
            validated_results = (
                validate_fn(raw_results, embedding_query, claim_text) if raw_results and "documents" in raw_results else raw_results
            )
            relevance_validation_ms = (time.monotonic() - relevance_started_at) * 1000.0
            documents = list(validated_results.get("documents") or [])
            metadatas = list(validated_results.get("metadatas") or [])
            distances = list(validated_results.get("distances") or [])
            noise_filter = {
                "enabled": False,
                "before": len(documents),
                "after": len(documents),
                "reason": "disabled_stage2_preserve_rerank_candidates" if comparison_group else "not_comparison_group",
            }
            after_count = len(documents)
            claim_total_ms = (time.monotonic() - claim_started_at) * 1000.0
            logger.info(
                "[%s/%s] 检索完成: hits_before=%s hits_after=%s rerank_enabled=%s rerank_applied=%s rerank_fallback=%s rerank_reason=%s rerank_provider=%s",
                index,
                len(claims),
                before_count,
                after_count,
                int(bool(rerank_meta.get("enabled"))),
                int(bool(rerank_meta.get("applied"))),
                int(bool(rerank_meta.get("fallback"))),
                str(rerank_meta.get("reason") or ""),
                str(rerank_meta.get("provider") or ""),
            )
            logger.info(
                "stage2 claim timing claim=%s trace_label=claim_%s ai_query_ms=%.2f query_expansion_ms=%.2f search_total_ms=%.2f relevance_validation_ms=%.2f claim_total_ms=%.2f query_chars=%s hits_before=%s hits_after=%s",
                claim_key[:120],
                index,
                ai_query_ms,
                query_expansion_ms,
                search_total_ms,
                relevance_validation_ms,
                claim_total_ms,
                len(embedding_query),
                before_count,
                after_count,
            )
            return {
                "index": index,
                "claim_key": claim_key,
                "documents": documents,
                "metadatas": metadatas,
                "distances": distances,
                "query": combined_query,
                "embedding_query": embedding_query,
                "query_guardrail": query_guardrail_details,
                "rerank": rerank_meta,
                "relevance_validation": {"before": before_count, "after": after_count},
                "comparison_group": comparison_group,
                "comparison_object": comparison_object,
                "comparison_aliases": comparison_aliases,
                "must_include_any": must_include_any,
                "avoid_confusions": avoid_confusions,
                "positive_context_terms": positive_context_terms,
                "negative_context_terms": negative_context_terms,
                "noise_filter": noise_filter,
                "timing": {
                    "ai_query_ms": ai_query_ms,
                    "query_expansion_ms": query_expansion_ms,
                    "search_total_ms": search_total_ms,
                    "relevance_validation_ms": relevance_validation_ms,
                    "claim_total_ms": claim_total_ms,
                },
                "ok": True,
            }
        except Stage2UpstreamGateCancelled:
            return {
                "index": index,
                "claim_key": claim_key,
                "documents": [],
                "metadatas": [],
                "distances": [],
                "query": combined_query,
                "embedding_query": embedding_query,
                "query_guardrail": query_guardrail_details,
                "rerank": {},
                "relevance_validation": {"before": 0, "after": 0},
                "timing": {
                    "ai_query_ms": ai_query_ms,
                    "query_expansion_ms": query_expansion_ms,
                    "search_total_ms": search_total_ms,
                    "relevance_validation_ms": relevance_validation_ms,
                    "claim_total_ms": (time.monotonic() - claim_started_at) * 1000.0,
                },
                "ok": False,
                "cancelled": True,
            }
        except Exception as exc:
            raise_if_upstream_pool_timeout(exc)
            claim_total_ms = (time.monotonic() - claim_started_at) * 1000.0
            logger.info(
                "stage2 claim timing claim=%s trace_label=claim_%s ai_query_ms=%.2f query_expansion_ms=%.2f search_total_ms=%.2f relevance_validation_ms=%.2f claim_total_ms=%.2f status=error error=%s",
                claim_key[:120],
                index,
                ai_query_ms,
                query_expansion_ms,
                search_total_ms,
                relevance_validation_ms,
                claim_total_ms,
                exc,
            )
            logger.warning("[%s/%s] 检索失败: %s", index, len(claims), exc)
            return {
                "index": index,
                "claim_key": claim_key,
                "documents": [],
                "metadatas": [],
                "distances": [],
                "query": combined_query,
                "embedding_query": embedding_query,
                "query_guardrail": query_guardrail_details,
                "rerank": {},
                "relevance_validation": {"before": 0, "after": 0},
                "timing": {
                    "ai_query_ms": ai_query_ms,
                    "query_expansion_ms": query_expansion_ms,
                    "search_total_ms": search_total_ms,
                    "relevance_validation_ms": relevance_validation_ms,
                    "claim_total_ms": claim_total_ms,
                },
                "ok": False,
                "error": str(exc),
            }

    claim_jobs = list(enumerate(claims, 1))
    claim_outputs: List[Dict[str, Any]] = []
    if len(claim_jobs) <= 1 or parallel_workers <= 1:
        for index, claim in claim_jobs:
            if _cancelled():
                logger.info("🛑 Stage2 串行检索已取消")
                break
            claim_outputs.append(_process_claim(index, claim))
    else:
        max_workers = min(parallel_workers, len(claim_jobs))
        cancelled_early = False
        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            future_map = {executor.submit(_process_claim, index, claim): index for index, claim in claim_jobs}
            pending = set(future_map)
            while pending:
                if _cancelled():
                    logger.info("🛑 Stage2 并行检索已取消，回收未完成任务")
                    cancelled_early = True
                    for future in pending:
                        future.cancel()
                    return {
                        "success": False,
                        "cancelled": True,
                        "documents": [],
                        "metadatas": [],
                        "distances": [],
                        "claim_to_results": {},
                        "unique_count": 0,
                        "total_count": 0,
                    }
                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    idx = future_map[future]
                    try:
                        claim_outputs.append(future.result())
                    except Exception as exc:
                        raise_if_upstream_pool_timeout(exc)
                        logger.warning("[%s/%s] 并行任务异常: %s", idx, len(claims), exc)
        finally:
            executor.shutdown(wait=not cancelled_early, cancel_futures=cancelled_early)

    if _cancelled():
        logger.info("🛑 Stage2 结果聚合前已取消")
        return {
            "success": False,
            "cancelled": True,
            "documents": [],
            "metadatas": [],
            "distances": [],
            "claim_to_results": {},
            "unique_count": 0,
            "total_count": 0,
        }

    claim_to_results: dict[str, dict[str, Any]] = {}
    all_documents: list[Any] = []
    all_metadatas: list[Any] = []
    all_distances: list[Any] = []
    for output in sorted(claim_outputs, key=lambda item: int(item.get("index", 0))):
        if not output.get("ok"):
            continue
        claim_to_results[output["claim_key"]] = {
            "documents": list(output["documents"]),
            "metadatas": list(output["metadatas"]),
            "distances": list(output["distances"]),
            "query": str(output["query"]),
            "embedding_query": str(output.get("embedding_query") or ""),
            "query_guardrail": dict(output.get("query_guardrail") or {}),
            "rerank": dict(output.get("rerank") or {}),
            "relevance_validation": dict(output.get("relevance_validation") or {}),
            "noise_filter": dict(output.get("noise_filter") or {}),
        }
        if output.get("comparison_group"):
            claim_to_results[output["claim_key"]]["comparison_object"] = str(output.get("comparison_object") or "")
        all_documents.extend(output["documents"])
        all_metadatas.extend(output["metadatas"])
        all_distances.extend(output["distances"])

    unique_indices: list[int] = []
    seen_contents: set[str] = set()
    for index, document in enumerate(all_documents):
        content_key = str(document or "")[:200]
        if content_key in seen_contents:
            continue
        seen_contents.add(content_key)
        unique_indices.append(index)

    unique_documents = [all_documents[index] for index in unique_indices]
    unique_metadatas = [all_metadatas[index] for index in unique_indices]
    unique_distances = [all_distances[index] for index in unique_indices]

    rerank_summary = {
        "enabled_claims": sum(1 for output in claim_outputs if bool((output.get("rerank") or {}).get("enabled"))),
        "applied_claims": sum(1 for output in claim_outputs if bool((output.get("rerank") or {}).get("applied"))),
        "fallback_claims": sum(1 for output in claim_outputs if bool((output.get("rerank") or {}).get("fallback"))),
    }
    completed_outputs = [output for output in claim_outputs if output.get("ok")]
    if completed_outputs:
        slowest_output = max(
            completed_outputs,
            key=lambda item: float(dict(item.get("timing") or {}).get("claim_total_ms") or 0.0),
        )
        avg_ai_query_ms = sum(float(dict(item.get("timing") or {}).get("ai_query_ms") or 0.0) for item in completed_outputs) / len(completed_outputs)
        avg_search_ms = sum(float(dict(item.get("timing") or {}).get("search_total_ms") or 0.0) for item in completed_outputs) / len(completed_outputs)
        avg_validation_ms = sum(
            float(dict(item.get("timing") or {}).get("relevance_validation_ms") or 0.0) for item in completed_outputs
        ) / len(completed_outputs)
        avg_claim_total_ms = sum(
            float(dict(item.get("timing") or {}).get("claim_total_ms") or 0.0) for item in completed_outputs
        ) / len(completed_outputs)
        logger.info(
            "Stage2 timing summary: claim_count=%s slowest_claim=%s slowest_claim_ms=%.2f avg_ai_query_ms=%.2f avg_search_ms=%.2f avg_relevance_validation_ms=%.2f avg_claim_total_ms=%.2f",
            len(completed_outputs),
            str(slowest_output.get("claim_key") or "")[:120],
            float(dict(slowest_output.get("timing") or {}).get("claim_total_ms") or 0.0),
            avg_ai_query_ms,
            avg_search_ms,
            avg_validation_ms,
            avg_claim_total_ms,
        )
    logger.info(
        "Stage2 汇总: claims=%s unique_docs=%s total_docs=%s rerank_enabled_claims=%s rerank_applied_claims=%s rerank_fallback_claims=%s",
        len(claim_to_results),
        len(unique_documents),
        len(all_documents),
        rerank_summary["enabled_claims"],
        rerank_summary["applied_claims"],
        rerank_summary["fallback_claims"],
    )

    result = {
        "success": True,
        "documents": unique_documents,
        "metadatas": unique_metadatas,
        "distances": unique_distances,
        "claim_to_results": claim_to_results,
        "comparison_plan": comparison_plan if comparison_enabled else None,
        "comparison_groups": _build_comparison_groups(
            comparison_plan=comparison_plan if comparison_enabled else None,
            claim_outputs=claim_outputs,
        ),
        "unique_count": len(unique_documents),
        "total_count": len(all_documents),
    }
    logger.info("✅ 检索完成：共找到 %s 个片段，去重后 %s 个", len(all_documents), len(unique_documents))
    return result


__all__ = [
    "Stage2RuntimeToggles",
    "apply_stage2_query_constraints",
    "extract_critical_entity_groups",
    "normalize_user_question_for_stage2",
    "resolve_stage2_parallel_workers",
    "resolve_stage2_runtime_toggles",
    "resolve_stage2_upstream_gate_limit",
    "run_single_claim_retrieval",
    "run_stage2_targeted_retrieval",
    "select_force_keywords",
]
