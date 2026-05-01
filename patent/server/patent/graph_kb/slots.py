from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_DOI_PATTERN = re.compile(r"10\.\d+/[A-Za-z0-9._\-()/]+", re.IGNORECASE)
_PATENT_ID_PATTERN = re.compile(r"\b((?:CN|US|WO|JP|EP|KR)[A-Z0-9]{6,})\b", re.IGNORECASE)
_IPC_FULL_PATTERN = re.compile(r"\b([A-H][0-9]{2}[A-Z][0-9]+/[0-9A-Z]+)\b", re.IGNORECASE)
_IPC_CODE_PREFIX_PATTERN = re.compile(r"\b([A-H][0-9]{2}[A-Z][0-9]+)\b(?!/)", re.IGNORECASE)
_IPC_PREFIX_PATTERN = re.compile(r"\b([A-H][0-9]{2}[A-Z])\b(?![0-9/])", re.IGNORECASE)

_APPLICANT_LISTING_RE = re.compile(
    r"^(?!(?:发明人|发明者|代理机构|专利代理机构|代理所))(?P<name>[\u4e00-\u9fffA-Za-z0-9()（）·\-.]+?)(?:有哪些专利|有多少专利|专利数量|的专利)$"
)
_INVENTOR_RE = re.compile(r"^(?:发明人|发明者)(?P<name>[\u4e00-\u9fffA-Za-z0-9()（）·\-.]+?)(?:有哪些专利|有多少专利|专利数量|的专利)$")
_AGENCY_RE = re.compile(
    r"^(?:代理机构|专利代理机构|代理所)(?P<name>[\u4e00-\u9fffA-Za-z0-9()（）·\-.]+?)(?:有哪些专利|有多少专利|专利数量|的专利)$"
)
_AGENCY_SUFFIX_RE = re.compile(
    r"^(?P<name>[\u4e00-\u9fffA-Za-z0-9()（）·\-.]+?)(?:代理了|代理)(?:哪些专利|多少专利|有多少专利|专利数量|的专利)$"
)
_LOOSE_MATERIAL_PATENT_RE = re.compile(
    r"涉及\s*(?P<term>[\u4e00-\u9fffA-Za-z0-9()（）·\-. ]{1,40}?)\s*(?:的)?专利"
)
_LOOSE_MATERIAL_EFFECT_RE = re.compile(
    r"(?P<term>[\u4e00-\u9fffA-Za-z0-9()（）·\-. ]{1,40}?)材料(?:对|的|能|会)"
)
_LOOSE_INVENTOR_RE = re.compile(r"(?:发明人|发明者)(?P<name>[\u4e00-\u9fffA-Za-z0-9()（）·\-.]{2,12})")
_KNOWN_ORGANIZATIONS = (
    "宁德时代新能源科技股份有限公司",
    "宁德时代",
    "合肥国轩高科动力能源有限公司",
    "广东邦普循环科技有限公司",
    "湖南邦普循环科技有限公司",
    "比亚迪股份有限公司",
    "中南大学",
)

_MATERIAL_TERMS = (
    "磷酸铁锂",
    "碳酸锂",
    "氢氧化锂",
    "葡萄糖",
    "蔗糖",
    "碳源",
    "锂源",
    "铁源",
    "磷源",
    "掺杂源",
    "石墨烯",
)
_MATERIAL_ROLE_TERMS = (
    "锂源",
    "铁源",
    "磷源",
    "碳源",
    "掺杂源",
    "氧化剂",
    "还原碳",
    "pH调节剂",
    "ph调节剂",
    "main",
    "auxiliary",
    "precursor",
    "additive",
)
_PROCESS_TERMS = (
    "喷雾干燥",
    "前驱体制备",
    "固液分离",
    "干燥",
    "配料混合",
    "混合",
    "煅烧",
    "烧结",
    "冷却",
    "球磨",
    "碳包覆",
)
_METRIC_TERMS = (
    "性能指标",
    "倍率性能",
    "大电流放电性能",
    "放电容量",
    "循环性能",
    "容量保持",
    "压实密度",
    "振实密度",
    "电导率",
)
_ATMOSPHERE_TERMS = ("气氛", "氮气", "空气", "惰性气氛", "氩气", "氧气", "atmosphere")
_ATTRIBUTE_VALUE_TERMS = (
    "电压范围",
    "电压",
    "比容量",
    "放电容量",
    "容量",
    "倍率性能",
    "倍率",
    "压实密度",
    "振实密度",
    "电导率",
    "循环性能",
    "循环",
    "能量密度",
    "功率密度",
    "性能",
)
_ATTRIBUTE_VALUE_QUESTION_HINTS = ("是多少", "范围是多少", "怎么样", "如何", "表现")
_COUNT_OBJECT_HINTS = ("专利", "件", "项", "申请", "授权", "公开")

