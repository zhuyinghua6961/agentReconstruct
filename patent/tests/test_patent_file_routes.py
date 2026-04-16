from __future__ import annotations

import re
import sys
import pytest
from pathlib import Path

import server.patent.file_routes as file_routes_module
import server.patent.pdf_service as pdf_service_module
import server.patent.tabular_service as tabular_service_module
from server.patent.cache_keys import build_file_route_cache_fingerprint
from server.patent.file_contract import build_patent_file_contract
from server.patent.file_routes import _file_route_runtime_signature, dispatch_patent_file_route, plan_patent_file_route
from server.patent.hybrid_synthesis import HYBRID_SYNTHESIS_PROMPT_VERSION
from server.patent.pdf_contract import format_multi_pdf_sections
from server.patent.pdf_service import PatentPdfAnswerClient, PatentPdfService
from server.patent.tabular_service import PatentTabularService


PDF_FILE = {
    "file_id": 11,
    "file_type": "pdf",
    "file_name": "battery-paper.pdf",
}

PDF_FILE_2 = {
    "file_id": 12,
    "file_type": "pdf",
    "file_name": "battery-paper-2.pdf",
}

TABLE_FILE = {
    "file_id": 33,
    "file_type": "xlsx",
    "file_name": "cells.xlsx",
}


def _section_body(markdown: str, heading: str) -> str:
    text = str(markdown or "")
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    next_heading = text.find("\n## ", start)
    if next_heading < 0:
        return text[start:].strip()
    return text[start:next_heading].strip()


def _first_bullet(markdown: str, heading: str) -> str:
    body = _section_body(markdown, heading)
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            return line[2:].strip()
    return body.strip()


def _write_csv(path: Path) -> None:
    path.write_text(
        "material,capacity_mAh,note\n"
        "LMFP,120,stable\n"
        "LFP,115,safe\n"
        "NCM,140,higher energy\n",
        encoding="utf-8",
    )


def _write_alt_csv(path: Path) -> None:
    path.write_text(
        "rate_c,temp_c,score\n"
        "1,25,0.81\n"
        "2,35,0.78\n"
        "3,45,0.73\n",
        encoding="utf-8",
    )


def _build_valid_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：围绕方案 {index} 展开研究，并给出明确的中文结论。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：采用表征测试与性能验证结合的方法，重点分析方案 {index}。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：面向应用方向 {index} 的性能优化场景。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 所有文献都提供了可比较的实验结论。",
            "",
            "## 总结",
            "- 这些文献展示了不同技术路线下的差异化优化方向。",
        ]
    )
    return "\n".join(lines)


def _build_valid_compare_answer_out_of_order(labels: list[str]) -> str:
    ordered = _build_valid_compare_answer(labels)
    content_blocks = []
    for heading in ["相同点", "总结", "应用领域差异", "研究方法差异", "具体内容对比"]:
        marker = f"## {heading}"
        start = ordered.index(marker)
        next_positions = [ordered.find(f"\n## {candidate}", start + 1) for candidate in ["具体内容对比", "研究方法差异", "应用领域差异", "相同点", "总结"]]
        next_positions = [position for position in next_positions if position > start]
        end = min(next_positions) if next_positions else len(ordered)
        content_blocks.append(ordered[start:end].strip())
    return "\n\n".join(content_blocks)


def _build_rich_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：围绕方案 {index} 的研究背景展开，并说明当前问题设置。",
                f"- {label}：给出了方案 {index} 的关键实验流程与验证路径。",
                f"- {label}：补充了方案 {index} 的定量结果与应用解释。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：先完成样品制备与分组设计。",
                f"- {label}：再进行表征测试与对照实验。",
                f"- {label}：最后结合定量分析总结方法差异。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：面向场景 {index} 的性能优化。",
                f"- {label}：强调场景 {index} 的部署限制。",
                f"- {label}：补充场景 {index} 的应用价值。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 所有文献都提供了逐篇可比的实验设计。",
            "- 所有文献都给出了中文结果总结。",
            "",
            "## 总结",
            "- 这些文献展示了不同方案在方法路径和应用场景上的差异化取舍。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_dominant_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，因此当前不能形成可靠的逐篇比较结论，只能提示用户补充原文证据后再比较。",
                "- 原文证据不足，因此当前不能展开研究对象、方法、结果或应用场景的可靠差异，只能提示用户补充完整原文。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, _label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                "- PDF中未提及该维度的具体信息，因此当前不能形成可靠的方法差异结论，只能提示用户补充原文证据后再比较。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, _label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                "- 原文证据不足，因此当前不能展开应用领域、部署场景或价值差异，只能提示用户补充完整原文。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 原文证据不足，因此当前不能形成可靠共同点，只能提示用户补充完整原文后再比较。",
            "",
            "## 总结",
            "- PDF中未提及该维度的具体信息，因此当前不能形成可靠的逐篇比较结论，只能提示用户补充原文证据后再比较。",
        ]
    )
    return "\n".join(lines)


def _build_mixed_placeholder_and_fact_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：未提及明确的应用场景边界，但报告了方案 {index} 的循环寿命提升 20% 和稳定性验证结果。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：证据不足以判断是否包含额外消融实验，但明确采用了分组对照和定量指标评估方案 {index}。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足以扩展到其他部署场景，但原文明确将方案 {index} 用于高负载连续运行条件。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献都在证据边界内报告了可比较的定量验证结果。",
            "",
            "## 总结",
            "- 两篇文献均保留了具体事实，同时明确说明了原文没有覆盖的比较边界。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但仍需要更多测试验证后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但仍需要补充更多评估后才能判断方法差异。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但仍需要后续对比分析后才能形成可靠判断。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只能提示需要更多测试验证。",
            "",
            "## 总结",
            "- 当前比较结果仍被占位式未来工作描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_tokens_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但仍需要更多 XRD 测试验证后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但仍需要补充 3 组测试后才能形成可靠判断。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但仍需要更多 SEM 对比后才能判断应用边界。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在提示需要更多 XRD 或 SEM 验证。",
            "",
            "## 总结",
            "- 当前比较结果仍被带英文术语和数字的未来工作占位描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_action_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但仍需要采用 XRD 进行更多测试后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但仍需要使用 3 组补充实验后才能形成可靠判断。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但仍需要构建更多对照验证后才能明确应用边界。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在提示后续需要采用更多验证手段。",
            "",
            "## 总结",
            "- 当前比较结果仍被带动作词的未来工作占位描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_mixed_placeholder_with_punctuation_fact_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：未提及明确的应用场景边界，报告了方案 {index} 的循环寿命提升 20%。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：证据不足，原文明确采用了分组对照和定量指标评估方案 {index}。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，原文明确将方案 {index} 用于高负载连续运行条件。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献都在证据边界内保留了具体事实。",
            "",
            "## 总结",
            "- 两篇文献均用标点边界同时表达了原文缺口和已确认事实。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_reporting_verbs_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但报告需要更多测试后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但结果显示进一步验证后才能形成可靠判断。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但表明后续补充原文后才能判断应用边界。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在提示未来还需要更多验证。",
            "",
            "## 总结",
            "- 当前比较结果仍被带报告动词的占位式 future-work 描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_mixed_placeholder_with_reporting_fact_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：未提及明确的应用场景，结果显示方案 {index} 存在明显峰位变化。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：证据不足，原文表明方案 {index} 采用了分层对照实验。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，报告显示方案 {index} 适用于连续运行工况。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献都保留了纯中文 reporting-fact 证据。",
            "",
            "## 总结",
            "- 两篇文献都同时说明了证据边界和已确认的中文事实载荷。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_trailing_after_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，采用对照实验后才能形成可靠判断。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，使用更多样本后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，构建补充实验后才能判断应用边界。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在描述 future-work 依赖条件。",
            "",
            "## 总结",
            "- 当前比较结果仍被以“后才能”结尾的占位式 future-work 描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_numeric_subject_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但结果显示 3 组样品仍需要更多测试后才能形成可靠判断。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但报告了 3 组样品仍需要更多测试后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但表明 2 个工况仍需要进一步验证后才能判断应用边界。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在描述带数字主语的 future-work 依赖条件。",
            "",
            "## 总结",
            "- 当前比较结果仍被带数字主语的占位式 future-work 描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_reporting_payload_nouns_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但表明 2 个工况仍需要进一步验证后才能判断应用边界。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但报告显示性能指标仍需要更多测试后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但结果显示容量数据仍需要进一步验证后才能判断差异。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在描述带名词载荷的 future-work 依赖条件。",
            "",
            "## 总结",
            "- 当前比较结果仍被带结果名词的占位式 future-work 描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_mixed_placeholder_with_concise_reporting_fact_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：未提及应用场景，结果显示峰位变化。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：证据不足，表明性能提升。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，报告显示适用于连续运行工况。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献都保留了短句 reporting-fact 证据。",
            "",
            "## 总结",
            "- 两篇文献都同时说明了证据边界和短句形式的已确认事实。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_reporting_payload_keywords_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但结果显示峰位仍需要进一步验证后才能形成可靠判断。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但报告显示差异仍需要更多测试后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但表明寿命仍需要进一步验证后才能判断差异。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在描述带结果名词的 future-work 依赖条件。",
            "",
            "## 总结",
            "- 当前比较结果仍被带结果名词的占位式 future-work 描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_action_subject_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但采用更多样本仍需要更多测试后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但使用补充实验仍需要进一步验证后才能形成可靠判断。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但构建更多对照仍需要进一步验证后才能判断应用边界。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在描述带强动作词的 future-work 主语。",
            "",
            "## 总结",
            "- 当前比较结果仍被带强动作词的占位式 future-work 描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_reporting_derived_nouns_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但报告显示稳定性仍需要更多测试后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但表明效率值仍需要进一步验证后才能判断差异。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但结果显示适用性仍需要进一步确认后才能形成可靠判断。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在描述带派生名词的 future-work 依赖条件。",
            "",
            "## 总结",
            "- 当前比较结果仍被带派生名词的占位式 future-work 描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_reporting_result_phrase_subject_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但报告显示适用于连续运行工况仍需要进一步验证后才能形成可靠判断。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但结果显示性能提升效果仍需要进一步确认后才能形成可靠结论。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但表明含量变化趋势仍需要更多测试后才能判断差异。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在描述结果短语作为 future-work 主语。",
            "",
            "## 总结",
            "- 当前比较结果仍被带结果短语主语的占位式 future-work 描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_mixed_placeholder_with_conjunction_caveat_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：未提及应用场景，但结果显示峰位变化并需要进一步验证。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：未提及应用场景，但报告了循环寿命提升20%并需要更多测试。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：未提及应用场景，但使用 TOF-SIMS 可视化分布并需要进一步验证。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献都保留了事实后接 caveat 的 mixed-fact 证据。",
            "",
            "## 总结",
            "- 两篇文献都同时说明了已确认事实和仍需验证的边界。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_weak_conjunction_caveat_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但结果显示变化并需要进一步验证。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但表明适用于工况并需要进一步验证。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但采用分析并需要进一步验证。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在描述弱事实词后接 caveat。",
            "",
            "## 总结",
            "- 当前比较结果仍被弱事实词加 caveat 的占位式描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_mixed_placeholder_with_chinese_method_conjunction_caveat_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：未提及应用场景，但采用分层对照实验并需要进一步验证。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：未提及应用场景，但使用原位退火策略并需要更多测试。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：未提及应用场景，但提出双层包覆方案并需要进一步验证。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献都保留了中文方法事实后接 caveat 的 mixed-fact 证据。",
            "",
            "## 总结",
            "- 两篇文献都同时说明了中文方法事实和仍需验证的边界。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_weak_method_noun_caveat_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但提出补充方案并需要进一步验证。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但构建更多对照体系并需要进一步确认。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但使用额外退火策略并需要更多测试。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在描述带弱方法名词的 future-work 主语。",
            "",
            "## 总结",
            "- 当前比较结果仍被弱方法名词加 caveat 的占位式描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_placeholder_future_work_with_weak_method_noun_variants_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：PDF中未提及该维度的具体信息，但提出相关方案并需要进一步验证。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：原文证据不足，但构建预备体系并需要进一步确认。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：信息不足，但采用此包覆方案并需要进一步验证。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献当前都只是在描述弱指代方法名词的 future-work 主语。",
            "",
            "## 总结",
            "- 当前比较结果仍被弱指代方法名词加 caveat 的占位式描述主导，不能作为正式结论。",
        ]
    )
    return "\n".join(lines)


def _build_truncation_leak_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：仅保留原始内容的 0.32%，因此难以确定具体研究内容。",
                f"- {label}：原始 5064 字符，保留 134 字符。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：由于截断比例过高，方法细节难以展开。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：当前只能看到被截断后的片段信息。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 两篇文献都泄漏了内部截断诊断信息。",
            "",
            "## 总结",
            "- 当前比较结果被内部截断信息主导，不能作为正式对比结论。",
        ]
    )
    return "\n".join(lines)


def test_build_patent_file_contract_consumes_gateway_canonical_fields_without_recanonicalizing():
    contract = build_patent_file_contract(
        route="hybrid_qa",
        source_scope="pdf+table+kb",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[PDF_FILE, TABLE_FILE],
        file_selection={
            "strategy": "explicit_selection",
            "selected_file_ids": [11, 33],
            "source_scope": "pdf+table+kb",
        },
        kb_enabled=True,
        allow_kb_verification=True,
    )

    assert contract.route == "hybrid_qa"
    assert contract.source_scope == "pdf+table+kb"
    assert contract.selected_file_ids == [11, 33]
    assert contract.primary_file_id == 11
    assert [item.family for item in contract.execution_files] == ["pdf", "table"]
    assert contract.file_selection["source_scope"] == "pdf+table+kb"
    assert contract.includes_kb is True


