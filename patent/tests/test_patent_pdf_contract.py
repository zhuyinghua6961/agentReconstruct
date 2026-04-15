from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import httpx
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
    validate_compare_context,
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
    assert "## 研究目的和背景" in patent_prompt
    assert "## 研究方法/实验设计" in patent_prompt
    assert "## 主要发现和结果" in patent_prompt
    assert "## 结论和意义" in patent_prompt
    assert "## 局限性" in patent_prompt
    assert "注*" in patent_prompt
    assert "PDF中未提及" in patent_prompt
    assert "1-3" not in patent_prompt
    assert "3-5" in patent_prompt
    assert "\n## 结论\n" not in patent_prompt
    assert "知识库信息仅用于验证" in patent_prompt or "知识库信息仅可用于验证" in patent_prompt
    assert "不要把知识库信息当作新的 PDF 事实" in patent_prompt
    assert "专利/文献" in patent_prompt
    assert "标准 Markdown 列表" in patent_prompt or "Markdown" in patent_prompt
    assert len(patent_prompt) < 6000


def test_non_compare_multi_pdf_summary_prompt_does_not_use_single_pdf_aligned_contract():
    patent_prompt = build_patent_pdf_answer_prompt(
        question="请总结这两篇文献的研究内容",
        pdf_content="==== 文献 1: paper-a.pdf ====\nA\n\n==== 文献 2: paper-b.pdf ====\nB",
        kb_section="",
        is_summary=True,
        is_compare=False,
        selected_file_labels=["paper-a.pdf", "paper-b.pdf"],
        route_hint="pdf_qa",
        source_scope="pdf",
    )

    assert "负责基于上传的单篇 PDF 原文给出结构化回答" not in patent_prompt
    assert "## 局限性" not in patent_prompt
    assert "注*" in patent_prompt
    assert "## 研究目的和背景" in patent_prompt
    assert "## 研究方法/实验设计" in patent_prompt
    assert "## 主要发现和结果" in patent_prompt
    assert "## 结论和意义" in patent_prompt


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


def test_pdf_only_prompt_does_not_reuse_table_summary_contract_terms():
    prompt = build_patent_pdf_answer_prompt(
        question="请总结这篇文献",
        pdf_content="标题: A study\nAbstract text\nResults text",
        kb_section="",
        is_summary=True,
        is_compare=False,
        selected_file_labels=["paper-a.pdf"],
        route_hint="pdf_qa",
        source_scope="pdf",
    )

    assert "全表统计摘要" not in prompt
    assert "代表性样例" not in prompt
    assert "不能把少量样例当成整体结论" not in prompt


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


def test_request_payload_sets_explicit_output_budget_for_pdf_summary():
    client = PatentPdfAnswerClient(api_key="key", base_url="https://example.com", model="model")
    try:
        payload = client._build_request_payload(
            question="请总结这篇文献",
            pdf_text="标题: A study\nAbstract text\nResults text",
            file_name="paper-a.pdf",
            include_kb=False,
            stream=False,
            selected_file_labels=["paper-a.pdf"],
        )
    finally:
        client.close()

    assert int(payload.get("max_tokens") or 0) >= 1800
    assert float(payload.get("top_p") or 0) >= 0.9


def test_pdf_answer_client_uses_injected_http_client_and_request_timeout():
    class _FakeHttpClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.closed = False

        def post(self, url, *, headers=None, json=None, timeout=None):
            self.calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "json": json,
                    "timeout": timeout,
                }
            )
            return httpx.Response(
                200,
                request=httpx.Request("POST", str(url)),
                json={"choices": [{"message": {"content": "pdf answer"}}]},
            )

        def close(self):
            self.closed = True

    http_client = _FakeHttpClient()
    client = PatentPdfAnswerClient(
        api_key="key",
        base_url="https://example.com",
        model="model",
        timeout_seconds=23.0,
        http_client=http_client,
    )

    answer = client.answer(
        question="请总结这篇文献",
        pdf_text="标题: A study\nAbstract text\nResults text",
        file_name="paper-a.pdf",
        include_kb=False,
        selected_file_labels=["paper-a.pdf"],
    )

    assert answer == "pdf answer"
    assert len(http_client.calls) == 1
    assert http_client.calls[0]["timeout"] == 23.0
    client.close()
    assert http_client.closed is False


