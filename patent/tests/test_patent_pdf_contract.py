from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from server.patent.pdf_contract import (
    CompareBudgetError,
    PDF_QA_SYSTEM_MESSAGE,
    build_compare_failure_message,
    build_kb_section,
    build_patent_pdf_answer_prompt,
    detect_targeted_document_index,
    format_multi_pdf_sections,
    is_compare_question,
    is_summary_question,
    smart_truncate_pdf_content,
)
from server.patent.pdf_service import PatentPdfAnswerClient


ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


FASTQA_PROMPTING = _load_module(
    "fastqa_prompting_contract",
    ROOT / "fastQA" / "app" / "modules" / "qa_pdf" / "prompting.py",
)
FASTQA_TRUNCATION = _load_module(
    "fastqa_truncation_contract",
    ROOT / "fastQA" / "app" / "modules" / "qa_pdf" / "truncation.py",
)


class _Logger:
    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


def test_system_prompt_matches_fastqa_contract():
    assert PDF_QA_SYSTEM_MESSAGE == FASTQA_PROMPTING.PDF_QA_SYSTEM_MESSAGE


def test_non_compare_summary_prompt_adapts_fastqa_structure_for_patent():
    kb_section = build_kb_section({"kb_answer": "KB evidence"})

    patent_prompt = build_patent_pdf_answer_prompt(
        question="请总结这篇文献",
        pdf_content="标题: A study\nAbstract text\nResults text",
        kb_section=kb_section,
        is_summary=True,
        is_compare=False,
        selected_file_labels=["paper-a.pdf"],
    )
    assert "## 结论" in patent_prompt
    assert "## 证据" in patent_prompt
    assert "## 对比" in patent_prompt
    assert "## 限制" in patent_prompt
    assert "知识库信息仅用于验证" in patent_prompt or "知识库信息仅可用于验证" in patent_prompt
    assert "不要把知识库信息当作新的 PDF 事实" in patent_prompt
    assert "专利/文献" in patent_prompt


def test_non_compare_non_summary_prompt_adapts_fastqa_structure_for_patent():
    patent_prompt = build_patent_pdf_answer_prompt(
        question="这篇文献里报告了什么结果？",
        pdf_content="标题: A study\nAbstract text\nResults text",
        kb_section="",
        is_summary=False,
        is_compare=False,
        selected_file_labels=["paper-a.pdf"],
    )
    assert "## 结论" in patent_prompt
    assert "## 证据" in patent_prompt
    assert "## 对比" in patent_prompt
    assert "## 限制" in patent_prompt
    assert "只允许使用 PDF 原文中明确出现的内容" in patent_prompt
    assert "不得把未在 PDF 出现的信息补写成结论" in patent_prompt


def test_hybrid_pdf_prompt_requires_file_side_scope_and_no_kb_override():
    prompt = build_patent_pdf_answer_prompt(
        question="请结合 PDF 和知识库给出结论",
        pdf_content="标题: A study\nAbstract text\nResults text",
        kb_section=build_kb_section({"kb_answer": "KB evidence"}),
        is_summary=False,
        is_compare=False,
        selected_file_labels=["paper-a.pdf"],
        route_hint="hybrid_qa",
        source_scope="pdf+kb",
    )

    assert "当前任务属于 patent 混合文件问答中的 PDF 证据分析环节" in prompt
    assert "先给出这份 PDF 单独能够支持的判断" in prompt
    assert "不要把知识库验证信息改写成 PDF 原文结论" in prompt
    assert "## 结论" in prompt
    assert "## 证据" in prompt
    assert "## 限制" in prompt


def test_request_payload_includes_kb_boundary_section_when_include_kb_is_enabled():
    client = PatentPdfAnswerClient(api_key="key", base_url="https://example.com", model="model")
    try:
        prompt_without_kb = client._build_request_payload(
            question="请总结这篇文献",
            pdf_text="标题: A study\nAbstract text\nResults text",
            file_name="paper-a.pdf",
            include_kb=False,
            stream=False,
            selected_file_labels=["paper-a.pdf"],
        )["messages"][1]["content"]
        prompt_with_kb = client._build_request_payload(
            question="请总结这篇文献",
            pdf_text="标题: A study\nAbstract text\nResults text",
            file_name="paper-a.pdf",
            include_kb=True,
            stream=False,
            selected_file_labels=["paper-a.pdf"],
        )["messages"][1]["content"]
    finally:
        client.close()

    expected_kb_section = build_kb_section({"kb_answer": "当前无额外知识库验证结果。"})

    assert prompt_without_kb != prompt_with_kb
    assert expected_kb_section.strip() in prompt_with_kb
    assert expected_kb_section.strip() not in prompt_without_kb