def test_build_patent_file_contract_rejects_source_scope_that_disagrees_with_selected_files():
    with pytest.raises(ValueError, match="source_scope"):
        build_patent_file_contract(
            route="hybrid_qa",
            source_scope="pdf+table",
            selected_file_ids=[11],
            primary_file_id=11,
            execution_files=[PDF_FILE],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=False,
            allow_kb_verification=False,
        )


def test_build_patent_file_contract_rejects_kb_scope_without_allow_kb_verification():
    with pytest.raises(ValueError, match="allow_kb_verification"):
        build_patent_file_contract(
            route="hybrid_qa",
            source_scope="pdf+kb",
            selected_file_ids=[11],
            primary_file_id=11,
            execution_files=[PDF_FILE],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=True,
            allow_kb_verification=False,
        )


def test_build_patent_file_contract_rejects_selected_files_outside_source_scope():
    with pytest.raises(ValueError, match="selected files"):
        build_patent_file_contract(
            route="pdf_qa",
            source_scope="pdf",
            selected_file_ids=[11, 33],
            primary_file_id=11,
            execution_files=[PDF_FILE, TABLE_FILE],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=False,
            allow_kb_verification=False,
        )


def test_build_patent_file_contract_accepts_xlsm_and_legacy_xls_payload():
    contract = build_patent_file_contract(
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "xlsm", "file_name": "cells.xlsm"}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    assert contract.execution_files[0].file_type == "xlsm"
    legacy = build_patent_file_contract(
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "excel", "file_name": "legacy.xls", "local_path": "/tmp/legacy.xls"}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    assert legacy.execution_files[0].file_type == "excel"

    with pytest.raises(ValueError, match="unsupported spreadsheet extension"):
        build_patent_file_contract(
            route="tabular_qa",
            source_scope="table",
            selected_file_ids=[33],
            primary_file_id=33,
            execution_files=[{"file_id": 33, "file_type": "table", "file_name": "cells.ods", "local_path": "/tmp/upload_blob"}],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=False,
            allow_kb_verification=False,
        )


@pytest.mark.parametrize(
    ("selected_file_ids", "primary_file_id", "execution_file_id"),
    [
        ([True], 33, 33),
        ([33], True, 33),
        ([33], 33, True),
        ([33], 33, 33.2),
    ],
)
def test_build_patent_file_contract_rejects_non_integer_file_identifiers(
    selected_file_ids,
    primary_file_id,
    execution_file_id,
):
    with pytest.raises(ValueError, match="file_id|selected_file_ids|primary_file_id"):
        build_patent_file_contract(
            route="tabular_qa",
            source_scope="table",
            selected_file_ids=selected_file_ids,
            primary_file_id=primary_file_id,
            execution_files=[{"file_id": execution_file_id, "file_type": "csv", "file_name": "cells.csv"}],
            file_selection={"strategy": "explicit_selection"},
            kb_enabled=False,
            allow_kb_verification=False,
        )


def test_dispatch_pdf_route_uses_patent_pdf_service():
    contract = build_patent_file_contract(
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[PDF_FILE],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert result["handler"] == "pdf"
    assert result["route"] == "pdf_qa"
    assert result["source_scope"] == "pdf"
    assert result["query_mode"] == "patent_pdf_qa"
    assert result["answer_text"]
    assert result["used_files"] == [PDF_FILE]
    assert result["steps"][0]["title"] == "进入 PDF 分支"
    assert result["timings"]["patent_pdf_route_ms"] == 1
    assert result["kb_enabled"] is False


def test_dispatch_only_uses_gateway_selected_files_when_execution_pool_is_larger():
    contract = build_patent_file_contract(
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[PDF_FILE, PDF_FILE_2],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11]},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert result["used_files"] == [PDF_FILE]
    assert result["selected_file_ids"] == [11]


def test_build_patent_file_contract_ignores_unselected_unsupported_execution_files():
    contract = build_patent_file_contract(
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[PDF_FILE, {"file_id": 99, "file_type": "docx", "file_name": "ignored.docx"}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11]},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    assert [item.file_id for item in contract.execution_files] == [11]


def test_dispatch_pdf_route_honors_primary_file_id_and_redacts_local_path():
    contract = build_patent_file_contract(
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=12,
        execution_files=[
            {**PDF_FILE, "local_path": "/tmp/first.pdf"},
            {**PDF_FILE_2, "local_path": "/tmp/second.pdf"},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert [item["file_id"] for item in result["used_files"]] == [12, 11]
    assert "local_path" not in result["used_files"][0]
    assert "local_path" not in result["used_files"][1]


def test_tabular_service_reads_legacy_xls_via_optional_pandas_bridge(monkeypatch):
    class _FakeFrame:
        def __init__(self, rows):
            self._rows = rows

        def fillna(self, _value):
            return self

        def itertuples(self, index=False, name=None):
            return iter(self._rows)

    class _FakePandas:
        @staticmethod
        def read_excel(_path, sheet_name=None, header=None):
            assert sheet_name is None
            assert header is None
            return {"Legacy": _FakeFrame([("material", "capacity_mAh"), ("LMFP", 120), ("LFP", 115)])}

    monkeypatch.setitem(sys.modules, "pandas", _FakePandas)

    sheets = PatentTabularService._read_legacy_excel_rows("/tmp/legacy.xls", max_sheets=2)

    assert sheets == [("Legacy", [["material", "capacity_mAh"], ["LMFP", "120"], ["LFP", "115"]])]


def test_dispatch_pdf_route_uses_real_pdf_text_summary_when_local_path_is_available(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a battery recycling catalyst. Experiments show 15% efficiency improvement and lower cost.",
        answer_question_fn=lambda **kwargs: "真实总结：本文提出电池回收催化方案，实验显示效率提升 15%，同时降低成本。",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["handler"] == "pdf"
    assert result["metadata"]["answer_mode"] == "pdf_text_summary"
    assert "真实总结" in result["answer_text"]
    assert "Patent PDF route answered" not in result["answer_text"]


def test_pdf_service_default_extractor_uses_file_qna_entrypoint_and_budget(monkeypatch, tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    called: dict[str, object] = {}

    def _fake_extract(pdf_path: str, *, max_pages: int = 50, exclude_references: bool = True) -> str:
        called["pdf_path"] = pdf_path
        called["max_pages"] = max_pages
        called["exclude_references"] = exclude_references
        return "标题: Sample\n\n--- 第 1 页 ---\nResults section.\n\n--- 第 2 页 ---\nConclusion section."

    monkeypatch.setattr(pdf_service_module, "extract_file_qa_pdf_text", _fake_extract, raising=False)

    service = PatentPdfService(answer_question_fn=lambda **kwargs: "不进入生成")
    documents = service._load_pdf_documents(execution_files=contract.selected_execution_files)

    assert len(documents) == 1
    assert documents[0]["label"] == "battery-paper.pdf"
    assert "Results section." in documents[0]["text"]
    assert called == {
        "pdf_path": str(pdf_path),
        "max_pages": 50,
        "exclude_references": True,
    }


def test_pdf_service_prefers_injected_answer_client_over_env_builder(tmp_path, monkeypatch):
    pdf_path = tmp_path / "battery-paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    monkeypatch.setattr(
        PatentPdfAnswerClient,
        "from_env",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("from_env should not be used when answer_client is injected")),
    )

    class _InjectedAnswerClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.closed = False

        def answer(self, **kwargs):
            self.calls.append(dict(kwargs))
            return "真实总结：本文提出电池回收催化方案，实验显示效率提升 15%，同时降低成本。"

        def close(self):
            self.closed = True

    injected_client = _InjectedAnswerClient()
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a battery recycling catalyst.",
        answer_client=injected_client,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["handler"] == "pdf"
    assert result["metadata"]["answer_mode"] == "pdf_text_summary"
    assert len(injected_client.calls) == 1
    assert injected_client.calls[0]["file_name"] == "battery-paper.pdf"
    assert injected_client.calls[0]["route_hint"] == "pdf_qa"
    assert "注*" in result["answer_text"]
    service.close()
    assert injected_client.closed is True


def test_pdf_service_keeps_injected_extract_pdf_text_fn_contract(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "Injected extractor result.",
        answer_question_fn=lambda **kwargs: "不进入生成",
    )
    documents = service._load_pdf_documents(execution_files=contract.selected_execution_files)

    assert len(documents) == 1
    assert documents[0]["text"] == "Injected extractor result."


def test_pdf_service_keeps_injected_extract_pdf_text_fn_contract_for_var_kwargs(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    captured: dict[str, object] = {}

    def _injector(path: str, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return "Injected extractor result from kwargs."

    service = PatentPdfService(
        extract_pdf_text_fn=_injector,
        answer_question_fn=lambda **kwargs: "不进入生成",
    )
    documents = service._load_pdf_documents(execution_files=contract.selected_execution_files)

    assert len(documents) == 1
    assert documents[0]["text"] == "Injected extractor result from kwargs."
    assert captured["path"] == str(pdf_path)
    assert captured["max_pages"] == 50
    assert captured["exclude_references"] is True


def test_pdf_service_default_extractor_keeps_compare_tail_sections_reachable(monkeypatch, tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    front_matter = "作者信息与版权页。 " * 220
    texts = {
        str(pdf_path_a): (
            f"{front_matter}\n\nAbstract A short.\n\nMethod A.\n\nResults A observed.\n\nConclusion A final."
        ),
        str(pdf_path_b): (
            f"{front_matter}\n\nAbstract B short.\n\nMethod B.\n\nResults B observed.\n\nConclusion B final."
        ),
    }
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的方法和结论",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    monkeypatch.setattr(
        pdf_service_module,
        "extract_file_qa_pdf_text",
        lambda pdf_path, max_pages=50, exclude_references=True: texts[pdf_path],
        raising=False,
    )

    service = PatentPdfService(answer_question_fn=lambda **kwargs: "不进入生成")
    pdf_documents = service._load_pdf_documents(execution_files=contract.selected_execution_files)
    prepared = service._prepare_answer_input(
        question=contract.question,
        pdf_text=format_multi_pdf_sections(pdf_documents),
        pdf_documents=pdf_documents,
        selected_file_labels=["paper-a.pdf", "paper-b.pdf"],
        available_file_labels=["paper-a.pdf", "paper-b.pdf"],
        compare_mode=True,
    )

    assert prepared["ok"] is True
    assert "Results A observed." in str(prepared["prepared_pdf_text"])
    assert "Conclusion B final." in str(prepared["prepared_pdf_text"])


def test_dispatch_pdf_route_summary_aligns_to_literature_summary_sections(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "This paper studies LMFP/LFP blending for safer charging. "
            "Results show lower polarization under high-rate charging. "
            "The discussion attributes the gain to improved transport stability. "
            "The conclusion notes that long-cycle validation is still limited."
        ),
        answer_question_fn=lambda **kwargs: "本文研究 LMFP/LFP 复配，并指出高倍率充电下的安全性得到改善。",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    assert "## 研究目的和背景" in answer
    assert "## 研究方法/实验设计" in answer
    assert "## 主要发现和结果" in answer
    assert "## 结论和意义" in answer
    assert "## 局限性" in answer
    assert "注*" in answer
    assert "long-cycle validation is still limited" in _section_body(answer, "局限性") or "limited" in _section_body(answer, "局限性")
    assert "long-cycle validation is still limited" in _section_body(answer, "局限性") or "limited" in _section_body(answer, "局限性")
    assert "LMFP/LFP" in answer
    assert "长循环验证仍有限" in _section_body(answer, "局限性") or "limited" in _section_body(answer, "局限性")


def test_dispatch_pdf_route_summary_preserves_well_structured_model_answer(tmp_path):
    pdf_path = tmp_path / "battery-paper-structured.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    structured_answer = "\n".join(
        [
            "## 研究目的和背景",
            "- 原文首先说明高倍率充电中的安全性问题。",
            "",
            "## 研究方法/实验设计",
            "- 研究对象为 LMFP/LFP 复配体系。",
            "- 方法流程：",
            "  - 先比较不同配比。",
            "  - 再验证高倍率充电表现。",
            "",
            "## 主要发现和结果",
            "- LMFP/LFP 复配改善了高倍率充电安全性。",
            "",
            "## 结论和意义",
            "- 该路线对兼顾能量密度与安全性有帮助。",
            "",
            "## 局限性",
            "- 原文指出长循环验证仍有限。",
        ]
    )
    contract = build_patent_file_contract(
        question="请总结这篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "Structured source text with multi-step method and limitations.",
            answer_question_fn=lambda **kwargs: structured_answer,
        ),
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    assert "## 局限性" in answer
    assert "- 方法流程：" in answer
    assert "  - 先比较不同配比。" in answer
    assert "高倍率充电安全性" in answer


def test_dispatch_pdf_route_summary_repair_rebuilds_legacy_four_block_answer_with_limitations(tmp_path):
    pdf_path = tmp_path / "battery-paper-legacy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "The paper explains the motivation, method setup, quantitative findings, "
                "and notes that long-cycle validation is still limited."
            ),
            answer_question_fn=lambda **kwargs: "\n".join(
                [
                    "## 结论",
                    "本文认为 LMFP/LFP 复配改善了高倍率充电表现。",
                    "",
                    "## 证据",
                    "- 原文报告了更稳定的充电过程。",
                    "",
                    "## 对比",
                    "- 当前没有其他对照对象。",
                    "",
                    "## 限制",
                    "- 长循环验证仍有限。",
                ]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    assert answer.count("## ") >= 5
    assert "## 局限性" in answer
    assert "注*" in answer


def test_dispatch_pdf_route_summary_conservative_repair_keeps_sparse_usable_answer_and_adds_limitations(tmp_path):
    pdf_path = tmp_path / "battery-paper-conservative.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "The paper studies LMFP/LFP blending for safer charging. "
                "Method setup compares blended and baseline electrodes. "
                "Results show safer high-rate charging. "
                "The conclusion notes that long-cycle validation is still limited."
            ),
            answer_question_fn=lambda **kwargs: "LMFP/LFP 复配改善了高倍率充电安全性。长循环验证仍有限。",
        ),
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    assert "## 研究目的和背景" in answer
    assert "## 研究方法/实验设计" in answer
    assert "## 主要发现和结果" in answer
    assert "## 结论和意义" in answer
    assert "## 局限性" in answer
    assert "注*" in answer
    assert "长循环验证仍有限" in _section_body(answer, "局限性") or "limited" in _section_body(answer, "局限性")
    assert "LMFP/LFP" in answer


def test_dispatch_pdf_route_summary_repair_expands_dense_pdf_evidence_into_longer_sections(tmp_path):
    pdf_path = tmp_path / "battery-paper-dense-summary.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    prepared_pdf_text = " ".join(
        [
            "研究背景指出户外摄像头虽然部署广泛，但自动化分析仍受到校准不足限制。",
            "研究目的在于构建一种可扩展的全球交通摄像头校准框架。",
            "方法部分说明研究对象覆盖真实交通摄像头与街景图像配准场景。",
            "方法部分说明先在相机周围采样多个全景图并提取透视图像。",
            "方法部分说明随后使用语义分割屏蔽车辆和行人等动态对象。",
            "方法部分说明再使用SuperPoint和SuperGlue完成特征提取与匹配。",
            "方法部分说明最后在Bundle Adjustment中引入全景约束并结合GPS完成尺度校准。",
            "结果显示该方法在焦距误差上优于现有方法。",
            "结果显示该方法在地面距离测量误差上显著降低。",
            "结果显示系统已经成功完成一百多个全球交通摄像头的三维重建与定位。",
            "结果显示该框架还能生成车辆活动热图并支持速度测量。",
            "结论指出该框架证明了街景图像可用于大规模精确校准交通摄像头。",
            "结论指出该研究为交通分析和安全管理提供了可落地的数据基础。",
            "局限性指出如果场景缺少足够背景纹理，特征匹配性能会下降。",
        ]
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: prepared_pdf_text,
            answer_question_fn=lambda **kwargs: "本文提出了一个交通摄像头校准框架，并证明其有效。",
        ),
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    method_lines = [line for line in _section_body(answer, "研究方法/实验设计").splitlines() if line.strip().startswith("- ")]
    result_lines = [line for line in _section_body(answer, "主要发现和结果").splitlines() if line.strip().startswith("- ")]

    assert len(method_lines) >= 4
    assert len(result_lines) >= 4
    assert "SuperPoint" in _section_body(answer, "研究方法/实验设计")
    assert "SuperGlue" in _section_body(answer, "研究方法/实验设计")
    assert "热图" in _section_body(answer, "主要发现和结果")


