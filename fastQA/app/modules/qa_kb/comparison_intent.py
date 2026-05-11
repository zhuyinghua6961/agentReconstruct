from __future__ import annotations

import re
import json
from typing import Any


_DEFAULT_DIMENSIONS = ["成本", "储存稳定性", "反应路径", "还原需求", "杂质风险", "电化学表现"]

_ALIAS_TABLE: dict[str, list[str]] = {
    "磷酸铁": ["FePO4", "iron phosphate"],
    "草酸亚铁": ["FeC2O4", "ferrous oxalate"],
    "铁红": ["Fe2O3", "hematite", "red iron oxide", "酸洗铁红"],
    "固相法": ["solid-state", "solid state", "solid-state synthesis"],
    "水热法": ["hydrothermal", "hydrothermal synthesis"],
    "溶胶凝胶法": ["sol-gel", "sol gel", "sol-gel method"],
    "葡萄糖": ["glucose"],
    "蔗糖": ["sucrose"],
    "柠檬酸": ["citric acid"],
    "碳酸锂": ["Li2CO3", "lithium carbonate"],
    "氢氧化锂": ["LiOH", "lithium hydroxide"],
    "磷酸锂": ["Li3PO4", "lithium phosphate"],
}

_AVOID_CONFUSIONS: dict[str, list[str]] = {
    "磷酸铁": ["磷酸铁锂"],
}

_COMPARISON_TRIGGERS = (
    "优劣势",
    "优缺点",
    "优势",
    "劣势",
    "区别",
    "差异",
    "对比",
    "比较",
    "分别",
    "各有什么",
    "适用什么场景",
)

_CONTEXT_WORDS = (
    "磷酸铁锂",
    "LFP",
    "LiFePO4",
    "制备",
    "粉体",
    "过程中",
    "原料",
    "作为",
    "碳源",
    "还原剂",
)

COMPARISON_RETRIEVAL_PROFILE_PROMPT = """你是 RAG 检索规划器，不回答用户问题。

任务：根据用户问题和已识别的对比对象，生成结构化检索 profile，用于向量数据库检索。

检索环境：
- 当前系统首先检索 LFP/电池材料论文摘要与总结向量库，随后可扩展到 MD/PDF 原文片段。
- 语料包含合成制备、掺杂改性、碳包覆、电化学性能、回收再生、废水/分离、资源化利用、储能应用等主题。
- 你需要根据用户意图动态区分目标主题和干扰主题。不要固定排除某个词：如果用户问回收，recycling/spent battery/recovery 应是正向主题；如果用户问制备原料，它们通常是负向噪声。

输出要求：
1. 只输出 JSON 对象，不要输出解释。
2. 不要生成答案或事实结论。
3. 每个 retrieval query 应短而具体，适合向量检索。
4. 每个对象必须保留 label，并尽量补充英文名/化学式别名。
5. negative_context_terms 只放与当前用户意图相反的噪声主题。

JSON 格式：
{{
  "enabled": true,
  "intent": "comparison",
  "objects": [
    {{
      "label": "对象名",
      "aliases": ["同义词/英文/化学式"],
      "retrieval_queries": ["面向检索的短查询"],
      "must_include_any": ["必须命中的核心实体"],
      "positive_context_terms": ["希望同现的上下文词"],
      "negative_context_terms": ["当前问题下应排除或降权的噪声主题"]
    }}
  ]
}}

用户问题：
{user_question}

已识别对比计划：
{comparison_plan}

Stage1 初始 claims：
{retrieval_claims}
"""