def test_compare_detection_accepts_implicit_compare_requests():
    assert is_compare_question("这两篇有什么异同", selected_pdf_count=2) is True
    assert is_compare_question("分别讲了什么", selected_pdf_count=2) is True
    assert is_compare_question("哪篇效果更好", selected_pdf_count=2) is True
    assert is_compare_question("比较第一篇、第二篇文献的方法", selected_pdf_count=2) is True
    assert is_compare_question("第一篇 vs 第二篇哪个好", selected_pdf_count=2) is True
    assert is_compare_question("对比第1篇、第2篇文献的结论", selected_pdf_count=2) is True


def test_compare_detection_rejects_single_document_questions_even_with_multiple_files_selected():
    assert is_compare_question("请总结第一篇文献", selected_pdf_count=2) is False
    assert is_compare_question("第一篇文献的方法是什么", selected_pdf_count=2) is False
    assert is_compare_question("比较第一篇文献中的方法和结果", selected_pdf_count=2) is False
    assert is_compare_question("对比第一篇文献里两种工艺的差异", selected_pdf_count=2) is False


def test_detect_targeted_document_index_supports_ordinals_and_file_names():
    labels = ["paper-a.pdf", "paper-b.pdf", "paper-c.pdf"]

    assert detect_targeted_document_index("请总结第三篇文献", selected_pdf_count=3, selected_file_labels=labels) == 2
    assert detect_targeted_document_index("只看 paper-b.pdf 的方法", selected_pdf_count=3, selected_file_labels=labels) == 1
    assert detect_targeted_document_index("Summarize the third document only", selected_pdf_count=3, selected_file_labels=labels) == 2
    long_labels = [f"paper-{index}.pdf" for index in range(1, 15)]
    assert detect_targeted_document_index("请总结第十四篇文献", selected_pdf_count=14, selected_file_labels=long_labels) == 13
    assert detect_targeted_document_index("Summarize the eleventh document only", selected_pdf_count=14, selected_file_labels=long_labels) == 10


def test_detect_targeted_document_index_prefers_exact_filename_boundaries_over_overlapping_stems():
    labels = ["paper-1.pdf", "paper-10.pdf"]

    assert detect_targeted_document_index("只看 paper-10.pdf 的方法", selected_pdf_count=2, selected_file_labels=labels) == 1
    assert detect_targeted_document_index("只看 paper-10 的方法", selected_pdf_count=2, selected_file_labels=labels) == 1


def test_detect_targeted_document_index_prefers_longer_suffix_variants_before_shorter_stems():
    labels = ["paper-a.pdf", "paper-a-v2.pdf"]

    assert detect_targeted_document_index("只看 paper-a-v2.pdf 的方法", selected_pdf_count=2, selected_file_labels=labels) == 1
    assert detect_targeted_document_index("只看 paper-a-v2 的方法", selected_pdf_count=2, selected_file_labels=labels) == 1


def test_multi_pdf_format_uses_fastqa_compatible_headers():
    formatted = format_multi_pdf_sections(
        [
            {"label": "paper-a.pdf", "text": "Abstract A.\n\nResults A."},
            {"label": "paper-b.pdf", "text": "Abstract B.\n\nResults B."},
        ]
    )

    assert "==== 文献 1: paper-a.pdf ====" in formatted
    assert "==== 文献 2: paper-b.pdf ====" in formatted
    assert FASTQA_TRUNCATION.MULTI_DOC_HEADER_PATTERN.search(formatted) is not None


