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


def test_non_compare_summary_prompt_matches_fastqa_contract():
    kb_section = build_kb_section({"kb_answer": "KB evidence"})

    patent_prompt = build_patent_pdf_answer_prompt(
        question="请总结这篇文献",
        pdf_content="标题: A study\nAbstract text\nResults text",
        kb_section=kb_section,
        is_summary=True,
        is_compare=False,
        selected_file_labels=["paper-a.pdf"],
    )
    fastqa_prompt = FASTQA_PROMPTING.build_pdf_answer_prompt(
        question="请总结这篇文献",
        pdf_content="标题: A study\nAbstract text\nResults text",
        kb_section=kb_section,
        is_summary=True,
    )

    assert patent_prompt == fastqa_prompt


def test_non_compare_non_summary_prompt_matches_fastqa_contract():
    patent_prompt = build_patent_pdf_answer_prompt(
        question="这篇文献里报告了什么结果？",
        pdf_content="标题: A study\nAbstract text\nResults text",
        kb_section="",
        is_summary=False,
        is_compare=False,
        selected_file_labels=["paper-a.pdf"],
    )
    fastqa_prompt = FASTQA_PROMPTING.build_pdf_answer_prompt(
        question="这篇文献里报告了什么结果？",
        pdf_content="标题: A study\nAbstract text\nResults text",
        kb_section="",
        is_summary=False,
    )

    assert patent_prompt == fastqa_prompt


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