def test_pdf_answer_client_from_env_accepts_injected_http_client(monkeypatch):
    class _FakeHttpClient:
        def __init__(self) -> None:
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setenv("PATENT_OPENAI_API_KEY", "key")
    monkeypatch.setenv("PATENT_OPENAI_BASE_URL", "https://example.com")
    monkeypatch.setenv("PATENT_OPENAI_MODEL", "model")
    http_client = _FakeHttpClient()

    client = PatentPdfAnswerClient.from_env(http_client=http_client)

    assert client is not None
    assert client._client is http_client
    client.close()
    assert http_client.closed is False


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


def test_compare_prompt_requires_five_section_contract_for_two_documents():
    prompt = build_patent_pdf_answer_prompt(
        question="对比一下这两篇文献的内容",
        pdf_content="==== 文献 1: paper-a.pdf ====\nA\n\n==== 文献 2: paper-b.pdf ====\nB",
        kb_section="",
        is_summary=False,
        is_compare=True,
        selected_file_labels=["paper-a.pdf", "paper-b.pdf"],
    )

    assert "共涉及 2 篇文献" in prompt
    assert "## 具体内容对比" in prompt
    assert "## 研究方法差异" in prompt
    assert "## 应用领域差异" in prompt
    assert "## 相同点" in prompt
    assert "## 总结" in prompt
    assert "### 文献 #1 核心内容（根据PDF原文）" in prompt
    assert "### 文献 #2 核心内容（根据PDF原文）" in prompt
    assert "### 文献 #1 采用的研究方法" in prompt
    assert "### 文献 #2 采用的研究方法" in prompt
    assert "### 文献 #1 关注的应用领域" in prompt
    assert "### 文献 #2 关注的应用领域" in prompt
    assert "高质量的中文总结" in prompt
    assert "不得直接摘录英文摘要" in prompt
    assert "证据不足" in prompt
    assert "paper-a.pdf" in prompt
    assert "paper-b.pdf" in prompt
    assert "各自概要" not in prompt
    assert "差异点" not in prompt


def test_compare_prompt_prefers_document_specific_extraction_before_insufficiency():
    prompt = build_patent_pdf_answer_prompt(
        question="对比一下这两篇文献",
        pdf_content="==== 文献 1: paper-a.pdf ====\nA\n\n==== 文献 2: paper-b.pdf ====\nB",
        kb_section="",
        is_summary=False,
        is_compare=True,
        selected_file_labels=["paper-a.pdf", "paper-b.pdf"],
    )

    assert "优先提取可确认的逐篇证据" in prompt
    assert "不要先输出大段“未提及”占位" in prompt


def test_compare_prompt_does_not_repeat_placeholder_guidance_excessively():
    prompt = build_patent_pdf_answer_prompt(
        question="对比一下这两篇文献",
        pdf_content="==== 文献 1: paper-a.pdf ====\nA\n\n==== 文献 2: paper-b.pdf ====\nB",
        kb_section="",
        is_summary=False,
        is_compare=True,
        selected_file_labels=["paper-a.pdf", "paper-b.pdf"],
    )

    assert prompt.count("PDF中未提及") <= 1
    assert prompt.count("原文证据不足") <= 1
    assert prompt.count("证据不足") <= 2


def test_compare_prompt_allows_compact_compare_for_three_to_four_documents():
    prompt = build_patent_pdf_answer_prompt(
        question="对比一下这四篇文献的方法和应用方向",
        pdf_content=(
            "==== 文献 1: paper-1.pdf ====\nA\n\n"
            "==== 文献 2: paper-2.pdf ====\nB\n\n"
            "==== 文献 3: paper-3.pdf ====\nC\n\n"
            "==== 文献 4: paper-4.pdf ====\nD"
        ),
        kb_section="",
        is_summary=False,
        is_compare=True,
        selected_file_labels=["paper-1.pdf", "paper-2.pdf", "paper-3.pdf", "paper-4.pdf"],
    )

    assert "共涉及 4 篇文献" in prompt
    assert "## 具体内容对比" in prompt
    assert "## 研究方法差异" in prompt
    assert "## 应用领域差异" in prompt
    assert "3 到 4 篇文献" in prompt or "3-4 篇文献" in prompt
    assert "可适当压缩" in prompt
    assert "每篇文献至少保留一个可区分的事实" in prompt
    assert "### 文献 #1 核心内容（根据PDF原文）" not in prompt
    assert "### 文献 #1 采用的研究方法" not in prompt
    assert "### 文献 #1 关注的应用领域" not in prompt