def test_dispatch_pdf_route_summary_fallback_from_degraded_answer_still_emits_limitations(tmp_path):
    pdf_path = tmp_path / "battery-paper-fallback.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "This paper studies LMFP/LFP blending for safer charging. "
                "Method setup compares blended and baseline electrodes. "
                "Results show safer high-rate charging. "
                "The conclusion notes that long-cycle validation is still limited."
            ),
            answer_question_fn=lambda **kwargs: "",
        ),
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    assert "## 研究目的和背景" in answer
    assert "## 研究方法/实验设计" in answer
    assert "## 主要发现和结果" in answer
    assert "## 结论和意义" in answer
    assert "## 局限性" in answer
    assert "注*" in answer
    assert "long-cycle validation is still limited" in _section_body(answer, "局限性") or "limited" in _section_body(answer, "局限性")


def test_dispatch_pdf_route_summary_fallback_discards_degraded_model_text(tmp_path):
    pdf_path = tmp_path / "battery-paper-fallback-degraded-text.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结这篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "This paper studies LMFP/LFP blending for safer charging. "
                "Method setup compares blended and baseline electrodes. "
                "Results show safer high-rate charging. "
                "The conclusion notes that long-cycle validation is still limited."
            ),
            answer_question_fn=lambda **kwargs: "暂时无法生成，请稍后重试。",
        ),
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    assert "暂时无法生成" not in answer
    assert "请稍后重试" not in answer
    assert "## 局限性" in answer


def test_dispatch_pdf_route_summary_preserves_existing_gap_limitations_without_duplicate_section(tmp_path):
    pdf_path = tmp_path / "battery-paper-gap-limitations.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    structured_answer = "\n".join(
        [
            "## 研究目的和背景",
            "- 原文说明高倍率充电场景下的安全性问题。",
            "",
            "## 研究方法/实验设计",
            "- 对 LMFP/LFP 复配体系进行对比测试。",
            "",
            "## 主要发现和结果",
            "- 复配体系改善了高倍率充电表现。",
            "",
            "## 结论和意义",
            "- 该路线有助于兼顾安全性与性能。",
            "",
            "## 局限性",
            "- PDF中未提及更长期的循环验证数据。",
            "",
            "注*：所有总结内容均严格基于文件原文中明确提到的信息，未添加任何通用知识或推测内容。",
        ]
    )
    contract = build_patent_file_contract(
        question="请总结这篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[{**PDF_FILE, "local_path": str(pdf_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "Structured source text with explicit gap wording.",
            answer_question_fn=lambda **kwargs: structured_answer,
        ),
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    assert answer.count("## 局限性") == 1
    assert answer.count("注*：") == 1


def test_dispatch_pdf_route_negative_compare_question_targets_only_first_document(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结第一篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    seen_inputs: list[str] = []

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A unique fact.\n\nResults A report 15% efficiency improvement."
                if path == str(pdf_path_a)
                else "Abstract B unique fact.\n\nResults B report 200-cycle retention."
            ),
            answer_question_fn=lambda **kwargs: seen_inputs.append(str(kwargs.get("pdf_text") or "")) or "第一篇文献总结：聚焦效率提升。",
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_summary"
    assert len(seen_inputs) == 1
    assert "paper-a.pdf" in seen_inputs[0]
    assert "15% efficiency improvement" in seen_inputs[0]
    assert "paper-b.pdf" not in seen_inputs[0]
    assert "200-cycle retention" not in seen_inputs[0]


def test_pdf_service_compare_mode_uses_dedicated_max_chars_budget_when_explicitly_provided():
    repeated_a = "Method A keeps detailed compare evidence. " * 500
    repeated_b = "Method B keeps detailed compare evidence. " * 500
    pdf_documents = [
        {"label": "paper-a.pdf", "text": f"Abstract A short.\n\n{repeated_a}\n\nResults A observed.\n\nConclusion A final."},
        {"label": "paper-b.pdf", "text": f"Abstract B short.\n\n{repeated_b}\n\nResults B observed.\n\nConclusion B final."},
    ]
    pdf_text = format_multi_pdf_sections(pdf_documents)

    service = PatentPdfService(
        answer_question_fn=lambda **kwargs: "不会进入生成阶段",
        max_pdf_chars=12000,
        compare_max_pdf_chars=50000,
    )

    prepared = service._prepare_answer_input(
        question="比较这两篇文献的方法差异",
        pdf_text=pdf_text,
        pdf_documents=pdf_documents,
        selected_file_labels=["paper-a.pdf", "paper-b.pdf"],
        available_file_labels=["paper-a.pdf", "paper-b.pdf"],
        compare_mode=True,
    )

    assert prepared["ok"] is True
    assert len(prepared["prepared_pdf_text"]) > 12000


def test_dispatch_pdf_route_multi_pdf_summary_keeps_legacy_summary_shape(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    captured_prompts: list[str] = []
    contract = build_patent_file_contract(
        question="请总结这两篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A unique fact.\n\nResults A report 15% efficiency improvement."
                if path == str(pdf_path_a)
                else "Abstract B unique fact.\n\nResults B report 200-cycle retention."
            ),
            answer_question_fn=lambda **kwargs: captured_prompts.append(str(kwargs.get("prompt") or "")) or "两篇文献分别报告了效率提升和循环保持率差异。",
        ),
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    assert captured_prompts
    assert "负责基于上传的单篇 PDF 原文给出结构化回答" not in captured_prompts[0]
    assert "## 局限性" not in captured_prompts[0]
    assert "## 研究目的和背景" in answer
    assert "## 研究方法/实验设计" in answer
    assert "## 主要发现和结果" in answer
    assert "## 结论和意义" in answer
    assert "## 局限性" not in answer
    assert "注*" in answer


def test_dispatch_pdf_route_targeted_first_document_does_not_fall_through_to_second_when_first_is_unreadable(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="请总结第一篇文献的研究内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    seen_inputs: list[str] = []

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                ""
                if path == str(pdf_path_a)
                else "Abstract B unique fact.\n\nResults B report 200-cycle retention."
            ),
            answer_question_fn=lambda **kwargs: seen_inputs.append(str(kwargs.get("pdf_text") or "")) or "不应使用第二篇生成答案。",
        ),
        tabular_service=PatentTabularService(),
    )

    assert seen_inputs == []
    assert result["metadata"]["answer_mode"] == "pdf_text_unavailable"
    assert "无法生成基于正文的总结" in result["answer_text"]


def test_dispatch_pdf_route_formats_two_selected_pdfs_for_compare_questions(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    captured: dict[str, str] = {}
    texts = {
        str(pdf_path_a): "Abstract A.\n\nResults A show 15% improvement.\n\nConclusion A supports route A.",
        str(pdf_path_b): "Abstract B.\n\nResults B show 5% decline.\n\nConclusion B rejects route A.",
    }

    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: texts[path],
        answer_question_fn=lambda **kwargs: captured.update(
            {
                "pdf_text": str(kwargs["pdf_text"]),
                "file_name": str(kwargs["file_name"]),
            }
        )
        or _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "==== 文献 1: paper-a.pdf ====" in captured["pdf_text"]
    assert "==== 文献 2: paper-b.pdf ====" in captured["pdf_text"]
    assert "paper-a.pdf" in captured["file_name"]
    assert "paper-b.pdf" in captured["file_name"]


def test_dispatch_pdf_route_compare_answer_is_restructured_with_five_target_sections(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A discusses manganese-rich cathodes.\n\nResults A show 15% efficiency improvement.\n\nConclusion A favors route A."
                if path == str(pdf_path_a)
                else "Abstract B studies phosphate stabilization.\n\nResults B keep 200-cycle retention.\n\nConclusion B favors route B."
            ),
            answer_question_fn=lambda **kwargs: _build_valid_compare_answer_out_of_order(["paper-a.pdf", "paper-b.pdf"]),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    answer = result["answer_text"]
    assert "## 具体内容对比" in answer
    assert "## 研究方法差异" in answer
    assert "## 应用领域差异" in answer
    assert "## 相同点" in answer
    assert "## 总结" in answer
    assert answer.index("## 具体内容对比") < answer.index("## 研究方法差异") < answer.index("## 应用领域差异") < answer.index("## 相同点") < answer.index("## 总结")
    assert "### 文献 #1 核心内容（根据PDF原文）" in answer
    assert "### 文献 #2 核心内容（根据PDF原文）" in answer
    assert "### 文献 #1 采用的研究方法" in answer
    assert "### 文献 #2 采用的研究方法" in answer
    assert "### 文献 #1 关注的应用领域" in answer
    assert "### 文献 #2 关注的应用领域" in answer
    assert "paper-a.pdf" in result["answer_text"]
    assert "paper-b.pdf" in result["answer_text"]
    assert "paper-a.pdf" in result["answer_text"]
    assert "paper-b.pdf" in result["answer_text"]
    assert "明确的中文结论" in result["answer_text"]
    assert "各自概要" not in answer
    assert "差异点" not in answer
    assert "paper-a.pdf" in result["metadata"]["prepared_pdf_text"]
    assert "paper-b.pdf" in result["metadata"]["prepared_pdf_text"]


def test_dispatch_pdf_route_compare_answer_restructures_when_markers_are_out_of_order(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A discusses manganese-rich cathodes.\n\nResults A show 15% efficiency improvement.\n\nConclusion A favors route A."
                if path == str(pdf_path_a)
                else "Abstract B studies phosphate stabilization.\n\nResults B keep 200-cycle retention.\n\nConclusion B favors route B."
            ),
            answer_question_fn=lambda **kwargs: _build_valid_compare_answer_out_of_order(["paper-a.pdf", "paper-b.pdf"]),
        ),
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    assert answer.index("## 具体内容对比") < answer.index("## 研究方法差异") < answer.index("## 应用领域差异") < answer.index("## 相同点") < answer.index("## 总结")


