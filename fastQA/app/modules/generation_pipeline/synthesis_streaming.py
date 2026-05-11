from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Set, Tuple

from app.integrations.llm import raise_if_upstream_pool_timeout
from app.modules.generation_pipeline.feature_flags import env_bool, env_int
from app.modules.generation_pipeline.answer_summary import (
    apply_answer_summary_experiment,
    build_summary_instruction,
    summary_experiment_enabled,
)
from app.modules.generation_pipeline.reference_alignment import (
    format_pdf_chunks_evidence as format_pdf_chunks_evidence_impl,
    rank_pdf_chunks_for_stage4_evidence,
)
from app.modules.generation_pipeline.synthesis_postprocess import (
    build_references_from_pdf_chunks,
    build_top5_reference_context,
    extract_cited_dois as extract_cited_dois_with_logging,
    log_top5_coverage,
)
from app.modules.qa_kb.comparison_validation import validate_comparison_answer


DOI_INLINE_PATTERN = re.compile(
    r"\(\s*(?:doi\s*=|DOI:\s*)?(10\.(?:[^\s,()]+|\([^\s,()]+\))+)\s*\)",
    re.IGNORECASE,
)

STAGE4_FACT_EXTRACTION_PROMPT = """你是一名严谨的文献证据卡片提取专家。

任务：根据原始问题，从下面证据文档中提取“可引用证据卡片”。

严格要求：
1. source_quote 必须来自证据文本，不能编造。
2. claim 可以是对 source_quote 的忠实改写；只有 use_allowed=cautious_inference 时才允许非常有限的归纳。
3. 不要求原文直接使用用户问题中的问法；只要证据能支持问题相关的材料、方法、条件、指标、现象、限制或对比线索，就应抽取为卡片。
4. 每条必须包含 DOI；DOI 可以保留证据文档中的格式，例如 10.xxx/yyy 或 10.xxx_yyy。
5. attributes 是动态对象：根据当前问题自由生成键值，不要套固定题型字段。
6. not_allowed 必须写清这条证据不能支持什么，防止 DOI 被贴到无关结论上。
7. 如果证据只能支持间接结论，设置 use_allowed=cautious_inference，并在 not_allowed 中说明边界；不要因为缺少“优势/劣势”等原词就返回空数组。
8. 若证据中出现**多个不同 DOI**（例如多篇文献分段），应**尽量为不同 DOI 各写至少一条**与问题相关的卡片；不要把全部卡片堆在同一 DOI 下（除非该证据段确实只含单篇）。
9. 若当前证据段**仅含单篇文献**，为该篇写 1–3 条卡片即可；doi 必须与证据中的该篇 DOI 一致。
10. 仅输出 JSON 数组，不要输出解释文字。

输出格式：
[
  {{
    "claim": "可用于回答问题的最小证据单元",
    "source_quote": "原文或证据片段中的直接依据",
    "doi": "10.xxx/yyy",
    "relevance_to_question": "这条证据为什么与当前问题有关",
    "use_allowed": "answer_fact | cautious_inference | background_only",
    "not_allowed": ["不能用它支持什么"],
    "attributes": {{"按当前问题动态生成": "材料、参数、现象、机制、条件、指标等"}}
  }}
]

原始问题：{user_question}
对比对象：{comparison_objects}

证据文档：
{evidence_documents}
"""

STAGE4_FACT_SYNTHESIS_PROMPT = """你是一位磷酸铁锂电池方面的专家，请基于可引用事实卡片回答用户问题。

## 最重要原则

1. 严格基于事实卡片，不编造事实、数据、参数、DOI 或文献结论。
2. 每个带 DOI 的数据、参数、结论都必须能在同一 DOI 的事实卡片中找到对应。
3. 对「优劣势/对比/选型」类问题：严禁对每个对象把成本、粒径、烧结窗口、三方对比等维度逐一写成整段「当前证据未提供…」「当前证据未涉及…」凑篇幅。应优先根据卡片里已有的工艺路线、前驱体角色、性能数据、放大或杂质线索组织叙述与合理推断；仅在该对象在卡片中几乎无可引用句、或某一缺失项会显著误导读者时，用一两句集中说明缺口即可。其它类型问题若结论强依赖某项具体数值而卡片中确实没有，再明确写文献未给出该项。
4. 可以做轻量工程解释，但不能把工程常识写成文献结论，也不能给工程解释挂 DOI。
5. 若事实卡片中包含 source_quote、attributes 或 not_allowed，必须利用这些边界，不能越界使用 DOI。
6. 不要把「证据缺口」写成好像被引用的文献证明了「缺点」；缺数据、缺同条件对比时，用一两句单独说明即可，且这类句子不要挂 DOI。正文用自然段落叙述即可，不必为每个对象机械套用固定小标题。

## 输入

原始问题：{user_question}

结构化分析计划（只用于保持对象顺序、分析维度和总结逻辑，不能作为事实来源；其中没有证据支撑的数值或结论不能直接输出）：
{answer_plan}

## 专家初稿（阶段一预回答，仅结构、角度与衔接参考）

{expert_draft}

**必读**：上文为工程师预草稿，**不是可引用事实**；其中的数值、结论、机理、DOI 等均**不得**当作已由文献证实的内容写入答案。带 `(doi=…)` 的句子只能对应**下方事实卡片**中可核对的表述。
- **可保留的初稿内容**：术语与定义、问题拆解、工艺/材料语境、段落顺序、对比维度、过渡语，以及不与证据绑定的行业常识；写成**无 `(doi=…)`** 的引导段或小节即可，与带 DOI 的实证段交替出现，避免全文「只有卡片没有作者声音」。
- **必须与证据绑定**：凡含具体实验数值、性能对比、文献独有结论的句子，只能来自事实卡片并挂对应 `(doi=…)`，且同段须有一句点明「该证据对用户问题的含义」。
- 与事实卡片冲突时**一律以卡片为准**。

可引用事实卡片（仅可引用此处 DOI）：
{facts_list}

## 答题策略：识别用户核心关注点

回答前先判断问题类型和回答重点：
- 机理分析：重点讲反应路径、化学过程、中间产物和限制条件。
- 工艺方法：重点讲制备路线、工艺条件、操作窗口、放大风险。
- 性能评价：重点讲容量、循环、倍率、效率、测试条件和对比结果。
- 对比分析：重点讲差异分析、优缺点、适用场景、风险点和证据缺口。
- 影响因素：重点讲因素识别、影响规律、数据支撑和可验证假设。

优先回答用户真正关心的内容，不要平均分配篇幅。对“优劣势/对比”类问题，重点是差异分析和从业选型，而不是简单罗列文献。

**优劣势与对比类问题（灵活叙述，勿套固定栏目）**

- **直接可引用的内容**：事实卡片明确写了某前驱体、某路线、某工艺条件或某性能指标时，如实概括并挂 `(doi=…)`。
- **间接但可用的内容**：卡片未逐字写「优势/劣势」，但给出了反应路径、前驱体角色、烧结/掺杂窗口、电化学结果、放大或杂质线索等，请用连贯段落把证据与用户问题串起来；可用「结合上述证据…」「可据此推断…」等自然过渡。推断不得捏造数值或卡片中不存在的结论，且推断句不要单独再挂 DOI（推断应承接前文已挂 DOI 的句子）。
- **证据较少时**：若某一对象缺少专用卡片，可谨慎引用化学/工艺角色相近的卡片作类比，并一句话点明类比边界；确实无可类比材料时，再集中说明当前检索下尚缺哪些信息，而不是整段只写「未提及」。
- **禁止样板结构**：不要用「小节标题 = 对象名 + 优势与劣势」且正文几乎只有缺口列举；不要把总结写成与上文重复的缺口清单。

## PDF原文与事实卡片数据提取

1. 详细优先：充分展示事实卡片中的细节，不要过度总结。
2. 数据完整：尽量提取工艺参数、性能数据、实验条件、材料角色、反应路径、容量、循环、倍率、效率等信息。
3. 缺失信息：只点明对读者判断重要的缺失，不要逐项枚举所有未在卡片中出现的参数类别。
4. 分析充分：基于事实卡片做方法学分析、适用场景分析和风险分析。
5. 对比问题不要输出对比表；小标题可用主题式命名（如工艺路线、掺杂策略、沉淀化学），不必逐字镜像用户问题里的「某原料的优势与劣势」。

## 标准回答结构

请尽量使用以下 Markdown 结构：

## 文献综述

简要说明当前事实卡片能支持哪些方面，不能支持哪些方面。

## 主要发现

围绕用户问题列出关键发现。多对象时每个对象都应有基于证据的讨论，但可用主题式分节组织，禁止某节只有缺口句而没有基于卡片的实质内容。

## 深度分析

展开工艺逻辑、性能含义、适用场景、风险点和证据缺口。直接证据和工程解释要分句写。

## 总结与建议

**核心判断**：用 1-2 句话给出当前证据下最可靠的判断。

**实践建议**：给出面向从业选型的建议，但不要超出事实卡片可支撑的边界。

**还需补充的数据**：用 2–4 条概括最关键的待补项即可，不要与上文重复罗列相同缺口。

## 引用规则

1. DOI 格式必须使用 `(doi=xxx)`，禁止裸 DOI。
2. 禁止在文末单独列 DOI 或参考文献列表。
3. 禁止一句话引用多个 DOI。
4. 禁止把事实卡片中没有的 DOI 加入答案。
"""

