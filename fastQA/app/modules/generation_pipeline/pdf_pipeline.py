from __future__ import annotations

import glob
from pathlib import Path
from typing import Any, Callable

from app.modules.storage.paper_storage import ensure_local_paper_pdf


def find_pdf_path(*, doi: str, papers_dir: str | Path, logger: Any) -> str | None:
    base_dir = Path(papers_dir).resolve()
    doi_clean = str(doi or "").strip()
    if not doi_clean:
        return None

    resolved = ensure_local_paper_pdf(doi=doi_clean, papers_dir=base_dir, logger=logger)
    if resolved is not None:
        logger.debug("stage3 found pdf via storage=%s", resolved.name)
        return str(resolved)

    possible_names = [
        f"{doi_clean}.pdf",
        doi_clean.replace("/", "_") + ".pdf",
    ]
    for filename in possible_names:
        candidate = base_dir / filename
        if candidate.exists():
            logger.debug("stage3 found pdf=%s", candidate.name)
            return str(candidate)

    parts = doi_clean.split("/")
    if len(parts) >= 2:
        pattern = f"{parts[0]}_{parts[-1]}*.pdf"
        matches = sorted(glob.glob(str(base_dir / pattern)))
        if matches:
            logger.debug("stage3 found pdf via glob=%s", Path(matches[0]).name)
            return str(Path(matches[0]).resolve())

    logger.debug("stage3 missing pdf for doi=%s", doi_clean)
    return None


def extract_chunks_from_pdf(*, pdf_path: str, doi: str, max_chunks: int, logger: Any) -> list[dict[str, Any]]:
    try:
        import fitz
    except Exception:
        logger.warning("stage3 fitz unavailable; skip pdf extraction")
        return []

    chunks: list[dict[str, Any]] = []
    current_chunk = ""
    current_chars = 0
    chunk_id = 0
    try:
        doc = fitz.open(pdf_path)
        max_pages = min(getattr(doc, "page_count", 0) or 0, 15)
        for page_num in range(max_pages):
            page = doc[page_num]
            text = str(page.get_text() or "")
            if not text.strip():
                continue
            if page_num == 0:
                if len(text) <= 1500:
                    continue
                text = text[1500:]

            for paragraph in text.split("\n\n"):
                paragraph = paragraph.strip()
                if len(paragraph) < 50:
                    continue
                if current_chunk and current_chars + len(paragraph) > 800:
                    chunks.append(
                        {
                            "doi": doi,
                            "page": page_num + 1,
                            "chunk_id": chunk_id,
                            "chunk_type": "paragraph",
                            "text": current_chunk.strip(),
                            "word_count": len(current_chunk.split()),
                        }
                    )
                    if len(chunks) >= max_chunks:
                        break
                    chunk_id += 1
                    current_chunk = paragraph
                    current_chars = len(paragraph)
                    continue

                current_chunk = f"{current_chunk}\n\n{paragraph}".strip() if current_chunk else paragraph
                current_chars += len(paragraph)
            if len(chunks) >= max_chunks:
                break

        close = getattr(doc, "close", None)
        if callable(close):
            close()
    except Exception as exc:
        logger.warning("stage3 pdf extraction failed: %s", exc)
        return []

    if current_chunk and len(chunks) < max_chunks:
        chunks.append(
            {
                "doi": doi,
                "page": 1,
                "chunk_id": chunk_id,
                "chunk_type": "paragraph",
                "text": current_chunk.strip(),
                "word_count": len(current_chunk.split()),
            }
        )
    return chunks


def stage3_load_pdf_chunks(
    *,
    dois: list[str],
    papers_dir: str | Path,
    max_chunks_per_doi: int,
    logger: Any,
    should_cancel: Callable[[], bool] | None = None,
    find_pdf_path_fn: Callable[..., str | None] | None = None,
    extract_chunks_fn: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    def _cancelled() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            return False

    finder = find_pdf_path_fn or find_pdf_path
    extractor = extract_chunks_fn or extract_chunks_from_pdf
    doi_to_chunks: dict[str, list[dict[str, Any]]] = {}
    missing_dois: list[str] = []
    total_chunks = 0
    logger.info(
        "stage3 pdf loading start requested_dois=%s max_chunks_per_doi=%s papers_dir=%s",
        len(dois),
        max_chunks_per_doi,
        Path(papers_dir).resolve(),
    )
    for doi in dois:
        if _cancelled():
            logger.info("stage3 cancelled before pdf loading completes")
            break
        pdf_path = finder(doi=doi, papers_dir=papers_dir, logger=logger)
        if not pdf_path:
            missing_dois.append(str(doi))
            logger.info("stage3 missing pdf doi=%s", doi)
            continue
        if _cancelled():
            logger.info("stage3 cancelled before extracting chunks")
            break
        chunks = extractor(pdf_path=pdf_path, doi=doi, max_chunks=max_chunks_per_doi, logger=logger)
        if chunks:
            doi_to_chunks[str(doi)] = list(chunks)
            total_chunks += len(chunks)
            logger.info(
                "stage3 loaded doi=%s chunk_count=%s pdf=%s",
                doi,
                len(chunks),
                Path(pdf_path).name,
            )
            continue
        logger.warning("stage3 extracted zero chunks doi=%s pdf=%s", doi, Path(pdf_path).name)
    logger.info(
        "stage3 pdf loading finished source_count=%s total_chunks=%s missing_pdf_count=%s missing_pdf_sample=%s",
        len(doi_to_chunks),
        total_chunks,
        len(missing_dois),
        missing_dois[:10],
    )
    return doi_to_chunks


__all__ = ["extract_chunks_from_pdf", "find_pdf_path", "stage3_load_pdf_chunks"]