_LIST_HINTS = ("有哪些", "哪些", "列出", "包括", "包含")
_COUNT_HINTS = ("有多少", "多少", "数量", "统计")
_COMPARE_HINTS = ("比较", "对比", "差异")
_RANK_HINTS = ("top", "最多", "最常见", "排名", "频率", "频次")
_RANK_PREFIX_RE = re.compile(r"(?:前\s*\d+|top\s*\d+)", re.IGNORECASE)
_LOOKUP_HINTS = ("是什么", "介绍", "概况", "信息")
_WHY_HOW_HINTS = ("为什么", "如何", "怎么", "机制", "原因", "提升", "改善", "影响")
_TREND_HINTS = ("趋势", "格局", "共性", "特点", "路线", "网络", "关联", "景观")
_FOLLOWUP_HINTS = ("它", "这个", "那件", "上面", "前者", "后者")


@dataclass(frozen=True)
class PatentGraphQuestionSlots:
    normalized_question: str
    patent_ids: tuple[str, ...] = ()
    ipc_prefixes: tuple[str, ...] = ()
    ipc_code_prefixes: tuple[str, ...] = ()
    ipc_full_codes: tuple[str, ...] = ()
    applicant_names: tuple[str, ...] = ()
    inventor_names: tuple[str, ...] = ()
    agency_names: tuple[str, ...] = ()
    material_terms: tuple[str, ...] = ()
    material_role_terms: tuple[str, ...] = ()
    process_terms: tuple[str, ...] = ()
    metric_terms: tuple[str, ...] = ()
    atmosphere_terms: tuple[str, ...] = ()
    asks_lookup: bool = False
    asks_list: bool = False
    asks_count: bool = False
    asks_compare: bool = False
    asks_rank: bool = False
    asks_process: bool = False
    asks_materials: bool = False
    asks_experiment: bool = False
    asks_problem_solution: bool = False
    asks_inventive_scope: bool = False
    asks_citation: bool = False
    asks_atmosphere: bool = False
    asks_embodiment: bool = False
    asks_why_how: bool = False
    asks_trend_landscape: bool = False
    asks_followup: bool = False
    asks_attribute_value: bool = False
    has_doi: bool = False

    def diagnostics(self) -> dict[str, Any]:
        return {
            "patent_ids": self.patent_ids,
            "ipc_prefixes": self.ipc_prefixes,
            "ipc_code_prefixes": self.ipc_code_prefixes,
            "ipc_full_codes": self.ipc_full_codes,
            "applicant_names": self.applicant_names,
            "inventor_names": self.inventor_names,
            "agency_names": self.agency_names,
            "material_terms": self.material_terms,
            "material_role_terms": self.material_role_terms,
            "process_terms": self.process_terms,
            "metric_terms": self.metric_terms,
            "atmosphere_terms": self.atmosphere_terms,
            "asks_lookup": self.asks_lookup,
            "asks_list": self.asks_list,
            "asks_count": self.asks_count,
            "asks_compare": self.asks_compare,
            "asks_rank": self.asks_rank,
            "asks_process": self.asks_process,
            "asks_materials": self.asks_materials,
            "asks_experiment": self.asks_experiment,
            "asks_problem_solution": self.asks_problem_solution,
            "asks_inventive_scope": self.asks_inventive_scope,
            "asks_citation": self.asks_citation,
            "asks_atmosphere": self.asks_atmosphere,
            "asks_embodiment": self.asks_embodiment,
            "asks_why_how": self.asks_why_how,
            "asks_trend_landscape": self.asks_trend_landscape,
            "asks_followup": self.asks_followup,
            "asks_attribute_value": self.asks_attribute_value,
            "has_doi": self.has_doi,
        }


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().rstrip("？?。.!！")


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(hint.lower() in lowered for hint in hints)


