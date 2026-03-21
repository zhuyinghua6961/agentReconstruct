from __future__ import annotations

import re
from typing import Any


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


def preprocess_retrieval_query(query: str, logger: Any | None = None) -> str:
    if not query:
        return ""

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
        kw = kw.strip()
        if not kw or len(kw) >= 20:
            continue
        kw = re.sub(r"[^\w\u4e00-\u9fff°C°:/\\-]", "", kw, flags=re.UNICODE)
        if kw and kw not in {"的", "和", "与", "或", "等", "中", "在", "于", "对", "由"}:
            cleaned_keywords.append(kw)

    unique_keywords: list[str] = []
    seen: set[str] = set()
    for kw in cleaned_keywords:
        if kw in seen:
            continue
        seen.add(kw)
        unique_keywords.append(kw)

    result = " ".join(unique_keywords[:15])
    if logger is not None:
        logger.info("stage2 preprocess query raw=%s result=%s", query[:80], result[:80])
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
