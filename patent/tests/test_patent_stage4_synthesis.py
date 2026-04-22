from __future__ import annotations

import httpx
import pytest
from types import SimpleNamespace

from server.patent.answering import PatentAnswerBuilder, build_fallback_patent_answer
from server.patent.retrieval_models import PatentEvidence, PatentRetrievalOutcome, PatentTableSupplement
from server.patent.runtime import PatentRuntime
from server.patent.stages.synthesis import run_stage4_synthesis_with_patent_evidence


def _sample_retrieval_results() -> dict[str, object]:
    return {
        "references": ["CN115132975B"],
        "reference_objects": [
            {
                "source_type": "patent",
                "canonical_patent_id": "CN115132975B",
                "publication_number": "CN115132975B",
                "application_number": "CN202110320984.1",
                "country": "CN",
                "kind_code": "B",
                "title": "一种锂离子电池及动力车辆",
                "section_type": "claim",
                "section_label": "Claim 1",
                "anchor": {"claim_number": 1, "paragraph_id": None},
                "snippet": "一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                "provider": "patent_archive",
                "original_available": True,
                "viewer_uri": "/api/patent/original/CN115132975B?section=claim&claim_number=1&format=html",
                "scores": {"abstract_score": 0.71, "chunk_score": 0.91},
            }
        ],
        "reference_links": [
            {
                "type": "original_view",
                "label": "View claim 1",
                "canonical_patent_id": "CN115132975B",
                "viewer_uri": "/api/patent/original/CN115132975B?section=claim&claim_number=1&format=html",
                "redirect_url": None,
            }
        ],
        "original_links": [
            {
                "type": "original_view",
                "label": "View claim 1",
                "canonical_patent_id": "CN115132975B",
                "section": "claim",
                "claim_number": 1,
                "paragraph_id": None,
                "viewer_uri": "/api/patent/original/CN115132975B?section=claim&claim_number=1&format=html",
                "redirect_url": None,
            }
        ],
        "metadata": {
            "retrieval_backend": "vector_hybrid",
            "retrieval_version": "retrieval-v2",
            "catalog_index_version": "catalog-v2",
        },
    }


def _sample_evidence_bundle() -> dict[str, object]:
    return {
        "source_ids": ["CN115132975B"],
        "evidences": [
            {
                "canonical_patent_id": "CN115132975B",
                "title": "一种锂离子电池及动力车辆",
                "abstract_text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                "matched_evidence": [
                    {
                        "section_type": "claim",
                        "section_label": "Claim 1",
                        "text": "一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                        "anchor": {"claim_number": 1, "paragraph_id": None},
                        "scores": {"abstract_score": 0.71, "chunk_score": 0.91},
                    },
                    {
                        "section_type": "description",
                        "section_label": "Paragraph p-007",
                        "text": "实施例表明该复配方案在高 SOC 充电时不易析锂。",
                        "anchor": {"claim_number": None, "paragraph_id": "p-007"},
                        "scores": {"chunk_score": 0.83},
                    },
                ],
                "table_supplements": [
                    {
                        "table_title": "表1 各实施例性能对比",
                        "columns": ["实验序号", "1C 放电容量保持率"],
                        "rows": [{"实验序号": "实施例1", "1C 放电容量保持率": "91.2%"}],
                        "source_image": "table-1.png",
                    }
                ],
                "reference_object": dict(_sample_retrieval_results()["reference_objects"][0]),
                "reference_link": dict(_sample_retrieval_results()["reference_links"][0]),
                "original_links": [dict(_sample_retrieval_results()["original_links"][0])],
                "scores": {"abstract_score": 0.71, "chunk_score": 0.91},
                "metadata": {"publication_number": "CN115132975B"},
            }
        ],
        "metadata": {"force_pdf": False},
    }


def _sample_multi_patent_retrieval_results(patent_ids: list[str]) -> dict[str, object]:
    return {
        "references": list(patent_ids),
        "reference_objects": [
            {
                "source_type": "patent",
                "canonical_patent_id": patent_id,
                "publication_number": patent_id,
                "application_number": f"{patent_id}-APP",
                "country": "CN",
                "kind_code": "A",
                "title": f"专利 {patent_id}",
                "provider": "patent_archive",
                "original_available": True,
            }
            for patent_id in patent_ids
        ],
        "reference_links": [],
        "original_links": [],
        "metadata": {
            "retrieval_backend": "vector_hybrid",
            "retrieval_version": "retrieval-v2",
            "catalog_index_version": "catalog-v2",
        },
    }


def _sample_multi_patent_evidence_bundle(patent_ids: list[str]) -> dict[str, object]:
    evidences: list[dict[str, object]] = []
    for patent_id in patent_ids:
        evidences.append(
            {
                "canonical_patent_id": patent_id,
                "title": f"专利 {patent_id}",
                "abstract_text": f"{patent_id} 的核心技术包括电压窗口控制与倍率优化。",
                "matched_evidence": [
                    {
                        "section_type": "claim",
                        "section_label": "Claim 1",
                        "text": f"{patent_id} 命中片段：用于提升循环稳定性。",
                        "anchor": {"claim_number": 1, "paragraph_id": None},
                        "scores": {"chunk_score": 0.9},
                    }
                ],
                "table_supplements": [],
                "reference_object": {
                    "canonical_patent_id": patent_id,
                    "publication_number": patent_id,
                    "title": f"专利 {patent_id}",
                    "provider": "patent_archive",
                    "original_available": True,
                },
                "reference_link": {},
                "original_links": [],
                "scores": {"chunk_score": 0.9},
                "metadata": {"publication_number": patent_id},
            }
        )
    return {
        "source_ids": list(patent_ids),
        "evidences": evidences,
        "metadata": {"force_pdf": False},
    }


