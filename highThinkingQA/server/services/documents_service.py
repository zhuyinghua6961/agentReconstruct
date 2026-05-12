"""Document APIs for PDF access, translation, and summarization."""

# Deprecated: retained only for the retired highThinkingQA document HTTP surface.


from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any

import config
from ingest.vector_store import get_collection_count
from server.services.pdf_extractor import extract_pdf_text as extract_pdf_text_impl
from server.storage.paper_storage import build_paper_filename, ensure_local_paper_pdf, normalize_doi, paper_pdf_exists

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None


class DocumentsService:
    def __init__(self) -> None:
        self._papers_dir = Path(config.PAPERS_DIR).resolve()
        self._max_pdf_pages = max(1, int(str(os.getenv("MAX_PDF_PAGES", "50") or "50")))
        self._translation_cache: dict[str, str] = {}
        self._translation_lock = threading.Lock()

    @staticmethod
    def _llm_api_key() -> str:
        return str(os.getenv("LLM_API_KEY", "")).strip() or str(getattr(config, "LLM_API_KEY", "")).strip()

    @staticmethod
    def _llm_base_url() -> str | None:
        value = str(os.getenv("LLM_BASE_URL", "")).strip() or str(getattr(config, "LLM_BASE_URL", "")).strip()
        return value or None

    @staticmethod
    def _llm_model() -> str:
        return (
            str(os.getenv("LLM_MODEL", "")).strip()
            or str(os.getenv("DOCUMENTS_LLM_MODEL", "")).strip()
            or str(getattr(config, "LLM_MODEL", "")).strip()
            or "qwen3-max"
        )

    def _openai_client(self):
        if OpenAI is None:
            return None
        api_key = self._llm_api_key()
        if not api_key:
            return None
        return OpenAI(api_key=api_key, base_url=self._llm_base_url())

    def _extract_pdf_body(
        self,
        *,
        pdf_path: Path,
        logger: Any,
        max_pages: int,
        exclude_references: bool = True,
    ) -> str:
        try:
            import fitz  # type: ignore

            pdf_support = True
        except Exception:
            fitz = None
            pdf_support = False
        return extract_pdf_text_impl(
            str(pdf_path),
            max_pages=max_pages,
            exclude_references=exclude_references,
            pdf_support=pdf_support,
            fitz_module=fitz,
            logger=logger,
            traceback_module=__import__("traceback"),
        )

    def view_pdf_path(self, doi: str, logger: Any) -> tuple[dict[str, Any], int, Path | None]:
        normalized = normalize_doi(doi)
        if not normalized:
            return {"success": False, "error": "pdf_not_found", "code": "NOT_FOUND", "doi": normalized}, 404, None
        pdf_path = ensure_local_paper_pdf(doi=normalized, papers_dir=self._papers_dir, logger=logger)
        if pdf_path is None:
            return {"success": False, "error": "pdf_not_found", "code": "NOT_FOUND", "doi": normalized}, 404, None
        return {"success": True, "doi": normalized, "filename": build_paper_filename(normalized)}, 200, pdf_path

    def translate(self, *, texts: list[Any], logger: Any) -> tuple[dict[str, Any], int]:
        if not isinstance(texts, list) or not texts:
            return {"success": False, "error": "invalid_texts", "code": "INVALID_ARGUMENT"}, 400

        client = self._openai_client()
        if client is None:
            return {"success": False, "error": "translation_disabled", "code": "TRANSLATION_DISABLED"}, 503

        translations: list[str] = []
        cache_hits = 0
        failures: list[dict[str, Any]] = []
        non_empty_count = 0
        failed_non_empty_count = 0

        for index, raw in enumerate(texts):
            text = str(raw or "")
            if not text.strip():
                translations.append("")
                continue
            non_empty_count += 1
            with self._translation_lock:
                cached = self._translation_cache.get(text)
            if cached is not None:
                cache_hits += 1
                translations.append(cached)
                continue
            try:
                resp = client.chat.completions.create(
                    model=self._llm_model(),
                    messages=[
                        {
                            "role": "system",
                            "content": "你是专业的学术论文翻译专家。请将英文文献翻译成准确、流畅的中文，保持专业术语准确。",
                        },
                        {
                            "role": "user",
                            "content": (
                                f"请将以下英文翻译成中文：\n\n{text}\n\n"
                                "要求：1. 只输出翻译结果 2. 不要解释 3. 保持术语准确"
                            ),
                        },
                    ],
                    temperature=0.3,
                )
                translated = str(resp.choices[0].message.content or "").strip()
            except Exception as exc:
                logger.warning("translation failed at index %s: %s", index, exc)
                translated = ""
            if not translated:
                failed_non_empty_count += 1
                failures.append({"index": index, "error": "translation_failed"})
                translations.append("")
                continue
            with self._translation_lock:
                self._translation_cache[text] = translated
            translations.append(translated)

        if non_empty_count > 0 and failed_non_empty_count >= non_empty_count:
            return {
                "success": False,
                "error": "translation_failed",
                "code": "TRANSLATION_FAILED",
                "data": {
                    "failed_count": failed_non_empty_count,
                    "total_count": len(texts),
                    "failures": failures,
                },
            }, 502

        payload: dict[str, Any] = {
            "success": True,
            "data": {
                "translations": translations,
                "count": len(translations),
                "cache_hits": cache_hits,
                "provider": "openai-compatible",
            },
            "translations": translations,
            "count": len(translations),
            "cache_hits": cache_hits,
        }
        if failures:
            payload["data"]["failed_count"] = failed_non_empty_count
            payload["data"]["failures"] = failures
        return payload, 200

    def summarize_pdf(self, doi: str, logger: Any) -> tuple[dict[str, Any], int]:
        client = self._openai_client()
        if client is None:
            return {"success": False, "error": "summary_disabled", "code": "SUMMARY_DISABLED"}, 503

        normalized = normalize_doi(doi)
        pdf_path = ensure_local_paper_pdf(doi=normalized, papers_dir=self._papers_dir, logger=logger)
        if pdf_path is None:
            return {"success": False, "error": "pdf_not_found", "code": "NOT_FOUND", "doi": normalized}, 404

        full_text = self._extract_pdf_body(
            pdf_path=pdf_path,
            logger=logger,
            max_pages=self._max_pdf_pages,
            exclude_references=True,
        )
        if not full_text or str(full_text).startswith("[错误]"):
            return {"success": False, "error": "pdf_extract_failed", "code": "PDF_EXTRACT_FAILED"}, 500
        if str(full_text).startswith("[警告]"):
            full_text = re.sub(r"^\[警告\].*?\n\n", "", str(full_text), flags=re.S)
        if len(full_text) > 12000:
            full_text = full_text[:12000]

        try:
            resp = client.chat.completions.create(
                model=self._llm_model(),
                messages=[
                    {
                        "role": "system",
                        "content": "你是一名材料领域文献速读助手，擅长用中文提炼论文要点。",
                    },
                    {
                        "role": "user",
                        "content": (
                            "请对以下文献内容生成一段详细中文总结，突出研究目的、方法、关键结果、数据结论、局限与结论，"
                            "长度控制在260到420字，不要附加外链或参考文献列表。\n\n"
                            f"{full_text}"
                        ),
                    },
                ],
                temperature=0.3,
                max_tokens=650,
            )
            summary = str(resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.error("pdf summary failed for %s: %s", normalized, exc)
            return {"success": False, "error": "summary_failed", "code": "SUMMARY_FAILED"}, 500

        return {
            "success": True,
            "data": {"doi": normalized, "summary": summary},
            "doi": normalized,
            "summary": summary,
        }, 200

    def extract_pdf_text(self, doi: str, logger: Any) -> tuple[dict[str, Any], int]:
        normalized = normalize_doi(doi)
        pdf_path = ensure_local_paper_pdf(doi=normalized, papers_dir=self._papers_dir, logger=logger)
        if pdf_path is None:
            return {"success": False, "error": "pdf_not_found", "code": "NOT_FOUND", "doi": normalized}, 404
        full_text = self._extract_pdf_body(
            pdf_path=pdf_path,
            logger=logger,
            max_pages=50,
            exclude_references=True,
        )
        if str(full_text).startswith("[错误]"):
            return {"success": False, "error": str(full_text), "code": "PDF_EXTRACT_FAILED"}, 500
        paragraphs = self._segment_paragraphs(str(full_text or ""))
        return {
            "success": True,
            "data": {"doi": normalized, "paragraphs": paragraphs, "total": len(paragraphs)},
            "doi": normalized,
            "paragraphs": paragraphs,
            "total": len(paragraphs),
        }, 200

    def check_pdf(self, doi: str, logger: Any) -> tuple[dict[str, Any], int]:
        normalized = normalize_doi(doi)
        exists = paper_pdf_exists(doi=normalized, papers_dir=self._papers_dir, logger=logger)
        filename = build_paper_filename(normalized)
        return {"success": True, "exists": exists, "doi": normalized, "filename": filename if exists else None}, 200

    @staticmethod
    def _segment_paragraphs(full_text: str) -> list[str]:
        paragraphs: list[str] = []
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", full_text)
        current_para = ""
        sentence_count = 0
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            current_para += sentence + " "
            sentence_count += 1
            if (sentence_count >= 2 and len(current_para) > 150) or len(current_para) > 400:
                paragraphs.append(current_para.strip())
                current_para = ""
                sentence_count = 0
        if current_para.strip():
            paragraphs.append(current_para.strip())
        return paragraphs[:100]


documents_service = DocumentsService()
