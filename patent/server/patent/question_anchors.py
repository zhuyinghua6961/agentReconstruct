from __future__ import annotations

import os
import re
from typing import Any

from server.patent.graph_kb.slots import extract_patent_graph_slots
from server.patent.models import PatentRetrievalClaim

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")
_IDENTIFIER_RE = re.compile(r"\b(?=[A-Z0-9/.,-]*\d)[A-Z]{2}[A-Z0-9][A-Z0-9/.,-]{4,}[A-Z0-9]\b")
_METRIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mAh/g|mah/g|g/cm3|g/cm³|C-rate|C|℃|°C|%)\b",
    re.IGNORECASE,
)
_MATERIAL_ABBR_RE = re.compile(r"\b(?:LFP|LMFP|LiFePO4|NCM|NCA|LCO|LTO|PEG|PVA|CNT)\b", re.IGNORECASE)

_STOPWORDS = frozenset(
    {
        "如何",
        "什么",
        "哪些",
        "为什么",
        "是否",
        "能否",
        "可以",
        "应该",
        "请问",
        "请",
        "帮",
        "关于",
        "对于",
        "以及",
        "或者",
        "还是",
        "比较",
        "分析",
        "评估",
        "总结",
        "介绍",
        "说明",
        "解释",
        "专利",
        "制备",
        "方法",
        "工艺",
        "路线",
        "影响",
        "作用",
        "效果",
        "最佳",
        "最优",
        "多少",
        "怎样",
        "怎么",
        "的",
        "和",
        "与",
        "或",
        "等",
        "中",
        "在",
        "于",
        "对",
        "由",
        "为",
        "是",
        "有",
        "用",
        "以",
        "及",
        "其",
        "该",
        "这",
        "那",
        "一个",
        "一种",
        "进行",
        "通过",
        "使用",
        "采用",
        "具有",
        "作为",
    }
)


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def intent_anchor_extract_enabled() -> bool:
    return _env_bool("PATENT_INTENT_ANCHOR_EXTRACT_ENABLED", True)


def anchor_terms_max() -> int:
    try:
        return max(1, min(int(str(os.getenv("PATENT_INTENT_ANCHOR_MAX_TERMS", "12")).strip()), 24))
    except Exception:
        return 12


def claim_keywords_max() -> int:
    try:
        return max(4, min(int(str(os.getenv("PATENT_STAGE1_CLAIM_KEYWORDS_MAX", "12")).strip()), 24))
    except Exception:
        return 12


def _unique_terms(values: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(text)
    return terms


def _prepare_question_for_tokenization(user_question: str) -> str:
    text = str(user_question or "")
    text = re.sub(r"[、，。；：？!?,.（）()]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_anchor_token(token: str) -> str:
    text = str(token or "").strip()
    if not text:
        return ""
    for prefix in ("以", "由", "用", "和", "及", "与"):
        if text.startswith(prefix) and len(text) > len(prefix) + 1:
            text = text[len(prefix) :]
    text = re.sub(r"为(?:铁源|碳源|锂源|磷源|掺杂源)$", "", text)
    text = re.sub(r"的(?:铁源|碳源|锂源|磷源)$", "", text)
    return text.strip()


def _tokenize_question(user_question: str) -> list[str]:
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(_prepare_question_for_tokenization(user_question)):
        text = _clean_anchor_token(str(token).strip())
        if not text or text in _STOPWORDS:
            continue
        if text.isdigit():
            continue
        if len(text) == 1 and "\u4e00" <= text <= "\u9fff":
            continue
        tokens.append(text)
    return tokens


def extract_rule_based_anchor_terms(user_question: str, *, max_terms: int | None = None) -> list[str]:
    question = str(user_question or "").strip()
    if not question:
        return []
    limit = int(max_terms or anchor_terms_max())
    slots = extract_patent_graph_slots(question)
    candidates: list[str] = []
    candidates.extend(str(item).strip() for item in list(slots.patent_ids or []) if str(item).strip())
    candidates.extend(str(item).strip() for item in list(slots.material_terms or []) if str(item).strip())
    candidates.extend(str(item).strip() for item in list(slots.material_role_terms or []) if str(item).strip())
    candidates.extend(str(item).strip() for item in list(slots.process_terms or []) if str(item).strip())
    candidates.extend(str(item).strip() for item in list(slots.metric_terms or []) if str(item).strip())
    candidates.extend(str(item).strip() for item in _METRIC_RE.findall(question) if str(item).strip())
    candidates.extend(str(item).strip() for item in _MATERIAL_ABBR_RE.findall(question) if str(item).strip())
    candidates.extend(_tokenize_question(question))
    return _unique_terms(candidates)[:limit]


def normalize_anchor_term_list(values: Any, *, max_terms: int | None = None) -> list[str]:
    if isinstance(values, str):
        iterable: list[Any] = [values]
    else:
        iterable = list(values or [])
    limit = int(max_terms or anchor_terms_max())
    terms = _unique_terms([" ".join(str(item or "").split()).strip() for item in iterable])
    return [term for term in terms if term][:limit]


def resolve_question_anchor_terms(
    *,
    user_question: str,
    intent_result: dict[str, Any] | None = None,
) -> list[str]:
    limit = anchor_terms_max()
    rule_terms = extract_rule_based_anchor_terms(user_question, max_terms=limit)
    llm_terms: list[str] = []
    if isinstance(intent_result, dict) and intent_result.get("ok"):
        llm_terms = normalize_anchor_term_list(intent_result.get("anchor_terms"), max_terms=limit)
    merged = _unique_terms([*llm_terms, *rule_terms])
    return merged[:limit]


def merge_anchor_terms_into_claims(
    claims: list[PatentRetrievalClaim],
    anchor_terms: list[str],
    *,
    max_keywords_per_claim: int | None = None,
) -> list[PatentRetrievalClaim]:
    anchors = normalize_anchor_term_list(anchor_terms, max_terms=anchor_terms_max())
    if not anchors or not claims:
        return list(claims or [])
    keyword_limit = int(max_keywords_per_claim or claim_keywords_max())
    merged: list[PatentRetrievalClaim] = []
    for claim in claims:
        combined = _unique_terms([*anchors, *[str(item).strip() for item in list(claim.keywords or []) if str(item).strip()]])
        merged.append(
            PatentRetrievalClaim(
                claim=str(claim.claim or ""),
                keywords=combined[:keyword_limit],
                preferred_sections=list(claim.preferred_sections or []),
                filters=dict(claim.filters or {}),
            )
        )
    return merged


__all__ = [
    "anchor_terms_max",
    "claim_keywords_max",
    "extract_rule_based_anchor_terms",
    "intent_anchor_extract_enabled",
    "merge_anchor_terms_into_claims",
    "normalize_anchor_term_list",
    "resolve_question_anchor_terms",
]
