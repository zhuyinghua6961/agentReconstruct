from __future__ import annotations

from typing import Any

from app.modules.documents.cache import DocumentsTranslationCache
from app.modules.documents.translator import SmartTranslator as LegacySmartTranslator

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


class DocumentsTranslationService:
    def __init__(self, *, translator_cls: Any = LegacySmartTranslator, openai_client_cls: Any = OpenAI) -> None:
        self._translator_cls = translator_cls
        self._openai_client_cls = openai_client_cls
        self._translator: Any | None = None

    def translator(self):
        if self._translator is None:
            self._translator = self._translator_cls(self._openai_client_cls)
            if getattr(self._translator, "cache", None) is not None and not isinstance(
                self._translator.cache,
                DocumentsTranslationCache,
            ):
                wrapped_cache = DocumentsTranslationCache()
                wrapped_cache.cache = dict(getattr(self._translator.cache, "cache", {}))
                self._translator.cache = wrapped_cache
        return self._translator

    def translate_batch(
        self,
        *,
        texts: list[Any],
        logger: Any,
        profile: str = "snippet",
        chunk_indexes: list[int] | None = None,
        chunk_count: int | None = None,
    ) -> tuple[dict[str, Any], int]:
        if not isinstance(texts, list) or len(texts) == 0:
            return {"success": False, "error": "invalid_texts", "code": "INVALID_ARGUMENT"}, 400

        translator = self.translator()
        if not getattr(translator, "enabled", False):
            return {"success": False, "error": "translation_disabled", "code": "TRANSLATION_DISABLED"}, 503

        normalized_profile = str(profile or "snippet").strip().lower() or "snippet"
        translations: list[str] = []
        failures: list[dict[str, Any]] = []
        cache_hits = 0
        non_empty_count = 0
        failed_non_empty_count = 0

        for index, text in enumerate(texts):
            normalized = str(text or "")
            if not normalized.strip():
                translations.append("")
                continue
            non_empty_count += 1
            cached = translator.cache.get(normalized, profile=normalized_profile)
            if cached:
                cache_hits += 1
                translations.append(cached)
                continue
            chunk_index = None
            if chunk_indexes is not None and index < len(chunk_indexes):
                chunk_index = int(chunk_indexes[index])
            translated = translator.translate(
                normalized,
                show_progress=False,
                profile=normalized_profile,
                chunk_index=chunk_index,
                chunk_count=chunk_count,
            )
            translated_text = str(translated or "")
            if translated_text.startswith("❌"):
                failed_non_empty_count += 1
                failures.append({"index": index, "error": translated_text[:500]})
                translations.append("")
                continue
            translations.append(translated_text)

        if non_empty_count > 0 and failed_non_empty_count >= non_empty_count:
            logger.error("translation failed for all non-empty segments")
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
                "provider": translator.provider,
            },
            "translations": translations,
            "count": len(translations),
            "cache_hits": cache_hits,
        }
        if failures:
            payload["data"]["failed_count"] = failed_non_empty_count
            payload["data"]["failures"] = failures
            logger.warning(
                "document_translation partial_segment_failures failed_count=%s non_empty_count=%s total_count=%s failure_indexes=%s",
                failed_non_empty_count,
                non_empty_count,
                len(texts),
                [item.get("index") for item in failures[:20]],
            )
        return payload, 200


documents_translation_service = DocumentsTranslationService()