def test_stage4_synthesis_assembles_shell_compatible_result_from_patent_evidence():
    captured: dict[str, object] = {}

    def _answer_builder(*, question, retrieval_outcome, context):
        captured["question"] = question
        captured["retrieval_outcome"] = retrieval_outcome
        captured["context"] = context
        return "综合摘要：LMFP/LFP/三元复配在高 SOC 充电安全与低 SOC 放电功率之间取得平衡。"

    result = run_stage4_synthesis_with_patent_evidence(
        user_question="如何评估该方案的替代窗口与风险？",
        deep_answer="先比较安全性、倍率和量产一致性。",
        patent_evidence_bundle=_sample_evidence_bundle(),
        retrieval_results=_sample_retrieval_results(),
        answer_builder=_answer_builder,
        conversation_context={
            "recent_turns_for_llm": [{"role": "user", "content": "Earlier turn"}],
            "summary_for_llm": {"short_summary": "Earlier summary"},
        },
    )

    assert result["success"] is True
    assert result["final_answer"].startswith("综合摘要：")
    assert "(CN115132975B)" in result["final_answer"]
    assert result["answer_text"] == result["final_answer"]
    assert result["references"] == ["CN115132975B"]
    assert result["reference_objects"][0]["canonical_patent_id"] == "CN115132975B"
    assert result["reference_links"][0]["type"] == "original_view"
    assert result["original_links"][0]["section"] == "claim"
    assert result["metadata"]["retrieval_backend"] == "vector_hybrid"
    assert result["metadata"]["retrieval_version"] == "retrieval-v2"
    assert result["metadata"]["catalog_index_version"] == "catalog-v2"
    assert result["metadata"]["matched_evidence_count"] == 2
    assert result["metadata"]["table_count"] == 1
    assert result["metadata"]["evidence_patent_count"] == 1
    assert result["metadata"]["citation_mode"] == "programmatic_repair"

    retrieval_outcome = captured["retrieval_outcome"]
    assert len(retrieval_outcome.evidences) == 2
    assert retrieval_outcome.evidences[0].matched_section_type == "claim"
    assert retrieval_outcome.evidences[0].table_supplements[0].table_title == "表1 各实施例性能对比"
    assert retrieval_outcome.evidences[1].paragraph_id == "p-007"
    assert captured["context"]["stage1_deep_answer"] == "先比较安全性、倍率和量产一致性。"


def test_patent_runtime_stage4_synthesis_uses_runtime_answer_builder():
    captured: dict[str, object] = {}

    def _answer_builder(*, question, retrieval_outcome, context):
        captured["question"] = question
        captured["retrieval_outcome"] = retrieval_outcome
        captured["context"] = context
        return "runtime synthesized answer"

    runtime = PatentRuntime(
        retrieval_service=object(),  # type: ignore[arg-type]
        resources=[],
        answer_builder=_answer_builder,
    )

    result = runtime.stage4_synthesis_with_patent_evidence(
        user_question="如何评估该方案的替代窗口与风险？",
        deep_answer="先比较安全性、倍率和量产一致性。",
        patent_evidence_bundle=_sample_evidence_bundle(),
        retrieval_results=_sample_retrieval_results(),
        conversation_context={"summary_for_llm": {"short_summary": "Earlier summary"}},
    )

    assert result["success"] is True
    assert result["final_answer"].startswith("runtime synthesized answer")
    assert "(CN115132975B)" in result["final_answer"]
    assert result["references"] == ["CN115132975B"]
    assert captured["question"] == "如何评估该方案的替代窗口与风险？"
    assert captured["context"]["summary_for_llm"]["short_summary"] == "Earlier summary"
    assert captured["context"]["stage1_deep_answer"] == "先比较安全性、倍率和量产一致性。"
    assert captured["context"]["allowed_patent_ids"] == ["CN115132975B"]


def test_patent_answer_builder_prompt_reads_normalized_stage4_context():
    builder = PatentAnswerBuilder(api_key="", base_url="http://example.invalid", model="test-model")
    prompt = builder._build_prompt(
        question="如何评估该方案的替代窗口与风险？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["CN115132975B"],
            reference_objects=[dict(_sample_retrieval_results()["reference_objects"][0])],
            reference_links=[dict(_sample_retrieval_results()["reference_links"][0])],
            original_links=[dict(_sample_retrieval_results()["original_links"][0])],
            evidences=[
                PatentEvidence(
                    canonical_patent_id="CN115132975B",
                    publication_number="CN115132975B",
                    application_number="CN202110320984.1",
                    title="一种锂离子电池及动力车辆",
                    abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                    matched_section_type="claim",
                    matched_section_label="Claim 1",
                    matched_snippet="一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                )
            ],
        ),
        context={
            "summary_for_llm": {"short_summary": "Earlier summary"},
            "recent_turns_for_llm": [{"role": "user", "content": "Earlier turn"}],
            "stage1_deep_answer": "先比较安全性、倍率和量产一致性。",
        },
    )
    builder.close()

    assert "Earlier summary" in prompt
    assert "Earlier turn" in prompt
    assert "先比较安全性、倍率和量产一致性。" in prompt
    assert "/api/patent/original/CN115132975B?section=claim&claim_number=1&format=html" in prompt
    assert "Claim 1" in prompt


def test_patent_answer_builder_prompt_includes_patent_id_whitelist_and_citation_contract():
    builder = PatentAnswerBuilder(api_key="", base_url="http://example.invalid", model="test-model")
    prompt = builder._build_prompt(
        question="如何评估该方案的替代窗口与风险？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["CN115132975B", "CN999999999A"],
            reference_objects=[dict(_sample_retrieval_results()["reference_objects"][0])],
            reference_links=[dict(_sample_retrieval_results()["reference_links"][0])],
            original_links=[dict(_sample_retrieval_results()["original_links"][0])],
            evidences=[
                PatentEvidence(
                    canonical_patent_id="CN115132975B",
                    publication_number="CN115132975B",
                    application_number="CN202110320984.1",
                    title="一种锂离子电池及动力车辆",
                    abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                    matched_section_type="claim",
                    matched_section_label="Claim 1",
                    matched_snippet="一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                )
            ],
        ),
        context={
            "allowed_patent_ids": ["CN115132975B", "CN999999999A"],
            "stage1_deep_answer": "先比较安全性、倍率和量产一致性。",
        },
    )
    builder.close()

    assert "允许引用的专利白名单" in prompt
    assert "CN115132975B" in prompt
    assert "CN999999999A" in prompt
    assert "(patent_id=公开号)" in prompt