def test_dispatch_pdf_route_compare_answer_preserves_valid_five_section_markdown_structure(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A discusses manganese-rich cathodes.\n\n"
                "Results A show 15% efficiency improvement.\n\n"
                "Conclusion A favors route A."
                if path == str(pdf_path_a)
                else "Abstract B studies phosphate stabilization.\n\n"
                "Results B keep 200-cycle retention.\n\n"
                "Conclusion B favors route B."
            ),
            answer_question_fn=lambda **kwargs: (
                "两篇文献对比分析\n\n"
                "## 具体内容对比\n"
                "### 文献 #1 核心内容（根据PDF原文）\n"
                "- paper-a.pdf：围绕富锰正极体系展开，并报告了效率提升趋势。\n"
                "### 文献 #2 核心内容（根据PDF原文）\n"
                "- paper-b.pdf：聚焦磷酸盐稳定化路线，并强调长循环保持能力。\n\n"
                "## 研究方法差异\n"
                "### 文献 #1 采用的研究方法\n"
                "- 通过电化学测试与倍率性能评估验证材料表现。\n"
                "### 文献 #2 采用的研究方法\n"
                "- 通过循环寿命测试与稳定性对照验证改性效果。\n\n"
                "## 应用领域差异\n"
                "### 文献 #1 关注的应用领域\n"
                "- 高倍率正极优化。\n"
                "### 文献 #2 关注的应用领域\n"
                "- 长循环稳定性提升。\n\n"
                "## 相同点\n"
                "- 都提供了实验结果和明确结论。\n\n"
                "## 总结\n"
                "- 两篇文献分别代表效率导向与稳定性导向。"
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    answer = result["answer_text"]
    assert "## 具体内容对比" in answer
    assert "## 研究方法差异" in answer
    assert "## 应用领域差异" in answer
    assert "## 相同点" in answer
    assert "## 总结" in answer
    assert answer.index("## 具体内容对比") < answer.index("## 研究方法差异") < answer.index("## 应用领域差异") < answer.index("## 相同点") < answer.index("## 总结")
    assert "各自概要：" not in answer
    assert "差异点：" not in answer
    assert answer.count("## 总结") == 1


def test_compare_normalization_preserves_rich_document_bullets_instead_of_collapsing_to_one_per_section(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_rich_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "paper-a.pdf：补充了方案 1 的定量结果与应用解释。" in result["answer_text"]
    assert "paper-b.pdf：最后结合定量分析总结方法差异。" in result["answer_text"]
    assert "paper-a.pdf：补充场景 1 的应用价值。" in result["answer_text"]
    assert result["answer_text"].count("- ") >= 18


def test_compare_validation_rejects_placeholder_dominant_answer(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_dominant_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_preserves_mixed_placeholder_boundary_with_specific_facts(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_mixed_placeholder_and_fact_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "未提及明确的应用场景边界，但报告了方案 1 的循环寿命提升 20%" in result["answer_text"]
    assert "证据不足以判断是否包含额外消融实验，但明确采用了分组对照" in result["answer_text"]
    assert "信息不足以扩展到其他部署场景，但原文明确将方案 2 用于高负载连续运行条件" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_future_work_tail(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_future_work_tokens(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_tokens_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_future_work_action_tokens(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_action_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_preserves_mixed_placeholder_with_punctuation_fact(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_mixed_placeholder_with_punctuation_fact_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "未提及明确的应用场景边界，报告了方案 1 的循环寿命提升 20%" in result["answer_text"]
    assert "证据不足，原文明确采用了分组对照和定量指标评估方案 1" in result["answer_text"]
    assert "信息不足，原文明确将方案 2 用于高负载连续运行条件" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_reporting_verbs_and_future_work(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_reporting_verbs_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_preserves_mixed_placeholder_with_reporting_fact(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_mixed_placeholder_with_reporting_fact_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "未提及明确的应用场景，结果显示方案 1 存在明显峰位变化" in result["answer_text"]
    assert "证据不足，原文表明方案 1 采用了分层对照实验" in result["answer_text"]
    assert "信息不足，报告显示方案 2 适用于连续运行工况" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_trailing_after_future_work(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_trailing_after_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_numeric_subject_future_work(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_numeric_subject_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_reporting_payload_nouns_future_work(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_reporting_payload_nouns_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_preserves_mixed_placeholder_with_concise_reporting_fact(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_mixed_placeholder_with_concise_reporting_fact_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "未提及应用场景，结果显示峰位变化" in result["answer_text"]
    assert "证据不足，表明性能提升" in result["answer_text"]
    assert "信息不足，报告显示适用于连续运行工况" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_reporting_payload_keywords_future_work(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_reporting_payload_keywords_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_action_subject_future_work(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_action_subject_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_reporting_derived_nouns_future_work(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_reporting_derived_nouns_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_reporting_result_phrase_subject_future_work(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_reporting_result_phrase_subject_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_preserves_mixed_placeholder_with_conjunction_caveat(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_mixed_placeholder_with_conjunction_caveat_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "未提及应用场景，但结果显示峰位变化并需要进一步验证" in result["answer_text"]
    assert "未提及应用场景，但报告了循环寿命提升20%并需要更多测试" in result["answer_text"]
    assert "未提及应用场景，但使用 TOF-SIMS 可视化分布并需要进一步验证" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_weak_conjunction_caveat(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_weak_conjunction_caveat_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_preserves_mixed_placeholder_with_chinese_method_conjunction_caveat(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_mixed_placeholder_with_chinese_method_conjunction_caveat_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "未提及应用场景，但采用分层对照实验并需要进一步验证" in result["answer_text"]
    assert "未提及应用场景，但使用原位退火策略并需要更多测试" in result["answer_text"]
    assert "未提及应用场景，但提出双层包覆方案并需要进一步验证" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_weak_method_noun_caveat(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_weak_method_noun_caveat_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_rejects_placeholder_with_weak_method_noun_variants_caveat(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确包含循环寿命提升 20%。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确包含循环寿命提升 20%。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_placeholder_future_work_with_weak_method_noun_variants_compare_answer(
                ["paper-a.pdf", "paper-b.pdf"]
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_compare_validation_rejects_answer_that_leaks_truncation_internals(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: _build_truncation_leak_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_dispatch_pdf_route_compare_answer_restructures_when_headings_exist_but_document_facts_are_missing(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A discusses manganese-rich cathodes.\n\nResults A show 15% efficiency improvement.\n\nConclusion A favors route A."
                if path == str(pdf_path_a)
                else "Abstract B studies phosphate stabilization.\n\nResults B keep 200-cycle retention.\n\nConclusion B favors route B."
            ),
            answer_question_fn=lambda **kwargs: "## 具体内容对比\n- 略。\n\n## 研究方法差异\n- 略。\n\n## 应用领域差异\n- 略。\n\n## 相同点\n- 都有实验。\n\n## 总结\n- 略。",
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "逐篇中文核心内容" in result["answer_text"]
    assert "15% efficiency improvement" not in result["answer_text"]
    assert "200-cycle retention" not in result["answer_text"]


def test_dispatch_pdf_route_compare_answer_restructures_when_only_labels_exist_without_distinct_facts(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A discusses manganese-rich cathodes.\n\nResults A show 15% efficiency improvement.\n\nConclusion A favors route A."
                if path == str(pdf_path_a)
                else "Abstract B studies phosphate stabilization.\n\nResults B keep 200-cycle retention.\n\nConclusion B favors route B."
            ),
            answer_question_fn=lambda **kwargs: "## 具体内容对比\n### 文献 #1 核心内容（根据PDF原文）\n- paper-a.pdf：略。\n### 文献 #2 核心内容（根据PDF原文）\n- paper-b.pdf：略。\n\n## 研究方法差异\n- 略。\n\n## 应用领域差异\n- 略。\n\n## 相同点\n- 都有实验。\n\n## 总结\n- 略。",
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "逐篇中文核心内容" in result["answer_text"]
    assert "15% efficiency improvement" not in result["answer_text"]
    assert "200-cycle retention" not in result["answer_text"]


def test_dispatch_pdf_route_compare_answer_restructures_when_only_shared_tokens_are_repeated(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A keeps a shared compare anchor.\n\nResults A show 15% efficiency improvement.\n\nConclusion A favors route A."
                if path == str(pdf_path_a)
                else "Abstract B keeps a shared compare anchor.\n\nResults B keep 200-cycle retention.\n\nConclusion B favors route B."
            ),
            answer_question_fn=lambda **kwargs: (
                "## 具体内容对比\n"
                "### 文献 #1 核心内容（根据PDF原文）\n"
                "- paper-a.pdf：shared compare anchor。\n"
                "### 文献 #2 核心内容（根据PDF原文）\n"
                "- paper-b.pdf：shared compare anchor。\n\n"
                "## 研究方法差异\n- 略。\n\n"
                "## 应用领域差异\n- 略。\n\n"
                "## 相同点\n- 都有实验。\n\n"
                "## 总结\n- 略。"
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "shared compare anchor" not in result["answer_text"]
    assert "逐篇中文核心内容" in result["answer_text"]


def test_dispatch_pdf_route_compare_answer_requires_per_document_structure_for_three_documents(tmp_path):
    pdf_paths = []
    execution_files = []
    selected_ids = []
    for index in range(3):
        pdf_path = tmp_path / f"paper-{index + 1}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
        pdf_paths.append(pdf_path)
        execution_files.append(
            {
                "file_id": 410 + index,
                "file_type": "pdf",
                "file_name": f"paper-{index + 1}.pdf",
                "local_path": str(pdf_path),
            }
        )
        selected_ids.append(410 + index)

    contract = build_patent_file_contract(
        question="对比一下这三篇文献的方法和应用方向",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=selected_ids,
        primary_file_id=selected_ids[0],
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "selected_file_ids": selected_ids, "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "中文摘要。\n\n实验结果明确。\n\n结论完整。",
            answer_question_fn=lambda **kwargs: (
                "## 具体内容对比\n"
                "- 三篇文献都讨论电池材料，但关注重点不同。\n\n"
                "## 研究方法差异\n"
                "- 有的使用表征测试，有的使用循环验证。\n\n"
                "## 应用领域差异\n"
                "- 覆盖倍率性能、循环稳定性和界面改性。\n\n"
                "## 相同点\n"
                "- 都给出了实验结论。\n\n"
                "## 总结\n"
                "- 这些文献展示了不同优化方向。"
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "逐篇" in result["answer_text"]


def test_dispatch_pdf_route_returns_explicit_compare_failure_when_selected_compare_docs_exceed_bound(tmp_path):
    pdf_paths = []
    execution_files = []
    selected_ids = []
    for index in range(5):
        pdf_path = tmp_path / f"paper-{index + 1}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
        pdf_paths.append(pdf_path)
        execution_files.append(
            {
                "file_id": 300 + index,
                "file_type": "pdf",
                "file_name": f"paper-{index + 1}.pdf",
                "local_path": str(pdf_path),
            }
        )
        selected_ids.append(300 + index)
    contract = build_patent_file_contract(
        question="对比一下这五篇文献",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=selected_ids,
        primary_file_id=selected_ids[0],
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "selected_file_ids": selected_ids},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    calls: list[dict[str, object]] = []
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "Abstract.\n\nResults observed.\n\nConclusion final.",
        answer_question_fn=lambda **kwargs: calls.append(dict(kwargs)) or (_ for _ in ()).throw(AssertionError("compare generation should not run")),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "超过 4 篇文献" in result["answer_text"]
    assert "缩小比较范围" in result["answer_text"]
    assert calls == []


def test_dispatch_pdf_route_rejects_bad_english_fragment_compare_output(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "Abstract A discusses manganese-rich cathodes.\n\nResults A show 15% efficiency improvement.\n\nConclusion A favors route A."
                if path == str(pdf_path_a)
                else "Abstract B studies phosphate stabilization.\n\nResults B keep 200-cycle retention.\n\nConclusion B favors route B."
            ),
            answer_question_fn=lambda **kwargs: (
                "## 具体内容对比\n"
                "### 文献 #1 核心内容（根据PDF原文）\n"
                "- Abstract A in- str fragment.\n"
                "### 文献 #2 核心内容（根据PDF原文）\n"
                "- Abstract B duplicate fragment.\n\n"
                "## 研究方法差异\n"
                "### 文献 #1 采用的研究方法\n"
                "- Results A 15% improvement.\n"
                "### 文献 #2 采用的研究方法\n"
                "- Results B 200-cycle retention.\n\n"
                "## 应用领域差异\n"
                "### 文献 #1 关注的应用领域\n"
                "- route A deployment.\n"
                "### 文献 #2 关注的应用领域\n"
                "- route B deployment.\n\n"
                "## 相同点\n"
                "- Both provide experiments.\n\n"
                "## 总结\n"
                "- 两篇文献存在差异。"
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "in- str" not in result["answer_text"]
    assert "Abstract A in- str fragment." not in result["answer_text"]
    assert "Abstract B duplicate fragment." not in result["answer_text"]
    assert "Results A 15% improvement." not in result["answer_text"]
    assert "Results B 200-cycle retention." not in result["answer_text"]


def test_dispatch_pdf_route_rejects_compare_output_with_hollow_shared_section_and_empty_summary(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: (
                "中文摘要 A。\n\n实验结果 A 明确。\n\n结论 A 完整。"
                if path == str(pdf_path_a)
                else "中文摘要 B。\n\n实验结果 B 明确。\n\n结论 B 完整。"
            ),
            answer_question_fn=lambda **kwargs: (
                "## 具体内容对比\n"
                "### 文献 #1 核心内容（根据PDF原文）\n"
                "- paper-a.pdf：围绕方案一展开研究，并给出明确的中文结论。\n"
                "### 文献 #2 核心内容（根据PDF原文）\n"
                "- paper-b.pdf：围绕方案二展开研究，并给出明确的中文结论。\n\n"
                "## 研究方法差异\n"
                "### 文献 #1 采用的研究方法\n"
                "- 采用表征测试与性能验证结合的方法。\n"
                "### 文献 #2 采用的研究方法\n"
                "- 采用循环测试与稳定性评估结合的方法。\n\n"
                "## 应用领域差异\n"
                "### 文献 #1 关注的应用领域\n"
                "- 面向高倍率性能优化。\n"
                "### 文献 #2 关注的应用领域\n"
                "- 面向长循环稳定性提升。\n\n"
                "## 相同点\n"
                "- Both provide experiments.\n\n"
                "## 总结\n"
            ),
        ),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "所选文献都提供了可用于逐篇比较的 PDF 原文证据。" not in result["answer_text"]


def test_dispatch_pdf_route_returns_explicit_compare_failure_when_only_one_pdf_is_readable(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A.\n\nResults A show 15% improvement.\n\nConclusion A supports route A."
            if path == str(pdf_path_a)
            else ""
        ),
        answer_question_fn=lambda **kwargs: "",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "paper-b.pdf" in result["answer_text"]
    assert "文档要点如下" not in result["answer_text"]


def test_dispatch_pdf_route_returns_explicit_compare_failure_when_model_returns_no_answer(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A.\n\nResults A show 15% improvement.\n\nConclusion A supports route A."
            if path == str(pdf_path_a)
            else "Abstract B.\n\nResults B show 5% decline.\n\nConclusion B rejects route A."
        ),
        answer_question_fn=lambda **kwargs: "",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "模型未返回可用的比较结果" in result["answer_text"]
    assert "文档要点如下" not in result["answer_text"]


def test_dispatch_pdf_route_preserves_tail_evidence_from_each_large_pdf_in_compare_mode(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    front_matter = "作者信息与版权页。 " * 1200
    captured: dict[str, str] = {}
    texts = {
        str(pdf_path_a): f"{front_matter}\n\nAbstract A.\n\nResults A show 15% improvement.\n\nConclusion A supports route A.",
        str(pdf_path_b): f"{front_matter}\n\nAbstract B.\n\nResults B show 5% decline.\n\nConclusion B rejects route A.",
    }

    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: texts[path],
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])}) or "对比结果",
    )

    dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert "Conclusion A supports route A." in captured["pdf_text"]
    assert "Conclusion B rejects route A." in captured["pdf_text"]


def test_dispatch_pdf_route_preserves_per_document_abstract_for_four_doc_compare_with_sufficient_compare_budget(tmp_path):
    pdf_paths = []
    execution_files = []
    selected_ids = []
    for index in range(4):
        pdf_path = tmp_path / f"paper-{index + 1}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
        pdf_paths.append(pdf_path)
        execution_files.append(
            {
                "file_id": 100 + index,
                "file_type": "pdf",
                "file_name": f"paper-{index + 1}.pdf",
                "local_path": str(pdf_path),
            }
        )
        selected_ids.append(100 + index)
    contract = build_patent_file_contract(
        question="对比一下这四篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=selected_ids,
        primary_file_id=selected_ids[0],
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "selected_file_ids": selected_ids},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    front_matter = "作者信息与版权页。 " * 200
    captured: dict[str, str] = {}
    texts = {
        str(path): (
            f"{front_matter}\n\n"
            f"Abstract {index} short.\n\n"
            f"Method {index} uses condition {index}.\n\n"
            f"Results {index} observed.\n\n"
            f"Conclusion {index} final."
        )
        for index, path in enumerate(pdf_paths, start=1)
    }
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: texts[path],
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])})
        or _build_valid_compare_answer([f"paper-{index}.pdf" for index in range(1, 5)]),
        max_pdf_chars=1000,
        compare_max_pdf_chars=50000,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    for index in range(1, 5):
        assert f"Abstract {index} short." in captured["pdf_text"]
        assert f"Results {index} observed." in captured["pdf_text"] or f"Conclusion {index} final." in captured["pdf_text"]


def test_dispatch_pdf_route_drops_reference_tail_from_compare_context_with_compare_budget_override(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    front_matter = "作者信息与版权页。 " * 120
    references = "参考文献\n[1] filler citation block. " * 80
    captured: dict[str, str] = {}
    texts = {
        str(pdf_path_a): (
            f"{front_matter}\n\nAbstract A short.\n\nMethod A.\n\n"
            f"Results A observed.\n\nConclusion A final.\n\n{references}"
        ),
        str(pdf_path_b): (
            f"{front_matter}\n\nAbstract B short.\n\nMethod B.\n\n"
            f"Results B observed.\n\nConclusion B final.\n\n{references}"
        ),
    }
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: texts[path],
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])})
        or _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
        max_pdf_chars=560,
        compare_max_pdf_chars=50000,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "参考文献" not in captured["pdf_text"]
    assert "Results A observed." in captured["pdf_text"] or "Conclusion A final." in captured["pdf_text"]
    assert "Results B observed." in captured["pdf_text"] or "Conclusion B final." in captured["pdf_text"]


def test_dispatch_pdf_route_rejects_invalid_compare_context_after_truncation(tmp_path, monkeypatch):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A short.\n\nResults A observed.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B short.\n\nResults B observed.\n\nConclusion B final."
        ),
        answer_question_fn=lambda **kwargs: "不应该进入生成阶段",
    )

    monkeypatch.setattr(
        pdf_service_module,
        "smart_truncate_pdf_content",
        lambda *args, **kwargs: (
            "==== 文献 1: paper-a.pdf ====\n作者信息与版权页。\n\n"
            "==== 文献 2: paper-b.pdf ====\n作者信息与版权页。"
        ),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]
    assert "最小比较上下文" in result["answer_text"]


def test_dispatch_pdf_route_allows_appendix_word_inside_body_content(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    captured: dict[str, str] = {}
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A mentions appendix-based evaluation setup.\n\n"
            "Results A observed.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B mentions appendix-based evaluation setup.\n\n"
            "Results B observed.\n\nConclusion B final."
        ),
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])})
        or _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "appendix-based evaluation setup" in captured["pdf_text"]


def test_dispatch_pdf_route_rejects_compare_context_when_one_document_loses_minimum_body(tmp_path, monkeypatch):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract shared compare anchor.\n\nResults shared compare anchor.\n\nConclusion shared compare anchor."
            if path == str(pdf_path_a)
            else "Abstract shared compare anchor.\n\nResults shared compare anchor.\n\nConclusion shared compare anchor."
        ),
        answer_question_fn=lambda **kwargs: "不应该进入生成阶段",
    )

    monkeypatch.setattr(
        pdf_service_module,
        "smart_truncate_pdf_content",
        lambda *args, **kwargs: (
            "==== 文献 1: paper-a.pdf ====\n"
            "Abstract shared compare anchor.\n\nResults shared compare anchor.\n\nConclusion shared compare anchor.\n\n"
            "==== 文献 2: paper-b.pdf ====\n作者信息与版权页。"
        ),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "无法完成完整比较" in result["answer_text"]


def test_dispatch_pdf_route_accepts_long_compare_paragraphs_when_continuous_body_budget_is_sufficient(tmp_path):
    pdf_paths = []
    execution_files = []
    selected_ids = []
    for index in range(4):
        pdf_path = tmp_path / f"paper-{index + 1}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
        pdf_paths.append(pdf_path)
        execution_files.append(
            {
                "file_id": 200 + index,
                "file_type": "pdf",
                "file_name": f"paper-{index + 1}.pdf",
                "local_path": str(pdf_path),
            }
        )
        selected_ids.append(200 + index)
    contract = build_patent_file_contract(
        question="对比一下这四篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=selected_ids,
        primary_file_id=selected_ids[0],
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "selected_file_ids": selected_ids},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    long_abstract = "Abstract section with detailed compare evidence. " * 12
    long_conclusion = "Conclusion section with detailed tail evidence. " * 12
    captured: dict[str, str] = {}
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            f"{long_abstract}\n\nMethod {Path(path).stem}.\n\nResults {Path(path).stem} observed.\n\n{long_conclusion}"
        ),
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])})
        or _build_valid_compare_answer([f"paper-{index}.pdf" for index in range(1, 5)]),
        max_pdf_chars=1000,
        compare_max_pdf_chars=50000,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    normalized = re.sub(r"\s+", " ", captured["pdf_text"])
    assert normalized.count("Abstract section with detailed compare evidence.") >= 4
    assert normalized.count("Conclusion section with detailed tail evidence.") >= 4


def test_dispatch_pdf_route_preserves_continuous_compare_body_for_flattened_single_newline_pdf_text(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    flat_front_matter = "作者信息与版权页。 " * 220
    captured: dict[str, str] = {}
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            f"{flat_front_matter}\nAbstract A short.\nMethod A.\nResults A observed.\nConclusion A final."
            if path == str(pdf_path_a)
            else f"{flat_front_matter}\nAbstract B short.\nMethod B.\nResults B observed.\nConclusion B final."
        ),
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])})
        or _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
        max_pdf_chars=560,
        compare_max_pdf_chars=50000,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "Abstract A short." in captured["pdf_text"]
    assert "Results A observed." in captured["pdf_text"] or "Conclusion A final." in captured["pdf_text"]
    assert "Abstract B short." in captured["pdf_text"]
    assert "Results B observed." in captured["pdf_text"] or "Conclusion B final." in captured["pdf_text"]


def test_dispatch_pdf_route_allows_late_appendix_based_body_paragraph(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A short.\n\nMethod A.\n\nAppendix-based evaluation setup improved recall.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B short.\n\nMethod B.\n\nAppendix-based evaluation setup improved precision.\n\nConclusion B final."
        ),
        answer_question_fn=lambda **kwargs: _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"


def test_dispatch_pdf_route_matches_compare_sections_by_exact_file_label(tmp_path, monkeypatch):
    pdf_path_short = tmp_path / "foo.pdf"
    pdf_path_long = tmp_path / "my-foo.pdf"
    pdf_path_short.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_long.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[21, 22],
        primary_file_id=21,
        execution_files=[
            {"file_id": 21, "file_type": "pdf", "file_name": "foo.pdf", "local_path": str(pdf_path_short)},
            {"file_id": 22, "file_type": "pdf", "file_name": "my-foo.pdf", "local_path": str(pdf_path_long)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [21, 22]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract foo exact.\n\nResults foo exact.\n\nConclusion foo exact."
            if path == str(pdf_path_short)
            else "Abstract myfoo exact.\n\nResults myfoo exact.\n\nConclusion myfoo exact."
        ),
        answer_question_fn=lambda **kwargs: _build_valid_compare_answer(["my-foo.pdf", "foo.pdf"]),
    )

    monkeypatch.setattr(
        pdf_service_module,
        "smart_truncate_pdf_content",
        lambda *args, **kwargs: (
            "==== 文献 1: my-foo.pdf ====\n"
            "Abstract myfoo exact.\n\nResults myfoo exact.\n\nConclusion myfoo exact.\n\n"
            "==== 文献 2: foo.pdf ====\n"
            "Abstract foo exact.\n\nResults foo exact.\n\nConclusion foo exact."
        ),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"


def test_dispatch_pdf_route_preserves_section_body_when_headings_are_standalone_lines(tmp_path):
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    contract = build_patent_file_contract(
        question="对比一下这两篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11, 12],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "paper-a.pdf", "local_path": str(pdf_path_a)},
            {**PDF_FILE_2, "file_name": "paper-b.pdf", "local_path": str(pdf_path_b)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    front_matter = "作者信息与版权页。 " * 200
    captured: dict[str, str] = {}
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            (
                f"{front_matter}\n\nAbstract\n\nAbstract body A keeps the real summary evidence.\n\n"
                "Methods\n\nMethod body A.\n\nResults\n\nResults body A keeps the real compare evidence.\n\n"
                "Conclusion\n\nConclusion body A keeps the real tail evidence."
            )
            if path == str(pdf_path_a)
            else (
                f"{front_matter}\n\nAbstract\n\nAbstract body B keeps the real summary evidence.\n\n"
                "Methods\n\nMethod body B.\n\nResults\n\nResults body B keeps the real compare evidence.\n\n"
                "Conclusion\n\nConclusion body B keeps the real tail evidence."
            )
        ),
        answer_question_fn=lambda **kwargs: captured.update({"pdf_text": str(kwargs["pdf_text"])})
        or _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
        max_pdf_chars=560,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_text_compare"
    assert "Abstract body A keeps the real" in captured["pdf_text"]
    assert "Abstract body B keeps the real" in captured["pdf_text"]
    assert "Results body A keeps the real" in captured["pdf_text"] or "Conclusion body A keeps the real" in captured["pdf_text"]
    assert "Results body B keeps the real" in captured["pdf_text"] or "Conclusion body B keeps the real" in captured["pdf_text"]


def test_dispatch_pdf_route_fails_explicitly_when_compare_budget_is_too_small(tmp_path):
    pdf_paths = []
    execution_files = []
    selected_ids = []
    for index in range(4):
        pdf_path = tmp_path / f"paper-{index + 1}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
        pdf_paths.append(pdf_path)
        execution_files.append(
            {
                "file_id": 100 + index,
                "file_type": "pdf",
                "file_name": f"paper-{index + 1}.pdf",
                "local_path": str(pdf_path),
            }
        )
        selected_ids.append(100 + index)
    contract = build_patent_file_contract(
        question="对比一下这四篇文献的内容",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=selected_ids,
        primary_file_id=selected_ids[0],
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "selected_file_ids": selected_ids},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            ("前置背景信息。 " * 120)
            + "\n\nAbstract.\n\nResults show measurable variation.\n\nConclusion contains unique tail evidence for "
            + Path(path).name
        ),
        answer_question_fn=lambda **kwargs: "",
        max_pdf_chars=120,
    )
    service._max_pdf_chars = 120

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=service,
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
    assert "compare 截断预算不足" in result["answer_text"]


def test_dispatch_tabular_route_uses_patent_tabular_service():
    contract = build_patent_file_contract(
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[TABLE_FILE],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert result["handler"] == "tabular"
    assert result["route"] == "tabular_qa"
    assert result["source_scope"] == "table"
    assert result["query_mode"] == "patent_tabular_qa"
    assert result["answer_text"]
    assert result["used_files"] == [TABLE_FILE]
    assert result["steps"][0]["title"] == "进入文件分支"
    assert result["timings"]["patent_tabular_route_ms"] == 1
    assert result["kb_enabled"] is False


def test_dispatch_tabular_route_uses_real_table_content_when_local_path_is_available(tmp_path):
    csv_path = tmp_path / "cells.csv"
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请总结这个表格的重点",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 方案容量 120mAh，LFP 更安全，NCM 能量更高。",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=service,
    )

    assert result["handler"] == "tabular"
    assert result["metadata"]["answer_mode"] == "table_execution_summary"
    assert "匹配工作表" in result["metadata"]["table_evidence_context"]
    assert "真实表格总结" in result["answer_text"]
    assert "Patent tabular route answered" not in result["answer_text"]


def test_dispatch_tabular_route_passes_patent_adapted_prompt_to_answer_fn(tmp_path):
    csv_path = tmp_path / "cells.csv"
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请总结这个表格的重点",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    captured: dict[str, str] = {}
    service = PatentTabularService(
        answer_question_fn=lambda **kwargs: captured.update(
            {
                "prompt": str(kwargs.get("prompt") or ""),
                "route_hint": str(kwargs.get("route_hint") or ""),
                "source_scope": str(kwargs.get("source_scope") or ""),
            }
        )
        or "真实表格总结：LMFP 方案容量 120mAh，LFP 更安全，NCM 能量更高。",
    )

    dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=service,
    )

    assert captured["route_hint"] == "tabular_qa"
    assert captured["source_scope"] == "table"
    assert "表格执行结果来自当前专利/文献文件的真实提取或计算结果" in captured["prompt"]
    assert "## 研究目的和背景" in captured["prompt"]
    assert "## 研究方法/实验设计" in captured["prompt"]
    assert "## 主要发现和结果" in captured["prompt"]
    assert "## 结论和意义" in captured["prompt"]


def test_dispatch_tabular_route_uses_file_name_suffix_when_local_path_has_no_extension(tmp_path):
    opaque_path = tmp_path / "upload_blob"
    _write_csv(opaque_path)
    contract = build_patent_file_contract(
        question="请总结这个表格的重点",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "excel", "file_name": "cells.csv", "local_path": str(opaque_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "table_execution_summary"
    assert "table_text_unavailable" not in str(result["metadata"])


def test_dispatch_tabular_route_marks_empty_structured_execution_as_unavailable(monkeypatch, tmp_path):
    csv_path = tmp_path / "cells.csv"
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="查一下不存在的材料",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    monkeypatch.setattr(
        tabular_service_module,
        "execute_tabular_plan",
        lambda **kwargs: {
            "sheet_name": "Sheet1",
            "operation": "lookup",
            "rows": [],
            "row_count": 0,
            "empty_reason": "no_lookup_match",
            "summary_stats": {"aggregate": "mean", "source_row_count": 0},
        },
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "table_execution_unavailable"
    assert result["metadata"]["table_evidence_context"] == ""
    assert "无法生成基于表格的回答" in result["answer_text"]


def test_dispatch_tabular_route_marks_structured_loader_failure_as_unavailable(monkeypatch, tmp_path):
    csv_path = tmp_path / "cells.csv"
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请总结这个表格的重点",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    monkeypatch.setattr(
        tabular_service_module,
        "load_workbook_cached",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert result["metadata"]["answer_mode"] == "table_execution_unavailable"
    assert result["metadata"]["table_evidence_context"] == ""
    assert "无法生成基于表格的回答" in result["answer_text"]


def test_dispatch_tabular_route_preserves_injected_extract_table_text_fn_contract(tmp_path):
    csv_path = tmp_path / "cells.csv"
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="Explain the table.",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)}],
        file_selection={"strategy": "explicit_selection"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    captured: dict[str, object] = {}

    def _custom_extract(path: str, *, question: str, file_name: str, file_type: str, max_rows_per_sheet: int = 8, max_sheets: int = 3) -> str:
        captured.update(
            {
                "path": path,
                "question": question,
                "file_name": file_name,
                "file_type": file_type,
                "max_rows_per_sheet": max_rows_per_sheet,
                "max_sheets": max_sheets,
            }
        )
        return "custom table evidence"

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(
            extract_table_text_fn=_custom_extract,
            answer_question_fn=lambda **kwargs: "真实表格总结：自定义提取器已生效。",
        ),
    )

    assert captured == {
        "path": str(csv_path),
        "question": "Explain the table.",
        "file_name": "cells.csv",
        "file_type": "csv",
        "max_rows_per_sheet": 8,
        "max_sheets": 3,
    }
    assert result["metadata"]["answer_mode"] == "table_execution_summary"
    assert "custom table evidence" in result["metadata"]["table_evidence_context"]
    assert "自定义提取器已生效" in result["answer_text"]


def test_dispatch_hybrid_route_uses_real_pdf_and_table_content_when_local_paths_are_available(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格总结结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
        answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
    )
    tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=pdf_service,
        tabular_service=tabular_service,
    )

    assert result["handler"] == "hybrid"
    assert result["metadata"]["answer_mode"] == "hybrid_unified_synthesis"
    assert "LMFP/LFP" in result["answer_text"]
    assert "120mAh" in result["answer_text"]
    assert "PDF 部分：" not in result["answer_text"]
    assert "表格部分：" not in result["answer_text"]
    assert "source_scope=" not in result["answer_text"]
    assert "Patent hybrid route combined selected PDF and table files" not in result["answer_text"]
    assert result["metadata"]["synthesis_contract"]["source_scope"] == "pdf+table"
    assert "pdf_evidence_context" in result["metadata"]["synthesis_contract"]
    assert "table_execution_context" in result["metadata"]["synthesis_contract"]
    assert "kb_evidence_context" in result["metadata"]["synthesis_contract"]
    assert "kb_reference_instruction" in result["metadata"]["synthesis_contract"]
    assert {
        "pdf_evidence_context",
        "table_execution_context",
        "kb_evidence_context",
        "kb_reference_instruction",
        "source_scope",
    }.issubset(set(result["metadata"]["synthesis_contract"].keys()))
    assert "旧 pdf summary 壳子" not in result["answer_text"]
    assert "旧 table summary 壳子" not in result["answer_text"]
    assert result["answer_text"].startswith("## 研究目的和背景")
    assert "PDF 原文证据：" not in result["answer_text"]
    assert "表格执行结果：" not in result["answer_text"]
    assert "## 局限性" in result["answer_text"]
    assert "注*" in result["answer_text"]
    assert "==== 文献 " not in result["answer_text"]
    assert not _first_bullet(result["answer_text"], "结论和意义").startswith("表格结果显示：")
    assert "真实 PDF 总结：" not in result["answer_text"]
    assert "LMFP/LFP 复配改善了充电安全性" in _section_body(result["answer_text"], "结论和意义")
    assert "列:" not in _section_body(result["answer_text"], "主要发现和结果")
    assert "真实表格总结：" not in result["answer_text"]
    assert "表格中未提供足够" not in result["answer_text"]


