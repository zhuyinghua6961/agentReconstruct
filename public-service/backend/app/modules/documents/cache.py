from __future__ import annotations

from app.modules.documents.translation_cache_impl import TranslationCache


class DocumentsTranslationCache(TranslationCache):
    """Compatibility wrapper so the documents module owns the cache surface."""
