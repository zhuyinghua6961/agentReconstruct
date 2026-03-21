from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Set

from app.modules.qa_pdf.engine import answer_from_pdf as answer_from_pdf_impl
from app.modules.qa_pdf.pdf_extractor import (
    exclude_references_section as exclude_references_section_impl,
    extract_pdf_text as extract_pdf_text_impl,
)
from app.modules.qa_pdf.truncation import smart_truncate_pdf_content as smart_truncate_pdf_content_impl


@dataclass
class WebPdfBindings:
    allowed_file: Callable[[str], bool]
    extract_pdf_text: Callable[..., str]
    answer_from_pdf: Callable[..., Any]


def build_web_pdf_bindings(
    *,
    allowed_extensions: Set[str],
    pdf_support: bool,
    fitz_module: Any,
    logger: Any,
    traceback_module: Any,
    max_pdf_chars: int,
    get_agent_llm_fn: Any,
) -> WebPdfBindings:
    def allowed_file(filename: str) -> bool:
        if "." not in str(filename or ""):
            return False
        return filename.rsplit(".", 1)[1].lower() in allowed_extensions

    def extract_pdf_text(pdf_path: str, *, max_pages: int = 50, exclude_references: bool = True) -> str:
        return extract_pdf_text_impl(
            pdf_path,
            max_pages=max_pages,
            exclude_references=exclude_references,
            pdf_support=pdf_support,
            fitz_module=fitz_module,
            logger=logger,
            traceback_module=traceback_module,
            exclude_references_section_fn=exclude_references_section_impl,
        )

    def answer_from_pdf(
        question: str,
        pdf_content: str,
        *,
        kb_verification: dict[str, Any] | None = None,
        stream: bool = False,
        first_token_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> Any:
        llm = get_agent_llm_fn() if callable(get_agent_llm_fn) else None
        return answer_from_pdf_impl(
            question,
            pdf_content,
            llm=llm,
            max_pdf_chars=max_pdf_chars,
            smart_truncate_fn=lambda content, max_chars, **kwargs: smart_truncate_pdf_content_impl(
                content,
                max_chars,
                logger=logger,
                **kwargs,
            ),
            logger=logger,
            traceback_module=traceback_module,
            kb_verification=kb_verification,
            stream=stream,
            first_token_timeout_sec=first_token_timeout_sec,
            is_cancelled=is_cancelled,
        )

    return WebPdfBindings(
        allowed_file=allowed_file,
        extract_pdf_text=extract_pdf_text,
        answer_from_pdf=answer_from_pdf,
    )