def test_dispatch_hybrid_route_passes_patent_adapted_prompts_to_pdf_and_tabular_subanswers(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF、表格和知识库总结结论",
        route="hybrid_qa",
        source_scope="pdf+table+kb",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+table+kb"},
        kb_enabled=True,
        allow_kb_verification=True,
    )
    captured: dict[str, str] = {}
    pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
        answer_question_fn=lambda **kwargs: captured.update(
            {
                "pdf_prompt": str(kwargs.get("prompt") or ""),
                "pdf_route_hint": str(kwargs.get("route_hint") or ""),
                "pdf_source_scope": str(kwargs.get("source_scope") or ""),
            }
        )
        or "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
    )
    tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: captured.update(
            {
                "table_prompt": str(kwargs.get("prompt") or ""),
                "table_route_hint": str(kwargs.get("route_hint") or ""),
                "table_source_scope": str(kwargs.get("source_scope") or ""),
            }
        )
        or "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
    )

    dispatch_patent_file_route(
        contract=contract,
        pdf_service=pdf_service,
        tabular_service=tabular_service,
    )

    assert captured["pdf_route_hint"] == "hybrid_qa"
    assert captured["pdf_source_scope"] == "pdf+table+kb"
    assert "当前任务属于 patent 混合文件问答中的 PDF 证据分析环节" in captured["pdf_prompt"]
    assert "不要把知识库验证信息改写成 PDF 原文结论" in captured["pdf_prompt"]
    assert captured["table_route_hint"] == "hybrid_qa"
    assert captured["table_source_scope"] == "pdf+table+kb"
    assert "当前任务属于 patent 混合文件问答中的表格证据分析环节" in captured["table_prompt"]
    assert "知识库或其他文件只能用于后续交叉验证" in captured["table_prompt"]
    assert "## 研究目的和背景" in captured["table_prompt"]


