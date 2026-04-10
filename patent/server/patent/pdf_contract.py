from __future__ import annotations

import re
from typing import Any


SUMMARY_KEYWORDS = ["总结", "概述", "概括", "研究内容", "主要内容", "核心内容", "研究重点", "重点"]

GENERIC_PHRASES = [
    "橄榄石型晶体结构",
    "理论容量",
    "170mah/g",
    "工作电压",
    "3.2-3.3v",
    "高温固相法",
    "水热法",
    "溶胶-凝胶法",
    "共沉淀法",
]

PDF_QA_SYSTEM_MESSAGE = """你是一位材料科学文献分析专家。你的任务是**严格基于PDF原文内容**回答问题。

**绝对禁止的行为**：
- ❌ 禁止使用预训练知识（如"磷酸铁锂材料具有橄榄石型晶体结构"等通用描述）
- ❌ 禁止输出通用的材料特性、工艺路线等信息
- ❌ 禁止编造或推测PDF中没有的内容

**必须遵守的规则**：
- ✅ 只使用PDF原文中明确提到的内容
- ✅ 如果PDF中没有相关信息，明确说明"PDF中未提及"
- ✅ 不要添加任何PDF中没有的内容

**重要**：如果PDF内容很少或提取不完整，请明确说明"PDF内容提取不完整，无法完整回答问题"，而不是使用通用知识来补充。"""

IMPORTANT_SECTIONS = {
    "abstract": ["abstract", "摘要", "summary"],
    "introduction": ["introduction", "引言", "背景", "background"],
    "results": ["results", "结果", "实验结果", "experimental results"],
    "discussion": ["discussion", "讨论", "分析"],
    "conclusion": ["conclusion", "结论", "总结", "conclusions"],
    "methods": ["methods", "方法", "methodology", "实验方法"],
    "materials": ["materials", "材料", "样品", "样本"],
}

MULTI_DOC_HEADER_PATTERN = re.compile(r"^\s*=+\s*文献\s*[^=\n]*=+\s*$", re.MULTILINE)
REFERENCE_SECTION_MARKERS = ("参考文献", "references", "bibliography", "appendix", "附录", "acknowledg")
SECTION_SPLIT_MARKERS = (
    "abstract",
    "introduction",
    "methods",
    "method",
    "results",
    "discussion",
    "conclusion",
    "summary",
    "摘要",
    "引言",
    "背景",
    "方法",
    "结果",
    "讨论",
    "结论",
    "参考文献",
    "附录",
)
EXPLICIT_COMPARE_MARKERS = ("对比", "比较", "compare", "versus", "vs", "异同")
IMPLICIT_COMPARE_MARKERS = (
    "有什么不同",
    "有什么相同",
    "分别讲了什么",
    "哪篇更好",
    "哪篇效果更好",
    "放在一起看",
    "放在一起比较",
    "有什么区别",
    "结论一致吗",
    "谁优谁劣",
)
SINGLE_DOC_ONLY_MARKERS = ("第一篇", "第1篇", "第二篇", "第2篇", "其中一篇", "其中第一篇", "其中第二篇")
MULTI_DOC_SCOPE_MARKERS = (
    "两篇",
    "这两篇",
    "第一篇和第二篇",
    "第一篇与第二篇",
    "第1篇和第2篇",
    "第1篇与第2篇",
    "两份文献",
    "两篇文献",
)
MIN_COMPARE_DOC_CHARS = 180
_CN_NUMERALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
}


class CompareBudgetError(RuntimeError):
    """Raised when compare mode cannot preserve the minimum per-document context."""


def is_summary_question(question: str) -> bool:
    question_lower = str(question or "").lower()
    return any(keyword in question_lower for keyword in SUMMARY_KEYWORDS)


def is_compare_question(question: str, *, selected_pdf_count: int = 1) -> bool:
    if int(selected_pdf_count or 0) < 2:
        return False
    text = str(question or "").strip().lower()
    if not text:
        return False
    has_single_doc_scope = any(marker.lower() in text for marker in SINGLE_DOC_ONLY_MARKERS)
    has_multi_doc_scope = any(marker.lower() in text for marker in MULTI_DOC_SCOPE_MARKERS) or (
        ("第一篇" in text or "第1篇" in text)
        and ("第二篇" in text or "第2篇" in text)
    )
    if has_single_doc_scope and not has_multi_doc_scope:
        return False
    if any(marker in text for marker in EXPLICIT_COMPARE_MARKERS):
        return True
    if any(marker in text for marker in IMPLICIT_COMPARE_MARKERS):
        return True
    if "两篇" in text and ("分别" in text or "一起" in text):
        return True
    return False