STAGE4_RESTRICTED_SYNTHESIS_PROMPT = """你是一位磷酸铁锂电池方面的专家，请基于检索到的相关文献详细内容回答用户问题。

## 最重要原则

1. 严格基于原文证据，不编造事实、数据、参数、DOI 或文献结论。
2. 每个带 DOI 的数据、参数、结论都必须能在同一 DOI 的原文证据中找到对应。
3. 对「优劣势/对比/选型」类问题：严禁对每个对象把多类维度逐一写成整段「当前证据未提供…」「当前证据未涉及…」凑篇幅。应优先根据原文中的工艺、性能、材料角色等信息组织叙述与合理推断；仅在该对象在原文中几乎无可引用句、或某一缺失项会显著误导读者时，用一两句说明缺口即可。其它类型问题若结论强依赖某项具体数值而原文中确实没有，再明确写文献未给出该项。
4. 可以做轻量工程解释，但不能把工程常识写成文献结论，也不能给工程解释挂 DOI。
5. 不要把「证据缺口」写成好像被引用的文献证明了「缺点」；缺数据、缺同条件对比时，用一两句单独说明即可，且这类句子不要挂 DOI。正文用自然段落叙述即可，不必为每个对象机械套用固定小标题。

## 输入

原始问题：{user_question}

结构化分析计划（只用于保持对象顺序、分析维度和总结逻辑，不能作为事实来源；其中没有证据支撑的数值或结论不能直接输出）：
{answer_plan}

## 专家初稿（阶段一预回答，仅结构、角度与衔接参考）

{expert_draft}

**必读**：上文为工程师预草稿，**不是可引用事实**；其中的数值、结论、机理、DOI 等均**不得**当作已由下文原文证实的内容写入答案。带 `(doi=…)` 的句子必须能在**同一条**原文证据中找到依据。
- **可保留的初稿内容**：术语、定义、问题框架、对比维度、过渡与工程常识，可写成**无 `(doi=…)`** 的短段，用于读者进入问题；不得在其中夹带证据未出现的具体文献数值。
- **文献专业分析**：每个主要带 DOI 的论断旁，用 1–2 句写清证据条件、量级及对问题的含义（机理或工程后果），避免仅摘录原文短语。
- 预草稿可用于段落顺序与衔接；与原文冲突时**一律以原文为准**。

原文证据（仅可引用此处出现的 DOI）：
{evidence_documents}

参考 DOI 优先级：
{top5_references}

## 答题策略：识别用户核心关注点

回答前先判断问题类型和回答重点：
- 机理分析：重点讲反应路径、化学过程、中间产物和限制条件。
- 工艺方法：重点讲制备路线、工艺条件、操作窗口、放大风险。
- 性能评价：重点讲容量、循环、倍率、效率、测试条件和对比结果。
- 对比分析：重点讲差异分析、优缺点、适用场景、风险点和证据缺口。
- 影响因素：重点讲因素识别、影响规律、数据支撑和可验证假设。

优先回答用户真正关心的内容，不要平均分配篇幅。对“优劣势/对比”类问题，重点是差异分析和从业选型，而不是简单罗列文献。

**优劣势与对比类问题（灵活叙述，勿套固定栏目）**

- **直接可引用的内容**：原文明确写了某前驱体、某路线、某工艺条件或某性能指标时，如实概括并挂 `(doi=…)`。
- **间接但可用的内容**：原文未逐字写「优势/劣势」，但给出了反应路径、前驱体角色、工艺窗口、电化学结果、放大或杂质线索等，请用连贯段落把证据与用户问题串起来；可用「结合上述证据…」「可据此推断…」等自然过渡。推断不得捏造数值或原文中不存在的结论，且推断句不要单独再挂 DOI。
- **证据较少时**：若某一对象缺少直接段落，可谨慎引用化学/工艺角色相近的文献作类比，并一句话点明类比边界；确实无可类比材料时，再集中说明当前检索下尚缺哪些信息。
- **禁止样板结构**：不要用「对象名 + 优势与劣势」式小节且正文几乎只有缺口列举；不要把总结写成与上文重复的缺口清单。

## PDF原文数据提取

1. 详细优先：充分利用 PDF原文 和 MD 原文中的细节，不要过度总结。
2. 数据完整：尽量提取工艺参数、性能数据、实验条件、材料角色、反应路径、容量、循环、倍率、效率等信息。
3. 缺失信息：只点明对读者判断重要的缺失，不要逐项枚举所有未在原文出现的参数类别。
4. 分析充分：基于原文做方法学分析、适用场景分析和风险分析。
5. 对比问题不要输出对比表；小标题可用主题式命名，不必逐字镜像用户问题里的「某原料的优势与劣势」。

## 标准回答结构

请尽量使用以下 Markdown 结构：

## 文献综述

简要说明当前原文证据能支持哪些方面，不能支持哪些方面。

## 主要发现

围绕用户问题列出关键发现。多对象时每个对象都应有基于证据的讨论，但可用主题式分节组织，禁止某节只有缺口句而没有基于原文的实质内容。

## 深度分析

展开工艺逻辑、性能含义、适用场景、风险点和证据缺口。直接证据和工程解释要分句写。

## 总结与建议

**核心判断**：用 1-2 句话给出当前证据下最可靠的判断。

**实践建议**：给出面向从业选型的建议，但不要超出原文证据可支撑的边界。

**还需补充的数据**：用 2–4 条概括最关键的待补项即可，不要与上文重复罗列相同缺口。

## 引用规则

1. DOI 格式必须使用 `(doi=xxx)`，禁止裸 DOI。
2. 禁止在文末单独列 DOI 或参考文献列表。
3. 禁止一句话引用多个 DOI。
4. 禁止引用原文证据中没有出现的 DOI。
"""