def test_dispatch_hybrid_route_preserves_real_pdf_and_table_subanswers_for_later_synthesis(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF、表格和知识库总结结论",
        route="hybrid_qa",
        source_scope="pdf+table+kb",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+table+kb"},
        kb_enabled=True,
        allow_kb_verification=True,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
        ),
    )

    pdf_answer = result["metadata"]["synthesis_contract"]["pdf_answer"]
    table_answer = result["metadata"]["synthesis_contract"]["tabular_answer"]
    assert "LMFP/LFP" in pdf_answer
    assert "120mAh" in table_answer
    assert "Patent PDF route answered" not in pdf_answer
    assert "Patent tabular route answered" not in table_answer


def test_dispatch_hybrid_route_with_kb_defers_hybrid_step_until_executor_merge(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF、表格和知识库总结结论",
        route="hybrid_qa",
        source_scope="pdf+table+kb",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+table+kb"},
        kb_enabled=True,
        allow_kb_verification=True,
    )
    progress_steps: list[dict[str, object]] = []

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
        ),
        progress_callback=progress_steps.append,
    )

    assert result["handler"] == "hybrid"
    assert all(step.get("step") != "hybrid_answer" for step in result["steps"])
    assert all(step.get("step") != "hybrid_answer" for step in result["metadata"]["steps"])
    assert all(step.get("step") != "hybrid_answer" for step in progress_steps)
    assert result["metadata"]["synthesis_contract"]["source_scope"] == "pdf+table+kb"


class _FakeHybridSynthesisService:
    def __init__(self, *, answer_text: str = "统一合成答案", runtime_signature: dict[str, object] | None = None) -> None:
        self.answer_text = answer_text
        self._runtime_signature = dict(runtime_signature or {"model": "hybrid-model", "prompt_version": "hybrid-v1"})
        self.calls: list[dict[str, object]] = []

    def answer(self, *, synthesis_contract: dict[str, object]) -> str:
        self.calls.append(dict(synthesis_contract))
        return self.answer_text

    def runtime_signature(self) -> dict[str, object]:
        return dict(self._runtime_signature)


class _ExplodingHybridSynthesisService(_FakeHybridSynthesisService):
    def answer(self, *, synthesis_contract: dict[str, object]) -> str:
        self.calls.append(dict(synthesis_contract))
        raise RuntimeError("hybrid synthesis boom")


class _FakeTabularAnswerClient:
    def __init__(self, *, model: str = "tabular-model") -> None:
        self._model = model

    def runtime_signature(self) -> dict[str, object]:
        return {"model": self._model, "top_p": 0.95}

    def answer(self, **kwargs):
        return "## 结论\n表格答案\n\n## 证据\n- LMFP 120mAh\n\n## 对比\n- 待后续对照\n\n## 限制\n- 仅表格证据"


def test_file_only_hybrid_uses_injected_hybrid_synthesis_service(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格回答结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 33], "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = _FakeHybridSynthesisService(
        answer_text="## 结论\n统一合成答案\n\n## 证据\n- PDF 与表格都支持\n\n## 对比\n- 文件证据一致\n\n## 限制\n- 仍需更多样本"
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "LMFP/LFP 复配改善充电安全，并报告循环稳定性提升。",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
        ),
        hybrid_synthesis_service=service,
    )

    assert service.calls
    assert result["answer_text"].startswith("## 结论")
    assert result["metadata"]["hybrid_synthesis_backend"] == "llm"
    assert result["metadata"]["hybrid_synthesis_prompt_version"] == HYBRID_SYNTHESIS_PROMPT_VERSION
    assert result["metadata"]["hybrid_synthesis_context_chars"] > 0
    assert "_hybrid_internal_state" not in result
    assert "pdf_synthesis_context" not in result["metadata"]["synthesis_contract"]
    assert "table_synthesis_context" not in result["metadata"]["synthesis_contract"]


def test_file_only_hybrid_normalizes_malformed_llm_answer_and_strips_internal_markers(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格回答结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 33], "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = _FakeHybridSynthesisService(
        answer_text=(
            "source_scope=pdf+table\n"
            "匹配工作表: cells\n"
            "执行操作: aggregate\n"
            "LMFP/LFP 复配改善充电安全，表格显示 LMFP 120mAh、LFP 115mAh。"
        )
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "LMFP/LFP 复配改善充电安全，并报告循环稳定性提升。",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
        ),
        hybrid_synthesis_service=service,
    )

    assert result["metadata"]["hybrid_synthesis_backend"] == "fallback_rules"
    assert "## 结论" in result["answer_text"]
    assert "## 证据" in result["answer_text"]
    assert "## 对比" in result["answer_text"]
    assert "## 限制" in result["answer_text"]
    assert "LMFP/LFP 复配改善充电安全" in result["answer_text"]
    assert "source_scope=" not in result["answer_text"]
    assert "匹配工作表:" not in result["answer_text"]
    assert "执行操作:" not in result["answer_text"]


def test_hybrid_route_with_kb_stashes_internal_state_and_public_contract_stays_compact(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF、表格和知识库回答结论",
        route="hybrid_qa",
        source_scope="pdf+table+kb",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 33], "source_scope": "pdf+table+kb"},
        kb_enabled=True,
        allow_kb_verification=True,
    )
    service = _FakeHybridSynthesisService()

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "LMFP/LFP 复配改善充电安全，并报告循环稳定性提升。",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
        ),
        hybrid_synthesis_service=service,
    )

    assert service.calls == []
    assert result["_hybrid_internal_state"]["synthesis_contract"]["pdf_synthesis_context"]
    assert result["_hybrid_internal_state"]["synthesis_contract"]["table_synthesis_context"]
    assert result["_hybrid_internal_state"]["synthesis_contract"]["kb_synthesis_context"] == ""
    assert result["_hybrid_internal_state"]["synthesis_contract"]["available_sources"] == ["pdf", "table"]
    assert result["_hybrid_internal_state"]["synthesis_contract"]["source_answer_modes"]["pdf"]
    assert result["_hybrid_internal_state"]["synthesis_contract"]["source_answer_modes"]["table"]
    assert "pdf_synthesis_context" not in result["metadata"]["synthesis_contract"]
    assert "table_synthesis_context" not in result["metadata"]["synthesis_contract"]