def detect_targeted_document_index(
    question: str,
    *,
    selected_pdf_count: int = 1,
    selected_file_labels: list[str] | None = None,
) -> int | None:
    if int(selected_pdf_count or 0) < 2:
        return None
    text = str(question or "").strip().lower()
    if not text:
        return None
    candidate_refs: list[tuple[int, str]] = []
    for index, label in enumerate(list(selected_file_labels or [])):
        normalized_label = str(label or "").strip().lower()
        normalized_stem = normalized_label.rsplit(".", 1)[0]
        if normalized_label:
            candidate_refs.append((index, normalized_label))
        if normalized_stem and normalized_stem != normalized_label:
            candidate_refs.append((index, normalized_stem))
    for index, candidate in sorted(candidate_refs, key=lambda item: len(item[1]), reverse=True):
        if _has_explicit_label_reference(text=text, candidate=candidate):
            return index

    match = re.search(r"第\s*([0-9]+|[一二三四五六七八九十]+)\s*篇", text)
    if match:
        ordinal = _parse_ordinal_token(str(match.group(1) or ""))
        if ordinal is not None and 1 <= ordinal <= int(selected_pdf_count):
            return ordinal - 1

    match = re.search(r"文献\s*([0-9]+|[一二三四五六七八九十]+)", text)
    if match:
        ordinal = _parse_ordinal_token(str(match.group(1) or ""))
        if ordinal is not None and 1 <= ordinal <= int(selected_pdf_count):
            return ordinal - 1

    for word, ordinal in _ORDINAL_WORDS.items():
        if re.search(rf"\b{word}\s+(?:paper|document)\b", text) and ordinal <= int(selected_pdf_count):
            return ordinal - 1
    return None


def _has_explicit_label_reference(*, text: str, candidate: str) -> bool:
    normalized_text = str(text or "").strip().lower()
    normalized_candidate = str(candidate or "").strip().lower()
    if not normalized_text or not normalized_candidate:
        return False
    if normalized_candidate in normalized_text:
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(normalized_candidate)}(?![A-Za-z0-9])")
        if pattern.search(normalized_text):
            return True
    return False


def _parse_ordinal_token(token: str) -> int | None:
    normalized = str(token or "").strip().lower()
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)
    if normalized in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[normalized]
    if normalized in _CN_NUMERALS:
        return _CN_NUMERALS[normalized]
    if "十" in normalized:
        if normalized == "十":
            return 10
        parts = normalized.split("十", 1)
        tens = _CN_NUMERALS.get(parts[0], 1 if parts[0] == "" else 0)
        units = _CN_NUMERALS.get(parts[1], 0 if parts[1] == "" else 0)
        value = tens * 10 + units
        return value if value > 0 else None
    return None


def build_kb_section(kb_verification: dict | None) -> str:
    if not (kb_verification and kb_verification.get("kb_answer")):
        return ""

    return f"""

**📚 知识库验证信息**（用于验证PDF中提到的内容是否真实存在）：
{kb_verification.get('kb_answer', '')}

**重要说明**：
- 知识库信息仅用于**验证**PDF中提到的内容是否真实存在
- 如果PDF中提到某个材料、方法或数据，且知识库中也有相关信息，可以标注"（知识库验证：存在相关数据）"
- **不要**使用知识库信息来补充PDF中没有的内容
- **不要**使用知识库信息来替代PDF原文中的具体数据
- 如果PDF中提到但知识库中没有，仍然以PDF为准，但可以标注"（知识库中未找到相关验证数据）"
"""