def test_patent_answer_builder_prompt_emphasizes_evidence_first_and_table_priority():
    builder = PatentAnswerBuilder(api_key="", base_url="http://example.invalid", model="test-model")
    prompt = builder._build_prompt(
        question="如何评估该方案的替代窗口与风险？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["CN115132975B", "CN999999999A"],
            reference_objects=[dict(_sample_retrieval_results()["reference_objects"][0])],
            reference_links=[dict(_sample_retrieval_results()["reference_links"][0])],
            original_links=[dict(_sample_retrieval_results()["original_links"][0])],
            evidences=[
                PatentEvidence(
                    canonical_patent_id="CN115132975B",
                    publication_number="CN115132975B",
                    application_number="CN202110320984.1",
                    title="一种锂离子电池及动力车辆",
                    abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                    matched_section_type="claim",
                    matched_section_label="Claim 1",
                    matched_snippet="一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                    table_supplements=[
                        PatentTableSupplement(
                            table_title="表1 各实施例性能对比",
                            columns=["样品", "倍率性能"],
                            rows=[{"样品": "实施例1", "倍率性能": "92%"}],
                        )
                    ],
                )
            ],
        ),
        context={
            "allowed_patent_ids": ["CN115132975B", "CN999999999A"],
            "stage1_deep_answer": "先比较安全性、倍率和量产一致性。",
        },
    )
    builder.close()

    assert "答案必须基于“检索证据”生成，而不是直接照搬“阶段1预分析”" in prompt
    assert "如果表格与正文片段同时存在，容量、倍率、循环等数值优先采用表格证据" in prompt
    assert "不要机械地在每句话后重复标注同一公开号" in prompt


def test_patent_answer_builder_request_payload_mentions_stage1_reference_only_and_non_mechanical_citation():
    builder = PatentAnswerBuilder(api_key="", base_url="http://example.invalid", model="test-model")
    payload = builder._build_request_payload(
        prompt="test prompt",
        allowed_patent_ids=["CN115132975B", "CN999999999A"],
        stream=False,
        min_distinct_citations=2,
    )
    builder.close()

    system_prompt = payload["messages"][0]["content"]

    assert "阶段1预分析只能作为结构和核验线索，不能直接当作事实来源" in system_prompt
    assert "不要机械地在每句话后重复标注同一公开号" in system_prompt
    assert "引用必须使用 `(patent_id=公开号)`" in system_prompt
    assert "不能使用 DOI" in system_prompt
    assert "最终答案至少引用 2 个不同公开号" in system_prompt


def test_stage4_synthesis_enforces_patent_id_whitelist_on_answer_builder_output():
    result = run_stage4_synthesis_with_patent_evidence(
        user_question="如何评估该方案的替代窗口与风险？",
        deep_answer="先比较安全性、倍率和量产一致性。",
        patent_evidence_bundle=_sample_evidence_bundle(),
        retrieval_results=_sample_retrieval_results(),
        answer_builder=lambda **kwargs: (
            "该方案在高SOC安全性与低SOC功率之间取得平衡 "
            "(patent_id=CN115132975B)。但另一篇外部专利也支持该结论 (patent_id=CN000000000A)。"
        ),
    )

    assert "(CN115132975B)" in result["final_answer"]
    assert "CN000000000A" not in result["final_answer"]
    assert "另一篇外部专利也支持该结论" not in result["final_answer"]
    assert result["metadata"]["allowed_patent_ids"] == ["CN115132975B"]
    assert result["metadata"]["cited_patent_ids"] == ["CN115132975B"]
    assert result["metadata"]["invalid_cited_patent_ids"] == ["CN000000000A"]


def test_stage4_synthesis_repairs_uncited_answer_without_replacing_builder_content():
    result = run_stage4_synthesis_with_patent_evidence(
        user_question="如何评估该方案的替代窗口与风险？",
        deep_answer="先比较安全性、倍率和量产一致性。",
        patent_evidence_bundle=_sample_evidence_bundle(),
        retrieval_results=_sample_retrieval_results(),
        answer_builder=lambda **kwargs: "这是一个没有任何专利引用标记的答案。",
    )

    assert result["success"] is True
    assert result["final_answer"].startswith("这是一个没有任何专利引用标记的答案。")
    assert "(CN115132975B)" in result["final_answer"]
    assert "围绕“" not in result["final_answer"]
    assert result["metadata"]["citation_mode"] == "programmatic_repair"