def _dedupe(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _clean_string_list(values: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(values, list):
        return []
    return _dedupe([str(item).strip() for item in values if str(item).strip()])[:limit]


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _is_comparison_question(question: str) -> bool:
    text = str(question or "")
    if not text:
        return False
    if not any(trigger in text for trigger in _COMPARISON_TRIGGERS):
        return False
    return "、" in text or "，" in text or "," in text or "和" in text


def _strip_object_noise(value: str) -> str:
    text = str(value or "").strip(" ？?，,。；;：:")
    text = re.sub(r"^(以|用|采用|基于)", "", text)
    text = re.sub(r"(为原料|作为原料|作为碳源|作为还原剂|制备.*|各有什么.*|有什么.*|的.*)$", "", text)
    text = text.strip(" ？?，,。；;：:")
    return text


def _extract_known_objects(question: str) -> list[str]:
    text = str(question or "")
    positions: list[tuple[int, str]] = []
    for label in _ALIAS_TABLE:
        index = text.find(label)
        if index >= 0:
            positions.append((index, label))
    positions.sort(key=lambda item: item[0])
    labels = [label for _, label in positions]
    return _dedupe(labels)


def _extract_delimited_objects(question: str) -> list[str]:
    text = str(question or "")
    prefix_match = re.search(r"(?:以|用|采用|基于)?(.+?)(?:各有什么|有什么区别|有什么差异|进行对比|对比|比较)", text)
    segment = prefix_match.group(1) if prefix_match else text
    raw_parts = re.split(r"[、,/，]|和", segment)
    candidates: list[str] = []
    for part in raw_parts:
        cleaned = _strip_object_noise(part)
        if not cleaned or len(cleaned) > 12:
            continue
        if cleaned in _CONTEXT_WORDS:
            continue
        if any(word in cleaned for word in ("问题", "过程中")):
            continue
        candidates.append(cleaned)
    return _dedupe(candidates)


def _extract_objects(question: str) -> list[str]:
    known = _extract_known_objects(question)
    if len(known) >= 2:
        return known
    delimited = _extract_delimited_objects(question)
    if len(delimited) >= 2:
        return delimited
    return known


def _extract_context_keywords(question: str, retrieval_claims: list[dict[str, Any]]) -> list[str]:
    keywords: list[str] = []
    text = str(question or "")
    for word in _CONTEXT_WORDS:
        if word in text:
            keywords.append(word)
    for claim in retrieval_claims:
        if not isinstance(claim, dict):
            continue
        keywords.extend(str(item).strip() for item in list(claim.get("keywords") or []) if str(item).strip())
    return _dedupe(keywords)


def build_comparison_plan(
    question: str,
    *,
    stage1_result: dict[str, Any] | None = None,
    retrieval_claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    claims = list(retrieval_claims or [])
    if not claims and isinstance(stage1_result, dict):
        raw_claims = stage1_result.get("retrieval_claims")
        claims = [item for item in list(raw_claims or []) if isinstance(item, dict)]

    objects = _extract_objects(question)
    enabled = _is_comparison_question(question) and len(objects) >= 2
    if not enabled:
        return {"enabled": False, "task_type": "", "objects": [], "dimensions": [], "context_keywords": []}

    plan_objects: list[dict[str, Any]] = []
    for label in objects[:6]:
        aliases = _ALIAS_TABLE.get(label, [])
        must_include_any = _dedupe([label, *aliases])
        plan_objects.append(
            {
                "label": label,
                "aliases": list(aliases),
                "must_include_any": must_include_any,
                "avoid_confusions": list(_AVOID_CONFUSIONS.get(label, [])),
            }
        )

    return {
        "enabled": True,
        "task_type": "multi_object_comparison",
        "objects": plan_objects,
        "dimensions": list(_DEFAULT_DIMENSIONS),
        "context_keywords": _extract_context_keywords(question, claims),
        "min_docs_per_object": 3,
        "min_md_chunks_per_object": 2,
    }


def normalize_comparison_retrieval_profile(
    *,
    base_plan: dict[str, Any],
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(base_plan, dict) or not base_plan.get("enabled"):
        return base_plan
    if not isinstance(profile, dict) or not profile.get("enabled"):
        return base_plan

    by_label = {
        str(item.get("label") or "").strip(): item
        for item in list(profile.get("objects") or [])
        if isinstance(item, dict) and str(item.get("label") or "").strip()
    }
    if not by_label:
        return base_plan

    next_plan = dict(base_plan)
    next_objects: list[dict[str, Any]] = []
    changed = False
    for item in list(base_plan.get("objects") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        merged = dict(item)
        incoming = by_label.get(label)
        if incoming:
            aliases = _clean_string_list(incoming.get("aliases"), limit=12)
            retrieval_queries = _clean_string_list(incoming.get("retrieval_queries"), limit=6)
            must_include_any = _clean_string_list(incoming.get("must_include_any"), limit=12)
            positive_context_terms = _clean_string_list(incoming.get("positive_context_terms"), limit=12)
            negative_context_terms = _clean_string_list(incoming.get("negative_context_terms"), limit=12)
            if aliases:
                merged["aliases"] = _dedupe([*list(merged.get("aliases") or []), *aliases])
            if retrieval_queries:
                merged["retrieval_queries"] = retrieval_queries
            if must_include_any:
                merged["must_include_any"] = _dedupe([label, *must_include_any])
            if positive_context_terms:
                merged["positive_context_terms"] = positive_context_terms
            if negative_context_terms:
                merged["negative_context_terms"] = negative_context_terms
            changed = True
        next_objects.append(merged)
    if not changed:
        return base_plan
    next_plan["objects"] = next_objects
    next_plan["profile_source"] = "llm"
    return next_plan


def generate_comparison_retrieval_profile(
    *,
    user_question: str,
    comparison_plan: dict[str, Any],
    retrieval_claims: list[dict[str, Any]],
    client: Any,
    model: str,
    logger: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(comparison_plan, dict) or not comparison_plan.get("enabled"):
        return comparison_plan
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是严谨的 RAG 检索规划器，只输出 JSON。"},
                {
                    "role": "user",
                    "content": COMPARISON_RETRIEVAL_PROFILE_PROMPT.format(
                        user_question=str(user_question or ""),
                        comparison_plan=json.dumps(comparison_plan, ensure_ascii=False),
                        retrieval_claims=json.dumps(retrieval_claims or [], ensure_ascii=False),
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=1200,
            stream=False,
        )
        raw = str(response.choices[0].message.content or "").strip()
        profile = _extract_json_object(raw)
        return normalize_comparison_retrieval_profile(base_plan=comparison_plan, profile=profile)
    except Exception as exc:
        if logger is not None:
            try:
                logger.warning("comparison retrieval profile generation failed: %s", exc)
            except Exception:
                pass
        return comparison_plan


def build_retrieval_claims_from_comparison_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(plan, dict) or not plan.get("enabled"):
        return []
    context_keywords = [str(item).strip() for item in list(plan.get("context_keywords") or []) if str(item).strip()]
    dimensions = [str(item).strip() for item in list(plan.get("dimensions") or []) if str(item).strip()]
    claims: list[dict[str, Any]] = []
    for item in list(plan.get("objects") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        aliases = [str(alias).strip() for alias in list(item.get("aliases") or []) if str(alias).strip()]
        retrieval_queries = [str(query).strip() for query in list(item.get("retrieval_queries") or []) if str(query).strip()]
        positive_context_terms = [str(term).strip() for term in list(item.get("positive_context_terms") or []) if str(term).strip()]
        negative_context_terms = [str(term).strip() for term in list(item.get("negative_context_terms") or []) if str(term).strip()]
        keywords = _dedupe([*context_keywords, label, *aliases, *dimensions])
        claims.append(
            {
                "claim": f"比较对象“{label}”在当前问题中的优势、劣势、适用场景和限制",
                "query": retrieval_queries[0] if retrieval_queries else "",
                "retrieval_queries": retrieval_queries,
                "keywords": keywords,
                "comparison_group": True,
                "comparison_object": label,
                "comparison_aliases": aliases,
                "must_include_any": list(item.get("must_include_any") or [label]),
                "avoid_confusions": list(item.get("avoid_confusions") or []),
                "positive_context_terms": positive_context_terms,
                "negative_context_terms": negative_context_terms,
            }
        )
    return claims


__all__ = [
    "build_comparison_plan",
    "build_retrieval_claims_from_comparison_plan",
    "generate_comparison_retrieval_profile",
    "normalize_comparison_retrieval_profile",
]
