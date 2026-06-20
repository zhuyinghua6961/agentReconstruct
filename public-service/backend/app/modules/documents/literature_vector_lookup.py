from __future__ import annotations

from typing import Any

from app.core.runtime import PublicServiceRuntime
from app.modules.retrieval.service import retrieval_service
from app.modules.storage.service import storage_service


def _metadata_value(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _chroma_get_by_doi(collection: Any, doi: str) -> dict[str, Any] | None:
    if collection is None:
        return None
    try:
        result = collection.get(where={"doi": doi}, include=["metadatas", "documents"])
    except Exception:
        return None
    if not isinstance(result, dict) or not result.get("ids"):
        return None
    return result


def resolve_literature_collections(
    *,
    agent: Any,
    runtime: PublicServiceRuntime | None,
) -> tuple[Any | None, Any | None]:
    semantic_expert = getattr(agent, "semantic_expert", None) if agent is not None else None
    fastqa_collection = getattr(semantic_expert, "collection", None) if semantic_expert is not None else None
    if fastqa_collection is None and runtime is not None:
        fastqa_collection = getattr(runtime, "vector_collection", None)

    highthinking_collection = getattr(runtime, "highthinking_chroma", None) if runtime is not None else None
    if highthinking_collection is None and runtime is not None:
        bindings = retrieval_service.build_highthinking_chroma(project_root=str(runtime.settings.data_root))
        highthinking_collection = bindings.collection
        if bindings.collection is not None:
            runtime.highthinking_chroma = bindings.collection

    return fastqa_collection, highthinking_collection


def _build_from_fastqa_result(*, doi: str, result: dict[str, Any]) -> dict[str, Any]:
    metadata = (result.get("metadatas") or [{}])[0] or {}
    documents = result.get("documents") or []
    content = str(documents[0] or "") if documents else ""
    return {
        "doi": doi,
        "title": _metadata_value(metadata, "title", "paper_title") or "未知标题",
        "authors": _metadata_value(metadata, "authors") or "未知作者",
        "journal": _metadata_value(metadata, "journal") or "未知期刊",
        "publication_date": _metadata_value(metadata, "date", "publication_date") or "未知日期",
        "abstract": _metadata_value(metadata, "abstract") or "无摘要",
        "content": content,
        "source": "fastqa_chroma",
    }


def _build_from_highthinking_result(*, doi: str, result: dict[str, Any]) -> dict[str, Any]:
    metadatas = list(result.get("metadatas") or [])
    documents = list(result.get("documents") or [])
    rows: list[tuple[int, str, str]] = []
    title = ""
    for metadata, document in zip(metadatas, documents):
        if not isinstance(metadata, dict):
            continue
        if not title:
            title = _metadata_value(metadata, "title", "paper_title")
        try:
            chunk_index = int(metadata.get("chunk_index") or 0)
        except (TypeError, ValueError):
            chunk_index = 0
        section_name = _metadata_value(metadata, "section_name") or "Section"
        text = str(document or "").strip()
        if text:
            rows.append((chunk_index, section_name, text))

    rows.sort(key=lambda item: item[0])
    content_parts: list[str] = []
    seen_sections: set[str] = set()
    for _, section_name, text in rows:
        if section_name in seen_sections:
            content_parts.append(text)
        else:
            seen_sections.add(section_name)
            content_parts.append(f"## {section_name}\n\n{text}")

    return {
        "doi": doi,
        "title": title or "未知标题",
        "authors": "未知作者",
        "journal": "未知期刊",
        "publication_date": "未知日期",
        "abstract": "无摘要",
        "content": "\n\n".join(content_parts),
        "source": "highthinking_chroma",
    }


def lookup_literature_from_vector_dbs(
    *,
    doi: str,
    fastqa_collection: Any,
    highthinking_collection: Any,
) -> dict[str, Any] | None:
    normalized = storage_service.normalize_doi(doi)
    if not normalized:
        return None

    fastqa_result = _chroma_get_by_doi(fastqa_collection, normalized)
    highthinking_result = _chroma_get_by_doi(highthinking_collection, normalized)

    if fastqa_result and highthinking_result:
        payload = _build_from_fastqa_result(doi=normalized, result=fastqa_result)
        ht_payload = _build_from_highthinking_result(doi=normalized, result=highthinking_result)
        if not payload.get("title") or payload.get("title") == "未知标题":
            payload["title"] = ht_payload.get("title") or payload["title"]
        if len(str(ht_payload.get("content") or "")) > len(str(payload.get("content") or "")):
            payload["content"] = ht_payload.get("content") or payload.get("content") or ""
        payload["source"] = "fastqa_chroma+highthinking_chroma"
        return payload

    if fastqa_result:
        return _build_from_fastqa_result(doi=normalized, result=fastqa_result)
    if highthinking_result:
        return _build_from_highthinking_result(doi=normalized, result=highthinking_result)
    return None
