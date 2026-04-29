from __future__ import annotations

import re
from typing import Any

from app.modules.graph_kb.models import GraphQuestionSlots


_DOI_RE = re.compile(r"(10\.\d{1,9}/[A-Za-z0-9._\-;()/]+)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
_TOP_K_RE = re.compile(r"(?:前\s*(\d+)|top\s*(\d+)|排名前\s*(\d+))", re.IGNORECASE)
_UNIT_RE = re.compile(r"(mA\s*h\s*g[⁻\-]?\s*1|mAh\s*/\s*g|g\s*/\s*cm[³3]|S\s*/\s*cm|%|°?\s*C\b)", re.IGNORECASE)

_DOI_EXPANSION_HINTS = ("展开", "上下文", "测试", "工艺", "原料", "配方", "设备", "context", "expand")
_COMMUNITY_HINTS = ("关系网络", "机制关联", "社区", "网络", "关联网络", "关系", "数据质量")
_ANALYSIS_HINTS = ("为什么", "如何", "影响", "分析", "趋势", "对比", "差异", "特点", "稳定", "机制", "总结", "规律")
_ENUMERATION_HINTS = ("有哪些", "哪些", "列出", "给出", "包括", "包含", "文献", "论文")
_COUNT_HINTS = ("统计", "数量", "多少篇", "几篇", "count")

_ENTITY_ALIASES = {
    "lfp": ("lfp", "lifepo4"),
    "lifepo4": ("lifepo4",),
    "li fe po4": ("lifepo4",),
    "lithium iron phosphate": ("lifepo4", "lithium iron phosphate"),
    "磷酸铁锂": ("lifepo4", "磷酸铁锂"),
    "ncm": ("ncm",),
    "三元": ("ncm", "三元"),
    "石墨": ("graphite", "石墨"),
}
_CARBON_SOURCE_ALIASES = {
    "蔗糖": ("sucrose", "蔗糖"),
    "sucrose": ("sucrose",),
    "葡萄糖": ("glucose", "葡萄糖"),
    "glucose": ("glucose",),
    "碳源": (),
}
_RECIPE_KEYWORDS = {
    "carbon_content": ("碳含量", "含碳量", "carbon content"),
    "dopant": ("掺杂剂", "dopant"),
    "doping_elements": ("掺杂元素", "doping element", "doping elements"),
    "additives": ("添加剂", "additive", "additives"),
    "ratios": ("比例", "ratio", "li/fe", "fe/p"),
}
_PROPERTY_KEYWORDS = (
    ("compaction_density", ("压实密度", "compaction density")),
    ("tap_density", ("振实密度", "tap density")),
    ("discharge_capacity", ("放电容量", "比容量", "容量", "specific capacity", "discharge capacity")),
    ("conductivity", ("电导率", "conductivity")),
    ("coulombic_efficiency", ("库伦效率", "coulombic efficiency")),
    ("capacity_retention", ("容量保持", "保持率", "循环性能", "循环", "retention")),
    ("particle_size", ("粒径", "particle size")),
    ("surface_area", ("比表面积", "surface area")),
    ("energy_density", ("能量密度", "energy density")),
    ("power_density", ("功率密度", "power density")),
)
_PROCESS_KEYWORDS = {
    "method": ("工艺", "制备", "方法", "preparation", "method"),
    "process": ("路线", "流程", "process"),
    "calcination": ("煅烧", "calcination"),
    "milling": ("球磨", "milling"),
    "sintering": ("烧结", "sintering"),
    "drying": ("干燥", "drying"),
    "temperature": ("温度", "temperature"),
    "time": ("时间", "时长", "time"),
    "atmosphere": ("气氛", "atmosphere"),
    "pressure": ("压力", "pressure"),
    "process_steps": ("步骤", "process step", "process steps"),
}
_OPERATOR_KEYWORDS = (
    (">=", ("不低于", "至少", ">=")),
    ("<=", ("不高于", "至多", "<=")),
    (">", ("超过", "大于", "高于", ">")),
    ("<", ("小于", "低于", "<")),
    ("=", ("等于", "为", "=")),
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(hint.lower() in lowered for hint in hints)


def _unique(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _extract_entities(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    entities: list[str] = []
    for alias, canonical_values in _ENTITY_ALIASES.items():
        if alias.lower() in lowered:
            entities.extend(canonical_values)
    return _unique(entities)


def _extract_carbon_sources(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    values: list[str] = []
    for alias, canonical_values in _CARBON_SOURCE_ALIASES.items():
        if alias.lower() in lowered:
            values.extend(canonical_values)
    return _unique(values)


def _extract_recipe_terms(text: str) -> dict[str, tuple[str, ...]]:
    lowered = text.lower()
    terms: dict[str, tuple[str, ...]] = {}
    carbon_sources = _extract_carbon_sources(text)
    if carbon_sources:
        terms["carbon_source"] = carbon_sources
    for key, hints in _RECIPE_KEYWORDS.items():
        matches = [hint for hint in hints if hint.lower() in lowered]
        if matches:
            terms[key] = _unique(matches)
    return terms


def _extract_property_field(text: str) -> str:
    lowered = text.lower()
    for field, hints in _PROPERTY_KEYWORDS:
        if any(hint.lower() in lowered for hint in hints):
            return field
    return ""


def _extract_operator(text: str) -> str:
    lowered = text.lower()
    for operator, hints in _OPERATOR_KEYWORDS:
        if any(hint.lower() in lowered for hint in hints):
            return operator
    return ""


def _extract_limit(text: str) -> tuple[str, int | None]:
    match = _TOP_K_RE.search(text)
    if match is None:
        return "", None
    for group in match.groups():
        if group:
            return "top", int(group)
    return "top", None


def _extract_threshold(text: str) -> float | None:
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    value = float(match.group(1))
    return int(value) if value.is_integer() else value


def _extract_unit(text: str) -> str:
    match = _UNIT_RE.search(text)
    if match is None:
        return ""
    unit = match.group(1).lower().replace(" ", "")
    if unit in {"mah/g", "mahg⁻1", "mahg-1"}:
        return "mAh/g"
    if unit in {"g/cm³", "g/cm3"}:
        return "g/cm3"
    if unit in {"c", "°c"}:
        return "C"
    return unit


def _extract_process_terms(text: str) -> dict[str, tuple[str, ...]]:
    lowered = text.lower()
    terms: dict[str, tuple[str, ...]] = {}
    for key, hints in _PROCESS_KEYWORDS.items():
        matches = [hint for hint in hints if hint.lower() in lowered]
        if matches:
            terms[key] = _unique(matches)
    return terms


def extract_graph_slots(question: str) -> GraphQuestionSlots:
    text = _text(question)
    doi_match = _DOI_RE.search(text)
    doi = doi_match.group(1).rstrip(".,;:，。；：") if doi_match else ""
    doi_intent = "expand" if doi and _contains_any(text, _DOI_EXPANSION_HINTS) else ("lookup" if doi else "")
    recipe_terms = _extract_recipe_terms(text)
    process_terms = _extract_process_terms(text)
    ranking, limit = _extract_limit(text)
    property_field = _extract_property_field(text)
    operator = _extract_operator(text)
    should_extract_numeric = bool(property_field or recipe_terms or process_terms or operator)

    return GraphQuestionSlots(
        doi=doi,
        doi_intent=doi_intent,
        entities=_extract_entities(text),
        recipe_terms=recipe_terms,
        process_terms=process_terms,
        property_field=property_field,
        operator=operator,
        threshold=_extract_threshold(text) if should_extract_numeric else None,
        unit=_extract_unit(text) if should_extract_numeric else "",
        ranking=ranking,
        limit=limit,
        community_signal=_contains_any(text, _COMMUNITY_HINTS),
        analysis_signal=_contains_any(text, _ANALYSIS_HINTS),
        enumeration_signal=_contains_any(text, _ENUMERATION_HINTS),
        count_signal=_contains_any(text, _COUNT_HINTS),
    )