def test_stage4_synthesis_repairs_to_meet_min_distinct_citations(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE4_MIN_CITATIONS", "3")
    monkeypatch.setenv("PATENT_STAGE4_REFERENCE_TOPK", "4")
    patent_ids = ["P1", "P2", "P3", "P4"]
    result = run_stage4_synthesis_with_patent_evidence(
        user_question="请给出多专利证据汇总结论",
        deep_answer="先看关键技术要点。",
        patent_evidence_bundle=_sample_multi_patent_evidence_bundle(patent_ids),
        retrieval_results=_sample_multi_patent_retrieval_results(patent_ids),
        answer_builder=lambda **kwargs: "该结论需要结合多篇专利共同判断。",
    )

    assert result["success"] is True
    assert result["metadata"]["stage4_min_citations_configured"] == 3
    assert result["metadata"]["stage4_min_citations_required"] == 3
    assert result["metadata"]["citation_mode"] == "programmatic_repair"
    assert len(result["metadata"]["cited_patent_ids"]) >= 3
    assert "(P1)" in result["final_answer"]
    assert "(P2)" in result["final_answer"]
    assert "(P3)" in result["final_answer"]


def test_stage4_min_citations_is_clamped_by_available_patents(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE4_MIN_CITATIONS", "10")
    monkeypatch.setenv("PATENT_STAGE4_REFERENCE_TOPK", "20")
    patent_ids = ["P1", "P2"]
    result = run_stage4_synthesis_with_patent_evidence(
        user_question="请给出双专利证据汇总结论",
        deep_answer="先看关键技术要点。",
        patent_evidence_bundle=_sample_multi_patent_evidence_bundle(patent_ids),
        retrieval_results=_sample_multi_patent_retrieval_results(patent_ids),
        answer_builder=lambda **kwargs: "已验证第一项证据 (patent_id=P1)。",
    )

    assert result["success"] is True
    assert result["metadata"]["stage4_min_citations_configured"] == 10
    assert result["metadata"]["stage4_min_citations_required"] == 2
    assert result["metadata"]["citation_mode"] == "programmatic_repair"
    assert set(result["metadata"]["cited_patent_ids"]) == {"P1", "P2"}
    assert "(P1)" in result["final_answer"]
    assert "(P2)" in result["final_answer"]


def test_stage4_reference_topk_is_enforced_as_citation_whitelist(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE4_MIN_CITATIONS", "2")
    monkeypatch.setenv("PATENT_STAGE4_REFERENCE_TOPK", "2")
    patent_ids = ["P1", "P2", "P3", "P4"]
    result = run_stage4_synthesis_with_patent_evidence(
        user_question="请给出 topk 专利证据汇总结论",
        deep_answer="先看关键技术要点。",
        patent_evidence_bundle=_sample_multi_patent_evidence_bundle(patent_ids),
        retrieval_results=_sample_multi_patent_retrieval_results(patent_ids),
        answer_builder=lambda **kwargs: (
            "主结论来自专利一 (patent_id=P1)。"
            "另有外部旁证 (patent_id=P3)。"
        ),
    )

    assert result["success"] is True
    assert result["metadata"]["allowed_patent_ids"] == ["P1", "P2"]
    assert result["metadata"]["allowed_patent_ids_all"] == ["P1", "P2", "P3", "P4"]
    assert result["metadata"]["stage4_min_citations_required"] == 2
    assert set(result["metadata"]["cited_patent_ids"]) == {"P1", "P2"}
    assert result["metadata"]["invalid_cited_patent_ids"] == ["P3"]
    assert "(P1)" in result["final_answer"]
    assert "(P2)" in result["final_answer"]
    assert "P3" not in result["final_answer"]


def test_stage4_context_allowed_patent_ids_is_trimmed_by_topk(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE4_MIN_CITATIONS", "2")
    monkeypatch.setenv("PATENT_STAGE4_REFERENCE_TOPK", "2")
    patent_ids = ["P1", "P2", "P3", "P4"]
    captured: dict[str, object] = {}

    def _answer_builder(*, context, **kwargs):
        del kwargs
        captured["context"] = context
        return "已验证第一项证据 (patent_id=P1)。"

    result = run_stage4_synthesis_with_patent_evidence(
        user_question="请给出 topk 专利证据汇总结论",
        deep_answer="先看关键技术要点。",
        patent_evidence_bundle=_sample_multi_patent_evidence_bundle(patent_ids),
        retrieval_results=_sample_multi_patent_retrieval_results(patent_ids),
        answer_builder=_answer_builder,
    )

    assert result["success"] is True
    assert captured["context"]["allowed_patent_ids"] == ["P1", "P2"]
    assert captured["context"]["allowed_patent_ids_all"] == ["P1", "P2", "P3", "P4"]


def test_stage4_streaming_respects_topk_whitelist_and_repairs_min_citations(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE4_MIN_CITATIONS", "2")
    monkeypatch.setenv("PATENT_STAGE4_REFERENCE_TOPK", "2")
    patent_ids = ["P1", "P2", "P3", "P4"]
    streamed_chunks: list[str] = []

    class _StreamingAnswerBuilder:
        def __call__(self, **kwargs):
            raise AssertionError("stream path should be preferred when available")

        def stream(self, *, question, retrieval_outcome, context):
            del question, retrieval_outcome, context
            yield "主结论来自专利一 (patent_id=P1)。"
            yield "另有外部旁证 (patent_id=P3)。"

    result = run_stage4_synthesis_with_patent_evidence(
        user_question="请给出 topk 专利证据汇总结论",
        deep_answer="先看关键技术要点。",
        patent_evidence_bundle=_sample_multi_patent_evidence_bundle(patent_ids),
        retrieval_results=_sample_multi_patent_retrieval_results(patent_ids),
        answer_builder=_StreamingAnswerBuilder(),
        content_callback=streamed_chunks.append,
    )

    streamed_text = "".join(streamed_chunks)
    assert result["success"] is True
    assert result["metadata"]["allowed_patent_ids"] == ["P1", "P2"]
    assert result["metadata"]["allowed_patent_ids_all"] == ["P1", "P2", "P3", "P4"]
    assert result["metadata"]["stage4_min_citations_required"] == 2
    assert result["metadata"]["invalid_cited_patent_ids"] == ["P3"]
    assert set(result["metadata"]["cited_patent_ids"]) == {"P1", "P2"}
    assert "(P1)" in result["final_answer"]
    assert "(P2)" in result["final_answer"]
    assert "P3" not in result["final_answer"]
    assert "P3" not in streamed_text


def test_stage4_synthesis_forwards_streamed_chunks_and_sanitizes_final_answer():
    streamed_chunks: list[str] = []

    class _StreamingAnswerBuilder:
        def __call__(self, **kwargs):
            raise AssertionError("stream path should be preferred when available")

        def stream(self, *, question, retrieval_outcome, context):
            del question, retrieval_outcome, context
            yield "这是流式输出的答案"
            yield "，引用外部专利 (patent_id=CN000000000A)。"

    result = run_stage4_synthesis_with_patent_evidence(
        user_question="如何评估该方案的替代窗口与风险？",
        deep_answer="先比较安全性、倍率和量产一致性。",
        patent_evidence_bundle=_sample_evidence_bundle(),
        retrieval_results=_sample_retrieval_results(),
        answer_builder=_StreamingAnswerBuilder(),
        content_callback=streamed_chunks.append,
    )

    streamed_text = "".join(streamed_chunks)
    assert "patent_id=" not in streamed_text
    assert "这是流式输出的答案" in streamed_text
    assert result["success"] is True
    assert result["final_answer"] == "这是流式输出的答案 (CN115132975B)"
    assert result["metadata"]["citation_mode"] == "programmatic_repair"
    assert result["metadata"]["invalid_cited_patent_ids"] == ["CN000000000A"]


def test_stage4_synthesis_unwraps_backticked_patent_citations_but_keeps_regular_code(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE4_MIN_CITATIONS", "1")
    monkeypatch.setenv("PATENT_STAGE4_REFERENCE_TOPK", "2")
    streamed_chunks: list[str] = []
    patent_ids = ["P1", "P2"]
    long_prefix = "前言" * 90

    class _StreamingAnswerBuilder:
        def __call__(self, **kwargs):
            raise AssertionError("stream path should be preferred when available")

        def stream(self, *, question, retrieval_outcome, context):
            del question, retrieval_outcome, context
            yield long_prefix
            yield "结论来自专利 `"
            yield "(patent_id=P1)`。补充证据见 `(patent_id=P1；P2)`。补充列表 `(P1、P2)`。普通代码 `x = y + z`。"

    result = run_stage4_synthesis_with_patent_evidence(
        user_question="请总结专利结论并保留普通代码示例",
        deep_answer="先保留正文结构。",
        patent_evidence_bundle=_sample_multi_patent_evidence_bundle(patent_ids),
        retrieval_results=_sample_multi_patent_retrieval_results(patent_ids),
        answer_builder=_StreamingAnswerBuilder(),
        content_callback=streamed_chunks.append,
    )

    streamed_text = "".join(streamed_chunks)
    assert "`(P1)`" not in streamed_text
    assert "`(P1；P2)`" not in streamed_text
    assert "`(P1、P2)`" not in streamed_text
    assert "(P1)" in streamed_text
    assert "(P1；P2)" in streamed_text
    assert "(P1、P2)" in streamed_text
    assert "`x = y + z`" in streamed_text
    assert result["success"] is True
    assert "`(P1)`" not in result["final_answer"]
    assert "`(P1；P2)`" not in result["final_answer"]
    assert "`(P1、P2)`" not in result["final_answer"]
    assert "(P1)" in result["final_answer"]
    assert "(P1；P2)" in result["final_answer"]
    assert "(P1、P2)" in result["final_answer"]
    assert "`x = y + z`" in result["final_answer"]


def test_patent_answer_builder_uses_injected_http_client_and_request_timeout():
    shared_pool = SimpleNamespace(
        config=SimpleNamespace(
            connect_timeout_seconds=1.5,
            read_timeout_seconds=2.5,
            stream_read_timeout_seconds=9.5,
            write_timeout_seconds=3.5,
            pool_timeout_seconds=4.5,
        ),
        snapshot=lambda: {
            "pool_owner": "app",
            "client_owner": "shared",
            "shared_client_id": "answer-shared",
            "pid": 1,
            "bootstrap_source": "startup",
            "pool_timeout_count": 0,
            "pool_wait_ms": 0.0,
        },
        record_pool_wait=lambda **_kwargs: None,
        record_pool_timeout=lambda **_kwargs: None,
    )

    class _FakeHttpClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.closed = False
            self._patent_shared_pool = shared_pool

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
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "该方案改善了高SOC充电安全性 (patent_id=CN115132975B)。"
                            }
                        }
                    ]
                },
            )

        def close(self):
            self.closed = True

    http_client = _FakeHttpClient()
    builder = PatentAnswerBuilder(
        api_key="test-key",
        base_url="http://example.invalid",
        model="test-model",
        timeout_seconds=19.0,
        http_client=http_client,
    )

    answer = builder(
        question="如何评估该方案的替代窗口与风险？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["CN115132975B"],
            reference_objects=[dict(_sample_retrieval_results()["reference_objects"][0])],
            reference_links=[dict(_sample_retrieval_results()["reference_links"][0])],
            original_links=[dict(_sample_retrieval_results()["original_links"][0])],
            evidences=[
                PatentEvidence(
                    canonical_patent_id="CN115132975B",
                    publication_number="CN115132975B",
                    application_number="CN202110320984.1",
                    title="一种锂离子电池及动力车辆",
                    abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                    matched_section_type="claim",
                    matched_section_label="Claim 1",
                    matched_snippet="一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                )
            ],
        ),
        context={"allowed_patent_ids": ["CN115132975B"]},
    )

    assert "(patent_id=CN115132975B)" in answer
    assert len(http_client.calls) == 1
    timeout = http_client.calls[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 1.5
    assert timeout.read == 2.5
    assert timeout.write == 3.5
    assert timeout.pool == 4.5
    builder.close()
    assert http_client.closed is False


def test_patent_answer_builder_rejects_transport_and_http_client_mix():
    with pytest.raises(ValueError, match="transport"):
        PatentAnswerBuilder(
            api_key="test-key",
            base_url="http://example.invalid",
            model="test-model",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request, json={"choices": []})),
            http_client=object(),
        )


