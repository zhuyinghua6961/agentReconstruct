from __future__ import annotations

from app.modules.documents.translation_cache_impl import TranslationCache


class DocumentsTranslationCache(TranslationCache):
    """Compatibility wrapper so the documents module owns the cache surface."""


def build_patent_original_cache_key(
    *,
    canonical_patent_id: str,
    section: str,
    claim_number: int | None,
    paragraph_id: str | None,
    response_format: str,
    original_version: str,
) -> str:
    anchor = f"claim:{claim_number}" if claim_number is not None else f"paragraph:{paragraph_id}" if paragraph_id else "section"
    return (
        f"patent-original:{canonical_patent_id}:{section}:{anchor}:{response_format}:{original_version}"
    )


def build_patent_original_etag(*, original_version: str) -> str:
    return f'"patent-original:{original_version}"'


def build_patent_original_cache_control() -> str:
    return "public, max-age=300"
