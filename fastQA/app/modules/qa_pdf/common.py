from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable, Iterable, List, Optional


class StreamCancelledError(RuntimeError):
    pass


def _is_cancelled(is_cancelled: Optional[Callable[[], bool]]) -> bool:
    if is_cancelled is None:
        return False
    try:
        return bool(is_cancelled())
    except Exception:
        return False


def raise_if_cancelled(is_cancelled: Optional[Callable[[], bool]]) -> None:
    if _is_cancelled(is_cancelled):
        raise StreamCancelledError("ask-stream request cancelled")


@dataclass
class IncrementalCleanState:
    accumulated_raw: str = ""
    accumulated_cleaned: str = ""
    last_emit_at: float = 0.0
    pending_started_at: float = 0.0


def incremental_clean_events_for_piece(
    piece: str,
    *,
    state: IncrementalCleanState,
    clean_answer_for_frontend: Callable[[str], str],
    filter_literature_markers_for_streaming: Callable[[str], str],
    sse_event: Callable[[dict], str],
) -> List[str]:
    if not piece:
        return []

    state.accumulated_raw += piece
    try:
        cleaned_full = clean_answer_for_frontend(state.accumulated_raw, lightweight=True)
    except TypeError:
        cleaned_full = clean_answer_for_frontend(state.accumulated_raw)
    new_cleaned_content = cleaned_full[len(state.accumulated_cleaned) :]
    state.accumulated_cleaned = cleaned_full

    if not new_cleaned_content:
        return []

    filtered_chunk = filter_literature_markers_for_streaming(new_cleaned_content)
    if not filtered_chunk:
        return []

    return [sse_event({"type": "content", "content": filtered_chunk})]


def extract_doi_from_pdf_filename(pdf_path: Optional[str]) -> Optional[str]:
    if not pdf_path:
        return None
    filename = Path(pdf_path).stem
    doi_match = re.search(r"10\.\d+/[^\s_]+", filename)
    if not doi_match:
        return None
    return doi_match.group(0).replace("_", "/")


def build_pdf_links(references: Iterable[str]) -> List[dict]:
    return [{"doi": doi, "pdf_url": f"/api/v1/view_pdf/{doi}"} for doi in references]


def _normalize_used_files(used_files: Iterable[dict] | None) -> List[dict]:
    if not isinstance(used_files, (list, tuple)):
        return []
    result: List[dict] = []
    for item in used_files:
        if not isinstance(item, dict):
            continue
        try:
            parsed_id = int(item.get("file_id"))
        except (TypeError, ValueError):
            parsed_id = 0
        try:
            parsed_no = int(item.get("file_no"))
        except (TypeError, ValueError):
            parsed_no = 0
        result.append(
            {
                "file_id": parsed_id,
                "file_no": parsed_no,
                "file_type": str(item.get("file_type") or "").strip(),
                "file_name": str(item.get("file_name") or "").strip(),
                "selected_reason": str(item.get("selected_reason") or "").strip(),
                "source": str(item.get("source") or "").strip(),
            }
        )
    return result


def build_done_event_payload(
    references: Iterable[str],
    max_refs: int = 15,
    *,
    route: str = "",
    used_files: Iterable[dict] | None = None,
    timings: dict | None = None,
    trace_id: str = "",
    file_selection: dict | None = None,
) -> dict:
    unique_refs: List[str] = []
    seen = set()
    for item in references:
        doi = str(item or "").strip()
        if not doi or doi in seen:
            continue
        seen.add(doi)
        unique_refs.append(doi)
        if len(unique_refs) >= max_refs:
            break

    return {
        "type": "done",
        "references": unique_refs,
        "pdf_links": build_pdf_links(unique_refs),
        "route": str(route or "").strip(),
        "used_files": _normalize_used_files(used_files),
        "timings": timings if isinstance(timings, dict) else {},
        "trace_id": str(trace_id or "").strip(),
        "file_selection": file_selection if isinstance(file_selection, dict) else {},
    }
