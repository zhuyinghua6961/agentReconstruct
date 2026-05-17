from __future__ import annotations

import os
import re
from typing import Any, Iterable


def normalize_chemical_notation(query: str) -> str:
    if not query:
        return query

    chemical_mappings = {
        "fe2p": "Fe2P",
        "fe2p2o7": "Fe2P2O7",
        "li4p2o7": "Li4P2O7",
        "fe2o3": "Fe2O3",
        "feo": "FeO",
        "fe3o4": "Fe3O4",
        "γ-fe2o3": "γ-Fe2O3",
        "α-fe2o3": "α-Fe2O3",
        "lifepo4": "LiFePO4",
        "lfp": "LFP",
        "li2co3": "Li2CO3",
        "nh4h2po4": "NH4H2PO4",
        "fec2o4": "FeC2O4",
    }

    result = query.lower()
    for lower_case, proper_case in chemical_mappings.items():
        result = re.sub(r"\b" + re.escape(lower_case) + r"\b", proper_case, result, flags=re.IGNORECASE)
    return result


def _is_kb_slot_noise_token(kw: str) -> bool:
    """Strip graph/KB slot artifacts from retrieval keyword streams."""
    low = str(kw or "").lower()
    if "_null" in low or "null_null" in low:
        return True
    normalized = low.replace(" ", "_").replace("-", "_")
    if "not_specified" in normalized:
        return True
    return False


def _clean_retrieval_token(kw: str, *, max_len: int = 20) -> str | None:
    kw = str(kw or "").strip()
    if not kw or len(kw) > max_len:
        return None
    if _is_kb_slot_noise_token(kw):
        return None
    kw = re.sub(r"[^\w\u4e00-\u9fff°C°:/\\-]", "", kw, flags=re.UNICODE)
    if not kw or kw in {"的", "和", "与", "或", "等", "中", "在", "于", "对", "由"}:
        return None
    return kw


def collect_retrieval_query_keywords(query: str) -> list[str]:
    """Normalize, expand synonyms, split into ordered unique keyword tokens (no length cap)."""
    if not query:
        return []

    query = normalize_chemical_notation(query)
    synonyms = {
        "PEG": "聚乙二醇",
        "LiFePO4": "磷酸铁锂 LFP",
        "LFP": "LiFePO4 磷酸铁锂",
        "磷酸铁锂": "LiFePO4 LFP",
        "PVDF": "聚偏氟乙烯",
        "NMP": "N-甲基吡咯烷酮",
        "CMC": "羧甲基纤维素钠",
        "SBR": "丁苯橡胶",
        "SP": "导电炭黑 Super P",
        "Super P": "导电炭黑 SP",
        "聚乙二醇": "PEG",
        "聚偏氟乙烯": "PVDF",
        "N-甲基吡咯烷酮": "NMP",
        "羧甲基纤维素钠": "CMC",
        "丁苯橡胶": "SBR",
        "导电炭黑": "SP Super P",
    }

    extensions: set[str] = set()
    for abbr, synonym in synonyms.items():
        pattern = r"\b" + re.escape(abbr) + r"\b"
        if re.search(pattern, query, flags=re.IGNORECASE):
            for item in synonym.split():
                extensions.add(item)

    if extensions:
        query = f"{query} {' '.join(sorted(extensions))}"

    query = re.sub(r"\(|\)|OR|AND|\"", "", query)
    query = re.sub(r"[;,.。；，、]", " ", query)

    cleaned_keywords: list[str] = []
    for kw in query.split():
        ck = _clean_retrieval_token(kw, max_len=20)
        if ck:
            cleaned_keywords.append(ck)

    unique_keywords: list[str] = []
    seen: set[str] = set()
    for kw in cleaned_keywords:
        if kw in seen:
            continue
        seen.add(kw)
        unique_keywords.append(kw)
    return unique_keywords


