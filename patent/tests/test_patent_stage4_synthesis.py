from __future__ import annotations

import httpx

from server.patent.answering import PatentAnswerBuilder, build_fallback_patent_answer
from server.patent.retrieval_models import PatentEvidence, PatentRetrievalOutcome
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
    assert "(patent_id=CN115132975B)" in result["final_answer"]
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
    assert "(patent_id=CN115132975B)" in result["final_answer"]
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

    assert "(patent_id=CN115132975B)" in result["final_answer"]
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
    assert "(patent_id=CN115132975B)" in result["final_answer"]
    assert "围绕“" not in result["final_answer"]
    assert result["metadata"]["citation_mode"] == "programmatic_repair"


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

    assert streamed_chunks == ["这是流式输出的答案", "，引用外部专利 (patent_id=CN000000000A)。"]
    assert result["success"] is True
    assert result["final_answer"] == "这是流式输出的答案 (patent_id=CN115132975B)"
    assert result["metadata"]["citation_mode"] == "programmatic_repair"
    assert result["metadata"]["invalid_cited_patent_ids"] == ["CN000000000A"]


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
    assert "另一篇外部专利也支持这一点" not in answer


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