def test_hybrid_route_passes_richer_table_synthesis_context_to_llm_contract(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请总结 PDF 和表格的研究结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 33], "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    service = _FakeHybridSynthesisService()

    dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "LMFP/LFP 复配改善充电安全，并报告循环稳定性提升。",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
        ),
        hybrid_synthesis_service=service,
    )

    assert service.calls
    synthesis_contract = service.calls[0]
    assert "全表统计摘要:" not in synthesis_contract["table_execution_context"]
    assert "全表统计摘要:" in synthesis_contract["table_synthesis_context"]
    assert len(synthesis_contract["table_synthesis_context"]) > len(synthesis_contract["table_execution_context"])
    assert synthesis_contract["available_sources"] == ["pdf", "table"]


def test_hybrid_synthesis_failure_falls_back_to_rule_synthesis(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格回答结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 33], "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "LMFP/LFP 复配改善充电安全，并报告循环稳定性提升。",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
        ),
        hybrid_synthesis_service=_ExplodingHybridSynthesisService(),
    )

    assert result["metadata"]["hybrid_synthesis_backend"] == "fallback_rules"
    assert result["answer_text"]
    assert "source_scope=" not in result["answer_text"]
    assert "匹配工作表:" not in result["answer_text"]
    assert "执行操作:" not in result["answer_text"]


def test_file_route_cache_fingerprint_changes_when_hybrid_runtime_signature_changes():
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格回答结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[PDF_FILE, TABLE_FILE],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 33], "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    plan = plan_patent_file_route(contract)
    tabular_service = PatentTabularService(answer_client=_FakeTabularAnswerClient(model="tabular-v1"), auto_answer_client=False)
    left = build_file_route_cache_fingerprint(
        question=contract.question,
        route=contract.route,
        source_scope=contract.source_scope,
        selected_file_ids=list(contract.selected_file_ids),
        primary_file_id=contract.primary_file_id,
        selected_execution_files=[item.as_payload() for item in contract.selected_execution_files],
        file_selection=dict(contract.file_selection),
        runtime_signature=_file_route_runtime_signature(
            plan=plan,
            pdf_service=PatentPdfService(),
            tabular_service=tabular_service,
            hybrid_synthesis_service=None,
        ),
    )
    right = build_file_route_cache_fingerprint(
        question=contract.question,
        route=contract.route,
        source_scope=contract.source_scope,
        selected_file_ids=list(contract.selected_file_ids),
        primary_file_id=contract.primary_file_id,
        selected_execution_files=[item.as_payload() for item in contract.selected_execution_files],
        file_selection=dict(contract.file_selection),
        runtime_signature=_file_route_runtime_signature(
            plan=plan,
            pdf_service=PatentPdfService(),
            tabular_service=tabular_service,
            hybrid_synthesis_service=_FakeHybridSynthesisService(runtime_signature={"model": "hybrid-v2", "prompt_version": "hybrid-v2"}),
        ),
    )

    assert left != right


def test_file_route_cache_fingerprint_changes_when_tabular_runtime_signature_changes():
    contract = build_patent_file_contract(
        question="请总结表格结论",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[TABLE_FILE],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [33], "source_scope": "table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    plan = plan_patent_file_route(contract)
    left = build_file_route_cache_fingerprint(
        question=contract.question,
        route=contract.route,
        source_scope=contract.source_scope,
        selected_file_ids=list(contract.selected_file_ids),
        primary_file_id=contract.primary_file_id,
        selected_execution_files=[item.as_payload() for item in contract.selected_execution_files],
        file_selection=dict(contract.file_selection),
        runtime_signature=_file_route_runtime_signature(
            plan=plan,
            pdf_service=PatentPdfService(),
            tabular_service=PatentTabularService(answer_client=_FakeTabularAnswerClient(model="tabular-v1"), auto_answer_client=False),
            hybrid_synthesis_service=None,
        ),
    )
    right = build_file_route_cache_fingerprint(
        question=contract.question,
        route=contract.route,
        source_scope=contract.source_scope,
        selected_file_ids=list(contract.selected_file_ids),
        primary_file_id=contract.primary_file_id,
        selected_execution_files=[item.as_payload() for item in contract.selected_execution_files],
        file_selection=dict(contract.file_selection),
        runtime_signature=_file_route_runtime_signature(
            plan=plan,
            pdf_service=PatentPdfService(),
            tabular_service=PatentTabularService(
                answer_client=_FakeTabularAnswerClient(model="tabular-v2"),
                auto_answer_client=False,
                max_table_chars=16000,
            ),
            hybrid_synthesis_service=None,
        ),
    )

    assert left != right


def test_file_route_runtime_signature_exposes_table_parity_versions_for_table_scopes():
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格回答结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[PDF_FILE, TABLE_FILE],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 33], "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    plan = plan_patent_file_route(contract)

    runtime_signature = _file_route_runtime_signature(
        plan=plan,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(auto_answer_client=False),
        hybrid_synthesis_service=None,
    )

    assert runtime_signature["table_parity_signature"]["planner_version"]
    assert runtime_signature["table_parity_signature"]["summary_context_version"]
    assert runtime_signature["table_parity_signature"]["prompt_version"]
    assert runtime_signature["table_parity_signature"]["table_context_budget"] > 0


def test_file_route_runtime_signature_exposes_new_compare_tables_versions_for_table_scopes():
    contract = build_patent_file_contract(
        question="对比一下这两个表格",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[TABLE_FILE],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [33], "source_scope": "table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    plan = plan_patent_file_route(contract)

    runtime_signature = _file_route_runtime_signature(
        plan=plan,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(auto_answer_client=False),
        hybrid_synthesis_service=None,
    )

    assert runtime_signature["table_parity_signature"]["compare_tables_version"]
    assert runtime_signature["table_parity_signature"]["compare_status_version"]


def test_file_route_cache_fingerprint_changes_when_compare_tables_runtime_signature_changes(monkeypatch):
    contract = build_patent_file_contract(
        question="对比一下这两个表格",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[TABLE_FILE],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [33], "source_scope": "table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    plan = plan_patent_file_route(contract)

    left = build_file_route_cache_fingerprint(
        question=contract.question,
        route=contract.route,
        source_scope=contract.source_scope,
        selected_file_ids=list(contract.selected_file_ids),
        primary_file_id=contract.primary_file_id,
        selected_execution_files=[item.as_payload() for item in contract.selected_execution_files],
        file_selection=dict(contract.file_selection),
        runtime_signature=_file_route_runtime_signature(
            plan=plan,
            pdf_service=PatentPdfService(),
            tabular_service=PatentTabularService(auto_answer_client=False),
            hybrid_synthesis_service=None,
        ),
    )

    monkeypatch.setattr(file_routes_module, "_PATENT_TABLE_COMPARE_TABLES_VERSION", "patent-tabular-compare-v999")
    right = build_file_route_cache_fingerprint(
        question=contract.question,
        route=contract.route,
        source_scope=contract.source_scope,
        selected_file_ids=list(contract.selected_file_ids),
        primary_file_id=contract.primary_file_id,
        selected_execution_files=[item.as_payload() for item in contract.selected_execution_files],
        file_selection=dict(contract.file_selection),
        runtime_signature=_file_route_runtime_signature(
            plan=plan,
            pdf_service=PatentPdfService(),
            tabular_service=PatentTabularService(auto_answer_client=False),
            hybrid_synthesis_service=None,
        ),
    )

    assert left != right


def test_non_table_routes_do_not_expose_compare_tables_parity_metadata():
    contract = build_patent_file_contract(
        question="请总结 PDF 结论",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[PDF_FILE],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    plan = plan_patent_file_route(contract)

    runtime_signature = _file_route_runtime_signature(
        plan=plan,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(auto_answer_client=False),
        hybrid_synthesis_service=None,
    )

    assert "table_parity_signature" not in runtime_signature


@pytest.mark.parametrize(
    ("route", "source_scope", "selected_file_ids", "primary_file_id", "execution_files", "kb_enabled"),
    [
        ("pdf_qa", "pdf", [11], 11, [PDF_FILE], False),
        ("hybrid_qa", "pdf+kb", [11], 11, [PDF_FILE], True),
    ],
)
def test_file_route_cache_fingerprint_ignores_table_runtime_changes_for_non_table_scopes(
    route,
    source_scope,
    selected_file_ids,
    primary_file_id,
    execution_files,
    kb_enabled,
):
    contract = build_patent_file_contract(
        question="请结合当前文件回答结论",
        route=route,
        source_scope=source_scope,
        selected_file_ids=selected_file_ids,
        primary_file_id=primary_file_id,
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "selected_file_ids": selected_file_ids, "source_scope": source_scope},
        kb_enabled=kb_enabled,
        allow_kb_verification=kb_enabled,
    )
    plan = plan_patent_file_route(contract)

    left = build_file_route_cache_fingerprint(
        question=contract.question,
        route=contract.route,
        source_scope=contract.source_scope,
        selected_file_ids=list(contract.selected_file_ids),
        primary_file_id=contract.primary_file_id,
        selected_execution_files=[item.as_payload() for item in contract.selected_execution_files],
        file_selection=dict(contract.file_selection),
        runtime_signature=_file_route_runtime_signature(
            plan=plan,
            pdf_service=PatentPdfService(),
            tabular_service=PatentTabularService(answer_client=_FakeTabularAnswerClient(model="tabular-v1"), auto_answer_client=False),
            hybrid_synthesis_service=None,
        ),
    )
    right = build_file_route_cache_fingerprint(
        question=contract.question,
        route=contract.route,
        source_scope=contract.source_scope,
        selected_file_ids=list(contract.selected_file_ids),
        primary_file_id=contract.primary_file_id,
        selected_execution_files=[item.as_payload() for item in contract.selected_execution_files],
        file_selection=dict(contract.file_selection),
        runtime_signature=_file_route_runtime_signature(
            plan=plan,
            pdf_service=PatentPdfService(),
            tabular_service=PatentTabularService(
                answer_client=_FakeTabularAnswerClient(model="tabular-v2"),
                auto_answer_client=False,
                max_table_chars=16000,
            ),
            hybrid_synthesis_service=None,
        ),
    )

    assert left == right


def test_tabular_client_failure_does_not_seed_file_route_cache(tmp_path):
    csv_path = tmp_path / "cells.csv"
    _write_csv(csv_path)

    class _CacheStub:
        def __init__(self) -> None:
            self.set_calls = 0

        def get_file_route_cache(self, *, fingerprint: str):
            return None

        def claim_file_route_singleflight(self, *, fingerprint: str, ttl_seconds: int):
            return "token-1"

        def clear_file_route_singleflight(self, *, fingerprint: str, token: str):
            return True

        def set_file_route_cache(self, *, fingerprint: str, payload, ttl_seconds: int):
            self.set_calls += 1
            return True

    class _ExplodingTabularClient:
        def answer(self, **kwargs):
            raise RuntimeError("tabular llm boom")

    cache = _CacheStub()
    contract = build_patent_file_contract(
        question="请总结表格结论",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [33], "source_scope": "table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        tabular_service=PatentTabularService(answer_client=_ExplodingTabularClient(), auto_answer_client=False),
        execution_cache=cache,
    )

    assert result["answer_text"]
    assert result["metadata"]["cache_hit"] is False
    assert cache.set_calls == 0


def test_tabular_structured_loader_failure_does_not_seed_file_route_cache(tmp_path, monkeypatch):
    csv_path = tmp_path / "cells.csv"
    _write_csv(csv_path)

    class _CacheStub:
        def __init__(self) -> None:
            self.set_calls = 0

        def get_file_route_cache(self, *, fingerprint: str):
            return None

        def claim_file_route_singleflight(self, *, fingerprint: str, ttl_seconds: int):
            return "token-1"

        def clear_file_route_singleflight(self, *, fingerprint: str, token: str):
            return True

        def set_file_route_cache(self, *, fingerprint: str, payload, ttl_seconds: int):
            self.set_calls += 1
            return True

    def _boom(**_kwargs):
        raise RuntimeError("workbook boom")

    monkeypatch.setattr(tabular_service_module, "load_workbook_cached", _boom)
    cache = _CacheStub()
    contract = build_patent_file_contract(
        question="请总结表格结论",
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[{"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [33], "source_scope": "table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        execution_cache=cache,
    )

    assert result["answer_text"]
    assert result["metadata"]["cache_hit"] is False
    assert cache.set_calls == 0


def test_file_only_hybrid_llm_failure_does_not_seed_file_route_cache(tmp_path):
    pdf_path = tmp_path / "battery-paper.pdf"
    csv_path = tmp_path / "cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)

    class _CacheStub:
        def __init__(self) -> None:
            self.set_calls = 0

        def get_file_route_cache(self, *, fingerprint: str):
            return None

        def claim_file_route_singleflight(self, *, fingerprint: str, ttl_seconds: int):
            return "token-1"

        def clear_file_route_singleflight(self, *, fingerprint: str, token: str):
            return True

        def set_file_route_cache(self, *, fingerprint: str, payload, ttl_seconds: int):
            self.set_calls += 1
            return True

    cache = _CacheStub()
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格回答结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11, 33], "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "LMFP/LFP 复配改善充电安全，并报告循环稳定性提升。",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
        ),
        hybrid_synthesis_service=_ExplodingHybridSynthesisService(),
        execution_cache=cache,
    )

    assert result["metadata"]["hybrid_synthesis_backend"] == "fallback_rules"
    assert cache.set_calls == 0


def test_dispatch_hybrid_route_marks_failure_when_no_usable_file_evidence_exists():
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格总结结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[PDF_FILE, TABLE_FILE],
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert "暂时无法生成联合回答" in result["answer_text"]
    assert result["steps"][-1]["step"] == "hybrid_answer"
    assert result["steps"][-1]["status"] == "error"
    assert result["metadata"]["steps"][-1]["status"] == "error"


def test_dispatch_hybrid_route_with_structured_stream_router_emits_preview_then_final(tmp_path):
    pdf_path = tmp_path / "battery-paper-preview.pdf"
    csv_path = tmp_path / "cells-preview.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格总结结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "file_name": "battery-paper-preview.pdf", "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "cells-preview.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
        answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性，并提供了实验验证。",
    )
    tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh，并且备注字段体现了差异。",
    )
    streamed_payloads: list[object] = []

    from server.patent import stream_events as stream_events_module

    router_cls = getattr(stream_events_module, "PatentStructuredContentRouter", None)
    assert router_cls is not None
    state = stream_events_module.PatentContentStreamState()
    router = router_cls(callback=streamed_payloads.append)

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=pdf_service,
        tabular_service=tabular_service,
        content_callback=router,
    )

    typed_events = [payload for payload in streamed_payloads if isinstance(payload, dict)]
    assert typed_events
    assert any(event["content_role"] == "preview" and event["content_source"] == "pdf" for event in typed_events)
    assert any(event["content_role"] == "preview" and event["content_source"] == "table" for event in typed_events)
    assert any(event["content_role"] == "final" and event["content_source"] == "hybrid" for event in typed_events)
    first_final_index = next(index for index, event in enumerate(typed_events) if event["content_role"] == "final")
    assert all(
        index < first_final_index
        for index, event in enumerate(typed_events)
        if event["content_role"] == "preview"
    )
    for event in typed_events:
        state.observe(event)
    final_text = "".join(event["content"] for event in typed_events if event["content_role"] == "final")
    assert final_text == result["answer_text"]


