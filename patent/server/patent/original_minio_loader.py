from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.patent.object_reader import ObjectReader, ObjectReaderProtocolError, ObjectReaderUnavailableError
from server.patent.retrieval_models import PatentTableSupplement


_LOGGER = logging.getLogger("patent.original_minio_loader")


def _record_metric(metrics: Any | None, name: str, **labels: Any) -> None:
    if metrics is None:
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


class PatentOriginalMinioLoader:
    def __init__(
        self,
        *,
        reader: ObjectReader,
        bucket: str | None = None,
        archive_root: str | Path | None = None,
        metrics: Any | None = None,
    ) -> None:
        self._reader = reader
        self._bucket = str(bucket or "").strip() or None
        self._archive_root = Path(archive_root).resolve() if archive_root is not None else None
        self._metrics = metrics if metrics is not None else getattr(reader, "_metrics", None)
        self.diagnostics: list[str] = []

    def load_manifest(self, canonical_patent_id: str) -> dict[str, Any] | None:
        object_name = self._manifest_object_name(canonical_patent_id)
        try:
            payload = self._reader.read_object_json(object_name, bucket=self._bucket)
        except (ObjectReaderProtocolError, ObjectReaderUnavailableError, json.JSONDecodeError, ValueError, TypeError):
            self._append_diagnostic("original_manifest_unavailable")
            return None
        if not isinstance(payload, dict):
            self._append_diagnostic("original_manifest_unavailable")
            return None
        return payload

    def load_tables(self, canonical_patent_id: str) -> list[PatentTableSupplement]:
        self.diagnostics = []
        manifest = self.load_manifest(canonical_patent_id)
        if not isinstance(manifest, dict):
            return []
        structured = dict((manifest.get("objects") or {}).get("structured") or {})
        availability = dict(manifest.get("availability") or {})
        tables_ref = str(structured.get("tables") or "").strip()
        if not tables_ref or not bool(availability.get("tables")):
            self._append_diagnostic("tables_unavailable")
            return []
        try:
            payload = self._reader.read_object_json(tables_ref, bucket=self._bucket)
        except (ObjectReaderProtocolError, ObjectReaderUnavailableError, Exception):
            self._append_diagnostic("tables_object_unavailable")
            return []
        if isinstance(payload, dict):
            items = list(payload.get("tables") or payload.get("items") or [])
        elif isinstance(payload, list):
            items = list(payload)
        else:
            self._append_diagnostic("tables_object_unavailable")
            return []
        supplements: list[PatentTableSupplement] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows: list[dict[str, str]] = []
            for row in list(item.get("rows") or []):
                if not isinstance(row, dict):
                    continue
                rows.append({str(key): str(value) for key, value in row.items()})
            if not rows:
                continue
            supplements.append(
                PatentTableSupplement(
                    table_title=str(item.get("table_title") or "").strip(),
                    columns=[str(value) for value in list(item.get("columns") or []) if str(value).strip()],
                    rows=rows,
                    source_image=str(item.get("source_image") or item.get("_source_image") or "").strip() or None,
                )
            )
        _record_metric(
            self._metrics,
            "patent_tables_minio_loaded_total",
            service="patent",
            source_family="patent_table",
            result="success",
        )
        return supplements

    def load_pdf_document(self, canonical_patent_id: str) -> dict[str, Any] | None:
        manifest = self.load_manifest(canonical_patent_id)
        if not isinstance(manifest, dict):
            return None
        objects = dict(manifest.get("objects") or {})
        fulltext_ref = str(objects.get("fulltext_pdf") or "").strip()
        if not fulltext_ref:
            return None
        try:
            bucket = self._bucket or "agentcode"
            stat = self._reader.stat_object(fulltext_ref, bucket=bucket)
            suffix = Path(fulltext_ref).suffix.lower() or ".pdf"
            path = self._reader.materialize_temp(
                f"minio://{bucket}/{fulltext_ref.lstrip('/')}",
                suffix=suffix,
            )
        except Exception:
            return None
        return {
            "path": str(path),
            "filename": Path(fulltext_ref).name or f"{self._normalize_canonical_patent_id(canonical_patent_id)}.pdf",
            "size_bytes": int(stat.size),
        }

    @staticmethod
    def _normalize_canonical_patent_id(canonical_patent_id: str) -> str:
        return str(canonical_patent_id or "").strip().upper()

    def _manifest_object_name(self, canonical_patent_id: str) -> str:
        normalized = self._normalize_canonical_patent_id(canonical_patent_id)
        return f"patent/originals/{normalized}/manifest.json"

    def _append_diagnostic(self, reason: str) -> None:
        self.diagnostics.append(reason)
        _record_metric(
            self._metrics,
            "patent_tables_minio_missing_total",
            service="patent",
            source_family="patent_table",
            result="missing",
            reason=reason,
        )