def test_patent_answer_builder_sanitizes_invalid_patent_id_citations_from_llm_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "该方案改善了高SOC充电安全性 (patent_id=CN115132975B)，"
                                "并且另一篇外部专利也支持这一点 (patent_id=CN000000000A)。"
                            )
                        }
                    }
                ]
            },
        )

    builder = PatentAnswerBuilder(
        api_key="test-key",
        base_url="http://example.invalid",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )
    answer = builder(
        question="如何评估该方案的替代窗口与风险？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["CN115132975B"],
            reference_objects=[dict(_sample_retrieval_results()["reference_objects"][0])],
            reference_links=[dict(_sample_retrieval_results()["reference_links"][0])],
            original_links=[dict(_sample_retrieval_results()["original_links"][0])],
            evidences=[
                PatentEvidence(
                    canonical_patent_id="CN115132975B",
                    publication_number="CN115132975B",
                    application_number="CN202110320984.1",
                    title="一种锂离子电池及动力车辆",
                    abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                    matched_section_type="claim",
                    matched_section_label="Claim 1",
                    matched_snippet="一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                )
            ],
        ),
        context={"allowed_patent_ids": ["CN115132975B"]},
    )
    builder.close()

    assert "(patent_id=CN115132975B)" in answer
    assert "CN000000000A" not in answer