def finalize_retrieval_keywords_for_embedding(
    combined_query: str,
    must_include: Iterable[str],
    *,
    max_keywords: int | None = None,
    max_injection_slots: int | None = None,
    logger: Any | None = None,
) -> str:
    """Build the keyword string passed to dense retrieval (must_include first, then core tokens).

    ``max_keywords`` defaults from ``QA_STAGE2_EMBEDDING_QUERY_MAX_KEYWORDS`` (default 15).
    ``max_injection_slots``: if >0, only the first N entries from ``must_include`` are forced
    at the front; if 0 or None, all non-empty must_include entries are prioritized.
    """
    if max_keywords is None:
        try:
            max_keywords = int(str(os.getenv("QA_STAGE2_EMBEDDING_QUERY_MAX_KEYWORDS", "15")).strip())
        except ValueError:
            max_keywords = 15
    max_keywords = max(4, min(int(max_keywords), 48))

    if max_injection_slots is None:
        raw_cap = str(os.getenv("QA_STAGE2_EMBEDDING_QUERY_MAX_INJECTION_SLOTS", "0")).strip()
        try:
            max_injection_slots = int(raw_cap)
        except ValueError:
            max_injection_slots = 0

    must_list = [str(x).strip() for x in must_include if str(x or "").strip()]
    if max_injection_slots and max_injection_slots > 0:
        must_list = must_list[: max(0, int(max_injection_slots))]

    out: list[str] = []
    seen: set[str] = set()

    for m in must_list:
        ck = _clean_retrieval_token(m, max_len=48)
        if not ck or ck in seen:
            continue
        seen.add(ck)
        out.append(ck)
        if len(out) >= max_keywords:
            result = " ".join(out)
            if logger is not None:
                logger.info(
                    "stage2 embedding finalize must_only query_in=%s must=%s out=%s",
                    (combined_query or "")[:120],
                    must_list,
                    result[:120],
                )
            return result

    for ck in collect_retrieval_query_keywords(combined_query):
        if ck in seen:
            continue
        seen.add(ck)
        out.append(ck)
        if len(out) >= max_keywords:
            break

    result = " ".join(out)
    if logger is not None:
        logger.info(
            "stage2 embedding finalize query_in=%s must=%s out=%s",
            (combined_query or "")[:120],
            must_list,
            result[:120],
        )
    return result


def preprocess_retrieval_query(query: str, logger: Any | None = None) -> str:
    if not query:
        return ""

    unique_keywords = collect_retrieval_query_keywords(query)
    result = " ".join(unique_keywords[:15])
    if logger is not None:
        raw_for_log = normalize_chemical_notation(query)
        logger.info("stage2 preprocess query raw=%s result=%s", raw_for_log[:80], result[:80])
    return result