def test_dispatch_hybrid_route_does_not_use_shell_subanswers_as_direct_conclusion(tmp_path):
    pdf_path = tmp_path / "spec.pdf"
    csv_path = tmp_path / "claims.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格总结结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "LMFP/LFP route reports safer charging behavior and stable cycling.",
            answer_question_fn=lambda **kwargs: "\n".join(
                [
                    "## 研究目的和背景",
                    "- 旧 pdf summary 壳子，不应主导最终格式。",
                    "",
                    "## 结论和意义",
                    "- 旧 pdf summary 壳子，不应主导最终格式。",
                ]
            ),
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "\n".join(
                [
                    "## 研究目的和背景",
                    "- 旧 table summary 壳子，不应主导最终格式。",
                    "",
                    "## 主要发现和结果",
                    "- 旧 table summary 壳子，不应主导最终格式。",
                ]
            ),
        ),
    )

    assert "旧 pdf summary 壳子" not in result["answer_text"]
    assert "旧 table summary 壳子" not in result["answer_text"]
    assert "PDF中未提及足够" not in result["answer_text"]
    assert "表格中未提供足够" not in result["answer_text"]
    assert "120" in result["answer_text"] or "LMFP/LFP" in result["answer_text"]


def test_dispatch_hybrid_route_with_only_shell_subanswers_and_no_evidence_stays_unavailable():
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格总结结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[PDF_FILE, TABLE_FILE],
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    class _ShellPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            return {
                "answer_text": "\n".join(
                    [
                        "## 研究目的和背景",
                        "- 旧 pdf summary 壳子，不应主导最终格式。",
                    ]
                ),
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"step": "pdf_answer", "title": "生成文件答案", "message": "ok", "status": "success"}],
                "metadata": {
                    "answer_mode": "pdf_text_summary",
                    "pdf_evidence_context": "",
                },
                "timings": {"pdf_ms": 1},
                "used_files": [item.as_payload() for item in contract.selected_execution_files if item.family == "pdf"],
                "file_selection": dict(contract.file_selection),
            }

    class _ShellTabularService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            return {
                "answer_text": "\n".join(
                    [
                        "## 研究目的和背景",
                        "- 旧 table summary 壳子，不应主导最终格式。",
                    ]
                ),
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"step": "tabular_answer", "title": "生成文件答案", "message": "ok", "status": "success"}],
                "metadata": {
                    "answer_mode": "table_execution_summary",
                    "table_evidence_context": "",
                },
                "timings": {"patent_tabular_route_ms": 1},
                "used_files": [item.as_payload() for item in contract.selected_execution_files if item.family == "table"],
                "file_selection": dict(contract.file_selection),
            }

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=_ShellPdfService(),
        tabular_service=_ShellTabularService(),
    )

    assert "暂时无法生成联合回答" in result["answer_text"]
    assert result["steps"][-1]["status"] == "error"
    assert result["metadata"]["steps"][-1]["status"] == "error"


def test_dispatch_hybrid_route_summary_filters_raw_table_structure_for_non_material_tables(tmp_path):
    pdf_path = tmp_path / "battery-paper-alt.pdf"
    csv_path = tmp_path / "alt-cells.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_alt_csv(csv_path)
    contract = build_patent_file_contract(
        question="请结合 PDF 和表格总结结论",
        route="hybrid_qa",
        source_scope="pdf+table",
        selected_file_ids=[11, 33],
        primary_file_id=11,
        execution_files=[
            {**PDF_FILE, "local_path": str(pdf_path)},
            {"file_id": 33, "file_type": "csv", "file_name": "alt-cells.csv", "local_path": str(csv_path)},
        ],
        file_selection={"strategy": "explicit_selection", "source_scope": "pdf+table"},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(
            extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
            answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
        ),
        tabular_service=PatentTabularService(
            answer_question_fn=lambda **kwargs: "真实表格总结：该表记录了不同倍率与温度条件下的评分变化。",
        ),
    )

    answer = result["answer_text"]
    assert "工作表:" not in answer
    assert "列:" not in answer
    assert "代表性行:" not in answer
    assert "真实表格总结：" not in answer


@pytest.mark.parametrize(
    ("source_scope", "handler", "include_kb", "families"),
    [
        ("pdf+kb", "pdf", True, ["pdf"]),
        ("table+kb", "tabular", True, ["table"]),
        ("pdf+table", "hybrid", False, ["pdf", "table"]),
        ("pdf+table+kb", "hybrid", True, ["pdf", "table"]),
    ],
)
def test_hybrid_route_planning_covers_all_supported_source_scopes(source_scope, handler, include_kb, families):
    selected_file_ids = [11] if families == ["pdf"] else [33] if families == ["table"] else [11, 33]
    primary_file_id = selected_file_ids[0]
    execution_files = [PDF_FILE] if families == ["pdf"] else [TABLE_FILE] if families == ["table"] else [PDF_FILE, TABLE_FILE]
    contract = build_patent_file_contract(
        route="hybrid_qa",
        source_scope=source_scope,
        selected_file_ids=selected_file_ids,
        primary_file_id=primary_file_id,
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "source_scope": source_scope},
        kb_enabled=include_kb,
        allow_kb_verification=include_kb,
    )

    plan = plan_patent_file_route(contract)
    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=PatentPdfService(),
        tabular_service=PatentTabularService(),
    )

    assert plan.handler == handler
    assert list(plan.file_families) == families
    assert plan.include_kb is include_kb
    assert result["handler"] == handler
    assert result["source_scope"] == source_scope
    assert result["query_mode"] == "patent_hybrid_qa"
    assert result["answer_text"]
    assert result["kb_enabled"] is include_kb


class _ExplodingExecuteService:
    def execute(self, **kwargs):
        raise AssertionError("service execute should not run on cache hit")


@pytest.mark.parametrize(
    ("route", "source_scope", "selected_file_ids", "primary_file_id", "execution_files", "cached_payload"),
    [
        (
            "pdf_qa",
            "pdf",
            [11],
            11,
            [PDF_FILE],
            {
                "handler": "pdf",
                "answer_text": "cached pdf answer",
                "route": "pdf_qa",
                "query_mode": "patent_pdf_qa",
                "source_scope": "pdf",
                "steps": [{"step": "dispatch", "title": "进入 PDF 分支", "message": "进入 PDF 问答分支", "status": "success"}],
                "metadata": {"answer_mode": "pdf_text_summary"},
                "timings": {"patent_pdf_route_ms": 1},
                "used_files": [PDF_FILE],
                "selected_file_ids": [11],
                "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11]},
                "kb_enabled": False,
            },
        ),
        (
            "tabular_qa",
            "table",
            [33],
            33,
            [TABLE_FILE],
            {
                "handler": "tabular",
                "answer_text": "cached table answer",
                "route": "tabular_qa",
                "query_mode": "patent_tabular_qa",
                "source_scope": "table",
                "steps": [{"step": "dispatch", "title": "进入文件分支", "message": "进入表格/混合问答分支", "status": "success"}],
                "metadata": {"answer_mode": "table_text_summary"},
                "timings": {"patent_table_route_ms": 1},
                "used_files": [TABLE_FILE],
                "selected_file_ids": [33],
                "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [33]},
                "kb_enabled": False,
            },
        ),
        (
            "hybrid_qa",
            "pdf+table",
            [11, 33],
            11,
            [PDF_FILE, TABLE_FILE],
            {
                "handler": "hybrid",
                "answer_text": "cached hybrid answer",
                "route": "hybrid_qa",
                "query_mode": "patent_hybrid_qa",
                "source_scope": "pdf+table",
                "steps": [{"step": "dispatch", "title": "进入文件分支", "message": "进入表格/混合问答分支", "status": "success"}],
                "metadata": {"answer_mode": "hybrid_unified_synthesis"},
                "timings": {"patent_hybrid_route_ms": 1},
                "used_files": [PDF_FILE, TABLE_FILE],
                "selected_file_ids": [11, 33],
                "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11, 33]},
                "kb_enabled": False,
            },
        ),
    ],
)
def test_dispatch_file_route_marks_cache_metadata_for_all_handlers(
    route,
    source_scope,
    selected_file_ids,
    primary_file_id,
    execution_files,
    cached_payload,
):
    class _CacheHitStub:
        def get_file_route_cache(self, *, fingerprint: str):
            return dict(cached_payload)

    contract = build_patent_file_contract(
        question="请总结选中的文件",
        route=route,
        source_scope=source_scope,
        selected_file_ids=selected_file_ids,
        primary_file_id=primary_file_id,
        execution_files=execution_files,
        file_selection={"strategy": "explicit_selection", "selected_file_ids": list(selected_file_ids)},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    result = dispatch_patent_file_route(
        contract=contract,
        pdf_service=_ExplodingExecuteService(),
        tabular_service=_ExplodingExecuteService(),
        execution_cache=_CacheHitStub(),
    )

    assert result["answer_text"] == cached_payload["answer_text"]
    assert result["metadata"]["cache_hit"] is True
    assert result["metadata"]["cache_namespace"] == "file-route"
    assert result["metadata"]["cache_fingerprint"]


def test_dispatch_file_route_singleflight_is_scoped_to_fingerprint_not_global_lock():
    class _FingerprintScopedLockCache:
        def __init__(self) -> None:
            self.blocked_fingerprint = ""
            self.claimed_fingerprints: list[str] = []

        def get_file_route_cache(self, *, fingerprint: str):
            return None

        def set_file_route_cache(self, *, fingerprint: str, payload, ttl_seconds: int):
            return True

        def claim_file_route_singleflight(self, *, fingerprint: str, ttl_seconds: int):
            self.claimed_fingerprints.append(fingerprint)
            if not self.blocked_fingerprint:
                self.blocked_fingerprint = fingerprint
            if fingerprint == self.blocked_fingerprint:
                return ""
            return f"token-{len(self.claimed_fingerprints)}"

        def get_file_route_singleflight_owner(self, *, fingerprint: str):
            return "other-owner" if fingerprint == self.blocked_fingerprint else ""

        def clear_file_route_singleflight(self, *, fingerprint: str, token: str):
            return True

    class _PdfServiceStub:
        def execute(self, **kwargs):
            return {
                "handler": "pdf",
                "answer_text": "fresh pdf answer",
                "route": "pdf_qa",
                "query_mode": "patent_pdf_qa",
                "source_scope": "pdf",
                "steps": [],
                "metadata": {"answer_mode": "pdf_text_summary"},
                "timings": {"patent_pdf_route_ms": 1},
                "used_files": [kwargs["contract"].selected_execution_files[0].as_payload()],
                "selected_file_ids": list(kwargs["contract"].selected_file_ids),
                "file_selection": dict(kwargs["contract"].file_selection),
                "kb_enabled": bool(kwargs.get("include_kb")),
            }

    cache = _FingerprintScopedLockCache()
    blocked_contract = build_patent_file_contract(
        question="请总结第一篇 PDF",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[PDF_FILE],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11]},
        kb_enabled=False,
        allow_kb_verification=False,
    )
    other_contract = build_patent_file_contract(
        question="请总结第二篇 PDF",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[12],
        primary_file_id=12,
        execution_files=[PDF_FILE_2],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [12]},
        kb_enabled=False,
        allow_kb_verification=False,
    )

    with pytest.raises(TimeoutError, match="singleflight wait timed out"):
        dispatch_patent_file_route(
            contract=blocked_contract,
            pdf_service=_PdfServiceStub(),
            tabular_service=PatentTabularService(),
            execution_cache=cache,
            singleflight_poll_interval_seconds=0.0,
            singleflight_wait_timeout_seconds=0.0,
        )

    result = dispatch_patent_file_route(
        contract=other_contract,
        pdf_service=_PdfServiceStub(),
        tabular_service=PatentTabularService(),
        execution_cache=cache,
        singleflight_poll_interval_seconds=0.0,
        singleflight_wait_timeout_seconds=0.0,
    )

    assert result["answer_text"] == "fresh pdf answer"
    assert cache.claimed_fingerprints[0] != cache.claimed_fingerprints[1]