def test_patent_answer_builder_generation_path_keeps_sanitization_with_mixed_table_and_snippet_evidence():
    captured_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload["json"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "根据表格和正文证据，该方案倍率性能可达 92% "
                                "(patent_id=CN115132975B)，"
                                "另一篇外部专利也报告了类似现象 (patent_id=CN000000000A)。"
                            )
                        }
                    }
                ]
            },
        )

    builder = PatentAnswerBuilder(
        api_key="test-key",
        base_url="http://example.invalid",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )
    answer = builder(
        question="如何评估该方案的替代窗口与风险？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["CN115132975B"],
            reference_objects=[dict(_sample_retrieval_results()["reference_objects"][0])],
            reference_links=[dict(_sample_retrieval_results()["reference_links"][0])],
            original_links=[dict(_sample_retrieval_results()["original_links"][0])],
            evidences=[
                PatentEvidence(
                    canonical_patent_id="CN115132975B",
                    publication_number="CN115132975B",
                    application_number="CN202110320984.1",
                    title="一种锂离子电池及动力车辆",
                    abstract_text="通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                    matched_section_type="claim",
                    matched_section_label="Claim 1",
                    matched_snippet="一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                    table_supplements=[
                        PatentTableSupplement(
                            table_title="表1 各实施例性能对比",
                            columns=["样品", "倍率性能"],
                            rows=[{"样品": "实施例1", "倍率性能": "92%"}],
                        )
                    ],
                )
            ],
        ),
        context={"allowed_patent_ids": ["CN115132975B"]},
    )
    builder.close()

    payload_text = str(captured_payload["json"])
    assert "表1 各实施例性能对比" in payload_text
    assert "Claim 1" in payload_text
    assert "不能使用 DOI" in payload_text
    assert "(patent_id=CN115132975B)" in answer
    assert "CN000000000A" not in answer
    assert "另一篇外部专利也报告了类似现象" not in answer


def test_stage4_fallback_answer_groups_snippets_by_patent_instead_of_dropping_later_patents():
    answer = build_fallback_patent_answer(
        question="多专利场景下是否会漏掉后面的 patent？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["P1", "P2"],
            reference_objects=[
                {
                    "canonical_patent_id": "P1",
                    "section_label": "Claim 1",
                    "viewer_uri": "/api/patent/original/P1?section=claim&claim_number=1&format=html",
                },
                {
                    "canonical_patent_id": "P2",
                    "section_label": "Claim 1",
                    "viewer_uri": "/api/patent/original/P2?section=claim&claim_number=1&format=html",
                },
            ],
            reference_links=[],
            original_links=[
                {
                    "canonical_patent_id": "P1",
                    "section": "claim",
                    "claim_number": 1,
                    "viewer_uri": "/api/patent/original/P1?section=claim&claim_number=1&format=html",
                },
                {
                    "canonical_patent_id": "P2",
                    "section": "claim",
                    "claim_number": 1,
                    "viewer_uri": "/api/patent/original/P2?section=claim&claim_number=1&format=html",
                },
            ],
            evidences=[
                PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P1 snippet 1"),
                PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="", matched_section_type="claim", matched_section_label="Claim 2", matched_snippet="P1 snippet 2"),
                PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="", matched_section_type="description", matched_section_label="Paragraph p-001", matched_snippet="P1 snippet 3"),
                PatentEvidence(canonical_patent_id="P2", publication_number="P2", application_number=None, title="专利 P2", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P2 snippet 1"),
            ],
        ),
    )

    assert "专利 P1" in answer
    assert "专利 P2" in answer
    assert "背景/法律套话" in answer
    assert "实质技术证据" in answer
    assert "(patent_id=P1)" in answer
    assert "(patent_id=P2)" in answer
    assert "/api/patent/original/P1?section=claim&claim_number=1&format=html" in answer
    assert "/api/patent/original/P2?section=claim&claim_number=1&format=html" in answer


