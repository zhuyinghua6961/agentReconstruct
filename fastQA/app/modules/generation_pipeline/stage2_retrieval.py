from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app.modules.generation_pipeline.feature_flags import env_bool, env_int
from app.modules.generation_pipeline.retrieval_validation import validate_retrieval_relevance
from app.modules.generation_pipeline.text_processing import extract_question_keywords, preprocess_retrieval_query


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
            env_bool("QA_RETRIEVAL_RERANK_ENABLED", True)
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
        constrained = " ".join(merged_prefix) + " " + query
    constrained = preprocess_retrieval_query_fn(constrained)
    return constrained, details


def _search_with_optional_rerank(
    *,
    literature_expert: Any,
    combined_query: str,
    n_results: int,
    toggles: Stage2RuntimeToggles,
) -> Dict[str, Any]:
    search_kwargs = {
        "n_results": n_results,
        "translate": False,
        "use_rerank": toggles.use_rerank,
        "rerank_candidates": toggles.rerank_candidates,
    }
    try:
        return literature_expert.search(combined_query, **search_kwargs)
    except TypeError:
        search_kwargs.pop("use_rerank", None)
        search_kwargs.pop("rerank_candidates", None)
        return literature_expert.search(combined_query, **search_kwargs)


def _generate_ai_query(
    *,
    client: Any | None,
    model: str | None,
    normalized_user_question: str,
    claim_text: str,
    keywords: list[str],
    preferred_sections: list[str],
    filters: dict[str, Any],
    entity_lock_enabled: bool,
) -> str:
    if client is None or not model:
        return ""

    sections_text = ", ".join(str(item).strip() for item in preferred_sections if str(item or "").strip()) or "无"
    filters_text = ", ".join(f"{key}={value}" for key, value in filters.items()) or "无"
    entity_guardrail_block = ""
    if entity_lock_enabled:
        entity_guardrail_block = (
            "\n【关键约束】\n"
            "- 如果用户问题中提到了具体化学元素，查询中必须保留该元素名称或符号\n"
            "- 禁止省略用户问题中明确提到的元素，否则会检索到不相关文献\n"
        )

    prompt = f"""
你是一个学术检索专家，擅长根据研究问题生成精准的文献检索查询。

【原始用户问题】
{normalized_user_question or claim_text}

【检索主张】
{claim_text}

【关键词参考】
{', '.join(keywords) if keywords else '无'}

【偏好段落】
{sections_text}

【筛选条件】
{filters_text}
{entity_guardrail_block}
【输出要求】
- 输出空格分隔的检索关键词列表
- 保留原问题中的材料体系、关键元素、数值和实验条件
- 不要输出解释性文字
- 长度控制在 40-60 字符
""".strip()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是一个学术检索专家，只输出检索查询本身，不要解释。"},
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
        if literature_expert:
            results = _search_with_optional_rerank(
                literature_expert=literature_expert,
                combined_query=final_query,
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
            "claim_index": claim_index,
        }
    except Exception as exc:
        logger.error("❌ [并行检索 %s] 失败: %s", claim_index, exc)
        return {
            "documents": [],
            "metadatas": [],
            "distances": [],
            "query": str(claim)[:50] if claim else "",
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
) -> Dict[str, Any]:
    def _cancelled() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            return False

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
    if not claims:
        return {
            "success": True,
            "documents": [],
            "metadatas": [],
            "distances": [],
            "claim_to_results": {},
            "unique_count": 0,
            "total_count": 0,
        }

    normalized_user_question = normalize_user_question_for_stage2(str(user_question or ""))
    if normalized_user_question and normalized_user_question != str(user_question or "").strip():
        logger.info("🧹 Stage2 已移除对话背景包装，仅使用当前问题进行检索约束")

    def _process_claim(index: int, claim: Any) -> Dict[str, Any]:
        if _cancelled():
            return {
                "index": index,
                "claim_key": f"claim_{index}",
                "documents": [],
                "metadatas": [],
                "distances": [],
                "query": "",
                "query_guardrail": {"injected_keywords": [], "injected_entities": []},
                "rerank": {},
                "relevance_validation": {"before": 0, "after": 0},
                "ok": False,
                "cancelled": True,
            }

        if isinstance(claim, dict):
            claim_text = str(claim.get("claim") or "").strip()
            keywords = [str(item).strip() for item in list(claim.get("keywords") or []) if str(item or "").strip()]
            preferred_sections = [str(item).strip() for item in list(claim.get("preferred_sections") or claim.get("preferred") or []) if str(item or "").strip()]
            filters = dict(claim.get("filters") or {}) if isinstance(claim.get("filters"), dict) else {}
        else:
            claim_text = str(claim or "").strip()
            keywords = []
            preferred_sections = []
            filters = {}

        claim_key = claim_text or f"claim_{index}"
        query_guardrail_details = {"injected_keywords": [], "injected_entities": []}
        combined_query = ""

        try:
            ai_generated_query = _generate_ai_query(
                client=client,
                model=model,
                normalized_user_question=normalized_user_question,
                claim_text=claim_text,
                keywords=keywords,
                preferred_sections=preferred_sections,
                filters=filters,
                entity_lock_enabled=toggles.entity_lock_enabled,
            )
            if ai_generated_query:
                combined_query = preprocess_fn(ai_generated_query)
                logger.info("[%s/%s] AI生成检索查询: %s...", index, len(claims), combined_query[:200])
        except Exception as exc:
            logger.warning("AI查询生成失败，使用传统方法: %s", exc)

        if not combined_query:
            if keywords:
                combined_query = preprocess_fn(f"{' '.join(keywords)} {claim_text}".strip())
            else:
                combined_query = preprocess_fn(claim_text)
            logger.info("[%s/%s] 回退到传统查询: %s...", index, len(claims), combined_query[:200])

        if query_expansion_enabled and expand_query_fn is not None:
            try:
                expanded_query = str(expand_query_fn(combined_query) or "").strip()
                if expanded_query:
                    combined_query = preprocess_fn(expanded_query)
                    logger.info("[%s/%s] 查询扩展后: %s...", index, len(claims), combined_query[:200])
            except Exception as exc:
                logger.warning("查询扩展失败，保持原查询: %s", exc)

        combined_query, query_guardrail_details = apply_stage2_query_constraints(
            query=combined_query,
            user_question=normalized_user_question,
            claim_keywords=keywords,
            preprocess_retrieval_query_fn=preprocess_fn,
            toggles=toggles,
            extract_question_keywords_fn=extract_keywords_fn,
        )
        if query_guardrail_details["injected_keywords"] or query_guardrail_details["injected_entities"]:
            logger.info(
                "[%s/%s] 查询约束生效: injected_keywords=%s injected_entities=%s",
                index,
                len(claims),
                query_guardrail_details["injected_keywords"],
                query_guardrail_details["injected_entities"],
            )

        try:
            raw_results = _search_with_optional_rerank(
                literature_expert=literature_expert,
                combined_query=combined_query,
                n_results=max(n_results_per_claim * 3, 8),
                toggles=toggles,
            )
            before_count = len(list(raw_results.get("documents") or []))
            rerank_meta = dict(raw_results.get("rerank") or {})
            validated_results = validate_fn(raw_results, combined_query, claim_text) if raw_results and "documents" in raw_results else raw_results
            documents = list(validated_results.get("documents") or [])
            metadatas = list(validated_results.get("metadatas") or [])
            distances = list(validated_results.get("distances") or [])
            after_count = len(documents)
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
            return {
                "index": index,
                "claim_key": claim_key,
                "documents": documents,
                "metadatas": metadatas,
                "distances": distances,
                "query": combined_query,
                "query_guardrail": query_guardrail_details,
                "rerank": rerank_meta,
                "relevance_validation": {"before": before_count, "after": after_count},
                "ok": True,
            }
        except Exception as exc:
            logger.warning("[%s/%s] 检索失败: %s", index, len(claims), exc)
            return {
                "index": index,
                "claim_key": claim_key,
                "documents": [],
                "metadatas": [],
                "distances": [],
                "query": combined_query,
                "query_guardrail": query_guardrail_details,
                "rerank": {},
                "relevance_validation": {"before": 0, "after": 0},
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
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(_process_claim, index, claim): index for index, claim in claim_jobs}
            pending = set(future_map)
            while pending:
                if _cancelled():
                    logger.info("🛑 Stage2 并行检索已取消，回收未完成任务")
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
                        logger.warning("[%s/%s] 并行任务异常: %s", idx, len(claims), exc)

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
            "query_guardrail": dict(output.get("query_guardrail") or {}),
            "rerank": dict(output.get("rerank") or {}),
            "relevance_validation": dict(output.get("relevance_validation") or {}),
        }
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
    "run_single_claim_retrieval",
    "run_stage2_targeted_retrieval",
    "select_force_keywords",
]