STAGE4_STRUCTURE_ONLY_PROMPT = """你是一名基于文献证据生成答案的专家。

请基于以下材料生成最终答案：

1. 原始问题：{user_question}
2. 开头段落（可保留）：{opening_paragraph}
3. 答案结构大纲（仅作框架）：{structure_outline}
4. 支持性文献原文：{evidence_documents}

要求：
1. 按结构大纲组织答案。
2. 具体数值和结论优先来自证据文档。
3. 引用使用 `(doi=xxx)`，且必须在证据中出现。
4. 每个关键要点需包含机理解释（如何/为什么）与定量信息（数值/单位/条件），禁止空泛结论。
5. 输出 Markdown，禁止文末单独 DOI 列表。

{top5_references}
"""


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _escape_braces(text: str) -> str:
    return str(text or "").replace("{", "{{").replace("}", "}}")


def _canonicalize_doi(doi: str) -> str:
    value = str(doi or "").strip()
    value = re.sub(r"[.,;:]+$", "", value)
    if "_" in value and "/" not in value:
        value = value.replace("_", "/", 1)
    return value


def _build_doi_variants(doi: str) -> Set[str]:
    canonical = _canonicalize_doi(doi)
    if not canonical:
        return set()
    return {canonical, canonical.replace("/", "_", 1)}


def format_pdf_chunks_evidence(pdf_chunks: dict[str, list[dict[str, Any]]], user_question: str = "") -> str:
    logger = type("_NoopLogger", (), {"debug": lambda *args, **kwargs: None})()
    return format_pdf_chunks_evidence_impl(
        pdf_chunks=pdf_chunks,
        user_question=user_question,
        logger=logger,
    )


