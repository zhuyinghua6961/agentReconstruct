from __future__ import annotations

from html import escape
from itertools import chain
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from app.core.config import get_settings
from app.core.runtime import PublicServiceRuntime
from app.core.errors import AppError
from app.modules.documents.cache import build_patent_original_cache_control, build_patent_original_etag
from app.modules.documents.patent_original_store import (
    PatentOriginalNotFoundError,
    PatentOriginalStore,
    PatentOriginalStoreBackendError,
    PatentOriginalUnavailableError,
)
from app.modules.documents.reference_preview import (
    build_reference_preview_batch,
    clamp_preview_max_items,
    collect_doi_candidates,
    normalize_dois,
)
from app.modules.documents.translation_service import documents_translation_service
from app.modules.storage.service import storage_service


try:
    from openai import OpenAI
except Exception:
    OpenAI = None

DEFAULT_LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return default


def format_material_content(node_data: dict[str, Any]) -> str:
    content_parts: list[str] = []

    categories = {
        "基本信息": ["material_name", "material_type"],
        "物理性质": ["tap_density", "compaction_density", "particle_size", "specific_surface_area", "porosity"],
        "电化学性能": ["initial_capacity", "capacity_retention", "coulombic_efficiency", "rate_capability", "cycle_life"],
        "制备工艺": ["preparation_method", "synthesis_temperature", "synthesis_time", "precursor", "coating_material"],
        "其他参数": [],
    }

    categorized = {cat: [] for cat in categories.keys()}
    uncategorized: list[tuple[str, Any]] = []

    for key, value in node_data.items():
        if value is None or value == "" or value == 0:
            continue
        categorized_flag = False
        for category, keys in categories.items():
            if key in keys:
                categorized[category].append((key, value))
                categorized_flag = True
                break
        if not categorized_flag:
            uncategorized.append((key, value))

    for category, items in categorized.items():
        if items:
            content_parts.append(f"<h4>{category}</h4>")
            for key, value in items:
                formatted_key = " ".join(word.capitalize() for word in key.split("_"))
                content_parts.append(f"<p><strong>{formatted_key}:</strong> {value}</p>")

    if uncategorized:
        content_parts.append("<h4>其他信息</h4>")
        for key, value in uncategorized:
            formatted_key = " ".join(word.capitalize() for word in key.split("_"))
            content_parts.append(f"<p><strong>{formatted_key}:</strong> {value}</p>")

    return "\n".join(content_parts) if content_parts else "<p>暂无详细内容</p>"