def test_compare_prompt_includes_required_comparison_structure():
    prompt = build_patent_pdf_answer_prompt(
        question="对比一下这两篇文献的内容",
        pdf_content="==== 文献 1: paper-a.pdf ====\nA\n\n==== 文献 2: paper-b.pdf ====\nB",
        kb_section="",
        is_summary=False,
        is_compare=True,
        selected_file_labels=["paper-a.pdf", "paper-b.pdf", "paper-c.pdf"],
    )

    assert "共涉及 3 篇文献" in prompt
    assert "先分别总结每篇文献" in prompt
    assert "相同点" in prompt
    assert "差异点" in prompt
    assert "研究主题/目标" in prompt
    assert "方法/技术路线" in prompt
    assert "核心结果/证据" in prompt
    assert "结论/贡献" in prompt
    assert "paper-a.pdf" in prompt
    assert "paper-b.pdf" in prompt
    assert "paper-c.pdf" in prompt


def test_compare_fallback_refuses_to_pretend_summary_success():
    fallback = build_compare_failure_message(
        question="对比一下这两篇文献的内容",
        available_docs=["paper-a.pdf"],
        missing_docs=["paper-b.pdf"],
        reason="只有一篇文献正文可用",
    )

    assert "无法完成完整比较" in fallback
    assert "paper-a.pdf" in fallback
    assert "paper-b.pdf" in fallback
    assert "文档要点如下" not in fallback


def test_multi_pdf_compare_truncation_keeps_both_document_labels_and_compare_evidence():
    formatted = format_multi_pdf_sections(
        [
            {
                "label": "paper-a.pdf",
                "text": "Abstract A.\n\nMethod A.\n\nResults A show 15% improvement.\n\nConclusion A supports catalyst route.",
            },
            {
                "label": "paper-b.pdf",
                "text": "Abstract B.\n\nMethod B.\n\nResults B show 5% decline.\n\nConclusion B rejects catalyst route.",
            },
        ]
    )

    truncated = smart_truncate_pdf_content(
        formatted,
        max_chars=420,
        logger=_Logger(),
        is_summary=False,
        question="对比一下这两篇文献的内容",
        is_compare=True,
    )

    assert "paper-a.pdf" in truncated
    assert "paper-b.pdf" in truncated
    assert "Results A" in truncated or "Conclusion A" in truncated
    assert "Results B" in truncated or "Conclusion B" in truncated


def test_non_compare_multi_doc_truncation_matches_fastqa_contract():
    formatted = format_multi_pdf_sections(
        [
            {"label": "paper-a.pdf", "text": "Abstract A.\n\nResults A show improvement.\n\nConclusion A."},
            {"label": "paper-b.pdf", "text": "Abstract B.\n\nResults B show decline.\n\nConclusion B."},
        ]
    )

    patent_truncated = smart_truncate_pdf_content(
        formatted,
        max_chars=240,
        logger=_Logger(),
        is_summary=False,
        question="请总结这些文献",
        is_compare=False,
    )
    fastqa_truncated = FASTQA_TRUNCATION.smart_truncate_pdf_content(
        formatted,
        max_chars=240,
        logger=_Logger(),
        is_summary=False,
        question="请总结这些文献",
    )

    assert patent_truncated == fastqa_truncated


def test_compare_truncation_keeps_tail_evidence_even_with_long_front_matter():
    front_matter = "作者信息与版权页。 " * 80
    formatted = format_multi_pdf_sections(
        [
            {
                "label": "paper-a.pdf",
                "text": f"{front_matter}\n\nAbstract A.\n\nResults A show 15% improvement.\n\nConclusion A supports route A.",
            },
            {
                "label": "paper-b.pdf",
                "text": f"{front_matter}\n\nAbstract B.\n\nResults B show 5% decline.\n\nConclusion B rejects route A.",
            },
        ]
    )

    truncated = smart_truncate_pdf_content(
        formatted,
        max_chars=560,
        logger=_Logger(),
        is_summary=False,
        question="对比一下这两篇文献的内容",
        is_compare=True,
    )

    assert "Conclusion A supports route A." in truncated
    assert "Conclusion B rejects route A." in truncated


