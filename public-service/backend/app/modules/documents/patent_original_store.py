from __future__ import annotations

from typing import Any

from app.modules.documents.schemas import PatentOriginalManifest, PatentOriginalResolvedSection
from app.modules.storage.service import storage_service


class PatentOriginalStoreError(RuntimeError):
    pass


class PatentOriginalNotFoundError(PatentOriginalStoreError):
    pass


class PatentOriginalUnavailableError(PatentOriginalStoreError):
    pass


class PatentOriginalStoreBackendError(PatentOriginalStoreError):
    pass


class PatentOriginalStore:
    def __init__(self, *, backend: Any | None = None, project_root: str | None = None) -> None:
        self._backend = backend
        self._project_root = project_root

    def load_manifest(self, canonical_patent_id: str) -> PatentOriginalManifest:
        object_name = storage_service.build_patent_original_manifest_object_name(canonical_patent_id)
        payload = self._read_json_object(object_name)
        if not isinstance(payload, dict):
            raise PatentOriginalNotFoundError(f"manifest not found for {canonical_patent_id}")
        return PatentOriginalManifest.model_validate(payload)

    def get_original_version(self, canonical_patent_id: str) -> str:
        return self.load_manifest(canonical_patent_id).original_version

    def resolve_section(
        self,
        *,
        canonical_patent_id: str,
        section: str,
        claim_number: int | None = None,
        paragraph_id: str | None = None,
        manifest: PatentOriginalManifest | None = None,
    ) -> PatentOriginalResolvedSection:
        active_manifest = manifest or self.load_manifest(canonical_patent_id)
        normalized_section = str(section or "").strip().lower()
        if normalized_section == "claim":
            return self._resolve_claim(active_manifest, claim_number=claim_number)
        if normalized_section == "description":
            return self._resolve_description(active_manifest, paragraph_id=paragraph_id)
        if normalized_section == "abstract":
            return self._resolve_abstract(active_manifest)
        if normalized_section == "figure":
            return self._resolve_figure(active_manifest)
        if normalized_section == "fulltext":
            return self._resolve_fulltext(active_manifest)
        raise PatentOriginalUnavailableError(f"unsupported section: {section}")

    def _read_json_object(self, object_name: str) -> dict[str, Any] | list[Any] | None:
        try:
            return storage_service.read_json_object(
                object_name=object_name,
                project_root=self._project_root,
                backend=self._backend,
            )
        except Exception as exc:
            raise PatentOriginalStoreBackendError(f"failed to read object: {object_name}") from exc

    def _stat_object(self, object_name: str) -> dict[str, Any] | None:
        try:
            return storage_service.stat_object(
                object_name=object_name,
                project_root=self._project_root,
                backend=self._backend,
            )
        except Exception as exc:
            raise PatentOriginalStoreBackendError(f"failed to stat object: {object_name}") from exc

    def _load_structured_object(self, object_name: str) -> dict[str, Any]:
        payload = self._read_json_object(object_name)
        if not isinstance(payload, dict):
            raise PatentOriginalUnavailableError(f"structured object unavailable: {object_name}")
        return payload

    def _resolve_claim(self, manifest: PatentOriginalManifest, *, claim_number: int | None) -> PatentOriginalResolvedSection:
        object_name = str(manifest.objects.structured.get("claims") or "").strip()
        payload = self._load_structured_object(object_name)
        claims = list(payload.get("claims") or [])
        matched = next(
            (
                item
                for item in claims
                if isinstance(item, dict) and int(item.get("claim_number") or 0) == int(claim_number or 0)
            ),
            None,
        )
        return PatentOriginalResolvedSection(
            canonical_patent_id=manifest.canonical_patent_id,
            section="claim",
            section_label=str((matched or {}).get("label") or payload.get("section_label") or "权利要求"),
            original_version=manifest.original_version,
            content=matched if matched is not None else payload,
            anchor_hit=matched is not None,
            claim_number=claim_number if matched is not None else None,
        )

    def _resolve_description(
        self,
        manifest: PatentOriginalManifest,
        *,
        paragraph_id: str | None,
    ) -> PatentOriginalResolvedSection:
        object_name = str(manifest.objects.structured.get("description") or "").strip()
        payload = self._load_structured_object(object_name)
        normalized_paragraph_id = str(paragraph_id or "").strip()
        paragraphs = list(payload.get("paragraphs") or [])
        matched = next(
            (
                item
                for item in paragraphs
                if isinstance(item, dict) and str(item.get("paragraph_id") or "").strip() == normalized_paragraph_id
            ),
            None,
        )
        return PatentOriginalResolvedSection(
            canonical_patent_id=manifest.canonical_patent_id,
            section="description",
            section_label=str((matched or {}).get("label") or payload.get("section_label") or "说明书"),
            original_version=manifest.original_version,
            content=matched if matched is not None else payload,
            anchor_hit=matched is not None,
            paragraph_id=normalized_paragraph_id if matched is not None else None,
        )

    def _resolve_abstract(self, manifest: PatentOriginalManifest) -> PatentOriginalResolvedSection:
        object_name = str(manifest.objects.structured.get("bibliography") or "").strip()
        payload = self._load_structured_object(object_name)
        return PatentOriginalResolvedSection(
            canonical_patent_id=manifest.canonical_patent_id,
            section="abstract",
            section_label="摘要",
            original_version=manifest.original_version,
            content=payload,
        )

    def _resolve_figure(self, manifest: PatentOriginalManifest) -> PatentOriginalResolvedSection:
        for figure_source in ("summary", "fulltext"):
            figure_group = manifest.objects.figures.get(figure_source)
            if figure_group is None:
                continue
            object_name = str(figure_group.primary_object or "").strip()
            if not object_name:
                ordered = [str(item).strip() for item in figure_group.ordered_objects if str(item or "").strip()]
                object_name = ordered[0] if ordered else ""
            if not object_name:
                continue
            stat = self._stat_object(object_name)
            if stat is None:
                raise PatentOriginalUnavailableError(
                    f"figure object unavailable for {manifest.canonical_patent_id}: {object_name}"
                )
            return PatentOriginalResolvedSection(
                canonical_patent_id=manifest.canonical_patent_id,
                section="figure",
                section_label="附图",
                original_version=manifest.original_version,
                anchor_hit=True,
                figure_source=figure_source,
                served_object_key=object_name,
                object_key=object_name,
                media_type=str(stat.get("content_type") or "application/octet-stream"),
            )
        raise PatentOriginalUnavailableError(f"figure object unavailable for {manifest.canonical_patent_id}")

    def _resolve_fulltext(self, manifest: PatentOriginalManifest) -> PatentOriginalResolvedSection:
        object_name = str(manifest.objects.fulltext_pdf or "").strip()
        if not object_name:
            raise PatentOriginalUnavailableError(f"fulltext pdf unavailable for {manifest.canonical_patent_id}")
        stat = self._stat_object(object_name)
        if stat is None:
            raise PatentOriginalUnavailableError(f"fulltext pdf unavailable for {manifest.canonical_patent_id}")
        return PatentOriginalResolvedSection(
            canonical_patent_id=manifest.canonical_patent_id,
            section="fulltext",
            section_label="全文",
            original_version=manifest.original_version,
            object_key=object_name,
            media_type=str(stat.get("content_type") or "application/pdf"),
        )