def _extract_terms(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    lowered = text.lower()
    return _unique([term for term in terms if term.lower() in lowered])


def _asks_attribute_value(text: str) -> bool:
    return _contains_any(text, _ATTRIBUTE_VALUE_TERMS) and _contains_any(text, _ATTRIBUTE_VALUE_QUESTION_HINTS)


def _asks_count_intent(text: str) -> bool:
    if "数量" in text or "统计" in text:
        return True
    if "有多少" in text and _contains_any(text, _COUNT_OBJECT_HINTS):
        return True
    if "多少" in text and _contains_any(text, _COUNT_OBJECT_HINTS):
        return True
    return False


def _regex_name(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    match = pattern.fullmatch(text)
    if match is None:
        return ()
    name = _text(match.group("name"))
    return (name,) if name else ()


def _extract_loose_inventors(text: str) -> tuple[str, ...]:
    return _unique([_text(item) for item in _LOOSE_INVENTOR_RE.findall(text)])


def _extract_known_organizations(text: str) -> tuple[str, ...]:
    return _unique([item for item in _KNOWN_ORGANIZATIONS if item in text])


def _extract_loose_material_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    role_terms = {item.lower() for item in _MATERIAL_ROLE_TERMS}
    for pattern in (_LOOSE_MATERIAL_PATENT_RE, _LOOSE_MATERIAL_EFFECT_RE):
        for match in pattern.finditer(text):
            term = _text(match.group("term"))
            if (
                not term
                or "角色" in term
                or "工艺" in term
                or "步骤" in term
                or "路线" in term
                or term.lower() in role_terms
            ):
                continue
            terms.append(term)
    return _unique(terms)


def extract_patent_graph_slots(question: str) -> PatentGraphQuestionSlots:
    text = _text(question)
    patent_ids = _unique([item.upper() for item in _PATENT_ID_PATTERN.findall(text)])
    ipc_full_codes = _unique([item.upper() for item in _IPC_FULL_PATTERN.findall(text)])
    ipc_code_prefixes = _unique([item.upper() for item in _IPC_CODE_PREFIX_PATTERN.findall(text)])
    ipc_prefixes = _unique([item.upper() for item in _IPC_PREFIX_PATTERN.findall(text)])

    material_terms = _unique(list(_extract_terms(text, _MATERIAL_TERMS)) + list(_extract_loose_material_terms(text)))
    material_role_terms = _extract_terms(text, _MATERIAL_ROLE_TERMS)
    process_terms = _extract_terms(text, _PROCESS_TERMS)
    metric_terms = _extract_terms(text, _METRIC_TERMS)
    atmosphere_terms = _extract_terms(text, _ATMOSPHERE_TERMS)

    asks_process = _contains_any(text, ("工艺", "步骤", "路线", "制备", "煅烧", "干燥", "烧结", "混合", "冷却", "process"))
    asks_materials = _contains_any(text, ("原料", "材料", "材料角色", "物料", "material"))
    asks_experiment = _contains_any(text, ("实验", "表格", "测量", "性能数据", "实验数据"))
    asks_problem_solution = _contains_any(text, ("技术问题", "技术方案", "方案", "应用场景"))
    asks_inventive_scope = _contains_any(text, ("发明点", "保护范围", "保护", "性能事实", "claim", "权利要求"))
    asks_citation = _contains_any(text, ("引用", "被引", "cites", "citation"))
    asks_atmosphere = _contains_any(text, ("气氛", "atmosphere"))
    asks_embodiment = _contains_any(text, ("实施例洞察", "实施方式洞察", "实施例结论", "洞察", "embodiment"))

    strict_applicants = _regex_name(_APPLICANT_LISTING_RE, text)
    strict_inventors = _regex_name(_INVENTOR_RE, text)
    return PatentGraphQuestionSlots(
        normalized_question=text,
        patent_ids=patent_ids,
        ipc_prefixes=ipc_prefixes,
        ipc_code_prefixes=ipc_code_prefixes,
        ipc_full_codes=ipc_full_codes,
        applicant_names=strict_applicants or _extract_known_organizations(text),
        inventor_names=strict_inventors or _extract_loose_inventors(text),
        agency_names=_regex_name(_AGENCY_RE, text) or _regex_name(_AGENCY_SUFFIX_RE, text),
        material_terms=material_terms,
        material_role_terms=material_role_terms,
        process_terms=process_terms,
        metric_terms=metric_terms,
        atmosphere_terms=atmosphere_terms,
        asks_lookup=_contains_any(text, _LOOKUP_HINTS),
        asks_list=_contains_any(text, _LIST_HINTS),
        asks_count=_asks_count_intent(text),
        asks_compare=_contains_any(text, _COMPARE_HINTS),
        asks_rank=_contains_any(text, _RANK_HINTS) or bool(_RANK_PREFIX_RE.search(text)),
        asks_process=asks_process,
        asks_materials=asks_materials,
        asks_experiment=asks_experiment,
        asks_problem_solution=asks_problem_solution,
        asks_inventive_scope=asks_inventive_scope,
        asks_citation=asks_citation,
        asks_atmosphere=asks_atmosphere,
        asks_embodiment=asks_embodiment,
        asks_why_how=_contains_any(text, _WHY_HOW_HINTS),
        asks_trend_landscape=_contains_any(text, _TREND_HINTS),
        asks_followup=_contains_any(text, _FOLLOWUP_HINTS),
        asks_attribute_value=_asks_attribute_value(text),
        has_doi=bool(_DOI_PATTERN.search(text)),
    )
