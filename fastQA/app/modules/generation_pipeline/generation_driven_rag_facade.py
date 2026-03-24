from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.modules.generation_pipeline.context_loading import (
    build_vector_db_context_for_prompt as build_vector_db_context_for_prompt_impl,
    load_pdf_sentences as load_pdf_sentences_impl,
    load_vector_db_topics as load_vector_db_topics_impl,
)
from app.modules.generation_pipeline.dependencies import MicroscopicSemanticExpert, SentenceAligner
from app.modules.generation_pipeline.prompt_templates import load_generation_prompts as load_generation_prompts_impl
from app.modules.generation_pipeline.query_expander import QueryExpander
from app.modules.generation_pipeline.retrieval_validation import validate_retrieval_relevance
from app.modules.generation_pipeline.reference_alignment import (
    align_dois_with_pdf_chunks as align_dois_with_pdf_chunks_impl,
    format_pdf_chunks_evidence as format_pdf_chunks_evidence_impl,
)
from app.modules.generation_pipeline.doi_inserter import (
    programmatic_insert_dois as programmatic_insert_dois_impl,
)
from app.modules.generation_pipeline.doi_validation import validate_and_fix_doi
from app.modules.generation_pipeline.audit_verification import (
    strict_verify_answer as strict_verify_answer_impl,
    write_alignment_audit as write_alignment_audit_impl,
)
from app.modules.generation_pipeline.stage1_planning import (
    run_stage1_pre_answer_and_planning as run_stage1_pre_answer_and_planning_impl,
)
from app.modules.generation_pipeline.stage2_retrieval import (
    run_stage2_targeted_retrieval as run_stage2_targeted_retrieval_impl,
)
from app.modules.generation_pipeline.pdf_pipeline import (
    stage3_load_pdf_chunks as stage3_load_pdf_chunks_impl,
)
from app.modules.generation_pipeline.md_expansion import (
    run_stage25_md_expansion as run_stage25_md_expansion_impl,
)
from app.modules.generation_pipeline.synthesis_streaming import (
    iter_stage4_synthesis_with_pdf_chunks as iter_stage4_synthesis_with_pdf_chunks_impl,
)
from app.modules.generation_pipeline.synthesis_postprocess import (
    build_references_from_pdf_chunks as build_references_from_pdf_chunks_impl,
    build_top5_reference_context as build_top5_reference_context_impl,
    extract_cited_dois as extract_cited_dois_impl,
    log_top5_coverage as log_top5_coverage_impl,
)
from app.modules.generation_pipeline.runtime_bootstrap import (
    apply_default_doi_runtime_settings as apply_default_doi_runtime_settings_impl,
    build_openai_client as build_openai_client_impl,
    ensure_literature_expert as ensure_literature_expert_impl,
    resolve_generation_runtime_inputs as resolve_generation_runtime_inputs_impl,
)
from app.modules.generation_pipeline.text_processing import extract_question_keywords, preprocess_retrieval_query
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class GenerationDrivenRAG:
    @staticmethod
    def _escape_braces(text: str) -> str:
        return str(text or "").replace("{", "{{").replace("}", "}}")

    class _SafeDict(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str | None = None,
        literature_expert: Optional[MicroscopicSemanticExpert] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        logger.info("initializing GenerationDrivenRAG")
        runtime_inputs = resolve_generation_runtime_inputs_impl(
            api_key=api_key,
            base_url=base_url,
            model=model,
            config=config,
        )
        self.api_key = runtime_inputs.api_key
        self.base_url = runtime_inputs.base_url
        self.model = runtime_inputs.model
        self.embedding_model_type = runtime_inputs.embedding_model_type
        self.embedding_api_url = runtime_inputs.embedding_api_url
        self.embedding_model_path = runtime_inputs.embedding_model_path
        self.chroma_db_path = runtime_inputs.chroma_db_path

        self.client = build_openai_client_impl(
            api_key=self.api_key,
            base_url=self.base_url,
            logger=logger,
        )
        self.literature_expert = ensure_literature_expert_impl(
            existing_expert=literature_expert,
            expert_cls=MicroscopicSemanticExpert,
            runtime_inputs=runtime_inputs,
            logger=logger,
        )
        self._query_expander: QueryExpander | None = None
        self._load_vector_db_topics()
        self._load_prompts()
        apply_default_doi_runtime_settings_impl(self)

    def _load_vector_db_topics(self) -> None:
        self._vector_db_topics = load_vector_db_topics_impl(logger=logger)

    def _get_vector_db_context_for_prompt(self) -> str:
        return build_vector_db_context_for_prompt_impl(self._vector_db_topics)

    def _load_prompts(self) -> None:
        self.stage1_prompt, self.stage2_prompt = load_generation_prompts_impl()

    def _programmatic_insert_dois(
        self,
        answer: str,
        retrieval_results: dict[str, Any],
        similarity_threshold: float | None = None,
        question: str | None = None,
    ) -> str:
        return programmatic_insert_dois_impl(
            agent=self,
            answer=answer,
            retrieval_results=retrieval_results,
            similarity_threshold=similarity_threshold,
            question=question,
            validate_and_fix_doi_fn=validate_and_fix_doi,
            aligner_cls=SentenceAligner,
            logger=logger,
        )

    def _write_alignment_audit(
        self,
        question: str | None,
        original_answer: str,
        audit_list: list[dict[str, Any]],
        retrieval_results: dict[str, Any] | None = None,
    ) -> None:
        write_alignment_audit_impl(
            question=question,
            original_answer=original_answer,
            audit_list=audit_list,
            retrieval_results=retrieval_results,
            logger=logger,
        )

    def _strict_verify_answer(
        self,
        answer: str,
        retrieval_results: dict[str, Any],
        question: str | None = None,
    ) -> str:
        return strict_verify_answer_impl(
            agent=self,
            answer=answer,
            retrieval_results=retrieval_results,
            question=question,
            validate_and_fix_doi_fn=validate_and_fix_doi,
            aligner_cls=SentenceAligner,
            logger=logger,
        )

    def _load_pdf_sentences(self, doi: str, max_pages: int = 30, max_chars: int = 15000):
        return load_pdf_sentences_impl(doi=doi, max_pages=max_pages, max_chars=max_chars, logger=logger)

    def _format_pdf_chunks_evidence(self, pdf_chunks: dict[str, list[dict[str, Any]]], user_question: str = "") -> str:
        return format_pdf_chunks_evidence_impl(
            pdf_chunks=pdf_chunks,
            user_question=user_question,
            logger=logger,
        )

    def _get_query_expander(self) -> QueryExpander:
        if self._query_expander is None:
            self._query_expander = QueryExpander(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
            )
        return self._query_expander

    def stage1_pre_answer_and_planning(
        self,
        user_question: str,
        conversation_context: dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return run_stage1_pre_answer_and_planning_impl(
            user_question=user_question,
            stage1_prompt=self.stage1_prompt,
            vector_db_context=self._get_vector_db_context_for_prompt(),
            client=self.client,
            model=self.model,
            logger=logger,
            conversation_context=conversation_context,
        )

    def stage2_targeted_retrieval(
        self,
        retrieval_claims,
        n_results_per_claim: int = 3,
        user_question: Optional[str] = None,
        should_cancel=None,
        active_stream_count=None,
    ) -> Dict[str, Any]:
        return run_stage2_targeted_retrieval_impl(
            retrieval_claims=list(retrieval_claims or []),
            n_results_per_claim=n_results_per_claim,
            user_question=user_question,
            client=self.client,
            model=self.model,
            literature_expert=self.literature_expert,
            preprocess_retrieval_query_fn=lambda query: preprocess_retrieval_query(query, logger=logger),
            validate_retrieval_relevance_fn=lambda results, query, claim_text: validate_retrieval_relevance(
                results,
                query,
                claim_text,
                logger,
            ),
            current_answer_context=None,
            logger=logger,
            extract_question_keywords_fn=extract_question_keywords,
            expand_query_fn=self._get_query_expander().expand,
            should_cancel=should_cancel,
            active_stream_count=active_stream_count,
        )

    def stage25_md_expansion(self, *, retrieval_results: dict[str, Any], user_question: str, dois: list[str]) -> dict[str, Any]:
        return run_stage25_md_expansion_impl(
            retrieval_results=retrieval_results,
            user_question=user_question,
            dois=dois,
            literature_expert=self.literature_expert,
            logger=logger,
        )

    def _extract_dois_from_results(self, retrieval_results: dict[str, Any]) -> list[str]:
        metadatas = list(retrieval_results.get("metadatas") or [])
        dois: list[str] = []
        for item in metadatas:
            if not isinstance(item, dict):
                continue
            doi = str(item.get("doi") or "").strip()
            if doi and doi not in dois:
                dois.append(doi)
        return dois

    def _align_dois_with_pdf_chunks(
        self,
        answer: str,
        pdf_chunks: dict[str, list[dict[str, Any]]],
        user_question: str = "",
    ) -> str:
        emb_model = None
        try:
            if self.literature_expert is not None:
                emb_model = getattr(self.literature_expert, "embedding_model", None)
        except Exception:
            emb_model = None

        threshold = getattr(self, "insert_similarity_threshold", 0.50)
        return align_dois_with_pdf_chunks_impl(
            answer=answer,
            pdf_chunks=pdf_chunks,
            emb_model=emb_model,
            threshold=threshold,
            logger=logger,
        )

    def stage3_load_pdf_chunks(self, dois: list[str], max_chunks_per_doi: int = 3, should_cancel=None) -> dict[str, list[dict[str, Any]]]:
        return stage3_load_pdf_chunks_impl(
            dois=list(dois or []),
            papers_dir=get_settings().papers_dir,
            max_chunks_per_doi=max_chunks_per_doi,
            logger=logger,
            should_cancel=should_cancel,
        )

    def stage4_synthesis_with_pdf_chunks(
        self,
        user_question: str,
        deep_answer: str,
        pdf_chunks: dict[str, list[dict[str, Any]]],
        retrieval_results: dict[str, Any] | None = None,
        should_cancel=None,
    ):
        yield from iter_stage4_synthesis_with_pdf_chunks_impl(
            user_question=user_question,
            deep_answer=deep_answer,
            pdf_chunks=pdf_chunks,
            retrieval_results=retrieval_results,
            stage2_prompt=self.stage2_prompt,
            client=self.client,
            model=self.model,
            safe_dict_cls=self._SafeDict,
            escape_braces_fn=self._escape_braces,
            format_pdf_chunks_evidence_fn=self._format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context_impl,
            extract_cited_dois_fn=extract_cited_dois_impl,
            log_top5_coverage_fn=log_top5_coverage_impl,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks_impl,
            programmatic_insert_dois_fn=self._programmatic_insert_dois,
            align_dois_with_pdf_chunks_fn=self._align_dois_with_pdf_chunks,
            should_cancel=should_cancel,
            logger=logger,
        )