def test_compare_prompt_refuses_to_promise_rich_contract_for_more_than_four_documents():
    prompt = build_patent_pdf_answer_prompt(
        question="对比一下这五篇文献",
        pdf_content=(
            "==== 文献 1: paper-1.pdf ====\nA\n\n"
            "==== 文献 2: paper-2.pdf ====\nB\n\n"
            "==== 文献 3: paper-3.pdf ====\nC\n\n"
            "==== 文献 4: paper-4.pdf ====\nD\n\n"
            "==== 文献 5: paper-5.pdf ====\nE"
        ),
        kb_section="",
        is_summary=False,
        is_compare=True,
        selected_file_labels=["paper-1.pdf", "paper-2.pdf", "paper-3.pdf", "paper-4.pdf", "paper-5.pdf"],
    )

    assert "共涉及 5 篇文献" in prompt
    assert "超过 4 篇文献" in prompt
    assert "请缩小比较范围" in prompt
    assert "请按以下五个一级章节组织回答" not in prompt
    assert "## 具体内容对比" not in prompt
    assert "## 研究方法差异" not in prompt
    assert "## 应用领域差异" not in prompt
    assert "### 文献 #1 核心内容（根据PDF原文）" not in prompt


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
        max_chars=560,
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


def test_compare_truncation_drops_reference_tail_even_when_total_input_fits_budget():
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
        max_chars=50000,
        logger=_Logger(),
        is_summary=False,
        question="对比一下这两篇文献的内容",
        is_compare=True,
    )

    assert "参考文献" not in truncated
    assert "Results A observed." in truncated or "Conclusion A final." in truncated
    assert "Results B observed." in truncated or "Conclusion B final." in truncated


def test_compare_truncation_accepts_short_documents_when_full_cleaned_context_fits_budget():
    formatted = format_multi_pdf_sections(
        [
            {"label": "paper-a.pdf", "text": "Abstract A.\n\nResults A."},
            {"label": "paper-b.pdf", "text": "Abstract B.\n\nResults B."},
        ]
    )

    truncated = smart_truncate_pdf_content(
        formatted,
        max_chars=300,
        logger=_Logger(),
        is_summary=False,
        question="对比一下这两篇文献的内容",
        is_compare=True,
    )

    assert "paper-a.pdf" in truncated
    assert "paper-b.pdf" in truncated
    assert "Abstract A." in truncated
    assert "Results B." in truncated


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


def test_validate_compare_context_accepts_balanced_heading_only_compare_windows():
    front_matter = "作者信息与版权页。 " * 200
    documents = [
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
    truncated = smart_truncate_pdf_content(
        format_multi_pdf_sections(documents),
        max_chars=560,
        logger=_Logger(),
        is_summary=False,
        question="对比一下这两篇文献的内容",
        is_compare=True,
    )

    validate_compare_context(truncated, documents, max_chars=560)


def test_compare_truncation_does_not_expose_model_visible_truncation_note():
    front_matter = "作者信息与版权页。 " * 240
    formatted = format_multi_pdf_sections(
        [
            {
                "label": "paper-a.pdf",
                "text": (
                    f"{front_matter}\n\nAbstract A short.\n\nMethod A with detailed compare evidence.\n\n"
                    "Results A observed.\n\nConclusion A final."
                ),
            },
            {
                "label": "paper-b.pdf",
                "text": (
                    f"{front_matter}\n\nAbstract B short.\n\nMethod B with detailed compare evidence.\n\n"
                    "Results B observed.\n\nConclusion B final."
                ),
            },
        ]
    )

    truncated = smart_truncate_pdf_content(
        formatted,
        max_chars=560,
        logger=_Logger(),
        is_summary=False,
        question="对比一下这两篇文献的方法差异",
        is_compare=True,
    )

    assert "仅保留原始内容" not in truncated
    assert re.search(r"原始\s*\d+\s*字符.*保留\s*\d+\s*字符", truncated) is None


def test_validate_compare_context_accepts_continuous_truncation_without_old_excerpt_targets():
    repeated_a = "Method A keeps detailed compare evidence. " * 80
    repeated_b = "Method B keeps detailed compare evidence. " * 80
    original_documents = [
        {
            "label": "paper-a.pdf",
            "text": f"Abstract A short.\n\n{repeated_a}\n\nResults A observed.\n\nConclusion A final.",
        },
        {
            "label": "paper-b.pdf",
            "text": f"Abstract B short.\n\n{repeated_b}\n\nResults B observed.\n\nConclusion B final.",
        },
    ]
    prepared = format_multi_pdf_sections(
        [
            {
                "label": "paper-a.pdf",
                "text": repeated_a + "Results A observed. " * 12,
            },
            {
                "label": "paper-b.pdf",
                "text": repeated_b + "Results B observed. " * 12,
            },
        ]
    )

    validate_compare_context(prepared, original_documents)


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