def test_compare_truncation_keeps_per_document_abstract_and_tail_evidence_with_long_front_matter():
    front_matter = "作者信息与版权页。 " * 200
    formatted = format_multi_pdf_sections(
        [
            {
                "label": f"paper-{index}.pdf",
                "text": (
                    f"{front_matter}\n\n"
                    f"Abstract {index} short.\n\n"
                    f"Method {index} uses condition {index}.\n\n"
                    f"Results {index} observed.\n\n"
                    f"Conclusion {index} final."
                ),
            }
            for index in range(1, 5)
        ]
    )

    truncated = smart_truncate_pdf_content(
        formatted,
        max_chars=1000,
        logger=_Logger(),
        is_summary=False,
        question="对比一下这四篇文献的内容",
        is_compare=True,
    )

    for index in range(1, 5):
        assert f"Abstract {index} short." in truncated
        assert f"Results {index} observed." in truncated or f"Conclusion {index} final." in truncated


def test_compare_truncation_drops_reference_tail_and_keeps_results_or_conclusion():
    front_matter = "作者信息与版权页。 " * 120
    references = "参考文献\n[1] filler citation block. " * 80
    formatted = format_multi_pdf_sections(
        [
            {
                "label": "paper-a.pdf",
                "text": (
                    f"{front_matter}\n\nAbstract A short.\n\nMethod A.\n\n"
                    f"Results A observed.\n\nConclusion A final.\n\n{references}"
                ),
            },
            {
                "label": "paper-b.pdf",
                "text": (
                    f"{front_matter}\n\nAbstract B short.\n\nMethod B.\n\n"
                    f"Results B observed.\n\nConclusion B final.\n\n{references}"
                ),
            },
        ]
    )

    truncated = smart_truncate_pdf_content(
        formatted,
        max_chars=560,
        logger=_Logger(),
        is_summary=False,
        question="对比一下这两篇文献的内容",
        is_compare=True,
    )

    assert "参考文献" not in truncated
    assert "Results A observed." in truncated or "Conclusion A final." in truncated
    assert "Results B observed." in truncated or "Conclusion B final." in truncated


def test_compare_truncation_prefers_section_body_over_heading_only_lines():
    front_matter = "作者信息与版权页。 " * 200
    formatted = format_multi_pdf_sections(
        [
            {
                "label": "paper-a.pdf",
                "text": (
                    f"{front_matter}\n\n"
                    "Abstract\n\n"
                    "Abstract body A keeps the real summary evidence.\n\n"
                    "Methods\n\n"
                    "Method body A.\n\n"
                    "Results\n\n"
                    "Results body A keeps the real compare evidence.\n\n"
                    "Conclusion\n\n"
                    "Conclusion body A keeps the real tail evidence."
                ),
            },
            {
                "label": "paper-b.pdf",
                "text": (
                    f"{front_matter}\n\n"
                    "Abstract\n\n"
                    "Abstract body B keeps the real summary evidence.\n\n"
                    "Methods\n\n"
                    "Method body B.\n\n"
                    "Results\n\n"
                    "Results body B keeps the real compare evidence.\n\n"
                    "Conclusion\n\n"
                    "Conclusion body B keeps the real tail evidence."
                ),
            },
        ]
    )

    truncated = smart_truncate_pdf_content(
        formatted,
        max_chars=560,
        logger=_Logger(),
        is_summary=False,
        question="对比一下这两篇文献的内容",
        is_compare=True,
    )

    assert "Abstract body A keeps the real" in truncated
    assert "Abstract body B keeps the real" in truncated
    assert "Results body A keeps the real" in truncated or "Conclusion body A keeps the real" in truncated
    assert "Results body B keeps the real" in truncated or "Conclusion body B keeps the real" in truncated


def test_multi_pdf_compare_truncation_raises_when_budget_cannot_preserve_all_documents():
    formatted = format_multi_pdf_sections(
        [
            {"label": "paper-a.pdf", "text": "Abstract A.\n\nResults A."},
            {"label": "paper-b.pdf", "text": "Abstract B.\n\nResults B."},
        ]
    )

    with pytest.raises(CompareBudgetError):
        smart_truncate_pdf_content(
            formatted,
            max_chars=80,
            logger=_Logger(),
            is_summary=False,
            question="对比一下这两篇文献的内容",
            is_compare=True,
        )


def test_summary_detection_keeps_fastqa_behavior():
    assert is_summary_question("请总结这篇文献") is True
    assert is_summary_question("What is the summary?") is False