def build_patent_pdf_answer_prompt(
    *,
    question: str,
    pdf_content: str,
    kb_section: str,
    is_summary: bool,
    is_compare: bool,
    selected_file_labels: list[str] | None = None,
    route_hint: str = "pdf_qa",
    source_scope: str = "pdf",
) -> str:
    if not is_compare:
        normalized_route = str(route_hint or "pdf_qa").strip().lower() or "pdf_qa"
        normalized_scope = str(source_scope or "pdf").strip() or "pdf"
        hybrid_mode = normalized_route == "hybrid_qa"
        route_intro = (
            "你是一位专利/文献证据分析助手。当前任务属于 patent 混合文件问答中的 PDF 证据分析环节。"
            if hybrid_mode
            else "你是一位专利/文献文件分析助手，负责基于上传的单篇 PDF 原文给出结构化回答。"
        )
        output_contract = """
**输出结构要求**：
- 请按以下 Markdown 结构回答：
  - `## 结论`
  - `## 证据`
  - `## 对比`
  - `## 限制`
- `## 证据` 中列出 2-4 条由 PDF 原文直接支持的事实
- `## 对比` 中如果没有可对照来源，明确写出当前缺少对照对象；如果这是混合问答子任务，只写这份 PDF 可提供的对照点
- `## 限制` 中明确说明未提及、证据不足或仍待其他来源交叉验证的部分
"""
        hybrid_scope_block = (
            f"""
**混合问答子任务要求**：
- 当前 route=`{normalized_route}`，source_scope=`{normalized_scope}`
- 先给出这份 PDF 单独能够支持的判断，再说明它能为后续跨来源合成提供哪些证据
- 不要把知识库验证信息改写成 PDF 原文结论
- 不要把知识库信息当作新的 PDF 事实
"""
            if hybrid_mode
            else ""
        )
        if is_summary:
            return f"""
{route_intro}

用户要求总结这篇**具体专利/文献**的研究内容，并且答案需要适配 patent 文件问答的结构化输出。

**🚨 核心约束（必须严格遵守）**：
1. **绝对禁止使用通用知识**：
   - ❌ 禁止输出"磷酸铁锂材料具有橄榄石型晶体结构"等通用描述（除非PDF中明确提到）
   - ❌ 禁止输出"理论容量约170mAh/g"等通用数据（除非PDF中明确提到）
   - ❌ 禁止输出"工作电压平台在3.2-3.3V"等通用信息（除非PDF中明确提到）
   - ❌ 禁止输出通用的工艺路线描述（除非PDF中明确提到）

2. **必须严格基于PDF原文（全文，已排除参考文献部分）**：
   - ✅ 只允许使用 PDF 原文中明确出现的内容
   - ✅ 如果PDF中没有提到某个方面，不要编造或推测
   - ✅ **重要**：你需要阅读PDF的**全文内容**（包括摘要、引言、方法、结果、讨论、结论等所有部分，但已排除参考文献部分）
   - ✅ 不要只关注摘要部分，要全面阅读全文的各个章节
   - ✅ 重点关注这篇文献的**具体研究内容**，包括：
     * 这篇文献的研究目的和背景（从PDF全文的引言和背景部分提取）
     * 这篇文献使用的具体研究方法/实验设计（从PDF全文的方法部分提取）
     * 这篇文献的主要发现和结果（从PDF全文的结果和讨论部分提取）
     * 这篇文献的结论和意义（从PDF全文的结论部分提取）

3. **知识库验证使用规则**（如果提供了知识库信息）：
   - ✅ 知识库信息仅可用于验证
   - ✅ 如果PDF中提到某个材料、方法或数据，且知识库中也有相关信息，可以标注"（知识库验证：存在相关数据）"
   - ❌ **不要**使用知识库信息来补充PDF中没有的内容
   - ❌ **不要**使用知识库信息来替代PDF原文中的具体数据
   - ❌ 不要把知识库信息当作新的 PDF 事实
   - ✅ 如果PDF中提到但知识库中没有，仍然以PDF为准，但可以标注"（知识库中未找到相关验证数据）"

4. **结论边界**：
   - ❌ 不得把未在 PDF 出现的信息补写成结论
   - ✅ 如果PDF中没有相关信息，明确说明"PDF中未提及"
   - ✅ 不要添加PDF中没有的内容

{output_contract}
{hybrid_scope_block}

**用户问题**: {question}

**PDF文献原文内容**（请仔细阅读，这是你唯一的信息来源）:
{pdf_content}
{kb_section}

**⚠️ 再次强调**：
1. 你只能使用上述PDF原文中的信息作为主要内容
2. 如果PDF中没有提到某个内容，不要使用你的通用知识来补充
3. 知识库信息仅用于验证，不能替代或补充PDF内容
4. 只输出PDF原文中明确存在的内容
5. 输出时必须显式区分：主结论、原文证据、可对照点、限制或未提及项

请仔细阅读PDF原文，总结这篇**具体专利/文献**的研究内容：
"""

        return f"""
{route_intro}

请**仅根据以下PDF文献内容**回答用户的问题，并保持 patent 文件问答所需的结构化输出。

**🚨 核心约束（必须严格遵守）**：
1. **绝对禁止使用通用知识**：
   - ❌ 禁止使用你的预训练知识来回答问题
   - ❌ 禁止输出通用的材料特性、工艺路线等信息
   - ❌ 禁止编造或推测PDF中没有的内容

2. **必须严格基于PDF原文**：
   - ✅ 只允许使用 PDF 原文中明确出现的内容
   - ✅ 如果PDF中没有相关信息，明确说明"PDF文献中未提及相关内容"
   - ✅ 引用时注明是"根据上传的PDF文献"

3. **知识库验证使用规则**（如果提供了知识库信息）：
   - ✅ 知识库信息仅可用于验证
   - ✅ 如果PDF中提到某个材料、方法或数据，且知识库中也有相关信息，可以标注"（知识库验证：存在相关数据）"
   - ❌ **不要**使用知识库信息来补充PDF中没有的内容
   - ❌ **不要**使用知识库信息来替代PDF原文中的具体数据
   - ❌ 不要把知识库信息当作新的 PDF 事实

4. **结论边界**：
   - ❌ 不得把未在 PDF 出现的信息补写成结论
   - ✅ 如果某个维度证据不足，要明确说明不确定或原文未提及

{output_contract}
{hybrid_scope_block}

**用户问题**: {question}

**PDF文献原文内容**（请仔细阅读，这是你唯一的信息来源）:
{pdf_content}
{kb_section}

**⚠️ 再次强调**：
1. 你只能使用上述PDF原文中的信息作为主要内容
2. 如果PDF中没有提到某个内容，不要使用你的通用知识来补充
3. 知识库信息仅用于验证，不能替代或补充PDF内容
4. 只输出PDF原文中明确存在的内容
5. 先回答可确认的内容，再指出证据空缺和限制

请回答：
"""

    labels = selected_file_labels or []
    doc_count = max(2, len(labels))
    labels_text = "\n".join(f"- {label}" for label in labels) if labels else "- 未知文献"
    return f"""
你是一位材料科学文献分析专家。用户要求对多篇**具体文献**进行对比分析。

**🚨 核心约束（必须严格遵守）**：
1. **绝对禁止使用通用知识**：
   - ❌ 禁止使用你的预训练知识补充文献中未出现的事实
   - ❌ 禁止输出通用背景来替代逐篇比较
   - ❌ 禁止把一篇文献的结论套用到另一篇文献上

2. **必须严格基于PDF原文**：
   - ✅ 只使用每篇PDF原文中明确提到的内容
   - ✅ 如果某篇文献没有相关信息，明确说明该文献证据不足
   - ✅ 如果某个比较维度在文献中没有出现，明确说明原文未提及

3. **知识库验证使用规则**（如果提供了知识库信息）：
   - ✅ 知识库信息仅可用于验证 PDF 中已经出现的内容
   - ❌ 不要使用知识库信息来补充 PDF 中没有的结论
   - ❌ 不要用知识库信息覆盖 PDF 原文中的具体结果

4. **输出要求**：
   - 使用标准Markdown格式
   - 明确说明本次比较共涉及 {doc_count} 篇文献
   - 先分别总结每篇文献
   - 再输出相同点、差异点、总体结论
   - 每篇文献概要中至少提供一个可区分的事实性信息

**参与比较的文献**：
{labels_text}

**用户问题**: {question}

**PDF文献原文内容**（请仔细阅读，这是你唯一的信息来源）:
{pdf_content}
{kb_section}

**⚠️ 再次强调**：
1. 你只能使用上述PDF原文中的信息作为主要内容
2. 如果某篇文献证据不足，不要假装比较已经完成
3. 知识库信息仅用于验证，不能替代或补充PDF内容
4. 不要输出泛化总结，必须完成逐篇比较

请按以下结构回答：
1. 逐篇给出文献概要（从文献 1 到文献 {doc_count}）
2. 按以下维度进行比较：研究主题/目标、方法/技术路线、核心结果/证据、结论/贡献
3. 相同点
4. 差异点
5. 总结
"""


