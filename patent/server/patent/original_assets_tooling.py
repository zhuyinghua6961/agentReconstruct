from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from html import escape, unescape
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_CLAIM_DIV_RE = re.compile(r"<div[^>]*num=[\"']?(?P<num>\d+)[\"']?[^>]*>(?P<html>.*?)</div>", re.DOTALL | re.IGNORECASE)
_PARAGRAPH_RE = re.compile(r"<b class=\"d_n\">\[(?P<num>\d+)\]</b>(?P<html>.*?)(?=<b class=\"d_n\">\[\d+\]</b>|$)", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_LOCAL_IMAGE_RE = re.compile(r"<img[^>]+src=\"(?P<src>[^\"]+)\"", re.IGNORECASE)


class UploadTarget(Protocol):
    def object_exists(self, *, object_name: str) -> bool:
        ...

    def upload_bytes(self, *, object_name: str, payload: bytes, content_type: str) -> None:
        ...

    def upload_file(self, *, object_name: str, source_path: str, content_type: str) -> None:
        ...

    def read_object_bytes(self, *, object_name: str) -> bytes | None:
        ...


@dataclass(frozen=True)
class PatentOriginalUploadSpec:
    object_name: str
    content_type: str
    content_bytes: bytes | None = None
    source_path: str | None = None

    def read_bytes(self) -> bytes:
        if self.content_bytes is not None:
            return self.content_bytes
        if self.source_path:
            return Path(self.source_path).read_bytes()
        raise ValueError(f"upload spec has no payload source: {self.object_name}")


@dataclass(frozen=True)
class PatentOriginalBackfillPlan:
    canonical_patent_id: str
    source_dir: str
    original_version: str
    manifest: dict[str, Any]
    uploads: list[PatentOriginalUploadSpec]


@dataclass(frozen=True)
class PatentOriginalParityReport:
    canonical_patent_id: str
    ok: bool
    missing_manifest: bool
    missing_structured_objects: list[str]
    missing_figure_objects: list[str]
    missing_fulltext_objects: list[str]
    drifted_objects: list[str]


def discover_patent_source_dirs(source_root: str | Path) -> list[Path]:
    root = Path(source_root).resolve()
    discovered = []
    for path in sorted(root.rglob("*")):
        if not path.is_dir():
            continue
        if (path / "权利要求.json").exists() and (path / "说明书.json").exists() and (path / "著录项目.json").exists():
            discovered.append(path)
    return discovered


def build_patent_original_backfill_plan(source_dir: str | Path, *, provider: str) -> PatentOriginalBackfillPlan:
    base_dir = Path(source_dir).resolve()
    canonical_patent_id = base_dir.name.strip().upper()
    prefix = _object_prefix(canonical_patent_id)

    structured_claims = _build_structured_claims(canonical_patent_id, _load_json(base_dir / "权利要求.json"))
    structured_description = _build_structured_description(canonical_patent_id, _load_json(base_dir / "说明书.json"))
    structured_bibliography = _build_structured_bibliography(
        canonical_patent_id,
        _load_json(base_dir / "著录项目.json"),
    )

    summary_figures, fulltext_figures = _discover_figure_paths(base_dir)
    pdf_path = _discover_pdf_path(base_dir, canonical_patent_id)

    uploads: list[PatentOriginalUploadSpec] = [
        PatentOriginalUploadSpec(
            object_name=f"{prefix}/structured/claims.json",
            content_type="application/json",
            content_bytes=_json_bytes(structured_claims),
        ),
        PatentOriginalUploadSpec(
            object_name=f"{prefix}/structured/description.json",
            content_type="application/json",
            content_bytes=_json_bytes(structured_description),
        ),
        PatentOriginalUploadSpec(
            object_name=f"{prefix}/structured/bibliography.json",
            content_type="application/json",
            content_bytes=_json_bytes(structured_bibliography),
        ),
    ]

    uploads.extend(_figure_upload_specs(prefix=prefix, base_dir=base_dir, figure_paths=summary_figures, figure_source="summary"))
    uploads.extend(_figure_upload_specs(prefix=prefix, base_dir=base_dir, figure_paths=fulltext_figures, figure_source="fulltext"))
    if pdf_path is not None:
        uploads.append(
            PatentOriginalUploadSpec(
                object_name=f"{prefix}/fulltext/original.pdf",
                content_type="application/pdf",
                source_path=str(pdf_path),
            )
        )

    manifest = {
        "canonical_patent_id": canonical_patent_id,
        "title": str(structured_bibliography.get("title") or canonical_patent_id),
        "provider": str(provider).strip(),
        "original_version": "",
        "country": str(structured_bibliography["bibliography"].get("country") or ""),
        "kind_code": str(structured_bibliography["bibliography"].get("kind_code") or ""),
        "publication_number": str(structured_bibliography["bibliography"].get("publication_number") or canonical_patent_id),
        "application_number": str(structured_bibliography["bibliography"].get("application_number") or ""),
        "objects": {
            "structured": {
                "claims": f"{prefix}/structured/claims.json",
                "description": f"{prefix}/structured/description.json",
                "bibliography": f"{prefix}/structured/bibliography.json",
            },
            "figures": _build_figure_manifest(
                prefix=prefix,
                base_dir=base_dir,
                summary_figures=summary_figures,
                fulltext_figures=fulltext_figures,
            ),
            "fulltext_pdf": f"{prefix}/fulltext/original.pdf" if pdf_path is not None else None,
        },
        "availability": {
            "claims": bool(structured_claims.get("claims")),
            "description": bool(structured_description.get("paragraphs")),
            "abstract": bool(structured_bibliography.get("abstract_text")),
            "figure": bool(summary_figures or fulltext_figures),
            "fulltext_pdf": pdf_path is not None,
        },
    }
    original_version = _compute_original_version(uploads, manifest_payload=manifest)
    manifest["original_version"] = original_version
    uploads.append(
        PatentOriginalUploadSpec(
            object_name=f"{prefix}/manifest.json",
            content_type="application/json",
            content_bytes=_json_bytes(manifest),
        )
    )

    return PatentOriginalBackfillPlan(
        canonical_patent_id=canonical_patent_id,
        source_dir=str(base_dir),
        original_version=original_version,
        manifest=manifest,
        uploads=uploads,
    )


def check_patent_original_parity(
    plan: PatentOriginalBackfillPlan,
    *,
    existing_object_names: Iterable[str],
    existing_object_bytes: dict[str, bytes] | None = None,
) -> PatentOriginalParityReport:
    existing = {str(item).strip() for item in existing_object_names if str(item).strip()}
    manifest_object = f"{_object_prefix(plan.canonical_patent_id)}/manifest.json"
    structured_objects = list(plan.manifest["objects"]["structured"].values())

    figure_objects: list[str] = []
    for figure_group in dict(plan.manifest["objects"].get("figures") or {}).values():
        if not isinstance(figure_group, dict):
            continue
        figure_objects.extend([str(item).strip() for item in figure_group.get("ordered_objects") or [] if str(item).strip()])

    fulltext_objects = []
    fulltext_pdf = str(plan.manifest["objects"].get("fulltext_pdf") or "").strip()
    if fulltext_pdf:
        fulltext_objects.append(fulltext_pdf)

    missing_structured = [item for item in structured_objects if item not in existing]
    missing_figures = [item for item in figure_objects if item not in existing]
    missing_fulltext = [item for item in fulltext_objects if item not in existing]
    missing_manifest = manifest_object not in existing
    drifted_objects: list[str] = []
    expected_uploads = {item.object_name: item for item in plan.uploads}
    existing_bytes = dict(existing_object_bytes or {})
    for object_name, upload in expected_uploads.items():
        if object_name not in existing:
            continue
        if object_name not in existing_bytes:
            continue
        if bytes(existing_bytes[object_name]) != upload.read_bytes():
            drifted_objects.append(object_name)

    return PatentOriginalParityReport(
        canonical_patent_id=plan.canonical_patent_id,
        ok=not (missing_manifest or missing_structured or missing_figures or missing_fulltext or drifted_objects),
        missing_manifest=missing_manifest,
        missing_structured_objects=missing_structured,
        missing_figure_objects=missing_figures,
        missing_fulltext_objects=missing_fulltext,
        drifted_objects=sorted(drifted_objects),
    )


def upload_patent_original_backfill_plan(
    plan: PatentOriginalBackfillPlan,
    *,
    target: UploadTarget,
    dry_run: bool = False,
    skip_existing: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    uploaded: list[str] = []
    skipped: list[str] = []
    total = len(plan.uploads)
    uploaded_count = 0
    skipped_count = 0
    for index, item in enumerate(plan.uploads, start=1):
        status = "uploaded"
        if skip_existing and _target_object_matches(target, upload=item):
            skipped.append(item.object_name)
            skipped_count += 1
            status = "skipped"
        else:
            uploaded.append(item.object_name)
            uploaded_count += 1
            if not dry_run:
                if item.content_bytes is not None:
                    target.upload_bytes(
                        object_name=item.object_name,
                        payload=item.content_bytes,
                        content_type=item.content_type,
                    )
                elif item.source_path is not None:
                    target.upload_file(
                        object_name=item.object_name,
                        source_path=item.source_path,
                        content_type=item.content_type,
                    )
                else:
                    raise ValueError(f"upload spec has no payload source: {item.object_name}")

        if callable(progress_callback):
            progress_callback(
                {
                    "canonical_patent_id": plan.canonical_patent_id,
                    "object_name": item.object_name,
                    "status": status,
                    "completed": index,
                    "total": total,
                    "uploaded": uploaded_count,
                    "skipped": skipped_count,
                    "dry_run": bool(dry_run),
                }
            )
    return {
        "canonical_patent_id": plan.canonical_patent_id,
        "original_version": plan.original_version,
        "uploaded_objects": uploaded,
        "skipped_objects": skipped,
        "uploaded_count": uploaded_count,
        "skipped_count": skipped_count,
        "dry_run": bool(dry_run),
    }


def _object_prefix(canonical_patent_id: str) -> str:
    return f"patent/originals/{str(canonical_patent_id).strip().upper()}"


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_text(value: str) -> str:
    return _SPACE_RE.sub(" ", unescape(_TAG_RE.sub(" ", str(value or "")))).strip()


def _normalize_html_fragment(value: str) -> str:
    stripped = str(value or "").strip()
    if "<" in stripped and ">" in stripped:
        return stripped
    return f"<p>{escape(_clean_text(stripped))}</p>"


def _build_structured_claims(canonical_patent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = list(payload.get("data") or [])
    claim_blocks = list((data[0] if data else {}).get("claims") or [])
    collected_html = "".join(str(item.get("claim_text") or "") for item in claim_blocks if isinstance(item, dict))

    grouped: dict[int, list[str]] = {}
    for match in _CLAIM_DIV_RE.finditer(collected_html):
        claim_number = int(match.group("num"))
        grouped.setdefault(claim_number, []).append(match.group("html").strip())

    claims = []
    for index, claim_number in enumerate(sorted(grouped), start=1):
        html_value = "".join(grouped[claim_number]).strip()
        claims.append(
            {
                "claim_number": claim_number,
                "label": f"权利要求{index}",
                "text": _clean_text(html_value),
                "html": _normalize_html_fragment(html_value),
            }
        )

    return {
        "canonical_patent_id": canonical_patent_id,
        "section": "claim",
        "section_label": "权利要求",
        "claims": claims,
    }


def _build_structured_description(canonical_patent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = list(payload.get("data") or [])
    descriptions = list((data[0] if data else {}).get("description") or [])
    raw_html = str((descriptions[0] if descriptions else {}).get("text") or "")
    paragraphs = []
    for index, match in enumerate(_PARAGRAPH_RE.finditer(raw_html), start=1):
        paragraph_html = match.group("html").strip()
        paragraphs.append(
            {
                "paragraph_id": f"p-{index:03d}",
                "label": f"段落{index}",
                "text": _clean_text(paragraph_html),
                "html": _normalize_html_fragment(paragraph_html),
            }
        )
    if not paragraphs and _clean_text(raw_html):
        paragraphs.append(
            {
                "paragraph_id": "p-001",
                "label": "段落1",
                "text": _clean_text(raw_html),
                "html": _normalize_html_fragment(raw_html),
            }
        )
    return {
        "canonical_patent_id": canonical_patent_id,
        "section": "description",
        "section_label": "说明书",
        "paragraphs": paragraphs,
    }


def _build_structured_bibliography(canonical_patent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = list(payload.get("data") or [])
    root = data[0] if data else {}
    bib = dict(root.get("bibliographic_data") or {})
    publication_reference = dict(bib.get("publication_reference") or {})
    application_reference = dict(bib.get("application_reference") or {})
    title = _extract_text_from_items(bib.get("invention_title"))
    abstract_text = _extract_text_from_items(bib.get("abstracts"))
    country = str(publication_reference.get("country") or "")
    kind_code = str(publication_reference.get("kind") or "")
    publication_number = str(root.get("pn") or "") or f"{country}{publication_reference.get('doc_number') or ''}{kind_code}"
    application_number = str(application_reference.get("doc_number") or "")
    return {
        "canonical_patent_id": canonical_patent_id,
        "section": "abstract",
        "title": title,
        "abstract_text": abstract_text,
        "abstract_html": _normalize_html_fragment(abstract_text),
        "bibliography": {
            "publication_number": publication_number,
            "application_number": application_number,
            "country": country,
            "kind_code": kind_code,
        },
    }


def _extract_text_from_items(value: Any) -> str:
    items = list(value or [])
    for item in items:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            return str(item.get("text")).strip()
    return ""


def _discover_pdf_path(base_dir: Path, canonical_patent_id: str) -> Path | None:
    preferred = base_dir / f"{canonical_patent_id}.pdf"
    if preferred.exists():
        return preferred
    pdfs = sorted(path for path in base_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
    return pdfs[0] if pdfs else None


def _discover_figure_paths(base_dir: Path) -> tuple[list[Path], list[Path]]:
    summary: list[Path] = []
    fulltext: list[Path] = []

    for path in sorted(base_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        parts = {part.lower() for part in path.parts}
        if {"summary", "summary_figures"} & parts or any("摘要" in part for part in path.parts):
            summary.append(path)
        else:
            fulltext.append(path)

    for candidate in _discover_aux_figure_candidates(base_dir):
        if candidate in summary or candidate in fulltext:
            continue
        fulltext.append(candidate)

    return sorted(summary), sorted(fulltext)


def _discover_aux_figure_candidates(base_dir: Path) -> list[Path]:
    discovered: list[Path] = []
    tables_path = next(iter(sorted(base_dir.glob("*_tables.json"))), None)
    if tables_path is not None:
        for item in json.loads(tables_path.read_text(encoding="utf-8")):
            source_image = str((item or {}).get("_source_image") or "").strip()
            if source_image:
                candidate = _resolve_candidate_path(base_dir, source_image)
                if candidate is not None and _safe_path_exists(candidate):
                    discovered.append(candidate)

    description_path = base_dir / "说明书.json"
    if description_path.exists():
        payload = _load_json(description_path)
        data = list(payload.get("data") or [])
        descriptions = list((data[0] if data else {}).get("description") or [])
        raw_html = str((descriptions[0] if descriptions else {}).get("text") or "")
        for match in _LOCAL_IMAGE_RE.finditer(raw_html):
            candidate = _resolve_candidate_path(base_dir, match.group("src"))
            if candidate is not None and _safe_path_exists(candidate):
                discovered.append(candidate)

    return sorted(set(discovered))


def _figure_upload_specs(
    *,
    prefix: str,
    base_dir: Path,
    figure_paths: list[Path],
    figure_source: str,
) -> list[PatentOriginalUploadSpec]:
    specs: list[PatentOriginalUploadSpec] = []
    for path in sorted(figure_paths, key=lambda item: item.name):
        specs.append(
            PatentOriginalUploadSpec(
                object_name=_build_figure_object_name(
                    prefix=prefix,
                    base_dir=base_dir,
                    figure_path=path,
                    figure_source=figure_source,
                ),
                content_type=_guess_content_type(path),
                source_path=str(path),
            )
        )
    return specs


def _build_figure_manifest(
    *,
    prefix: str,
    base_dir: Path,
    summary_figures: list[Path],
    fulltext_figures: list[Path],
) -> dict[str, Any]:
    figure_manifest: dict[str, Any] = {}
    for source_name, paths in (("summary", summary_figures), ("fulltext", fulltext_figures)):
        if not paths:
            continue
        ordered_objects = [
            _build_figure_object_name(
                prefix=prefix,
                base_dir=base_dir,
                figure_path=path,
                figure_source=source_name,
            )
            for path in sorted(paths, key=lambda item: item.name)
        ]
        figure_manifest[source_name] = {
            "primary_object": ordered_objects[0],
            "ordered_objects": ordered_objects,
        }
    return figure_manifest


def _compute_original_version(uploads: list[PatentOriginalUploadSpec], *, manifest_payload: dict[str, Any] | None = None) -> str:
    digest = hashlib.sha256()
    for item in sorted(uploads, key=lambda entry: entry.object_name):
        digest.update(item.object_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(item.read_bytes()).digest())
        digest.update(b"\0")
    if manifest_payload is not None:
        manifest_for_hash = dict(manifest_payload)
        manifest_for_hash["original_version"] = ""
        digest.update(_json_bytes(manifest_for_hash))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _guess_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".pdf":
        return "application/pdf"
    return "application/octet-stream"


def _target_object_exists(target: UploadTarget, *, object_name: str) -> bool:
    exists = getattr(target, "object_exists", None)
    if not callable(exists):
        return False
    try:
        return bool(exists(object_name=object_name))
    except Exception:
        return False


def _target_object_matches(target: UploadTarget, *, upload: PatentOriginalUploadSpec) -> bool:
    if not _target_object_exists(target, object_name=upload.object_name):
        return False
    reader = getattr(target, "read_object_bytes", None)
    if not callable(reader):
        return True
    try:
        existing = reader(object_name=upload.object_name)
    except Exception:
        return False
    if existing is None:
        return False
    return bytes(existing) == upload.read_bytes()


def _resolve_candidate_path(base_dir: Path, raw_path: str) -> Path | None:
    candidate_text = str(raw_path or "").strip()
    if "://" in candidate_text:
        return None
    candidate = Path(candidate_text)
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    return candidate


def _build_figure_object_name(*, prefix: str, base_dir: Path, figure_path: Path, figure_source: str) -> str:
    path = figure_path.resolve()
    try:
        relative_parts = list(path.relative_to(base_dir.resolve()).parts)
    except ValueError:
        external_hash = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
        relative_parts = ["external", external_hash, figure_path.name]

    while relative_parts and _is_source_marker(relative_parts[0], figure_source):
        relative_parts.pop(0)
    if not relative_parts:
        relative_parts = [figure_path.name]
    return f"{prefix}/figures/{figure_source}/{'/'.join(relative_parts)}"


def _is_source_marker(part: str, figure_source: str) -> bool:
    lowered = str(part or "").lower()
    if figure_source == "summary":
        return lowered in {"summary", "summary_figures"} or ("摘要" in str(part))
    return lowered in {"fulltext", "fulltext_figures"} or ("全文" in str(part))


def _safe_path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False