def test_stage4_fallback_deprioritizes_background_or_legal_boilerplate_snippets():
    answer = build_fallback_patent_answer(
        question="需要区分真正的技术证据和背景套话吗？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["P1"],
            reference_objects=[
                {
                    "canonical_patent_id": "P1",
                    "section_label": "Claim 1",
                    "viewer_uri": "/api/patent/original/P1?section=claim&claim_number=1&format=html",
                }
            ],
            reference_links=[],
            original_links=[
                {
                    "canonical_patent_id": "P1",
                    "section": "claim",
                    "claim_number": 1,
                    "viewer_uri": "/api/patent/original/P1?section=claim&claim_number=1&format=html",
                }
            ],
            evidences=[
                PatentEvidence(
                    canonical_patent_id="P1",
                    publication_number="P1",
                    application_number=None,
                    title="专利 P1",
                    abstract_text="",
                    matched_section_type="background",
                    matched_section_label="Background",
                    matched_snippet="背景技术通常采用常规方法，本发明旨在提供一种改进方案。",
                ),
                PatentEvidence(
                    canonical_patent_id="P1",
                    publication_number="P1",
                    application_number=None,
                    title="专利 P1",
                    abstract_text="",
                    matched_section_type="claim",
                    matched_section_label="Claim 1",
                    matched_snippet="正极活性材料包括 LMFP、LFP 与三元材料。",
                ),
            ],
        ),
    )

    assert "Claim 1命中片段：正极活性材料包括 LMFP、LFP 与三元材料。" in answer
    assert "背景/法律套话已降权" in answer
    assert "背景技术通常采用常规方法" not in answer


def test_patent_answer_builder_prompt_groups_by_patent_not_by_snippet_row():
    builder = PatentAnswerBuilder(api_key="", base_url="http://example.invalid", model="test-model")
    prompt = builder._build_prompt(
        question="多专利场景下是否会漏掉后面的 patent？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["P1", "P2"],
            reference_objects=[],
            reference_links=[],
            original_links=[],
            evidences=[
                PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P1 snippet 1"),
                PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="", matched_section_type="claim", matched_section_label="Claim 2", matched_snippet="P1 snippet 2"),
                PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="", matched_section_type="description", matched_section_label="Paragraph p-001", matched_snippet="P1 snippet 3"),
                PatentEvidence(canonical_patent_id="P2", publication_number="P2", application_number=None, title="专利 P2", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P2 snippet 1"),
            ],
        ),
        context={},
    )
    builder.close()

    assert "专利: 专利 P1 (P1)" in prompt
    assert "专利: 专利 P2 (P2)" in prompt