def format_multi_pdf_sections(documents: list[dict[str, str]]) -> str:
    sections: list[str] = []
    for index, document in enumerate(documents, start=1):
        label = str(document.get("label") or f"file-{index}").strip() or f"file-{index}"
        text = str(document.get("text") or "").strip()
        if not text:
            continue
        sections.append(f"==== 文献 {index}: {label} ====\n{text}")
    return "\n\n".join(sections).strip()


def build_compare_failure_message(
    *,
    question: str,
    available_docs: list[str],
    missing_docs: list[str] | None = None,
    reason: str = "",
) -> str:
    available = "、".join(item for item in available_docs if item) or "无"
    missing = "、".join(item for item in (missing_docs or []) if item) or "无"
    reason_text = f"原因：{reason}\n" if reason else ""
    return (
        "当前无法完成完整比较。\n"
        f"{reason_text}"
        f"已读取文献：{available}\n"
        f"缺失或不可用文献：{missing}\n"
        "请确保参与比较的每篇 PDF 都有可读正文后再重试。"
    ).strip()


def build_extractive_fallback_summary(*, question: str, pdf_text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(pdf_text or "")).strip()
    if not cleaned:
        return "当前未拿到可用的 PDF 正文内容，无法生成基于原文的总结。"

    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？.!?])\s+", cleaned)
        if item.strip()
    ]
    picked: list[str] = []
    for sentence in sentences:
        if len(sentence) < 20:
            continue
        picked.append(_truncate(sentence, 220))
        if len(picked) >= 4:
            break

    if not picked:
        picked.append(_truncate(cleaned, 400))

    if is_summary_question(question):
        lines = ["基于 PDF 原文提取，文档要点如下："]
        lines.extend(f"{index}. {item}" for index, item in enumerate(picked, start=1))
        return "\n".join(lines)

    return "\n".join(picked)