class DocumentsService:
    _MAX_TRANSLATION_SEGMENT_CHARS = 1400
    _MAX_TRANSLATION_SEGMENTS = 80

    @staticmethod
    def _retrieval_dependency_payload(
        runtime: PublicServiceRuntime | None,
        *,
        mode: str,
        detail: str,
    ) -> dict[str, Any]:
        retrieval_component = dict((runtime.component_status or {}).get("retrieval") or {}) if runtime is not None else {}
        agent_component = dict((runtime.component_status or {}).get("agent") or {}) if runtime is not None else {}
        return {
            "dependency": {
                "name": "retrieval_runtime",
                "mode": str(mode or "required"),
                "detail": str(detail or ""),
                "retrieval_component": retrieval_component,
                "agent_component": agent_component,
                "agent_initialized": bool(getattr(runtime, "agent", None)) if runtime is not None else False,
                "vector_collection_available": getattr(runtime, "vector_collection", None) is not None if runtime is not None else False,
                "neo4j_available": bool(getattr(getattr(runtime, "neo4j_client", None), "available", False)) if runtime is not None else False,
            }
        }

    def __init__(self) -> None:
        self._papers_dir = self._resolve_papers_dir()
        self._max_pdf_pages = max(1, int(str(os.getenv("MAX_PDF_PAGES", "50") or "50")))
        self._openai_api_key = _first_env("LLM_API_KEY")
        self._openai_base_url = _first_env("LLM_BASE_URL", default=DEFAULT_LLM_BASE_URL)
        self._openai_model = _first_env("LLM_MODEL", default="deepseek-v3.1")

    def _resolve_papers_dir(self) -> Path:
        path = get_settings().papers_dir
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except PermissionError:
            fallback = (Path(tempfile.gettempdir()) / "public-service-papers").resolve()
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def _get_patent_original_store(self) -> PatentOriginalStore:
        return PatentOriginalStore(project_root=str(get_settings().local_storage_root))

    @staticmethod
    def _normalize_patent_original_format(*, section: str, response_format: str | None) -> str:
        normalized = str(response_format or "").strip().lower()
        if normalized:
            return normalized
        if str(section or "").strip().lower() == "fulltext":
            return "pdf"
        return "json"

    @staticmethod
    def _validate_patent_original_request(
        *,
        section: str,
        claim_number: int | None,
        paragraph_id: str | None,
        response_format: str | None,
    ) -> None:
        normalized_section = str(section or "").strip().lower()
        if normalized_section not in {"abstract", "claim", "description", "figure", "fulltext"}:
            raise AppError(message="invalid section", code="INVALID_REQUEST", status_code=400)
        normalized_format = str(response_format or "").strip().lower()
        if normalized_format and normalized_format not in {"json", "html", "text", "redirect"}:
            raise AppError(message="invalid format", code="INVALID_REQUEST", status_code=400)
        if normalized_section == "fulltext" and normalized_format in {"json", "html", "text"}:
            raise AppError(message="format is not supported for fulltext", code="INVALID_REQUEST", status_code=400)
        if normalized_section != "fulltext" and normalized_format == "redirect":
            raise AppError(message="redirect is only supported for fulltext", code="INVALID_REQUEST", status_code=400)
        if normalized_section != "claim" and claim_number is not None:
            raise AppError(message="claim_number is only allowed when section=claim", code="INVALID_REQUEST", status_code=400)
        if normalized_section != "description" and paragraph_id is not None:
            raise AppError(message="paragraph_id is only allowed when section=description", code="INVALID_REQUEST", status_code=400)
        if claim_number is not None and int(claim_number) <= 0:
            raise AppError(message="claim_number must be greater than 0", code="INVALID_REQUEST", status_code=400)

    @staticmethod
    def _render_patent_original_html(*, section_label: str, content: Any, section: str) -> str:
        if isinstance(content, dict) and isinstance(content.get("html"), str) and content.get("html"):
            return str(content.get("html"))
        if section == "abstract" and isinstance(content, dict) and isinstance(content.get("abstract_html"), str):
            return str(content.get("abstract_html"))
        if section == "claim" and isinstance(content, dict) and isinstance(content.get("claims"), list):
            parts = [str(item.get("html") or "") for item in content.get("claims") or [] if isinstance(item, dict)]
            if parts:
                return "".join(parts)
        if section == "description" and isinstance(content, dict) and isinstance(content.get("paragraphs"), list):
            parts = [str(item.get("html") or "") for item in content.get("paragraphs") or [] if isinstance(item, dict)]
            if parts:
                return "".join(parts)
        if section == "figure" and isinstance(content, dict):
            figure_source = escape(str(content.get("figure_source") or ""))
            object_key = escape(str(content.get("served_object_key") or ""))
            return (
                f"<article><h1>{escape(section_label)}</h1>"
                f"<p>figure_source: {figure_source}</p>"
                f"<p>served_object_key: {object_key}</p></article>"
            )
        return f"<article><h1>{escape(section_label)}</h1><p>{escape(str(content or ''))}</p></article>"

    @staticmethod
    def _render_patent_original_text(*, content: Any, section: str) -> str:
        if isinstance(content, dict) and isinstance(content.get("text"), str) and content.get("text"):
            return str(content.get("text"))
        if section == "abstract" and isinstance(content, dict) and isinstance(content.get("abstract_text"), str):
            return str(content.get("abstract_text"))
        if section == "claim" and isinstance(content, dict) and isinstance(content.get("claims"), list):
            parts = [str(item.get("text") or "") for item in content.get("claims") or [] if isinstance(item, dict)]
            if parts:
                return "\n".join(parts)
        if section == "description" and isinstance(content, dict) and isinstance(content.get("paragraphs"), list):
            parts = [str(item.get("text") or "") for item in content.get("paragraphs") or [] if isinstance(item, dict)]
            if parts:
                return "\n".join(parts)
        if section == "figure" and isinstance(content, dict):
            return (
                f"figure_source: {content.get('figure_source') or ''}\n"
                f"served_object_key: {content.get('served_object_key') or ''}"
            ).strip()
        return str(content or "")

    @staticmethod
    def _normalize_document_translation_type(document_type: str) -> str:
        normalized = str(document_type or "").strip().lower()
        if normalized in {"doi", "paper", "literature"}:
            return "doi"
        if normalized in {"patent", "patent_id"}:
            return "patent"
        return ""

    def _append_translation_segments(self, segments: list[str], text: Any) -> None:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return
        max_chars = self._MAX_TRANSLATION_SEGMENT_CHARS
        if len(normalized) <= max_chars:
            segments.append(normalized)
            return

        remaining = normalized
        while remaining:
            if len(remaining) <= max_chars:
                segments.append(remaining.strip())
                break
            split_at = max(
                remaining.rfind("\n\n", 0, max_chars),
                remaining.rfind("\n", 0, max_chars),
                remaining.rfind(". ", 0, max_chars),
                remaining.rfind("? ", 0, max_chars),
                remaining.rfind("! ", 0, max_chars),
                remaining.rfind("。", 0, max_chars),
                remaining.rfind("；", 0, max_chars),
                remaining.rfind(";", 0, max_chars),
            )
            if split_at <= 0:
                split_at = max_chars
            chunk = remaining[:split_at].strip()
            if chunk:
                segments.append(chunk)
            remaining = remaining[split_at:].strip()

    def _trim_translation_segments(self, segments: list[str]) -> tuple[list[str], bool]:
        if len(segments) <= self._MAX_TRANSLATION_SEGMENTS:
            return segments, False
        return segments[: self._MAX_TRANSLATION_SEGMENTS], True

    @staticmethod
    def _resolve_document_translation_cache_status(*, segment_count: int, cache_hits: int) -> str:
        if segment_count > 0 and cache_hits >= segment_count:
            return "hit"
        if cache_hits > 0:
            return "partial"
        return "miss"

    @staticmethod
    def _encode_sse_payload(payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")

    def _build_doi_translation_segments(self, *, doi: str, logger: Any) -> tuple[list[str], dict[str, Any], int]:
        normalized = storage_service.normalize_doi(doi)
        pdf_path = self._ensure_local_pdf(doi=normalized, logger=logger)
        if not pdf_path:
            return [], {"success": False, "error": f"PDF文件不存在: {normalized or doi}", "code": "PDF_NOT_FOUND"}, 404

        full_text = self._extract_pdf_body(
            pdf_path=pdf_path,
            logger=logger,
            max_pages=self._max_pdf_pages,
            exclude_references=True,
        )
        if not full_text or str(full_text).startswith("[错误]"):
            return [], {"success": False, "error": str(full_text or "未能提取到PDF正文"), "code": "PDF_TEXT_EXTRACTION_FAILED"}, 500

        base_segments = self._segment_paragraphs(str(full_text or ""))
        segments: list[str] = []
        for item in base_segments:
            self._append_translation_segments(segments, item)
        trimmed_segments, truncated = self._trim_translation_segments(segments)
        return trimmed_segments, {
            "success": True,
            "document_type": "doi",
            "document_id": normalized,
            "segment_count": len(trimmed_segments),
            "truncated": truncated,
        }, 200

    def _build_patent_translation_segments(self, *, canonical_patent_id: str) -> tuple[list[str], dict[str, Any], int]:
        store = self._get_patent_original_store()
        try:
            manifest = store.load_manifest(canonical_patent_id)
            abstract = store.resolve_section(
                canonical_patent_id=manifest.canonical_patent_id,
                section="abstract",
                manifest=manifest,
            )
            claims = store.resolve_section(
                canonical_patent_id=manifest.canonical_patent_id,
                section="claim",
                manifest=manifest,
            )
            description = store.resolve_section(
                canonical_patent_id=manifest.canonical_patent_id,
                section="description",
                manifest=manifest,
            )
        except PatentOriginalNotFoundError as exc:
            return [], {"success": False, "error": str(exc), "code": "PATENT_NOT_FOUND"}, 404
        except PatentOriginalUnavailableError as exc:
            return [], {"success": False, "error": str(exc), "code": "ORIGINAL_NOT_AVAILABLE"}, 404
        except PatentOriginalStoreBackendError as exc:
            return [], {"success": False, "error": str(exc), "code": "OBJECT_STORE_UNAVAILABLE"}, 503

        segments: list[str] = []
        abstract_text = str(dict(abstract.content or {}).get("abstract_text") or "").strip() if isinstance(abstract.content, dict) else ""
        if abstract_text:
            self._append_translation_segments(segments, f"Abstract\n{abstract_text}")

        claim_lines = []
        if isinstance(claims.content, dict):
            for item in list(claims.content.get("claims") or []):
                if not isinstance(item, dict):
                    continue
                claim_text = str(item.get("text") or "").strip()
                if not claim_text:
                    continue
                claim_number = int(item.get("claim_number") or 0)
                prefix = f"{claim_number}. " if claim_number > 0 else ""
                claim_lines.append(f"{prefix}{claim_text}")
        if claim_lines:
            self._append_translation_segments(segments, "Claims\n" + "\n".join(claim_lines))

        description_lines = []
        if isinstance(description.content, dict):
            for item in list(description.content.get("paragraphs") or []):
                if not isinstance(item, dict):
                    continue
                paragraph_text = str(item.get("text") or "").strip()
                if paragraph_text:
                    description_lines.append(paragraph_text)
        if description_lines:
            self._append_translation_segments(segments, "Description\n" + "\n\n".join(description_lines))

        trimmed_segments, truncated = self._trim_translation_segments(segments)
        return trimmed_segments, {
            "success": True,
            "document_type": "patent",
            "document_id": manifest.canonical_patent_id,
            "title": manifest.title,
            "segment_count": len(trimmed_segments),
            "truncated": truncated,
        }, 200

    def _prepare_document_translation(self, *, document_type: str, document_id: str, logger: Any) -> tuple[list[str], dict[str, Any], int]:
        normalized_type = self._normalize_document_translation_type(document_type)
        normalized_id = str(document_id or "").strip()
        if not normalized_type or not normalized_id:
            return [], {"success": False, "error": "invalid_document_request", "code": "INVALID_ARGUMENT"}, 400

        if normalized_type == "doi":
            segments, payload, status_code = self._build_doi_translation_segments(doi=normalized_id, logger=logger)
        else:
            segments, payload, status_code = self._build_patent_translation_segments(canonical_patent_id=normalized_id.upper())

        if status_code != 200:
            return [], payload, status_code
        if not segments:
            return [], {
                **payload,
                "success": False,
                "error": "document_text_unavailable",
                "code": "DOCUMENT_TEXT_UNAVAILABLE",
            }, 404
        return segments, payload, 200

    def patent_original_view(
        self,
        *,
        canonical_patent_id: str,
        section: str,
        claim_number: int | None,
        paragraph_id: str | None,
        response_format: str | None,
        head_only: bool,
        logger: Any,
    ) -> dict[str, Any]:
        _ = logger
        self._validate_patent_original_request(
            section=section,
            claim_number=claim_number,
            paragraph_id=paragraph_id,
            response_format=response_format,
        )
        store = self._get_patent_original_store()
        try:
            manifest = store.load_manifest(canonical_patent_id)
            resolved = store.resolve_section(
                canonical_patent_id=canonical_patent_id,
                section=section,
                claim_number=claim_number,
                paragraph_id=paragraph_id,
                manifest=manifest,
            )
        except PatentOriginalNotFoundError as exc:
            raise AppError(message=str(exc), code="PATENT_NOT_FOUND", status_code=404) from exc
        except PatentOriginalUnavailableError as exc:
            raise AppError(message=str(exc), code="ORIGINAL_NOT_AVAILABLE", status_code=404) from exc
        except PatentOriginalStoreBackendError as exc:
            raise AppError(message=str(exc), code="OBJECT_STORE_UNAVAILABLE", status_code=503) from exc

        normalized_format = self._normalize_patent_original_format(section=resolved.section, response_format=response_format)
        headers = {
            "etag": build_patent_original_etag(original_version=manifest.original_version),
            "cache-control": build_patent_original_cache_control(),
        }

        if resolved.section == "fulltext":
            if normalized_format == "redirect":
                raise AppError(message="provider_redirect_unavailable", code="PROVIDER_REDIRECT_ONLY", status_code=404)
            if not head_only:
                try:
                    body_iter = iter(
                        storage_service.iter_object_bytes(
                            object_name=str(resolved.object_key or ""),
                            project_root=str(get_settings().local_storage_root),
                        )
                    )
                    first_chunk = next(body_iter, b"")
                except StopIteration:
                    first_chunk = b""
                except Exception as exc:
                    raise AppError(message=str(exc), code="OBJECT_STORE_UNAVAILABLE", status_code=503) from exc
                if not first_chunk:
                    raise AppError(message="fulltext pdf unavailable", code="ORIGINAL_NOT_AVAILABLE", status_code=404)
                return {
                    "status_code": 200,
                    "headers": headers,
                    "media_type": str(resolved.media_type or "application/pdf"),
                    "body_iter": chain((first_chunk,), body_iter),
                }
            return {
                "status_code": 200,
                "headers": headers,
                "media_type": str(resolved.media_type or "application/pdf"),
                "body": b"",
            }

        content = resolved.content
        if resolved.section == "figure":
            content = {
                "figure_source": resolved.figure_source,
                "served_object_key": resolved.served_object_key,
                "media_type": resolved.media_type,
            }

        payload = {
            "success": True,
            "canonical_patent_id": manifest.canonical_patent_id,
            "title": manifest.title,
            "provider": manifest.provider,
            "section": resolved.section,
            "section_label": resolved.section_label,
            "content_format": normalized_format,
            "content": content,
            "original_version": manifest.original_version,
        }
        if resolved.claim_number is not None:
            payload["claim_number"] = resolved.claim_number
        if resolved.paragraph_id is not None:
            payload["paragraph_id"] = resolved.paragraph_id
        if resolved.figure_source:
            payload["figure_source"] = resolved.figure_source
        if resolved.served_object_key:
            payload["served_object_key"] = resolved.served_object_key

        if normalized_format == "html":
            return {
                "status_code": 200,
                "headers": headers,
                "media_type": "text/html; charset=utf-8",
                "body": self._render_patent_original_html(
                    section_label=str(resolved.section_label or resolved.section),
                    content=content,
                    section=resolved.section,
                ),
            }
        if normalized_format == "text":
            return {
                "status_code": 200,
                "headers": headers,
                "media_type": "text/plain; charset=utf-8",
                "body": self._render_patent_original_text(content=content, section=resolved.section),
            }
        return {
            "status_code": 200,
            "headers": headers,
            "media_type": "application/json",
            "body": payload,
        }

    def _ensure_local_pdf(self, *, doi: str, logger: Any) -> Path | None:
        normalized = storage_service.normalize_doi(doi)
        return storage_service.ensure_local_paper_pdf(
            doi=normalized,
            papers_dir=self._papers_dir,
            project_root=str(get_settings().local_storage_root),
            logger=logger,
        )

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
        except Exception as exc:
            return f"[错误] PDF解析依赖不可用: {exc}"

        try:
            doc = fitz.open(str(pdf_path))
        except Exception as exc:
            return f"[错误] 无法打开PDF: {exc}"

        try:
            segments: list[str] = []
            page_limit = max(1, int(max_pages or 1))
            for page_index, page in enumerate(doc):
                if page_index >= page_limit:
                    break
                text = str(page.get_text("text") or "")
                if not text.strip():
                    continue
                if exclude_references and page_index >= max(1, page_limit - 5):
                    lowered = text.lower()
                    if "references" in lowered or "bibliography" in lowered:
                        break
                segments.append(text)
            return "\n".join(segments)
        except Exception as exc:
            logger.warning("extract pdf text failed: %s", exc)
            return f"[错误] PDF文本提取失败: {exc}"
        finally:
            doc.close()

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
        if len(paragraphs) > 100:
            paragraphs = paragraphs[:100]
        return paragraphs

    def view_pdf_path(self, doi: str, logger: Any) -> tuple[dict[str, Any], int, Path | None]:
        try:
            normalized = storage_service.normalize_doi(doi)
            pdf_path = self._ensure_local_pdf(doi=normalized, logger=logger)
            if not pdf_path:
                return {"error": f"PDF文件不存在: {normalized or doi}"}, 404, None
            return {"doi": normalized}, 200, pdf_path
        except Exception as exc:
            return {"error": f"查看PDF失败: {exc}"}, 500, None

    def summarize_pdf(self, doi: str, logger: Any) -> tuple[dict[str, Any], int]:
        try:
            normalized = storage_service.normalize_doi(doi)
            logger.info("🧾 请求PDF总结: %s", normalized or doi)
            if OpenAI is None:
                return {"error": "OpenAI SDK 不可用"}, 503
            pdf_path = self._ensure_local_pdf(doi=normalized, logger=logger)
            if not pdf_path:
                return {"error": f"PDF文件不存在: {normalized or doi}"}, 404

            full_text = self._extract_pdf_body(
                pdf_path=pdf_path,
                logger=logger,
                max_pages=self._max_pdf_pages,
                exclude_references=True,
            )
            if not full_text or str(full_text).startswith("[错误]"):
                return {"error": "未能提取到PDF正文或文件为扫描版"}, 500
            if len(full_text) > 12000:
                full_text = full_text[:12000]

            prompt = (
                "请对以下文献内容生成一段更详细的中文总结，突出研究目的、方法、关键结果、数据或数值结论、局限与结论，"
                "长度控制在 260-420 字，不要加入参考文献列表，不要附加doi或外链。\n\n"
                f"{full_text}"
            )

            client = OpenAI(api_key=self._openai_api_key, base_url=self._openai_base_url)
            resp = client.chat.completions.create(
                model=self._openai_model,
                messages=[
                    {"role": "system", "content": "你是一名材料领域文献速读助手，擅长用中文提炼论文要点。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=650,
            )
            summary = str(resp.choices[0].message.content or "").strip()
            logger.info("✅ PDF总结生成完成")
            return {"doi": normalized, "summary": summary}, 200
        except Exception as exc:
            logger.error("❌ PDF总结失败: %s", exc)
            return {"error": f"总结失败: {str(exc)}"}, 500

    def extract_pdf_text(self, doi: str, logger: Any) -> tuple[dict[str, Any], int]:
        try:
            normalized = storage_service.normalize_doi(doi)
            logger.info("📖 提取PDF文本: %s", normalized or doi)
            pdf_path = self._ensure_local_pdf(doi=normalized, logger=logger)
            if not pdf_path:
                return {"error": f"PDF文件不存在: {normalized or doi}"}, 404

            full_text = self._extract_pdf_body(
                pdf_path=pdf_path,
                logger=logger,
                max_pages=50,
                exclude_references=True,
            )
            if str(full_text).startswith("[错误]"):
                return {"error": full_text}, 500

            paragraphs = self._segment_paragraphs(str(full_text or ""))
            logger.info("✅ 提取完成，共 %s 段", len(paragraphs))
            return {"doi": normalized, "paragraphs": paragraphs, "total": len(paragraphs)}, 200
        except Exception as exc:
            logger.error("❌ 提取PDF文本失败: %s", exc)
            return {"error": f"提取失败: {str(exc)}"}, 500

    def translate(self, *, texts: list[Any], logger: Any) -> tuple[dict[str, Any], int]:
        return documents_translation_service.translate_batch(texts=texts, logger=logger)

    def translate_document(self, *, document_type: str, document_id: str, logger: Any) -> tuple[dict[str, Any], int]:
        segments, payload, status_code = self._prepare_document_translation(
            document_type=document_type,
            document_id=document_id,
            logger=logger,
        )
        if status_code != 200:
            return payload, status_code

        translation_payload, translation_status = self.translate(texts=segments, logger=logger)
        if translation_status != 200 or translation_payload.get("success") is False:
            return translation_payload, translation_status

        translated_segments = [str(item or "").strip() for item in list(translation_payload.get("translations") or [])]
        translated_text = "\n\n".join(item for item in translated_segments if item)
        payload_segment_count = int(payload.get("segment_count") or 0)
        segment_count = payload_segment_count if payload_segment_count > 0 else len(segments)
        translation_data = translation_payload.get("data", {}) if isinstance(translation_payload.get("data"), dict) else {}
        cache_hits = int(translation_payload.get("cache_hits") or translation_data.get("cache_hits") or 0)
        cache_status = self._resolve_document_translation_cache_status(segment_count=segment_count, cache_hits=cache_hits)
        return {
            **payload,
            "success": True,
            "translated_text": translated_text,
            "translations": translated_segments,
            "source_segments": segments,
            "translation_count": len(translated_segments),
            "cache_hits": cache_hits,
            "cache_status": cache_status,
            "provider": translation_data.get("provider") if translation_data else translation_payload.get("provider"),
        }, 200

    def stream_translate_document(self, *, document_type: str, document_id: str, logger: Any) -> dict[str, Any]:
        segments, payload, status_code = self._prepare_document_translation(
            document_type=document_type,
            document_id=document_id,
            logger=logger,
        )
        if status_code != 200:
            return {
                "status_code": status_code,
                "media_type": "application/json",
                "body": payload,
            }

        payload_segment_count = int(payload.get("segment_count") or 0)
        segment_count = payload_segment_count if payload_segment_count > 0 else len(segments)

        def _body_iter() -> Iterable[bytes]:
            translated_segments: list[str] = []
            cache_hits = 0
            provider = ""

            yield self._encode_sse_payload(
                {
                    "type": "start",
                    "document_type": payload.get("document_type"),
                    "document_id": payload.get("document_id"),
                    "title": payload.get("title"),
                    "segment_count": segment_count,
                    "truncated": bool(payload.get("truncated")),
                }
            )

            for index, segment in enumerate(segments):
                translation_payload, translation_status = self.translate(texts=[segment], logger=logger)
                if translation_status != 200 or translation_payload.get("success") is False:
                    yield self._encode_sse_payload(
                        {
                            "type": "error",
                            "index": index,
                            "segment_count": segment_count,
                            "code": str(translation_payload.get("code") or "TRANSLATION_FAILED"),
                            "error": str(translation_payload.get("error") or "translation_failed"),
                            "message": str(translation_payload.get("message") or translation_payload.get("error") or "translation_failed"),
                            "status_code": translation_status,
                        }
                    )
                    return

                translation_data = translation_payload.get("data", {}) if isinstance(translation_payload.get("data"), dict) else {}
                translated_segment = str(next(iter(list(translation_payload.get("translations") or [""])), "") or "").strip()
                segment_cache_hits = int(translation_payload.get("cache_hits") or translation_data.get("cache_hits") or 0)
                cache_hits += 1 if segment_cache_hits > 0 else 0
                provider = str(translation_data.get("provider") or translation_payload.get("provider") or provider)
                translated_segments.append(translated_segment)

                yield self._encode_sse_payload(
                    {
                        "type": "segment",
                        "index": index,
                        "progress": index + 1,
                        "segment_count": segment_count,
                        "translation": translated_segment,
                        "cache_hit": segment_cache_hits > 0,
                    }
                )

            translated_text = "\n\n".join(item for item in translated_segments if item)
            yield self._encode_sse_payload(
                {
                    "type": "done",
                    "success": True,
                    "document_type": payload.get("document_type"),
                    "document_id": payload.get("document_id"),
                    "title": payload.get("title"),
                    "segment_count": segment_count,
                    "translation_count": len(translated_segments),
                    "translated_text": translated_text,
                    "cache_hits": cache_hits,
                    "cache_status": self._resolve_document_translation_cache_status(segment_count=segment_count, cache_hits=cache_hits),
                    "provider": provider,
                    "truncated": bool(payload.get("truncated")),
                }
            )

        return {
            "status_code": 200,
            "headers": {
                "cache-control": "no-cache",
                "x-accel-buffering": "no",
            },
            "media_type": "text/event-stream",
            "body_iter": _body_iter(),
        }

    def check_pdf(self, doi: str) -> tuple[dict[str, Any], int]:
        normalized = storage_service.normalize_doi(doi)
        exists = storage_service.paper_exists(
            doi=normalized,
            papers_dir=self._papers_dir,
            project_root=str(get_settings().local_storage_root),
        )
        filename = storage_service.build_paper_filename(normalized)
        return {"exists": exists, "doi": normalized, "filename": filename if exists else None}, 200

    def literature_content(
        self,
        *,
        doi: str,
        agent: Any,
        logger: Any,
        runtime: PublicServiceRuntime | None = None,
    ) -> tuple[dict[str, Any], int]:
        try:
            if not doi:
                return {"error": "缺少DOI参数"}, 200

            logger.info("📖 获取文献内容: %s", doi)
            if not agent:
                return {
                    "success": False,
                    "error": "知识库运行时未初始化",
                    "code": "RETRIEVAL_RUNTIME_UNAVAILABLE",
                    **self._retrieval_dependency_payload(
                        runtime,
                        mode="required",
                        detail="literature_content requires retrieval metadata runtime",
                    ),
                }, 200

            graph = getattr(agent, "graph", None)
            semantic_expert = getattr(agent, "semantic_expert", None)
            collection = getattr(semantic_expert, "collection", None) if semantic_expert is not None else None

            result = []
            if graph is not None:
                query = """
                MATCH (n)
                WHERE n.doi = $doi OR n.material_name = $doi OR n.material_name CONTAINS $doi
                WITH n,
                  CASE
                    WHEN n.doi = $doi THEN 0
                    WHEN n.material_name = $doi THEN 1
                    ELSE 2
                  END AS match_rank
                RETURN n
                ORDER BY match_rank ASC
                LIMIT 1
                """
                result = graph.run(query, doi=doi).data()

            if not result:
                if collection is not None:
                    try:
                        search_result = collection.get(where={"doi": doi})
                        if search_result and search_result["ids"]:
                            doc_index = 0
                            return {
                                "doi": doi,
                                "title": search_result["metadatas"][doc_index].get("title", "未知标题"),
                                "authors": search_result["metadatas"][doc_index].get("authors", "未知作者"),
                                "journal": search_result["metadatas"][doc_index].get("journal", "未知期刊"),
                                "publication_date": search_result["metadatas"][doc_index].get("date", "未知日期"),
                                "abstract": search_result["metadatas"][doc_index].get("abstract", "无摘要"),
                                "content": search_result["documents"][doc_index],
                            }, 200
                    except Exception as exc:
                        logger.warning("从ChromaDB查询失败: %s", exc)
                return {"error": "未找到该文献"}, 200

            node_data = dict(result[0]["n"])
            return {
                "doi": doi,
                "title": node_data.get("title", f"文献 {doi}"),
                "authors": node_data.get("authors", "未知作者"),
                "journal": node_data.get("journal", "未知期刊"),
                "publication_date": node_data.get("publication_date", "未知日期"),
                "abstract": node_data.get("abstract", "无摘要信息"),
                "content": format_material_content(node_data),
            }, 200
        except Exception as exc:
            logger.error("获取文献内容失败: %s", exc)
            return {"error": f"获取文献内容失败: {str(exc)}"}, 200

    def reference_preview(
        self,
        *,
        dois_text: str,
        doi_list: Iterable[str],
        max_items: Any,
        agent: Any,
        logger: Any,
        runtime: PublicServiceRuntime | None = None,
    ) -> tuple[dict[str, Any], int]:
        doi_list = list(doi_list)
        clamped_max = clamp_preview_max_items(max_items)
        raw_candidates = collect_doi_candidates(dois_text=dois_text, doi_list=doi_list)
        dois = normalize_dois(dois_text=dois_text, doi_list=doi_list, max_items=clamped_max)
        if not dois:
            return {
                "items": [],
                "count": 0,
                "requested_count": 0,
                "max_items": clamped_max,
                "truncated": False,
            }, 200
        items = build_reference_preview_batch(
            dois=dois,
            agent=agent,
            papers_dir=self._papers_dir,
            logger=logger,
        )
        requested_unique_count = len(dict.fromkeys(raw_candidates))
        response = {
            "items": items,
            "count": len(items),
            "requested_count": requested_unique_count,
            "max_items": clamped_max,
            "truncated": requested_unique_count > len(dois),
        }
        if not agent:
            response.update(
                self._retrieval_dependency_payload(
                    runtime,
                    mode="optional",
                    detail="reference_preview metadata enrichment unavailable; pdf existence is still evaluated",
                )
            )
        return response, 200


documents_service = DocumentsService()