def test_patent_answer_builder_prompt_does_not_drop_patents_beyond_first_three():
    builder = PatentAnswerBuilder(api_key="", base_url="http://example.invalid", model="test-model")
    prompt = builder._build_prompt(
        question="多专利场景下是否会漏掉后面的 patent？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["P1", "P2", "P3", "P4"],
            reference_objects=[],
            reference_links=[],
            original_links=[],
            evidences=[
                PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P1 snippet"),
                PatentEvidence(canonical_patent_id="P2", publication_number="P2", application_number=None, title="专利 P2", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P2 snippet"),
                PatentEvidence(canonical_patent_id="P3", publication_number="P3", application_number=None, title="专利 P3", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P3 snippet"),
                PatentEvidence(canonical_patent_id="P4", publication_number="P4", application_number=None, title="专利 P4", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P4 snippet"),
            ],
        ),
        context={},
    )
    builder.close()

    assert "专利: 专利 P1 (P1)" in prompt
    assert "专利: 专利 P2 (P2)" in prompt
    assert "专利: 专利 P3 (P3)" in prompt
    assert "专利: 专利 P4 (P4)" in prompt


def test_patent_answer_builder_prompt_filters_evidence_to_allowed_patent_ids():
    builder = PatentAnswerBuilder(api_key="", base_url="http://example.invalid", model="test-model")
    prompt = builder._build_prompt(
        question="请基于 top2 专利总结电压特征",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["P1", "P2", "P3", "P4"],
            reference_objects=[],
            reference_links=[],
            original_links=[],
            evidences=[
                PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="P1 abstract", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P1 snippet"),
                PatentEvidence(canonical_patent_id="P2", publication_number="P2", application_number=None, title="专利 P2", abstract_text="P2 abstract", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P2 snippet"),
                PatentEvidence(canonical_patent_id="P3", publication_number="P3", application_number=None, title="专利 P3", abstract_text="P3 abstract", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P3 snippet"),
                PatentEvidence(canonical_patent_id="P4", publication_number="P4", application_number=None, title="专利 P4", abstract_text="P4 abstract", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P4 snippet"),
            ],
        ),
        context={
            "allowed_patent_ids": ["P1", "P2"],
            "allowed_patent_ids_all": ["P1", "P2", "P3", "P4"],
        },
    )
    builder.close()

    assert "专利: 专利 P1 (P1)" in prompt
    assert "专利: 专利 P2 (P2)" in prompt
    assert "专利: 专利 P3 (P3)" not in prompt
    assert "专利: 专利 P4 (P4)" not in prompt


def test_patent_answer_builder_prompt_truncates_long_evidence_fields(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE4_EVIDENCE_ABSTRACT_MAX_CHARS", "40")
    monkeypatch.setenv("PATENT_STAGE4_EVIDENCE_SNIPPET_MAX_CHARS", "50")
    monkeypatch.setenv("PATENT_STAGE4_EVIDENCE_TABLE_MAX_CHARS", "60")

    long_abstract = "摘要" + ("A" * 120) + "TAIL"
    long_snippet = "片段" + ("B" * 140) + "TAIL"
    long_table_cell = "表格值" + ("C" * 120) + "TAIL"

    builder = PatentAnswerBuilder(api_key="", base_url="http://example.invalid", model="test-model")
    prompt = builder._build_prompt(
        question="请总结长证据文本",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["P1"],
            reference_objects=[],
            reference_links=[],
            original_links=[],
            evidences=[
                PatentEvidence(
                    canonical_patent_id="P1",
                    publication_number="P1",
                    application_number=None,
                    title="专利 P1",
                    abstract_text=long_abstract,
                    matched_section_type="claim",
                    matched_section_label="Claim 1",
                    matched_snippet=long_snippet,
                    table_supplements=[
                        PatentTableSupplement(
                            table_title="表1",
                            columns=["列1"],
                            rows=[{"列1": long_table_cell}],
                        )
                    ],
                )
            ],
        ),
        context={"allowed_patent_ids": ["P1"]},
    )
    builder.close()

    assert "TAIL" not in prompt
    assert "..." in prompt or "…" in prompt


def test_patent_answer_builder_stream_logs_prompt_and_evidence_chars(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"这是流式答案 (patent_id=P1)。"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    builder = PatentAnswerBuilder(
        api_key="test-key",
        base_url="http://example.invalid",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )

    with caplog.at_level("INFO", logger="patent.answering"):
        chunks = list(
            builder.stream(
                question="请总结 top1 证据",
                retrieval_outcome=PatentRetrievalOutcome(
                    retrieval_backend="vector_hybrid",
                    retrieval_version="retrieval-v2",
                    catalog_index_version="catalog-v2",
                    references=["P1", "P2"],
                    reference_objects=[],
                    reference_links=[],
                    original_links=[],
                    evidences=[
                        PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="P1 abstract", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P1 snippet"),
                        PatentEvidence(canonical_patent_id="P2", publication_number="P2", application_number=None, title="专利 P2", abstract_text="P2 abstract", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P2 snippet"),
                    ],
                ),
                context={"allowed_patent_ids": ["P1"]},
            )
        )
    builder.close()

    assert "".join(chunks) == "这是流式答案 (patent_id=P1)。"
    assert any("prompt_chars=" in record.message and "evidence_chars=" in record.message for record in caplog.records)
    assert any(
        "patent answer builder stream request payload ready" in record.message and "stream=True" in record.message
        for record in caplog.records
    )
    assert any(
        "patent answer builder stream request object built" in record.message and "method=POST" in record.message
        for record in caplog.records
    )
    assert any(
        "patent answer builder stream request dispatch start" in record.message and "timeout_seconds=" in record.message
        for record in caplog.records
    )
    assert any(
        "patent answer builder stream request dispatch returned" in record.message and "status_code=200" in record.message
        for record in caplog.records
    )
    assert any("patent answer builder stream response headers received" in record.message and "status_code=200" in record.message for record in caplog.records)
    assert any(
        "patent answer builder stream first response line received" in record.message and "line_chars=" in record.message
        for record in caplog.records
    )
    assert any("patent answer builder stream first payload received" in record.message and "elapsed_ms=" in record.message for record in caplog.records)
    assert any("patent answer builder stream first chunk" in record.message and "chunk_chars=" in record.message for record in caplog.records)
    assert any("patent answer builder stream completed" in record.message and "answer_chars=" in record.message for record in caplog.records)


def test_patent_answer_builder_request_logs_prompt_and_evidence_chars(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "这是同步答案 (patent_id=P1)。"}}]},
        )

    builder = PatentAnswerBuilder(
        api_key="test-key",
        base_url="http://example.invalid",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )

    with caplog.at_level("INFO", logger="patent.answering"):
        answer = builder(
            question="请总结 top1 证据",
            retrieval_outcome=PatentRetrievalOutcome(
                retrieval_backend="vector_hybrid",
                retrieval_version="retrieval-v2",
                catalog_index_version="catalog-v2",
                references=["P1", "P2"],
                reference_objects=[],
                reference_links=[],
                original_links=[],
                evidences=[
                    PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="P1 abstract", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P1 snippet"),
                    PatentEvidence(canonical_patent_id="P2", publication_number="P2", application_number=None, title="专利 P2", abstract_text="P2 abstract", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P2 snippet"),
                ],
            ),
            context={"allowed_patent_ids": ["P1"]},
        )
    builder.close()

    assert answer == "这是同步答案 (patent_id=P1)。"
    assert any(
        "patent answer builder request start" in record.message
        and "prompt_chars=" in record.message
        and "evidence_chars=" in record.message
        for record in caplog.records
    )
    assert any(
        "patent answer builder request payload ready" in record.message and "stream=False" in record.message
        for record in caplog.records
    )
    assert any(
        "patent answer builder request object built" in record.message and "method=POST" in record.message
        for record in caplog.records
    )
    assert any(
        "patent answer builder request dispatch start" in record.message and "timeout_seconds=" in record.message
        for record in caplog.records
    )
    assert any(
        "patent answer builder request dispatch returned" in record.message and "status_code=200" in record.message
        for record in caplog.records
    )
    assert any(
        "patent answer builder llm response headers received" in record.message
        and "status_code=200" in record.message
        for record in caplog.records
    )
    assert any(
        "patent answer builder llm response body parsed" in record.message and "response_chars=" in record.message
        for record in caplog.records
    )
    assert any(
        "patent answer builder llm response received" in record.message
        and "elapsed_ms=" in record.message
        for record in caplog.records
    )


def test_stage4_fallback_answer_does_not_drop_patents_beyond_first_three():
    answer = build_fallback_patent_answer(
        question="多专利场景下是否会漏掉后面的 patent？",
        retrieval_outcome=PatentRetrievalOutcome(
            retrieval_backend="vector_hybrid",
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            references=["P1", "P2", "P3", "P4"],
            reference_objects=[],
            reference_links=[],
            original_links=[],
            evidences=[
                PatentEvidence(canonical_patent_id="P1", publication_number="P1", application_number=None, title="专利 P1", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P1 snippet"),
                PatentEvidence(canonical_patent_id="P2", publication_number="P2", application_number=None, title="专利 P2", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P2 snippet"),
                PatentEvidence(canonical_patent_id="P3", publication_number="P3", application_number=None, title="专利 P3", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P3 snippet"),
                PatentEvidence(canonical_patent_id="P4", publication_number="P4", application_number=None, title="专利 P4", abstract_text="", matched_section_type="claim", matched_section_label="Claim 1", matched_snippet="P4 snippet"),
            ],
        ),
    )

    assert "专利 P1" in answer
    assert "专利 P2" in answer
    assert "专利 P3" in answer
    assert "专利 P4" in answer