def _truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _clip_text_with_boundary(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    floor = max(1, int(limit * 0.6))
    boundary = max(
        text.rfind("\n", floor, limit),
        text.rfind("。", floor, limit),
        text.rfind(".", floor, limit),
        text.rfind("；", floor, limit),
        text.rfind(";", floor, limit),
    )
    cut = boundary if boundary > 0 else limit
    clipped = text[:cut].rstrip()
    if len(clipped) < len(text):
        clipped += "..."
    return clipped


def _clip_text_from_end_with_boundary(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    start = len(text) - limit
    ceiling = min(len(text), start + max(1, int(limit * 0.4)))
    boundary = max(
        text.find("\n", start, ceiling),
        text.find("。", start, ceiling),
        text.find(".", start, ceiling),
        text.find("；", start, ceiling),
        text.find(";", start, ceiling),
    )
    cut = boundary + 1 if boundary >= start else start
    clipped = text[cut:].lstrip()
    if len(clipped) < len(text):
        clipped = "..." + clipped
    return clipped


def _split_paragraphs(text: str) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    normalized = re.sub(r"\n+", "\n\n", normalized)
    for marker in SECTION_SPLIT_MARKERS:
        normalized = re.sub(
            rf"(?<!^)(?<!\n\n)(?=\s*{re.escape(marker)}(?:\b|[：:\s]))",
            "\n\n",
            normalized,
            flags=re.IGNORECASE,
        )
    return [item.strip() for item in re.split(r"\n{2,}", normalized) if item.strip()]


def _is_reference_like_paragraph(paragraph: str) -> bool:
    normalized = re.sub(r"^[\s#>*\-\d\.\)\(【】\[\]:：]+", "", str(paragraph or "")).strip().lower()
    if not normalized:
        return False
    if normalized.startswith("参考文献") or normalized.startswith("附录"):
        return True
    english_heading_prefixes = (
        "references",
        "bibliography",
        "appendix",
        "acknowledgment",
        "acknowledgements",
        "acknowledgment",
        "acknowledgements",
    )
    for marker in english_heading_prefixes:
        if normalized == marker:
            return True
        if normalized.startswith(f"{marker} "):
            return True
        if normalized.startswith(f"{marker}:") or normalized.startswith(f"{marker}："):
            return True
        if marker == "appendix" and re.match(r"^appendix\s+[a-z0-9]+", normalized):
            return True
    return False


def _has_reference_like_tail(text: str) -> bool:
    paragraphs = _split_paragraphs(text)
    cutoff = max(1, len(paragraphs) // 2)
    for index, paragraph in enumerate(paragraphs):
        if index < cutoff:
            continue
        if _is_reference_like_paragraph(paragraph):
            return True
    return False


def _strip_reference_like_tail(text: str) -> str:
    paragraphs = _split_paragraphs(text)
    if len(paragraphs) < 2:
        return str(text or "").strip()
    cutoff = max(1, len(paragraphs) // 2)
    for index, paragraph in enumerate(paragraphs):
        if index < cutoff:
            continue
        if _is_reference_like_paragraph(paragraph):
            return "\n\n".join(paragraphs[:index]).strip()
    return "\n\n".join(paragraphs).strip()


def _split_multi_doc_sections(pdf_content: str) -> list[tuple[str, str]]:
    matches = list(MULTI_DOC_HEADER_PATTERN.finditer(pdf_content))
    if len(matches) < 2:
        return []

    sections: list[tuple[str, str]] = []
    for idx, matched in enumerate(matches):
        start = matched.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(pdf_content)
        header = matched.group(0).strip()
        body = str(pdf_content[start:end]).strip()
        if body:
            sections.append((header, body))
    return sections


def _find_first_matching_paragraph(paragraphs: list[str], section_names: tuple[str, ...]) -> int | None:
    for section_name in section_names:
        for keyword in IMPORTANT_SECTIONS.get(section_name, []):
            keyword_lower = keyword.lower()
            for index, paragraph in enumerate(paragraphs):
                lowered = paragraph.lower()
                if keyword_lower in lowered:
                    return index
    return None


def _find_last_matching_paragraph(paragraphs: list[str], section_names: tuple[str, ...]) -> int | None:
    for section_name in section_names:
        for keyword in IMPORTANT_SECTIONS.get(section_name, []):
            keyword_lower = keyword.lower()
            for index in range(len(paragraphs) - 1, -1, -1):
                lowered = paragraphs[index].lower()
                if keyword_lower in lowered:
                    return index
    return None


def _is_heading_only_paragraph(paragraph: str, section_names: tuple[str, ...]) -> bool:
    normalized = re.sub(r"^[\s#>*\-\d\.\)\(【】\[\]:：]+", "", str(paragraph or "")).strip().lower()
    if not normalized:
        return False
    for section_name in section_names:
        for keyword in IMPORTANT_SECTIONS.get(section_name, []):
            keyword_lower = keyword.lower()
            if normalized == keyword_lower:
                return True
    return False


def _resolve_content_paragraph_index(paragraphs: list[str], index: int | None, section_names: tuple[str, ...]) -> int | None:
    if index is None or index < 0 or index >= len(paragraphs):
        return index
    if not _is_heading_only_paragraph(paragraphs[index], section_names):
        return index
    for candidate in range(index + 1, len(paragraphs)):
        if not _is_heading_only_paragraph(paragraphs[candidate], tuple(IMPORTANT_SECTIONS.keys())):
            return candidate
    return index


def _build_compare_paragraph_selection(body: str) -> tuple[list[str], list[str]]:
    normalized = _strip_reference_like_tail(body)
    paragraphs = _split_paragraphs(normalized)
    if not paragraphs:
        return [], []

    front_index = _find_first_matching_paragraph(paragraphs, ("abstract", "introduction"))
    methods_index = _find_first_matching_paragraph(paragraphs, ("methods",))
    tail_index = _find_last_matching_paragraph(paragraphs, ("conclusion", "discussion", "results"))
    front_index = _resolve_content_paragraph_index(paragraphs, front_index, ("abstract", "introduction"))
    methods_index = _resolve_content_paragraph_index(paragraphs, methods_index, ("methods",))
    tail_index = _resolve_content_paragraph_index(paragraphs, tail_index, ("conclusion", "discussion", "results"))

    if front_index is None:
        front_index = 0
    if tail_index is None:
        tail_index = len(paragraphs) - 1

    selected_indices: list[int] = []
    for index in (front_index, methods_index, tail_index):
        if index is None or index < 0 or index >= len(paragraphs):
            continue
        if index not in selected_indices:
            selected_indices.append(index)

    selected_paragraphs = [paragraphs[index] for index in selected_indices]
    required_targets: list[str] = []
    for index in (front_index, tail_index):
        if index is None or index < 0 or index >= len(paragraphs):
            continue
        target = _clip_text_with_boundary(paragraphs[index], min(48, len(paragraphs[index])))
        target = target.replace("...", "").strip()
        if target and target not in required_targets:
            required_targets.append(target)
    return selected_paragraphs, required_targets


def _extract_compare_excerpt(body: str, budget: int) -> str:
    normalized = _strip_reference_like_tail(body)
    if len(normalized) <= budget:
        return normalized
    selected_paragraphs, _required_targets = _build_compare_paragraph_selection(normalized)
    if selected_paragraphs:
        joined = "\n\n".join(selected_paragraphs).strip()
        if len(joined) <= budget:
            return joined
        separator_cost = max(0, 2 * (len(selected_paragraphs) - 1))
        available = max(1, budget - separator_cost)
        base = max(60, available // len(selected_paragraphs))
        remainder = max(0, available - base * len(selected_paragraphs))
        clipped_parts: list[str] = []
        for index, paragraph in enumerate(selected_paragraphs):
            limit = base + (1 if index < remainder else 0)
            clipped_parts.append(_clip_text_with_boundary(paragraph, limit))
        return "\n\n".join(part for part in clipped_parts if part).strip()

    front_budget = max(80, int(budget * 0.48))
    back_budget = max(80, budget - front_budget - 12)
    front = _clip_text_with_boundary(normalized, front_budget)
    back = _clip_text_from_end_with_boundary(normalized, back_budget)
    if not back or back == front or len(front) + len(back) + 12 >= len(normalized):
        return _clip_text_with_boundary(normalized, budget)
    return f"{front}\n...\n{back}"


def validate_compare_context(prepared_pdf_text: str, documents: list[dict[str, str]]) -> None:
    sections = _split_multi_doc_sections(prepared_pdf_text)
    if len(sections) < len(documents):
        raise CompareBudgetError("compare 截断后未保留全部文献的最小比较上下文")

    for document in documents:
        label = str(document.get("label") or "").strip()
        original_text = str(document.get("text") or "").strip()
        matched_body = ""
        for header, body in sections:
            header_label = ""
            cleaned_header = str(header or "").strip().strip("=")
            if ":" in cleaned_header:
                header_label = cleaned_header.split(":", 1)[1].strip()
            if label and header_label == label:
                matched_body = body
                break
        if not matched_body:
            raise CompareBudgetError("compare 截断后缺少文献分段，无法完成逐篇比较")

        _, required_targets = _build_compare_paragraph_selection(original_text)
        normalized_body = re.sub(r"\s+", " ", _strip_reference_like_tail(matched_body)).strip()
        if not normalized_body:
            raise CompareBudgetError("compare 截断后存在空文献分段，无法完成逐篇比较")
        if _has_reference_like_tail(matched_body):
            raise CompareBudgetError("compare 截断后混入了参考文献尾部，无法保留最小比较上下文")

        for target in required_targets:
            normalized_target = re.sub(r"\s+", " ", str(target or "")).strip()
            if normalized_target and normalized_target not in normalized_body:
                raise CompareBudgetError("compare 截断后未保留每篇文献的最小比较上下文")


def _truncate_multi_pdf_content(pdf_content: str, *, max_chars: int, logger: Any, compare_mode: bool) -> str:
    sections = _split_multi_doc_sections(pdf_content)
    if len(sections) < 2:
        return ""

    total_docs = len(sections)
    header_cost = sum(len(header) + 3 for header, _body in sections)
    reserve = min(260, max(120, int(max_chars * 0.08)))
    available_for_body = max(0, max_chars - reserve - header_cost)
    if available_for_body <= 0:
        if compare_mode:
            raise CompareBudgetError("compare 截断预算不足，无法保留全部文献的最小比较上下文")
        logger.warning("⚠️ 多文献截断预算不足，回退到普通截断")
        return ""

    if compare_mode:
        minimum_required = total_docs * MIN_COMPARE_DOC_CHARS
        if available_for_body < minimum_required:
            raise CompareBudgetError("compare 截断预算不足，无法为每篇文献保留最小比较上下文")

    base = max(MIN_COMPARE_DOC_CHARS if compare_mode else 80, available_for_body // total_docs)
    remainder = max(0, available_for_body - base * total_docs)

    selected_parts: list[str] = []
    for idx, (header, body) in enumerate(sections):
        budget = base + (1 if idx < remainder else 0)
        excerpt = _extract_compare_excerpt(body, budget) if compare_mode else _clip_text_with_boundary(body, budget)
        selected_parts.append(f"{header}\n{excerpt}")

    result = "\n\n".join(selected_parts).strip()
    note = (
        f"\n\n[注意：已从 {total_docs} 篇文献中按均衡配额截断，原始 {len(pdf_content)} 字符，保留 {len(result)} 字符]"
    )
    max_body_chars = max_chars - len(note)
    if len(result) > max_body_chars and not compare_mode:
        result = _clip_text_with_boundary(result, max_body_chars)
    if len(result) > max_body_chars and compare_mode:
        raise CompareBudgetError("compare 截断结果超过预算，无法保留全部文献的最小比较上下文")
    final_text = result + note
    logger.info(f"✅ 多文献均衡截断完成，最终长度: {len(final_text)} 字符")
    return final_text


def _locate_section_indices(paragraphs: list[str], content_lower: str) -> dict[str, int]:
    section_indices: dict[str, int] = {}
    for section_name, keywords in IMPORTANT_SECTIONS.items():
        for keyword in keywords:
            if keyword not in content_lower:
                continue
            for idx, para in enumerate(paragraphs):
                if keyword in para.lower():
                    section_indices[section_name] = idx
                    break
            if section_name in section_indices:
                break
    return section_indices


def _get_priority_and_allocation(is_summary: bool, question: str, max_chars: int) -> tuple[list[str], dict[str, float]]:
    if is_summary:
        return (
            ["abstract", "introduction", "results", "discussion", "conclusion", "methods"],
            {
                "abstract": max_chars * 0.2,
                "introduction": max_chars * 0.2,
                "results": max_chars * 0.25,
                "discussion": max_chars * 0.15,
                "conclusion": max_chars * 0.15,
                "methods": max_chars * 0.05,
            },
        )

    question_lower = str(question or "").lower()
    if any(word in question_lower for word in ["性能", "property", "properties", "capacity", "voltage"]):
        priority_order = ["results", "discussion", "abstract", "introduction", "conclusion", "methods"]
    elif any(word in question_lower for word in ["方法", "工艺", "method", "synthesis", "preparation"]):
        priority_order = ["methods", "results", "introduction", "abstract", "discussion", "conclusion"]
    else:
        priority_order = ["abstract", "introduction", "results", "methods", "discussion", "conclusion"]

    char_allocation = {section: max_chars * 0.15 for section in priority_order}
    if priority_order:
        char_allocation[priority_order[0]] = max_chars * 0.25
    return priority_order, char_allocation


def smart_truncate_pdf_content(
    pdf_content: str,
    max_chars: int,
    *,
    logger: Any,
    is_summary: bool = False,
    question: str = "",
    is_compare: bool = False,
) -> str:
    if len(pdf_content) <= max_chars:
        return pdf_content

    multi_doc_result = _truncate_multi_pdf_content(
        pdf_content,
        max_chars=max_chars,
        logger=logger,
        compare_mode=is_compare,
    )
    if multi_doc_result:
        return multi_doc_result

    logger.info(f"⚡ 开始智能截断PDF内容，原始长度: {len(pdf_content)} -> 目标: {max_chars}")
    paragraphs = pdf_content.split("\n\n")
    section_indices = _locate_section_indices(paragraphs, pdf_content.lower())
    priority_order, char_allocation = _get_priority_and_allocation(is_summary, question, max_chars)

    selected_paragraphs: list[str] = []
    total_chars = 0
    for section_name in priority_order:
        if section_name not in section_indices or total_chars >= max_chars:
            continue

        start_idx = section_indices[section_name]
        allocated_chars = int(char_allocation.get(section_name, max_chars * 0.1))
        section_content = ""
        current_idx = start_idx

        while current_idx < len(paragraphs) and len(section_content) < allocated_chars and total_chars + len(section_content) < max_chars:
            para = paragraphs[current_idx]
            if len(section_content + para) > allocated_chars:
                remaining_chars = allocated_chars - len(section_content)
                if remaining_chars > 100:
                    section_content += para[:remaining_chars] + "..."
                break
            section_content += para + "\n\n"
            current_idx += 1

        if section_content.strip():
            selected_paragraphs.append(f"【{section_name.upper()}】\n{section_content.strip()}")
            total_chars += len(section_content)

    if total_chars < max_chars * 0.8:
        remaining_chars = max_chars - total_chars
        front_content = pdf_content[:remaining_chars]
        if front_content.strip():
            selected_paragraphs.insert(0, f"【FRONT_CONTENT】\n{front_content}")

    result = "\n\n".join(selected_paragraphs)
    if len(result) > max_chars:
        result = result[: max_chars - 100] + "..."

    result += f"\n\n[注意：PDF原文共{len(pdf_content)}字符，此处经过智能截断，仅保留最相关内容，共{len(result)}字符]"
    logger.info(f"✅ 智能截断完成，最终长度: {len(result)} 字符")
    return result


__all__ = [
    "CompareBudgetError",
    "GENERIC_PHRASES",
    "PDF_QA_SYSTEM_MESSAGE",
    "build_compare_failure_message",
    "build_extractive_fallback_summary",
    "build_kb_section",
    "build_patent_pdf_answer_prompt",
    "detect_targeted_document_index",
    "format_multi_pdf_sections",
    "is_compare_question",
    "is_summary_question",
    "smart_truncate_pdf_content",
    "validate_compare_context",
]