def extract_cited_dois(final_answer: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in DOI_INLINE_PATTERN.finditer(str(final_answer or "")):
        doi = _canonicalize_doi(match.group(1))
        if not doi or doi in seen:
            continue
        seen.add(doi)
        found.append(doi)
    return found


def _normalize_answer_doi_citations(answer: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        doi = _canonicalize_doi(match.group(1))
        return f"(doi={doi})" if doi else match.group(0)

    return DOI_INLINE_PATTERN.sub(_replace, str(answer or ""))


_NUMERIC_CLAIM_PATTERN = re.compile(
    r"(?<![A-Za-z])\d+(?:\.\d+)?(?:\s*(?:-|–|~|至)\s*\d+(?:\.\d+)?)?\s*"
    r"(?:%|wt%|mAh\s*g(?:-1|⁻¹)?|mAh/g|Ah|C|°C|℃|K|ppm|μm|um|nm|m²/g|m2/g|"
    r"S/cm|kJ/mol|eV|g/cm³|g/cm3)",
    re.IGNORECASE,
)
_ALNUM_TERM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9._+-]{1,}")
_CJK_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_CJK_STOP_CHARS = set("的一是在和与及或并中上下为可将通过采用使用进行具有得到材料路线原料")


def _extract_numeric_claims(text: str) -> Set[str]:
    return {re.sub(r"\s+", "", match.group(0)).lower() for match in _NUMERIC_CLAIM_PATTERN.finditer(str(text or ""))}


def _build_fact_text_by_doi(facts: List[Dict[str, str]]) -> Dict[str, str]:
    grouped: Dict[str, List[str]] = {}
    for item in facts:
        doi = _canonicalize_doi(str(item.get("doi") or ""))
        fact = str(item.get("fact") or item.get("claim") or item.get("source_quote") or "").strip()
        if not doi or not fact:
            continue
        grouped.setdefault(doi, []).append(fact)
    return {doi: "\n".join(items) for doi, items in grouped.items()}


def _extract_alignment_terms(text: str) -> Set[str]:
    cleaned = DOI_INLINE_PATTERN.sub("", str(text or "")).lower()
    terms = {match.group(0) for match in _ALNUM_TERM_PATTERN.finditer(cleaned)}
    terms.update(char for char in _CJK_CHAR_PATTERN.findall(cleaned) if char not in _CJK_STOP_CHARS)
    return terms


def _line_has_fact_overlap(line: str, cited_dois: List[str], fact_text_by_doi: Dict[str, str]) -> bool:
    line_terms = _extract_alignment_terms(line)
    if not line_terms:
        return True
    best_overlap = 0
    for doi in cited_dois:
        fact_terms = _extract_alignment_terms(fact_text_by_doi.get(doi, ""))
        if not fact_terms:
            continue
        best_overlap = max(best_overlap, len(line_terms & fact_terms))
    required_overlap = 2 if len(line_terms) <= 5 else max(3, len(line_terms) // 4)
    return best_overlap >= required_overlap


def _split_sentence_segments(line: str) -> List[str]:
    segments: List[str] = []
    buffer: List[str] = []
    for char in str(line or ""):
        buffer.append(char)
        if char in "。！？!?":
            segment = "".join(buffer)
            if segment:
                segments.append(segment)
            buffer = []
    remainder = "".join(buffer)
    if remainder:
        segments.append(remainder)
    return segments or [line]


def _cited_segment_is_supported(
    *,
    segment: str,
    cited_dois: List[str],
    fact_text_by_doi: Dict[str, str],
) -> bool:
    if not _line_has_fact_overlap(segment, cited_dois, fact_text_by_doi):
        return False
    segment_numeric_claims = _extract_numeric_claims(DOI_INLINE_PATTERN.sub("", segment))
    if not segment_numeric_claims:
        return True
    supported_numeric_claims: Set[str] = set()
    for doi in cited_dois:
        supported_numeric_claims.update(_extract_numeric_claims(fact_text_by_doi.get(doi, "")))
    return not (segment_numeric_claims - supported_numeric_claims)


def _remove_fact_mode_unsupported_cited_lines(
    *,
    answer: str,
    fact_text_by_doi: Dict[str, str],
    logger: Any,
) -> str:
    if not answer or not fact_text_by_doi:
        return answer
    kept_lines: List[str] = []
    removed_lines: List[str] = []
    for line in str(answer).splitlines():
        kept_segments: List[str] = []
        for segment in _split_sentence_segments(line):
            cited_dois = [_canonicalize_doi(match.group(1)) for match in DOI_INLINE_PATTERN.finditer(segment)]
            cited_dois = [doi for doi in cited_dois if doi]
            if not cited_dois:
                kept_segments.append(segment)
                continue
            if not _cited_segment_is_supported(
                segment=segment,
                cited_dois=cited_dois,
                fact_text_by_doi=fact_text_by_doi,
            ):
                removed_lines.append(segment)
                continue
            kept_segments.append(segment)
        kept_line = "".join(kept_segments).strip()
        if kept_line:
            kept_lines.append(kept_line)
    if removed_lines:
        logger.warning("Stage4 removed fact-mode cited lines unsupported by cited fact cards: %s", removed_lines)
    return "\n".join(kept_lines).strip()


def _validate_answer_dois_with_pdf_chunks(
    *,
    answer: str,
    pdf_chunks: Dict[str, List[Dict[str, Any]]],
) -> Tuple[str, List[str], List[str]]:
    if not answer:
        return answer, [], []
    used_dois = sorted(set(m.group(1).strip() for m in DOI_INLINE_PATTERN.finditer(answer)))
    if not used_dois:
        return answer, [], []
    raw_keys = {str(k or "").strip() for k in (pdf_chunks or {}).keys() if str(k or "").strip()}
    canonical_keys = {_canonicalize_doi(k) for k in raw_keys if _canonicalize_doi(k)}
    valid: List[str] = []
    invalid: List[str] = []
    for doi in used_dois:
        variants = _build_doi_variants(doi)
        canonical = _canonicalize_doi(doi)
        matched = any(v in raw_keys for v in variants) or (canonical in canonical_keys if canonical else False)
        if matched:
            valid.append(doi)
        else:
            invalid.append(doi)
    cleaned = answer
    for doi in invalid:
        cleaned = re.sub(
            r"\s*\(\s*(?:doi\s*=|DOI:\s*)?" + re.escape(doi) + r"\s*\)",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = _normalize_answer_doi_citations(cleaned)
    return cleaned, valid, invalid


def _validate_answer_dois_with_allowed_dois(
    *,
    answer: str,
    allowed_dois: Set[str],
) -> Tuple[str, List[str], List[str]]:
    if not answer:
        return answer, [], []
    canonical_allowed = {_canonicalize_doi(doi) for doi in allowed_dois if _canonicalize_doi(doi)}
    raw_allowed = set()
    for doi in canonical_allowed:
        raw_allowed.update(_build_doi_variants(doi))
    used_dois = sorted(set(m.group(1).strip() for m in DOI_INLINE_PATTERN.finditer(answer)))
    valid: List[str] = []
    invalid: List[str] = []
    for doi in used_dois:
        canonical = _canonicalize_doi(doi)
        variants = _build_doi_variants(doi)
        matched = bool(canonical and canonical in canonical_allowed) or any(variant in raw_allowed for variant in variants)
        if matched:
            valid.append(doi)
        else:
            invalid.append(doi)
    cleaned = answer
    for doi in invalid:
        cleaned = re.sub(
            r"\s*\(\s*(?:doi\s*=|DOI:\s*)?" + re.escape(doi) + r"\s*\)",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = _normalize_answer_doi_citations(cleaned)
    return cleaned, valid, invalid


def _extract_citable_facts_from_evidence(
    *,
    evidence_documents: str,
    user_question: str,
    comparison_objects: str,
    client: Any,
    model: str,
    logger: Any,
    max_tokens: int | None = None,
) -> List[Dict[str, str]]:
    resolved_tokens = (
        env_int("QA_STAGE4_FACT_EXTRACTION_MAX_TOKENS", 1200, minimum=200, maximum=8000)
        if max_tokens is None
        else max(200, min(int(max_tokens), 8000))
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是严谨的事实提取器，只输出 JSON 数组。"},
                {
                    "role": "user",
                    "content": STAGE4_FACT_EXTRACTION_PROMPT.format(
                        user_question=user_question,
                        comparison_objects=comparison_objects or "无",
                        evidence_documents=evidence_documents,
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=resolved_tokens,
            stream=False,
        )
        raw = str(response.choices[0].message.content or "").strip()
    except Exception as exc:
        raise_if_upstream_pool_timeout(exc)
        logger.warning("Stage4 two-stage fact extraction failed: %s", exc)
        return []
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return []
    facts: List[Dict[str, Any]] = []
    if not isinstance(data, list):
        return facts
    for item in data:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact") or item.get("claim") or "").strip()
        source_quote = str(item.get("source_quote") or "").strip()
        doi = _canonicalize_doi(str(item.get("doi") or "").strip())
        use_allowed = str(item.get("use_allowed") or "answer_fact").strip() or "answer_fact"
        if use_allowed == "background_only":
            continue
        if (fact or source_quote) and doi.startswith("10."):
            card: Dict[str, Any] = {
                "fact": fact or source_quote,
                "claim": fact or source_quote,
                "source_quote": source_quote,
                "doi": doi,
                "relevance_to_question": str(item.get("relevance_to_question") or "").strip(),
                "use_allowed": use_allowed,
                "not_allowed": [str(value).strip() for value in list(item.get("not_allowed") or []) if str(value).strip()]
                if isinstance(item.get("not_allowed"), list)
                else [],
                "attributes": item.get("attributes") if isinstance(item.get("attributes"), dict) else {},
            }
            facts.append(card)
    return facts


def _dedupe_fact_card_key(card: Dict[str, Any]) -> tuple[str, str]:
    doi = _canonicalize_doi(str(card.get("doi") or "").strip())
    text = str(card.get("fact") or card.get("claim") or card.get("source_quote") or "").strip()
    return (doi, text[:500].lower())


def _extract_facts_for_stage4_two_stage(
    *,
    pdf_chunks: dict[str, list[dict[str, Any]]],
    evidence_documents: str,
    user_question: str,
    comparison_objects: str,
    client: Any,
    model: str,
    logger: Any,
    format_pdf_chunks_evidence_fn: Callable[[dict[str, list[dict[str, Any]]], str], str],
) -> List[Dict[str, Any]]:
    """Single-pass or per-DOI fact extraction for two-stage Stage4."""
    per_doi = env_bool("QA_STAGE4_FACT_EXTRACTION_PER_DOI_ENABLED", False)
    per_doi_max = env_int("QA_STAGE4_FACT_PER_DOI_MAX_DOIS", 10, minimum=1, maximum=25)
    merged_cap = env_int("QA_STAGE4_FACT_PER_DOI_MAX_MERGED_CARDS", 48, minimum=4, maximum=120)
    per_slice_tokens = env_int("QA_STAGE4_FACT_PER_DOI_EXTRACTION_MAX_TOKENS", 0, minimum=0, maximum=8000)
    slice_max_kw: dict[str, Any] = {}
    if per_slice_tokens > 0:
        slice_max_kw["max_tokens"] = per_slice_tokens

    if not per_doi or len(pdf_chunks) <= 1:
        return _extract_citable_facts_from_evidence(
            evidence_documents=evidence_documents,
            user_question=user_question,
            comparison_objects=comparison_objects,
            client=client,
            model=model,
            logger=logger,
        )

    ranked, _filtered = rank_pdf_chunks_for_stage4_evidence(pdf_chunks, user_question, logger, max_dois=per_doi_max)
    if not ranked:
        return _extract_citable_facts_from_evidence(
            evidence_documents=evidence_documents,
            user_question=user_question,
            comparison_objects=comparison_objects,
            client=client,
            model=model,
            logger=logger,
        )

    merged: List[Dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    extraction_rounds = 0
    for row in ranked:
        doi = row.get("doi")
        chunks = row.get("chunks") or []
        if not doi or not chunks:
            continue
        sub = {str(doi): list(chunks)}
        slice_evidence = format_pdf_chunks_evidence_fn(sub, user_question)
        if not str(slice_evidence or "").strip():
            continue
        extraction_rounds += 1
        part = _extract_citable_facts_from_evidence(
            evidence_documents=slice_evidence,
            user_question=user_question,
            comparison_objects=comparison_objects,
            client=client,
            model=model,
            logger=logger,
            **slice_max_kw,
        )
        for card in part:
            key = _dedupe_fact_card_key(card)
            if not key[0] or key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(card)
            if len(merged) >= merged_cap:
                break
        if len(merged) >= merged_cap:
            break

    distinct = len({_canonicalize_doi(str(c.get("doi") or "")) for c in merged if c.get("doi")})
    logger.info(
        "stage4 fact per-doi extraction rounds=%s merged_cards=%s distinct_fact_dois=%s",
        extraction_rounds,
        len(merged),
        distinct,
    )

    if not merged:
        return _extract_citable_facts_from_evidence(
            evidence_documents=evidence_documents,
            user_question=user_question,
            comparison_objects=comparison_objects,
            client=client,
            model=model,
            logger=logger,
        )
    return merged


def _format_comparison_objects_for_fact_extraction(retrieval_results: dict[str, Any] | None) -> str:
    groups = (retrieval_results or {}).get("comparison_groups") if isinstance(retrieval_results, dict) else None
    if not isinstance(groups, list):
        return ""
    parts: List[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        label = str(group.get("label") or "").strip()
        if not label:
            continue
        aliases = [str(item).strip() for item in list(group.get("aliases") or []) if str(item).strip()]
        if aliases:
            parts.append(f"{label}（{', '.join(aliases)}）")
        else:
            parts.append(label)
    return "；".join(parts)


def _format_facts_for_prompt(facts: List[Dict[str, Any]]) -> str:
    if not facts:
        return "（无）"
    lines: List[str] = []
    for idx, item in enumerate(facts[:120], 1):
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        not_allowed = item.get("not_allowed") if isinstance(item.get("not_allowed"), list) else []
        extras: List[str] = []
        if item.get("source_quote"):
            extras.append(f"source_quote={item.get('source_quote')}")
        if item.get("relevance_to_question"):
            extras.append(f"relevance={item.get('relevance_to_question')}")
        if item.get("use_allowed"):
            extras.append(f"use_allowed={item.get('use_allowed')}")
        if not_allowed:
            extras.append(f"not_allowed={'; '.join(str(value) for value in not_allowed)}")
        if attributes:
            extras.append(f"attributes={json.dumps(attributes, ensure_ascii=False)}")
        suffix = " | " + " | ".join(extras) if extras else ""
        lines.append(f"- F{idx:03d} | doi={item['doi']} | claim={item['fact']}{suffix}")
    return "\n".join(lines)


def _format_comparison_evidence_contract(retrieval_results: dict[str, Any] | None) -> str:
    groups = (retrieval_results or {}).get("comparison_groups") if isinstance(retrieval_results, dict) else None
    if not isinstance(groups, list) or not groups:
        return ""
    lines = [
        "【多对象对比证据包】",
        "必须分别覆盖每个对比对象；每个对象的优势、劣势、适用场景只能使用该对象证据包或公共PDF/MD证据支撑。",
        "如果某个对象 evidence_status 不是 sufficient，必须明确说明当前库证据不足，不能强行给确定结论。",
    ]
    for index, group in enumerate(groups, 1):
        if not isinstance(group, dict):
            continue
        label = str(group.get("label") or "").strip()
        if not label:
            continue
        aliases = ", ".join(str(item).strip() for item in list(group.get("aliases") or []) if str(item).strip()) or "无"
        status = str(group.get("evidence_status") or "unknown")
        reason = str(group.get("missing_evidence_reason") or "")
        dois = ", ".join(str(item).strip() for item in list(group.get("doi_candidates") or [])[:8] if str(item).strip()) or "无"
        md_hits = [item for item in list(group.get("md_hits") or []) if isinstance(item, dict)]
        sample = "；".join(str(item.get("text") or "").replace("\n", " ")[:160] for item in md_hits[:2] if str(item.get("text") or "").strip()) or "无"
        lines.append(
            f"{index}. 对象：{label}；别名：{aliases}；evidence_status={status}；"
            f"missing_reason={reason or '无'}；候选DOI：{dois}；MD证据摘录：{sample}"
        )
    return "\n".join(lines)


def _summarize_conversation_context_for_log(conversation_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(conversation_context, dict):
        return {
            "present": False,
            "turns": 0,
            "summary_present": False,
            "short_summary_present": False,
            "open_threads": 0,
            "memory_facts": 0,
        }

    turns = conversation_context.get("recent_turns_for_llm")
    summary = conversation_context.get("summary_for_llm")
    normalized_turns = [item for item in turns if isinstance(item, dict)] if isinstance(turns, list) else []
    normalized_summary = summary if isinstance(summary, dict) else {}
    open_threads = normalized_summary.get("open_threads") if isinstance(normalized_summary.get("open_threads"), list) else []
    memory_facts = normalized_summary.get("memory_facts") if isinstance(normalized_summary.get("memory_facts"), list) else []
    short_summary = " ".join(str(normalized_summary.get("short_summary") or "").split()).strip()
    return {
        "present": True,
        "turns": len(normalized_turns),
        "summary_present": bool(normalized_summary),
        "short_summary_present": bool(short_summary),
        "open_threads": len([item for item in open_threads if str(item).strip()]),
        "memory_facts": len([item for item in memory_facts if str(item).strip()]),
    }


def _format_conversation_context_for_stage4(conversation_context: dict[str, Any] | None) -> str:
    if not isinstance(conversation_context, dict):
        return ""

    parts: list[str] = []
    summary = conversation_context.get("summary_for_llm")
    if isinstance(summary, dict):
        short_summary = " ".join(str(summary.get("short_summary") or "").split()).strip()
        if short_summary:
            parts.append(f"会话摘要：{short_summary}")
        open_threads = [str(item).strip() for item in list(summary.get("open_threads") or []) if str(item).strip()]
        if open_threads:
            parts.append(f"待继续话题：{'；'.join(open_threads)}")
        memory_facts = [str(item).strip() for item in list(summary.get("memory_facts") or []) if str(item).strip()]
        if memory_facts:
            parts.append(f"已知事实：{'；'.join(memory_facts)}")

    turns = conversation_context.get("recent_turns_for_llm")
    if isinstance(turns, list):
        rendered_turns: list[str] = []
        for item in turns:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = " ".join(str(item.get("content") or "").split()).strip()
            if not content:
                continue
            role_label = "用户" if role == "user" else "助手"
            rendered_turns.append(f"{role_label}: {content}")
        if rendered_turns:
            parts.append("最近对话：\n" + "\n".join(rendered_turns))

    return "\n\n".join(parts).strip()


def _format_answer_plan_for_stage4(answer_plan: Any) -> str:
    if not isinstance(answer_plan, dict) or not answer_plan:
        return ""
    try:
        return json.dumps(answer_plan, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return ""


def _expert_draft_block_for_stage4(*, deep_answer: str) -> str:
    """阶段一预回答：可选注入事实/受限合成，对齐旧版「专家初稿」参与角度与衔接（非事实源）。"""
    if not env_bool("QA_STAGE4_FACT_SYNTHESIS_INCLUDE_DEEP_ANSWER", False):
        return (
            "（当前未注入专家初稿。将环境变量 QA_STAGE4_FACT_SYNTHESIS_INCLUDE_DEEP_ANSWER=true "
            "后，将把阶段一预回答作为「仅结构、角度与衔接」参考注入本提示。）"
        )
    text = str(deep_answer or "").strip()
    if not text:
        return "（阶段一预回答为空。）"
    max_chars = env_int("QA_STAGE4_EXPERT_DRAFT_MAX_CHARS", 12000, minimum=0, maximum=100000)
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n…（以上已按 QA_STAGE4_EXPERT_DRAFT_MAX_CHARS 截断）"
    return text


def _extract_structure_from_deep_answer(deep_answer: str) -> Tuple[str, str]:
    text = str(deep_answer or "").strip()
    if not text:
        return "（无）", ""
    lines = [line.rstrip() for line in text.splitlines()]
    opening: List[str] = []
    outline: List[str] = []
    seen_heading = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        is_heading = bool(re.match(r"^#{2,6}\s+", stripped)) or bool(re.match(r"^\d+[.)、]\s+", stripped))
        if is_heading:
            seen_heading = True
            normalized = re.sub(r"^#{2,6}\s*", "", stripped)
            normalized = re.sub(r"^\d+[.)、]\s*", "", normalized).strip()
            if normalized:
                outline.append(normalized)
            continue
        if not seen_heading and len(opening) < 4:
            opening.append(stripped)
    opening_paragraph = "\n".join(opening).strip() if opening else "（无）"
    if not outline:
        return opening_paragraph, ""
    outline_text = "\n".join(f"{idx}. {title}" for idx, title in enumerate(outline[:12], 1))
    return opening_paragraph, outline_text


def iter_stage4_synthesis_with_pdf_chunks(
    *,
    user_question: str,
    deep_answer: str,
    pdf_chunks: dict[str, list[dict[str, Any]]],
    retrieval_results: dict[str, Any] | None,
    stage2_prompt: str,
    client: Any,
    model: str,
    safe_dict_cls: Any | None = None,
    escape_braces_fn: Callable[[str], str] | None = None,
    format_pdf_chunks_evidence_fn: Callable[[dict[str, list[dict[str, Any]]], str], str] | None = None,
    build_top5_reference_context_fn: Callable[..., Any] | None = None,
    extract_cited_dois_fn: Callable[..., Any] | None = None,
    log_top5_coverage_fn: Callable[..., None] | None = None,
    build_references_from_pdf_chunks_fn: Callable[..., list[dict[str, Any]]] | None = None,
    programmatic_insert_dois_fn: Callable[..., str] | None = None,
    align_dois_with_pdf_chunks_fn: Callable[..., str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    conversation_context: dict[str, Any] | None = None,
    answer_plan: dict[str, Any] | None = None,
    graph_fact_block: str = "",
    logger: Any,
) -> Any:
    def _cancelled() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            return False

    if _cancelled():
        yield {"success": False, "cancelled": True, "error": "cancelled"}
        return

    safe_dict_cls = safe_dict_cls or _SafeDict
    escape_braces_fn = escape_braces_fn or _escape_braces
    format_pdf_chunks_evidence_fn = format_pdf_chunks_evidence_fn or format_pdf_chunks_evidence
    build_top5_reference_context_fn = build_top5_reference_context_fn or build_top5_reference_context
    extract_cited_dois_fn = extract_cited_dois_fn or extract_cited_dois_with_logging
    log_top5_coverage_fn = log_top5_coverage_fn or log_top5_coverage
    build_references_from_pdf_chunks_fn = build_references_from_pdf_chunks_fn or build_references_from_pdf_chunks

    evidence_documents = format_pdf_chunks_evidence_fn(pdf_chunks, user_question)
    if not evidence_documents:
        logger.warning("stage4 synthesis skipped because evidence_documents is empty")
        yield {"success": False, "error": "no_pdf_chunks"}
        return

    logger.info(
        "stage4 synthesis start question_chars=%s deep_answer_chars=%s pdf_source_count=%s evidence_chars=%s retrieval_metadata_count=%s",
        len(str(user_question or "")),
        len(str(deep_answer or "")),
        len(pdf_chunks),
        len(evidence_documents),
        len(list((retrieval_results or {}).get("metadatas") or [])),
    )

    try:
        stage4_topk = env_int("QA_STAGE4_REFERENCE_TOPK", 5, minimum=3, maximum=20)
        stage4_min_citations = env_int("QA_STAGE4_MIN_CITATIONS", 4, minimum=1, maximum=20)
        if stage4_min_citations > stage4_topk:
            stage4_min_citations = stage4_topk
        stage4_element_guard = env_bool("QA_STAGE4_ELEMENT_GUARD", True)
        stage4_citation_verify = env_bool(
            "QA_STAGE4_CITATION_VERIFY_AFTER_SYNTHESIS",
            env_bool("CITATION_VERIFY_AFTER_SYNTHESIS", True),
        )
        use_two_stage = env_bool(
            "QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED",
            env_bool("TWO_STAGE_SYNTHESIS", False),
        )
        use_structure_only = env_bool(
            "QA_STAGE4_STRUCTURE_ONLY_MODE",
            env_bool("USE_STRUCTURE_ONLY_SYNTHESIS", False),
        )

        top5_with_scores, top5_reference_list = build_top5_reference_context_fn(
            retrieval_results=retrieval_results,
            logger=logger,
            topk=stage4_topk,
            min_citations=stage4_min_citations,
            element_guard=stage4_element_guard,
            user_question=user_question,
            pdf_chunks=pdf_chunks,
        )
        logger.info(
            "stage4 reference policy topk=%s min_citations=%s element_guard=%s citation_verify=%s two_stage=%s structure_only=%s top_ref_count=%s top_ref_sample=%s",
            stage4_topk,
            stage4_min_citations,
            stage4_element_guard,
            stage4_citation_verify,
            use_two_stage,
            use_structure_only,
            len(top5_with_scores),
            [doi for doi, _score in top5_with_scores[:5]],
        )

        answer_plan_block = _format_answer_plan_for_stage4(answer_plan)
        expert_draft_block = _expert_draft_block_for_stage4(deep_answer=deep_answer)
        safe_kwargs = safe_dict_cls(
            user_question=escape_braces_fn(user_question),
            deep_answer=escape_braces_fn(deep_answer),
            evidence_documents=escape_braces_fn(evidence_documents),
            top5_references=escape_braces_fn(top5_reference_list),
            answer_plan=escape_braces_fn(answer_plan_block),
        )
        prompt = ""
        prompt_mode = "legacy_stage2_prompt"
        fact_mode_allowed_dois: Set[str] = set()
        fact_mode_fact_text_by_doi: Dict[str, str] = {}
        if use_two_stage:
            comparison_objects = _format_comparison_objects_for_fact_extraction(retrieval_results)
            facts = _extract_facts_for_stage4_two_stage(
                pdf_chunks=pdf_chunks,
                evidence_documents=evidence_documents,
                user_question=user_question,
                comparison_objects=comparison_objects,
                client=client,
                model=model,
                logger=logger,
                format_pdf_chunks_evidence_fn=format_pdf_chunks_evidence_fn,
            )
            if not facts:
                logger.info("stage4 fact extraction summary fact_cards=0 distinct_fact_dois=0 doi_sample=[]")
            if facts:
                fact_mode_allowed_dois = {_canonicalize_doi(item.get("doi", "")) for item in facts if item.get("doi")}
                fact_mode_fact_text_by_doi = _build_fact_text_by_doi(facts)
                logger.info(
                    "stage4 fact extraction summary fact_cards=%s distinct_fact_dois=%s doi_sample=%s",
                    len(facts),
                    len(fact_mode_allowed_dois),
                    sorted(fact_mode_allowed_dois)[:15],
                )
                prompt_mode = "two_stage_fact_synthesis"
                prompt = STAGE4_FACT_SYNTHESIS_PROMPT.format_map(
                    safe_dict_cls(
                        user_question=escape_braces_fn(user_question),
                        facts_list=escape_braces_fn(_format_facts_for_prompt(facts)),
                        answer_plan=escape_braces_fn(answer_plan_block),
                        expert_draft=escape_braces_fn(expert_draft_block),
                    )
                )
            elif env_bool("QA_STAGE4_REQUIRE_FACTS_FOR_DOI_SYNTHESIS", False):
                fallback_mode = str(os.getenv("QA_STAGE4_EMPTY_FACTS_FALLBACK_MODE", "restricted_synthesis") or "").strip().lower()
                if fallback_mode == "restricted_synthesis" and evidence_documents.strip():
                    logger.warning("Stage4 two-stage fact extraction returned no citable facts; using restricted evidence synthesis")
                    prompt_mode = "restricted_synthesis_empty_fact_cards"
                    prompt = STAGE4_RESTRICTED_SYNTHESIS_PROMPT.format_map(
                        safe_dict_cls(
                            user_question=escape_braces_fn(user_question),
                            evidence_documents=escape_braces_fn(evidence_documents),
                            top5_references=escape_braces_fn(top5_reference_list),
                            answer_plan=escape_braces_fn(answer_plan_block),
                            expert_draft=escape_braces_fn(expert_draft_block),
                        )
                    )
                else:
                    logger.warning("Stage4 two-stage fact extraction returned no citable facts; skipping legacy DOI synthesis fallback")
                    yield {
                        "success": True,
                        "final_answer": "当前检索证据不足以生成带 DOI 的可靠结论。请扩大检索条件或补充更直接的原文证据后再试。",
                        "query_mode": "生成驱动检索（可引用事实不足）",
                        "references": [],
                        "cited_dois": [],
                    }
                    return
        if not prompt and use_structure_only and not use_two_stage:
            opening_paragraph, structure_outline = _extract_structure_from_deep_answer(deep_answer)
            if structure_outline:
                prompt_mode = "structure_only"
                prompt = STAGE4_STRUCTURE_ONLY_PROMPT.format_map(
                    safe_dict_cls(
                        user_question=escape_braces_fn(user_question),
                        opening_paragraph=escape_braces_fn(opening_paragraph),
                        structure_outline=escape_braces_fn(structure_outline),
                        evidence_documents=escape_braces_fn(evidence_documents),
                        top5_references=escape_braces_fn(top5_reference_list),
                        answer_plan=escape_braces_fn(answer_plan_block),
                    )
                )
        if not prompt:
            prompt = stage2_prompt.format_map(safe_kwargs)

        if graph_fact_block:
            prompt = (
                "以下是图谱结构化事实，只能作为补充线索，不能覆盖 PDF 证据：\n"
                f"{graph_fact_block}\n\n"
                f"{prompt}"
            )

        comparison_contract = _format_comparison_evidence_contract(retrieval_results)
        if comparison_contract:
            prompt = f"{comparison_contract}\n\n{prompt}"

        conversation_context_block = _format_conversation_context_for_stage4(conversation_context)
        if conversation_context_block:
            context_log = _summarize_conversation_context_for_log(conversation_context)
            logger.info(
                "stage4 conversation context attached turns=%s summary_present=%s short_summary_present=%s open_threads=%s memory_facts=%s",
                context_log["turns"],
                context_log["summary_present"],
                context_log["short_summary_present"],
                context_log["open_threads"],
                context_log["memory_facts"],
            )
            prompt = (
                "以下是当前会话上下文，仅用于承接当前问题与上文指代，不能覆盖文献证据：\n"
                f"{conversation_context_block}\n\n"
                f"{prompt}"
            )

        logger.info(
            "stage4 prompt prepared mode=%s prompt_chars=%s top_reference_list_chars=%s",
            prompt_mode,
            len(prompt),
            len(top5_reference_list),
        )

        summary_enabled = summary_experiment_enabled()
        summary_instruction = build_summary_instruction(enabled=summary_enabled)

        citation_requirement = f"必须至少引用{stage4_min_citations}篇不同的文献（最多{stage4_topk}篇）"
        detail_requirement = (
            "每个核心要点必须包含机理解释与定量信息，并点明该信息与用户问题的关联；"
            "带 `(doi=…)` 的句子不能只堆数字，须在同句或紧邻一句内完成「证据结论 + 为何重要/如何作用」的简要专业解读"
        )
        fact_mode_rules = ""
        if fact_mode_allowed_dois:
            citation_requirement = "引用数量以事实列表中实际可用 DOI 为上限；不要为了满足引用数量而新增或替换 DOI"
            detail_requirement = (
                "每个核心要点优先使用事实列表中的机理解释与定量信息，并说明其对用户问题的含义；"
                "每条带 `(doi=…)` 的文献论断须让读者看出「文献里是什么、对问题意味着什么」；"
                "不要为了满足定量信息要求编造证据中没有的数字"
            )
            diversity_n = min(stage4_min_citations, len(fact_mode_allowed_dois))
            diversity_block = ""
            if len(fact_mode_allowed_dois) >= 2:
                diversity_block = f"""
- **多文献分散（事实列表含 {len(fact_mode_allowed_dois)} 个不同 DOI）**：正文中须出现 **至少 {diversity_n} 个不同的** `(doi=…)`，且应分布在**不同分点或小节**（例如「主要发现」里至少两条分别对应不同 doi），不得用同一篇文献的 `(doi=…)` 串起全部独立实证句而把其他列表中的 DOI 闲置。
- 若某一论点只能由同一篇支撑，可连续使用该 doi，但**不得**在列表有多篇可用时，把可分到其他 doi 的要点也全部写成同一 doi。"""
            fact_mode_rules = f"""

## 事实列表引用约束：
- 事实列表中没有的 DOI 禁止引用，即使它出现在专家初稿、参考文献列表或 PDF 证据之外的上下文中。
- 不要为了满足引用数量而添加事实列表之外的 DOI。
- 不要为了满足定量信息要求编造事实列表中没有的数值、比例、活化能、产业占比或成本差异。
{diversity_block}"""

        system_prompt = f"""你是一位严谨的材料科学文献分析专家，擅长将专业知识与文献证据有机结合。

## 任务要求：
1. 根据提供的PDF原文证据生成答案
2. 在答案的相关句子末尾插入DOI引用，而不是在答案最后列出
3. {citation_requirement}
4. 每个带 `(doi=…)` 的句子末尾只插入 1 个最相关 DOI；若需多文献对比，请拆成多句，每句一 DOI
5. {detail_requirement}
6. 不要输出步骤描述到最终答案

## 叙述结构：领域常识与文献实证兼顾（重要）
- 允许先用简短段落交代问题背景、术语界定或工程常识（可与专家初稿/结构化计划对齐），**此类句子不要写 `(doi=…)`**，也不得把未出现在证据中的具体数值写成文献结论。
- 进入实证部分时，每个主要论点应形成「证据数据/结论 → 机理或与问题的关联 → `(doi=…)`」的完整链条，避免只有碎片摘录而无分析。
- 禁止把全文写成无衔接的要点列表；各节之间用过渡句承接，使读者能同时获得「懂问题」与「信文献」两层信息。

## 引用规则：
- 正确：句子内容 + 空格 + `(doi=xxx)`
- 错误：句子内容 + 空格 + `(10.xxx/yyy)`
- 错误：在答案最后统一列出所有DOI
- 错误：一句话引用多个DOI
{fact_mode_rules}
"""
        if summary_instruction:
            system_prompt = f"{system_prompt}{summary_instruction}"

        stream_user_prompt = prompt
        stream_temperature = 0.3
        if prompt_mode == "two_stage_fact_synthesis" and fact_mode_allowed_dois and len(fact_mode_allowed_dois) >= 2:
            diversity_user_n = min(stage4_min_citations, len(fact_mode_allowed_dois))
            header = (
                f"【硬性要求·置顶】事实卡片含 {len(fact_mode_allowed_dois)} 个不同 DOI。"
                f"正文须出现 **至少 {diversity_user_n} 个不同的** `(doi=…)`（每个一条独立实证论述），"
                f"且不得把可分到其它 DOI 的要点仍全部写在同一篇下；"
                f"若少于 {diversity_user_n} 个不同 `(doi=…)` 视为未完成本题。\n\n"
            )
            footer = (
                f"\n\n---\n【输出前再检】至少 {diversity_user_n} 个不同 `(doi=…)`；"
                f"「主要发现」中请交替使用多篇卡片对应的不同 doi。\n"
            )
            stream_user_prompt = header + prompt + footer
            if len(fact_mode_allowed_dois) >= 3:
                raw_t = str(os.getenv("QA_STAGE4_FACT_MULTI_DOI_STREAM_TEMPERATURE", "0.12") or "").strip()
                try:
                    stream_temperature = max(0.0, min(float(raw_t), 0.5))
                except ValueError:
                    stream_temperature = 0.12
        logger.info(
            "stage4 llm request start model=%s prompt_mode=%s prompt_chars=%s stream_user_chars=%s top_reference_list_chars=%s",
            model,
            prompt_mode,
            len(prompt),
            len(stream_user_prompt),
            len(top5_reference_list),
        )
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": stream_user_prompt},
            ],
            temperature=stream_temperature,
            max_tokens=4000,
            stream=True,
        )
        final_chunks: list[str] = []
        first_chunk_logged = False
        stream_started = time.perf_counter()
        for chunk in stream:
            if _cancelled():
                yield {"success": False, "cancelled": True, "error": "cancelled"}
                return
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None)
            if not content:
                continue
            text = str(content)
            final_chunks.append(text)
            if not first_chunk_logged:
                first_chunk_logged = True
                logger.info(
                    "stage4 llm first chunk received chunk_chars=%s elapsed_ms=%.3f",
                    len(text),
                    (time.perf_counter() - stream_started) * 1000,
                )
            yield text

        final_answer = "".join(final_chunks).strip()
        logger.info(
            "stage4 llm stream completed chunk_count=%s answer_chars=%s elapsed_ms=%.3f",
            len(final_chunks),
            len(final_answer),
            (time.perf_counter() - stream_started) * 1000,
        )

        def _refresh_cited_dois(answer: str) -> tuple[list[str], set[str]]:
            cited_dois_result = extract_cited_dois_fn(final_answer=answer, logger=logger)
            if isinstance(cited_dois_result, tuple) and len(cited_dois_result) == 2:
                return list(cited_dois_result[0] or []), set(cited_dois_result[1] or set())
            cited_dois = list(cited_dois_result or [])
            return cited_dois, set(cited_dois)

        def _validate_answer(answer: str, *, suffix: str = "") -> str:
            if not stage4_citation_verify:
                return answer
            if fact_mode_allowed_dois:
                cleaned_answer, _valid_dois, invalid_dois = _validate_answer_dois_with_allowed_dois(
                    answer=answer,
                    allowed_dois=fact_mode_allowed_dois,
                )
                if invalid_dois:
                    logger.warning("Stage4 removed DOI references outside fact list%s: %s", suffix, invalid_dois)
                cleaned_answer = _remove_fact_mode_unsupported_cited_lines(
                    answer=cleaned_answer,
                    fact_text_by_doi=fact_mode_fact_text_by_doi,
                    logger=logger,
                )
                return cleaned_answer
            if not pdf_chunks:
                return answer
            cleaned_answer, _valid_dois, invalid_dois = _validate_answer_dois_with_pdf_chunks(
                answer=answer,
                pdf_chunks=pdf_chunks,
            )
            if invalid_dois:
                logger.warning("Stage4 removed invalid DOI references%s: %s", suffix, invalid_dois)
            return cleaned_answer

        final_answer = _validate_answer(final_answer)
        cited_dois, cited_dois_set = _refresh_cited_dois(final_answer)
        logger.info(
            "stage4 cited DOI summary before repair count=%s dois=%s",
            len(cited_dois),
            cited_dois[:10],
        )

        if (
            stage4_citation_verify
            and retrieval_results is not None
            and programmatic_insert_dois_fn is not None
            and len(cited_dois) < stage4_min_citations
            and not fact_mode_allowed_dois
        ):
            logger.info(
                "stage4 programmatic DOI repair triggered cited_before=%s min_required=%s",
                len(cited_dois),
                stage4_min_citations,
            )
            try:
                repaired_answer = str(
                    programmatic_insert_dois_fn(
                        answer=final_answer,
                        retrieval_results=retrieval_results,
                        similarity_threshold=None,
                        question=user_question,
                    ) or ""
                ).strip()
                if repaired_answer and repaired_answer != final_answer:
                    final_answer = _validate_answer(repaired_answer, suffix=" after programmatic insertion")
                    cited_dois, cited_dois_set = _refresh_cited_dois(final_answer)
                logger.info(
                    "stage4 programmatic DOI repair finished changed=%s cited_after=%s dois=%s",
                    bool(repaired_answer and repaired_answer != final_answer),
                    len(cited_dois),
                    cited_dois[:10],
                )
            except Exception as exc:
                logger.warning("Stage4 programmatic DOI insertion failed: %s", exc)

        if (
            stage4_citation_verify
            and not cited_dois
            and pdf_chunks
            and align_dois_with_pdf_chunks_fn is not None
            and not fact_mode_allowed_dois
        ):
            logger.info("stage4 fallback DOI alignment triggered because cited_dois is empty")
            try:
                aligned_answer = str(
                    align_dois_with_pdf_chunks_fn(
                        final_answer,
                        pdf_chunks,
                        user_question=user_question,
                    ) or ""
                ).strip()
                if aligned_answer and aligned_answer != final_answer:
                    final_answer = _validate_answer(aligned_answer, suffix=" after fallback alignment")
                    cited_dois, cited_dois_set = _refresh_cited_dois(final_answer)
                logger.info(
                    "stage4 fallback DOI alignment finished changed=%s cited_after=%s dois=%s",
                    bool(aligned_answer and aligned_answer != final_answer),
                    len(cited_dois),
                    cited_dois[:10],
                )
            except Exception as exc:
                logger.warning("Stage4 DOI fallback alignment failed: %s", exc)

        comparison_validation = validate_comparison_answer(final_answer, retrieval_results=retrieval_results)
        if comparison_validation.get("changed"):
            final_answer = str(comparison_validation.get("answer") or final_answer)
            cited_dois, cited_dois_set = _refresh_cited_dois(final_answer)
            logger.info(
                "stage4 comparison validation appended note missing_objects=%s insufficient_objects=%s",
                comparison_validation.get("missing_objects"),
                comparison_validation.get("insufficient_objects"),
            )

        final_answer, summary_meta = apply_answer_summary_experiment(
            final_answer,
            enabled=summary_enabled,
        )
        cited_dois, cited_dois_set = _refresh_cited_dois(final_answer)
        logger.info(
            "stage4 answer summary experiment enabled=%s generated=%s format=%s length=%s has_citation=%s skipped_reason=%s",
            summary_meta.get("enabled"),
            summary_meta.get("generated"),
            summary_meta.get("format"),
            summary_meta.get("length"),
            summary_meta.get("has_citation"),
            summary_meta.get("skipped_reason"),
        )

        try:
            log_top5_coverage_fn(cited_dois_set=cited_dois_set, top5_with_scores=top5_with_scores, logger=logger)
        except Exception as exc:
            logger.warning("Stage4 top-k coverage logging failed: %s", exc)

        try:
            references = build_references_from_pdf_chunks_fn(cited_dois=cited_dois, pdf_chunks=pdf_chunks)
        except Exception as exc:
            logger.warning("Stage4 reference building failed: %s", exc)
            references = []
        logger.info(
            "stage4 references built count=%s sample=%s",
            len(references),
            [item.get("doi") for item in references[:10]],
        )

        logger.info(
            "stage4 synthesis succeeded final_answer_chars=%s cited_doi_count=%s references=%s",
            len(final_answer),
            len(cited_dois),
            len(references),
        )
        yield {
            "success": True,
            "final_answer": final_answer,
            "references": references,
            "cited_dois": cited_dois,
            "source_count": len(pdf_chunks),
        }
    except Exception as exc:
        raise_if_upstream_pool_timeout(exc)
        logger.error("stage4 synthesis failed: %s", exc, exc_info=True)
        yield {"success": False, "error": str(exc)}


__all__ = [
    "build_references_from_pdf_chunks",
    "extract_cited_dois",
    "format_pdf_chunks_evidence",
    "iter_stage4_synthesis_with_pdf_chunks",
]