def extract_question_keywords_with_weights(question: str) -> dict[str, float]:
    if not question:
        return {}

    combo_pattern = r"([\u4e00-\u9fff]{2,6})\s*和\s*([\u4e00-\u9fff]{2,6})\s*作为"
    combo_matches = re.findall(combo_pattern, question)
    and_pattern = r"([A-Za-z]+)\s*(?:和|与|and)\s*([A-Za-z]+)"
    english_combo = re.findall(and_pattern, question, re.IGNORECASE)
    chemicals = re.findall(r"[A-Z][a-z]?\d*[A-Z]?\d*|LiFePO4|LFP|NMC|NCA|NCM", question)
    numbers = re.findall(r"\d+[:/]?\d*%?|\d+\s*(?:倍|次|轮|周|天|小时|分钟|秒|℃|°C|nm|μm|mm|cm|m|g|mg|kg|L|mL)", question)

    keywords_weight: dict[str, float] = {}
    for pattern in [r"最佳|最优|最合适|最高|最低|最大|最小|最", r"比例|配比|比率|混合比", r"多少|数值", r"如何|怎么|怎样|为什么"]:
        for match in re.findall(pattern, question):
            keywords_weight[match] = keywords_weight.get(match, 0.0) + 3.0

    for x, y in combo_matches:
        keywords_weight[x] = keywords_weight.get(x, 0.0) + 4.0
        keywords_weight[y] = keywords_weight.get(y, 0.0) + 4.0
        combo = f"{x}和{y}"
        keywords_weight[combo] = keywords_weight.get(combo, 0.0) + 5.0

    for x, y in english_combo:
        x_lower, y_lower = x.lower(), y.lower()
        if x_lower not in {"the", "and", "for", "with", "from"}:
            keywords_weight[x_lower] = keywords_weight.get(x_lower, 0.0) + 4.0
        if y_lower not in {"the", "and", "for", "with", "from"}:
            keywords_weight[y_lower] = keywords_weight.get(y_lower, 0.0) + 4.0

    cn_doping_matches = re.findall(r"([\u4e00-\u9fff]{1,3})掺杂", question)
    for element in cn_doping_matches:
        if len(element) <= 3:
            keywords_weight[element] = keywords_weight.get(element, 0.0) + 4.0
            keywords_weight[f"{element}掺杂"] = keywords_weight.get(f"{element}掺杂", 0.0) + 5.0

    en_doping_matches = re.findall(r"\b([A-Z][a-z]?)\s*(?:-| )?(?:doped|doping)\b", question, flags=re.IGNORECASE)
    for element in en_doping_matches:
        symbol = element.capitalize()
        keywords_weight[symbol] = keywords_weight.get(symbol, 0.0) + 4.0
        keywords_weight[f"{symbol} doping"] = keywords_weight.get(f"{symbol} doping", 0.0) + 5.0

    for term in [
        "碳源", "包覆", "掺杂", "碳包覆", "水热", "溶胶", "共沉淀", "固相", "温度", "时间", "压力",
        "葡萄糖", "蔗糖", "PEG", "聚乙二醇", "柠檬酸", "导电率", "电子导电率", "离子导电率", "容量", "比容量",
        "首次容量", "循环", "循环寿命", "循环稳定性", "倍率", "倍率性能", "碳热还原", "还原", "首次库仑效率",
        "库仑效率", "能量密度", "功率密度", "振实密度", "压实密度",
    ]:
        if term in question:
            keywords_weight[term] = keywords_weight.get(term, 0.0) + 2.0

    lower_q = question.lower()
    for term, weight in [
        ("rate capability", 3.0),
        ("cycle life", 3.0),
        ("initial coulombic efficiency", 3.0),
        ("ionic conductivity", 3.0),
        ("electronic conductivity", 3.0),
        ("energy density", 2.5),
        ("power density", 2.5),
        ("tap density", 2.5),
    ]:
        if term in lower_q:
            keywords_weight[term] = keywords_weight.get(term, 0.0) + weight

    for num in numbers:
        keywords_weight[num] = keywords_weight.get(num, 0.0) + 2.0
    for chem in chemicals:
        keywords_weight[chem] = keywords_weight.get(chem, 0.0) + 1.0

    stopwords = {
        "什么", "如何", "怎样", "多少", "哪些", "为什么", "怎么", "哪个", "那些", "这个", "那个",
        "的是", "了", "它", "它们", "其", "其他", "有关", "关于", "具有", "作为", "使用", "采用",
        "通过", "进行", "得到", "实现", "显示", "表明",
    }
    single_char_elements = {"钛", "镁", "锰", "锌", "钒", "氟", "铜", "铝", "铁", "磷", "锂"}
    return {
        key: value
        for key, value in keywords_weight.items()
        if ((len(key) >= 2) or (key in single_char_elements)) and key not in stopwords
    }


def extract_question_keywords(question: str) -> list[str]:
    keywords_with_weights = extract_question_keywords_with_weights(question)
    return sorted(keywords_with_weights.keys(), key=lambda key: keywords_with_weights[key], reverse=True)
