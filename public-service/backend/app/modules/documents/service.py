from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from app.core.config import get_settings
from app.core.runtime import PublicServiceRuntime
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
        self._openai_api_key = str(os.getenv("OPENAI_API_KEY", "") or "")
        self._openai_base_url = str(os.getenv("OPENAI_BASE_URL", "") or "")

    def _resolve_papers_dir(self) -> Path:
        path = get_settings().papers_dir
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except PermissionError:
            fallback = (Path(tempfile.gettempdir()) / "public-service-papers").resolve()
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def _ensure_local_pdf(self, *, doi: str, logger: Any) -> Path | None:
        return storage_service.ensure_local_paper_pdf(
            doi=doi,
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
            pdf_path = self._ensure_local_pdf(doi=doi, logger=logger)
            if not pdf_path:
                return {"error": f"PDF文件不存在: {doi}"}, 404, None
            return {}, 200, pdf_path
        except Exception as exc:
            return {"error": f"查看PDF失败: {exc}"}, 500, None

    def summarize_pdf(self, doi: str, logger: Any) -> tuple[dict[str, Any], int]:
        try:
            logger.info("🧾 请求PDF总结: %s", doi)
            if OpenAI is None:
                return {"error": "OpenAI SDK 不可用"}, 503
            pdf_path = self._ensure_local_pdf(doi=doi, logger=logger)
            if not pdf_path:
                return {"error": f"PDF文件不存在: {doi}"}, 404

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
                model="deepseek-v3.1",
                messages=[
                    {"role": "system", "content": "你是一名材料领域文献速读助手，擅长用中文提炼论文要点。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=650,
            )
            summary = str(resp.choices[0].message.content or "").strip()
            logger.info("✅ PDF总结生成完成")
            return {"doi": doi, "summary": summary}, 200
        except Exception as exc:
            logger.error("❌ PDF总结失败: %s", exc)
            return {"error": f"总结失败: {str(exc)}"}, 500

    def extract_pdf_text(self, doi: str, logger: Any) -> tuple[dict[str, Any], int]:
        try:
            logger.info("📖 提取PDF文本: %s", doi)
            pdf_path = self._ensure_local_pdf(doi=doi, logger=logger)
            if not pdf_path:
                return {"error": f"PDF文件不存在: {doi}"}, 404

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
            return {"doi": doi, "paragraphs": paragraphs, "total": len(paragraphs)}, 200
        except Exception as exc:
            logger.error("❌ 提取PDF文本失败: %s", exc)
            return {"error": f"提取失败: {str(exc)}"}, 500

    def translate(self, *, texts: list[Any], logger: Any) -> tuple[dict[str, Any], int]:
        return documents_translation_service.translate_batch(texts=texts, logger=logger)

    def check_pdf(self, doi: str) -> tuple[dict[str, Any], int]:
        exists = storage_service.paper_exists(
            doi=doi,
            papers_dir=self._papers_dir,
            project_root=str(get_settings().local_storage_root),
        )
        filename = storage_service.build_paper_filename(doi)
        return {"exists": exists, "doi": doi, "filename": filename if exists else None}, 200

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
