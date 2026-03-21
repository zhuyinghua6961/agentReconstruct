from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.core.config import get_settings
from app.integrations.redis import RedisService
from app.modules.qa_cache.metrics import increment_cache_metric
from app.modules.qa_cache.pdf_cache import build_pdf_text_lock_key, cache_pdf_text, get_cached_pdf_text
from app.modules.qa_cache.singleflight import run_singleflight


def clean_answer_for_frontend(answer: str, *, lightweight: bool = False) -> str:
    if not answer:
        return answer

    answer = re.sub(r"\(do+i\s*=", r"(doi=", answer, flags=re.IGNORECASE)
    answer = re.sub(r"\bdo+i\s*=", r"doi=", answer, flags=re.IGNORECASE)
    answer = re.sub(r"\(d[0o]+i+\s*=", r"(doi=", answer, flags=re.IGNORECASE)
    answer = re.sub(r"\bd[0o]+i+\s*=", r"doi=", answer, flags=re.IGNORECASE)
    answer = re.sub(r"\n(?:##\s*)?参考文献[\s\S]*$", "", answer, flags=re.IGNORECASE)
    answer = re.sub(r"^\s*📄\s*查看原文\s*$", "", answer, flags=re.MULTILINE)
    answer = re.sub(r"📄\s*查看原文", "", answer)
    answer = re.sub(r"·\s*查看原文", "", answer)
    answer = re.sub(r"\(\s*\)", "", answer)
    answer = re.sub(r"\[\s*\]", "", answer)
    answer = re.sub(r"[ \t]+", " ", answer)
    answer = re.sub(r"\n{3,}", "\n\n", answer)
    if lightweight:
        return answer.strip()
    answer = re.sub(r"\s+([，。、；：])", r"\1", answer)
    answer = re.sub(r"^ +| +$", "", answer, flags=re.MULTILINE)
    return answer.strip()


def filter_literature_markers_for_streaming(content: str) -> str:
    literature_marker_pattern = r"\[需要文献支撑:[^\[\]]*(?:\]|$)"
    return re.sub(literature_marker_pattern, "", str(content or ""), flags=re.IGNORECASE)


def log_qa_interaction(
    question: str,
    answer: str,
    query_mode: str | None = None,
    references: list[str] | None = None,
    extra: dict[str, Any] | None = None,
    logger: Any = None,
    log_dir: Path | None = None,
) -> None:
    try:
        if not question and not answer:
            return
        target_dir = Path(log_dir or get_settings().logs_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = target_dir / f"qa_{date_str}.jsonl"
        record: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "question": question,
            "answer": answer,
            "query_mode": query_mode,
            "references": references or [],
        }
        if extra:
            for key, value in extra.items():
                if key not in record:
                    record[key] = value
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        if logger is not None:
            logger.warning("记录问答日志失败: %s", exc)


def load_pdf_content_for_streaming(
    *,
    question: str,
    pdf_path: str,
    executor: Any,
    timeout_error_cls: Any,
    extract_pdf_text_fn: Callable[..., str],
    max_pdf_pages: int,
    logger: Any,
    redis_service: RedisService | None = None,
) -> tuple[str | None, str | None]:
    _ = question
    exclude_refs = True
    cached_text = get_cached_pdf_text(
        redis_service=redis_service,
        pdf_path=pdf_path,
        max_pages=max_pdf_pages,
        exclude_references=exclude_refs,
    )
    if cached_text:
        increment_cache_metric("pdftext", "cache_hit")
        return cached_text, None
    increment_cache_metric("pdftext", "cache_miss")

    def _compute() -> tuple[str | None, str | None]:
        if executor:
            pdf_future = executor.submit(
                extract_pdf_text_fn,
                pdf_path,
                max_pages=max_pdf_pages,
                exclude_references=exclude_refs,
            )
            try:
                pdf_content = pdf_future.result(timeout=20)
            except timeout_error_cls:
                logger.warning("PDF 提取超时，继续使用空内容或仅使用已检索到的部分")
                pdf_content = ""
            except Exception as exc:
                logger.warning("PDF 提取失败: %s", exc)
                return None, f"PDF 提取失败: {exc}"
        else:
            pdf_content = extract_pdf_text_fn(pdf_path, max_pages=max_pdf_pages, exclude_references=exclude_refs)

        if isinstance(pdf_content, str) and pdf_content.startswith("[错误]"):
            return None, pdf_content

        cache_pdf_text(
            redis_service=redis_service,
            pdf_path=pdf_path,
            max_pages=max_pdf_pages,
            exclude_references=exclude_refs,
            content=str(pdf_content or ""),
        )
        return str(pdf_content or ""), None

    if redis_service is None or not redis_service.available:
        return _compute()

    lock_key = build_pdf_text_lock_key(
        redis_service=redis_service,
        pdf_path=pdf_path,
        max_pages=max_pdf_pages,
        exclude_references=exclude_refs,
    )
    if not lock_key:
        return _compute()

    result = run_singleflight(
        redis_service=redis_service,
        lock_key=lock_key,
        namespace="pdftext",
        read_cached_fn=lambda: get_cached_pdf_text(
            redis_service=redis_service,
            pdf_path=pdf_path,
            max_pages=max_pdf_pages,
            exclude_references=exclude_refs,
        ),
        compute_fn=_compute,
    )
    if isinstance(result, tuple):
        return result
    return str(result or ""), None
