from __future__ import annotations

import json
import logging
from typing import Any

from app.modules.documents.schemas import PatentOriginalManifest, PatentOriginalResolvedSection
from app.integrations.storage.factory import get_storage_backend
from app.integrations.storage.local import LocalStorageBackend
from app.modules.storage.service import storage_service


class PatentOriginalStoreError(RuntimeError):
    pass


class PatentOriginalNotFoundError(PatentOriginalStoreError):
    pass


class PatentOriginalUnavailableError(PatentOriginalStoreError):
    pass


class PatentOriginalStoreBackendError(PatentOriginalStoreError):
    pass


_LOGGER = logging.getLogger("public_service.documents.patent_original_store")


def _record_metric(metrics: Any | None, name: str, **labels: Any) -> None:
    if metrics is None:
        try:
            from app.modules.qa_cache.metrics import increment_cache_metric

            increment_cache_metric("qa_original", name)
        except Exception:
            pass
        _LOGGER.info("qa_original_metric name=%s labels=%s", name, labels)
        return
    for method_name in ("increment", "inc", "record"):
        method = getattr(metrics, method_name, None)
        if not callable(method):
            continue
        try:
            method(name, **labels)
            return
        except TypeError:
            try:
                method(name, labels)
                return
            except TypeError:
                continue
    counter = getattr(metrics, "counter", None)
    if callable(counter):
        try:
            metric = counter(name, **labels)
            inc = getattr(metric, "inc", None)
            if callable(inc):
                inc()
                return
        except TypeError:
            pass
    if callable(metrics):
        try:
            metrics(name, **labels)
        except TypeError:
            metrics(name, labels)


def _source_family_for_object(object_name: str) -> str:
    lower = str(object_name or "").lower()
    if lower.endswith("/structured/tables.json") or "/structured/tables" in lower:
        return "patent_table"
    if "/fulltext/" in lower or lower.endswith(".pdf"):
        return "patent_fulltext"
    return "patent_structured"


class PatentOriginalStore:
    def __init__(
        self,
        *,
        backend: Any | None = None,
        project_root: str | None = None,
        metrics: Any | None = None,
        strict_minio_only: bool | None = None,
    ) -> None:
        self._backend = backend
        self._project_root = project_root
        self._metrics = metrics
        self._strict_minio_only = self._resolve_strict_minio_only() if strict_minio_only is None else bool(strict_minio_only)

    @staticmethod
    def _resolve_strict_minio_only() -> bool:
        import os

        raw = str(os.getenv("QA_ORIGINAL_MINIO_ONLY", "true")).strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def _resolve_backend(self) -> Any:
        if self._backend is not None:
            return self._backend
        return get_storage_backend(project_root=self._project_root)

    def load_manifest(self, canonical_patent_id: str) -> PatentOriginalManifest:
        object_name = storage_service.build_patent_original_manifest_object_name(canonical_patent_id)
        payload = self._read_json_object(object_name)
        if not isinstance(payload, dict):
            _record_metric(
                self._metrics,
                "qa_original_minio_read_failed_total",
                service="public-service",
                source_family="patent_structured",
                result="failure",
                reason="manifest_unavailable",
            )
            raise PatentOriginalNotFoundError(f"manifest not found for {canonical_patent_id}")
        _record_metric(
            self._metrics,
            "qa_original_minio_read_total",
            service="public-service",
            source_family="patent_structured",
            result="success",
        )
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
            backend = self._resolve_backend()
            if self._strict_minio_only and isinstance(backend, LocalStorageBackend):
                _record_metric(
                    self._metrics,
                    "qa_original_minio_read_failed_total",
                    service="public-service",
                    source_family=_source_family_for_object(object_name),
                    result="failure",
                    reason="local_backend_disallowed",
                )
                raise PatentOriginalStoreBackendError("local storage backend unavailable in strict MinIO mode")
            reader = getattr(backend, "read_object_bytes", None)
            if callable(reader):
                payload = reader(object_name=object_name)
                return None if payload is None else json.loads(payload.decode("utf-8"))
            path = storage_service._resolve_local_backend_path(backend=backend, object_name=object_name)  # type: ignore[attr-defined]
            if not path.exists() or not path.is_file():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except PatentOriginalStoreBackendError:
            raise
        except Exception as exc:
            _record_metric(
                self._metrics,
                "qa_original_minio_read_failed_total",
                service="public-service",
                source_family=_source_family_for_object(object_name),
                result="failure",
                reason="object_read_failed",
            )
            raise PatentOriginalStoreBackendError(f"failed to read object: {object_name}") from exc

    def _stat_object(self, object_name: str) -> dict[str, Any] | None:
        try:
            backend = self._resolve_backend()
            if self._strict_minio_only and isinstance(backend, LocalStorageBackend):
                _record_metric(
                    self._metrics,
                    "qa_original_minio_read_failed_total",
                    service="public-service",
                    source_family=_source_family_for_object(object_name),
                    result="failure",
                    reason="local_backend_disallowed",
                )
                raise PatentOriginalStoreBackendError("local storage backend unavailable in strict MinIO mode")
            stater = getattr(backend, "stat_object", None)
            if callable(stater):
                stat = stater(object_name=object_name)
            else:
                path = storage_service._resolve_local_backend_path(backend=backend, object_name=object_name)  # type: ignore[attr-defined]
                if not path.exists() or not path.is_file():
                    stat = None
                else:
                    stat = {
                        "object_name": object_name,
                        "etag": "",
                        "size": int(path.stat().st_size),
                    }
        except PatentOriginalStoreBackendError:
            raise
        except Exception as exc:
            _record_metric(
                self._metrics,
                "qa_original_minio_read_failed_total",
                service="public-service",
                source_family=_source_family_for_object(object_name),
                result="failure",
                reason="object_stat_failed",
            )
            raise PatentOriginalStoreBackendError(f"failed to stat object: {object_name}") from exc
        if stat is None:
            _record_metric(
                self._metrics,
                "qa_original_minio_read_failed_total",
                service="public-service",
                source_family=_source_family_for_object(object_name),
                result="failure",
                reason="object_missing",
            )
        else:
            _record_metric(
                self._metrics,
                "qa_original_minio_read_total",
                service="public-service",
                source_family=_source_family_for_object(object_name),
                result="success",
            )
        return stat

    def _load_structured_object(self, object_name: str) -> dict[str, Any]:
        payload = self._read_json_object(object_name)
        if not isinstance(payload, dict):
            raise PatentOriginalUnavailableError(f"structured object unavailable: {object_name}")
        _record_metric(
            self._metrics,
            "qa_original_minio_read_total",
            service="public-service",
            source_family=_source_family_for_object(object_name),
            result="success",
        )
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
            candidates: list[str] = []
            primary_object = str(figure_group.primary_object or "").strip()
            if primary_object:
                candidates.append(primary_object)
            candidates.extend(
                item
                for item in [str(entry).strip() for entry in figure_group.ordered_objects if str(entry or "").strip()]
                if item and item not in candidates
            )
            for object_name in candidates:
                stat = self._stat_object(object_name)
                if stat is None:
                    continue
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
