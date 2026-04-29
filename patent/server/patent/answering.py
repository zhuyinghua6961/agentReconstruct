from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from server.patent.retrieval_models import PatentEvidence, PatentRetrievalOutcome, PatentTableSupplement
from server.patent.upstream_transport import (
    build_patent_request_timeout,
    describe_patent_transport,
    record_patent_dispatch_error,
    record_patent_dispatch_success,
)

_LOGGER = logging.getLogger("patent.answering")
_PATENT_ID_CITATION_RE = re.compile(r"\(\s*patent_id\s*=\s*([A-Za-z0-9._/\-]+)\s*\)", re.IGNORECASE)
_BACKTICK_CODE_SPAN_RE = re.compile(r"`(?P<body>[^`\n]{1,200})`")
_PATENT_CITATION_LIST_ITEM_RE = re.compile(r"^(?:patent_id\s*=\s*)?([A-Za-z0-9._/\-]+)$", re.IGNORECASE)
_CLAUSE_BOUNDARIES = "\n。！？!?；;，,"
_STREAM_CITATION_TAIL_HOLD = 160


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _truncate(value: str, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def _normalize_patent_id(value: object) -> str:
    return str(value or "").strip().upper()


def _normalize_patent_id_list(values: list[object] | tuple[object, ...] | None) -> list[str]:
    normalized: list[str] = []
    for item in list(values or []):
        patent_id = _normalize_patent_id(item)
        if patent_id and patent_id not in normalized:
            normalized.append(patent_id)
    return normalized


def _resolve_min_distinct_citations(*, context: dict[str, Any], allowed_patent_ids: list[str]) -> int:
    if not allowed_patent_ids:
        return 0

    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    raw_required = context.get("stage4_min_citations_required")
    raw_configured = context.get("stage4_min_citations_configured")
    configured_default = max(1, _coerce_int(raw_configured, 10))
    required = _coerce_int(raw_required, configured_default)
    return max(1, min(required, len(allowed_patent_ids)))


def _extract_content_fragments(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            fragments.extend(_extract_content_fragments(item))
        return fragments
    if isinstance(value, dict):
        fragments: list[str] = []
        text = value.get("text")
        if isinstance(text, str) and text:
            fragments.append(text)
        elif text is not None:
            fragments.extend(_extract_content_fragments(text))
        content = value.get("content")
        if content is not None and content is not value:
            fragments.extend(_extract_content_fragments(content))
        return fragments
    return []


def _extract_stream_fragments(payload: dict[str, Any]) -> list[str]:
    fragments: list[str] = []
    for choice in list(payload.get("choices") or []):
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict):
            fragments.extend(_extract_content_fragments(delta.get("content")))
        message = choice.get("message")
        if isinstance(message, dict):
            fragments.extend(_extract_content_fragments(message.get("content")))
    return [fragment for fragment in fragments if isinstance(fragment, str) and fragment]


def extract_cited_patent_ids(answer_text: str) -> list[str]:
    cited: list[str] = []
    for match in _PATENT_ID_CITATION_RE.finditer(str(answer_text or "")):
        patent_id = _normalize_patent_id(match.group(1))
        if patent_id and patent_id not in cited:
            cited.append(patent_id)
    return cited


def _unwrap_backticked_patent_citation_blocks(text: str, *, allowed_patent_ids: list[str] | None) -> str:
    allowed = set(_normalize_patent_id_list(allowed_patent_ids))
    if not allowed:
        return str(text or "")

    def _replace(match: re.Match[str]) -> str:
        body = str(match.group("body") or "").strip()
        if not (body.startswith("(") and body.endswith(")")):
            return match.group(0)
        inner = body[1:-1].strip()
        if not inner:
            return match.group(0)
        raw_parts = [part.strip() for part in re.split(r"\s*[,，、;；]\s*", inner) if str(part).strip()]
        if not raw_parts:
            return match.group(0)
        for raw_part in raw_parts:
            token_match = _PATENT_CITATION_LIST_ITEM_RE.fullmatch(raw_part)
            if token_match is None:
                return match.group(0)
            patent_id = _normalize_patent_id(token_match.group(1))
            if not patent_id or patent_id not in allowed:
                return match.group(0)
        return body

    return _BACKTICK_CODE_SPAN_RE.sub(_replace, str(text or ""))


def render_patent_citations_for_user(
    answer_text: str,
    *,
    allowed_patent_ids: list[str] | None,
    trim: bool = True,
) -> str:
    allowed = set(_normalize_patent_id_list(allowed_patent_ids))

    def _replace(match: re.Match[str]) -> str:
        patent_id = _normalize_patent_id(match.group(1))
        if patent_id and (not allowed or patent_id in allowed):
            return f"({patent_id})"
        return ""

    rendered = _unwrap_backticked_patent_citation_blocks(
        str(answer_text or ""),
        allowed_patent_ids=allowed_patent_ids,
    )
    rendered = _PATENT_ID_CITATION_RE.sub(_replace, rendered)
    rendered = re.sub(r"patent_id\s*=\s*", "", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"\(\s+\)", "", rendered)
    rendered = re.sub(r"\s+([，。！？；：,.;!?])", r"\1", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    if trim:
        rendered = re.sub(r"[ \t]{2,}", " ", rendered)
        return rendered.strip()
    return rendered


class PatentCitationStreamSanitizer:
    def __init__(self, *, allowed_patent_ids: list[str] | None) -> None:
        self._allowed_patent_ids = _normalize_patent_id_list(allowed_patent_ids)
        self._tail = ""

    def consume(self, chunk: str) -> str:
        text = str(chunk or "")
        if not text:
            return ""
        self._tail = f"{self._tail}{text}"
        if len(self._tail) <= _STREAM_CITATION_TAIL_HOLD:
            return ""
        flushable = self._tail[:-_STREAM_CITATION_TAIL_HOLD]
        self._tail = self._tail[-_STREAM_CITATION_TAIL_HOLD:]
        return render_patent_citations_for_user(
            flushable,
            allowed_patent_ids=self._allowed_patent_ids,
            trim=False,
        )

    def finalize(self) -> str:
        if not self._tail:
            return ""
        remaining = self._tail
        self._tail = ""
        return render_patent_citations_for_user(
            remaining,
            allowed_patent_ids=self._allowed_patent_ids,
            trim=False,
        )


def _remove_segment_around_marker(text: str, marker: str) -> str:
    updated = str(text or "")
    while marker in updated:
        index = updated.find(marker)
        start = index
        while start > 0 and updated[start - 1] not in _CLAUSE_BOUNDARIES:
            start -= 1
        if start > 0 and updated[start - 1] in "，,；;":
            start -= 1

        end = index + len(marker)
        while end < len(updated) and updated[end] not in _CLAUSE_BOUNDARIES:
            end += 1
        if end < len(updated) and updated[end] in _CLAUSE_BOUNDARIES:
            end += 1
        updated = (updated[:start] + updated[end:]).strip()
    return updated


def sanitize_patent_id_citations(answer_text: str, *, allowed_patent_ids: list[str] | None) -> tuple[str, list[str], list[str]]:
    allowed = set(_normalize_patent_id_list(allowed_patent_ids))
    cited: list[str] = []
    invalid: list[str] = []
    invalid_markers: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        patent_id = _normalize_patent_id(match.group(1))
        if patent_id and (not allowed or patent_id in allowed):
            if patent_id not in cited:
                cited.append(patent_id)
            return f"(patent_id={patent_id})"
        if patent_id and patent_id not in invalid:
            invalid.append(patent_id)
        marker = f"__INVALID_PATENT_CITATION_{patent_id}__"
        invalid_markers.append(marker)
        return marker

    sanitized = _PATENT_ID_CITATION_RE.sub(_replace, str(answer_text or ""))
    for marker in invalid_markers:
        sanitized = _remove_segment_around_marker(sanitized, marker)
    sanitized = re.sub(r"[ \t]+", " ", sanitized)
    sanitized = re.sub(r"\s+([，。！？；：,.;!?])", r"\1", sanitized)
    sanitized = re.sub(r"\(\s+\)", "", sanitized)
    sanitized = re.sub(r"[，,；;]\s*[。！？!?]", lambda m: str(m.group(0))[-1], sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip(), cited, invalid


def _table_summary(table: PatentTableSupplement) -> str:
    title = table.table_title or "未命名表格"
    header = " / ".join(table.columns[:6])
    rows = []
    for row in table.rows[:3]:
        pairs = [f"{key}={value}" for key, value in list(row.items())[:4]]
        if pairs:
            rows.append("；".join(pairs))
    row_text = " | ".join(rows)
    if header and row_text:
        return f"{title}（列: {header}）: {row_text}"
    if row_text:
        return f"{title}: {row_text}"
    return title


def _group_evidences_by_patent(evidences: list[PatentEvidence], *, max_patents: int | None = None) -> list[tuple[str, list[PatentEvidence]]]:
    grouped: list[tuple[str, list[PatentEvidence]]] = []
    by_patent: dict[str, list[PatentEvidence]] = {}
    for evidence in evidences:
        patent_id = str(evidence.canonical_patent_id or "").strip().upper()
        if not patent_id:
            continue
        if patent_id not in by_patent:
            if max_patents is not None and len(grouped) >= max_patents:
                continue
            by_patent[patent_id] = []
            grouped.append((patent_id, by_patent[patent_id]))
        by_patent[patent_id].append(evidence)
    return grouped


def _reference_summary(
    *,
    retrieval_outcome: PatentRetrievalOutcome,
    patent_id: str,
) -> str:
    patent_id = str(patent_id or "").strip().upper()
    if not patent_id:
        return ""
    original_link = next(
        (
            dict(item)
            for item in list(retrieval_outcome.original_links)
            if isinstance(item, dict) and str(item.get("canonical_patent_id") or "").strip().upper() == patent_id
        ),
        {},
    )
    reference_object = next(
        (
            dict(item)
            for item in list(retrieval_outcome.reference_objects)
            if isinstance(item, dict) and str(item.get("canonical_patent_id") or "").strip().upper() == patent_id
        ),
        {},
    )
    reference_link = next(
        (
            dict(item)
            for item in list(retrieval_outcome.reference_links)
            if isinstance(item, dict) and str(item.get("canonical_patent_id") or "").strip().upper() == patent_id
        ),
        {},
    )
    section = str(original_link.get("section") or "").strip().lower()
    claim_number = original_link.get("claim_number")
    paragraph_id = str(original_link.get("paragraph_id") or "").strip()
    if section == "claim" and claim_number is not None:
        anchor = f"Claim {claim_number}"
    elif paragraph_id:
        anchor = f"Paragraph {paragraph_id}"
    else:
        anchor = str(reference_object.get("section_label") or reference_link.get("label") or section or "原文定位").strip()
    viewer_uri = str(
        original_link.get("viewer_uri")
        or reference_object.get("viewer_uri")
        or reference_link.get("viewer_uri")
        or ""
    ).strip()
    if viewer_uri:
        return f"{anchor} | {viewer_uri}"
    return anchor


def _build_stage4_evidence_section(
    *,
    retrieval_outcome: PatentRetrievalOutcome,
    allowed_patent_ids: list[str],
) -> tuple[list[str], dict[str, int]]:
    allowed_set = set(_normalize_patent_id_list(allowed_patent_ids))
    filtered_evidences = [
        evidence
        for evidence in list(retrieval_outcome.evidences)
        if not allowed_set or _normalize_patent_id(evidence.canonical_patent_id) in allowed_set
    ]
    grouped_evidences = _group_evidences_by_patent(
        filtered_evidences,
        max_patents=len(allowed_patent_ids) if allowed_patent_ids else None,
    )
    snippets_per_patent = _env_int("PATENT_STAGE4_EVIDENCE_SNIPPETS_PER_PATENT", 3, minimum=1, maximum=10)
    snippet_max_chars = _env_int("PATENT_STAGE4_EVIDENCE_SNIPPET_MAX_CHARS", 600, minimum=80, maximum=5000)
    abstract_max_chars = _env_int("PATENT_STAGE4_EVIDENCE_ABSTRACT_MAX_CHARS", 400, minimum=80, maximum=5000)
    tables_per_patent = _env_int("PATENT_STAGE4_EVIDENCE_TABLES_PER_PATENT", 2, minimum=1, maximum=10)
    table_max_chars = _env_int("PATENT_STAGE4_EVIDENCE_TABLE_MAX_CHARS", 400, minimum=80, maximum=5000)

    lines = ["", "6. 检索证据（已按专利归并）："]
    snippet_count = 0
    table_count = 0
    for index, (_, patent_evidences) in enumerate(grouped_evidences, start=1):
        evidence = patent_evidences[0]
        lines.append(f"{index}. 专利: {evidence.title} ({evidence.canonical_patent_id})")
        if evidence.abstract_text:
            lines.append(f"   摘要: {_truncate(evidence.abstract_text, limit=abstract_max_chars)}")
        for snippet in patent_evidences[:snippets_per_patent]:
            if snippet.matched_section_label and snippet.matched_snippet:
                snippet_count += 1
                lines.append(
                    f"   命中片段[{snippet.matched_section_label}]: "
                    f"{_truncate(snippet.matched_snippet, limit=snippet_max_chars)}"
                )
        for table in evidence.table_supplements[:tables_per_patent]:
            table_count += 1
            lines.append(f"   表格: {_truncate(_table_summary(table), limit=table_max_chars)}")
        reference_summary = _reference_summary(retrieval_outcome=retrieval_outcome, patent_id=evidence.canonical_patent_id)
        if reference_summary:
            lines.append(f"   原文定位: {reference_summary}")

    evidence_text = "\n".join(lines)
    return lines, {
        "evidence_chars": len(evidence_text),
        "evidence_patent_count": len(grouped_evidences),
        "evidence_item_count": len(filtered_evidences),
        "snippet_count": snippet_count,
        "table_count": table_count,
    }


def _is_boilerplate_snippet(evidence: PatentEvidence) -> bool:
    section_type = str(evidence.matched_section_type or "").strip().lower()
    section_label = str(evidence.matched_section_label or "").strip().lower()
    snippet = str(evidence.matched_snippet or "").strip().lower()
    if section_type in {"background", "legal"}:
        return True
    if any(keyword in section_label for keyword in ("background", "legal", "背景", "法律")):
        return True
    boilerplate_markers = (
        "背景技术",
        "技术领域",
        "现有技术",
        "本发明旨在",
        "本发明提供",
        "本申请公开",
        "保护范围",
        "法律",
        "本领域技术人员",
    )
    return any(marker in snippet for marker in boilerplate_markers)


def build_fallback_patent_answer(
    *,
    question: str,
    retrieval_outcome: PatentRetrievalOutcome,
    context: dict[str, Any] | None = None,
) -> str:
    context = dict(context or {})
    stage1_deep_answer = str(context.get("stage1_deep_answer") or "").strip()
    if not retrieval_outcome.evidences:
        return stage1_deep_answer or "Patent retrieval found no matching results."
    grouped_evidences = _group_evidences_by_patent(list(retrieval_outcome.evidences))
    lines = [f"围绕“{question}”，当前检索命中了 {len(grouped_evidences)} 篇核心专利证据："]
    if stage1_deep_answer:
        lines.append(f"阶段1预分析：{stage1_deep_answer}")
    graph_kb = dict(context.get("graph_kb") or {})
    if graph_kb:
        lines.append("图谱辅助线索：以下结构化线索仅用于补充检索定位，不作为可引用证据。")
        graph_mode = str(graph_kb.get("mode") or "").strip()
        if graph_mode:
            lines.append(f"图谱模式：{graph_mode}")
        graph_candidates = _normalize_patent_id_list(graph_kb.get("stage4_graph_candidate_patent_ids"))
        if graph_candidates:
            lines.append(f"图谱候选专利（仅供定位，不作为引用）：{', '.join(graph_candidates)}")
        fact_block = " ".join(str(graph_kb.get("stage4_fact_block") or "").split()).strip()
        if fact_block:
            lines.append(f"图谱事实：{fact_block}")
    lines.append("证据口径：实质技术证据优先采用权利要求/说明书命中片段、摘要和同专利表格；背景/法律套话仅作背景说明，不作为核心结论依据。")
    for index, (_, patent_evidences) in enumerate(grouped_evidences, start=1):
        evidence = patent_evidences[0]
        segments = []
        boilerplate_labels: list[str] = []
        technical_snippets = [item for item in patent_evidences if not _is_boilerplate_snippet(item)]
        for snippet in technical_snippets[:2]:
            if snippet.matched_section_label and snippet.matched_snippet:
                segments.append(f"{snippet.matched_section_label}命中片段：{_truncate(snippet.matched_snippet)}")
        for snippet in patent_evidences:
            if not _is_boilerplate_snippet(snippet):
                continue
            label = str(snippet.matched_section_label or snippet.matched_section_type or "未命名片段").strip()
            if label and label not in boilerplate_labels:
                boilerplate_labels.append(label)
        if not segments and evidence.abstract_text:
            segments.append(f"摘要信号：{_truncate(evidence.abstract_text)}")
        if evidence.table_supplements:
            segments.append(f"表格补充：{_truncate(_table_summary(evidence.table_supplements[0]), limit=220)}")
        if boilerplate_labels:
            segments.append(f"背景/法律套话已降权：{', '.join(boilerplate_labels)}")
        if not segments:
            segments.append("当前仅命中专利元数据，尚未抽取到更具体的正文证据。")
        reference_summary = _reference_summary(retrieval_outcome=retrieval_outcome, patent_id=evidence.canonical_patent_id)
        if reference_summary:
            segments.append(f"原文定位：{reference_summary}")
        lines.append(
            f"{index}. 《{evidence.title}》(patent_id={evidence.canonical_patent_id})；实质技术证据：" + "；".join(segment for segment in segments if segment)
        )
    lines.append("综合判断：上述结论基于当前命中的说明书片段、摘要和同专利表格数据整理而成，适合先做技术替代方向筛查，再继续下钻原文核验。")
    return "\n".join(lines)


@dataclass
class PatentAnswerBuilder:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 30.0
    transport: httpx.BaseTransport | None = None
    http_client: Any | None = None
    _client: Any = field(init=False, repr=False)
    _owns_http_client: bool = field(init=False, repr=False, default=False)

    def __post_init__(self) -> None:
        if self.transport is not None and self.http_client is not None:
            raise ValueError("transport cannot be combined with http_client")
        self._owns_http_client = self.http_client is None
        self._client = self.http_client or httpx.Client(timeout=self.timeout_seconds, transport=self.transport)
        transport = describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client)
        _LOGGER.info(
            "patent answer builder initialized model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s",
            self.model,
            self.base_url,
            self.timeout_seconds,
            transport.get("client_owner"),
            transport.get("shared_client_id"),
        )

    def close(self) -> None:
        if self._owns_http_client:
            self._client.close()

    def __call__(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any] | None = None,
    ) -> str:
        context = dict(context or {})
        allowed_patent_ids = _normalize_patent_id_list(
            list(context.get("allowed_patent_ids") or []) or list(retrieval_outcome.references)
        )
        if not self.api_key or not self.base_url or not self.model:
            _LOGGER.warning(
                "patent answer builder missing llm config api_key_set=%s base_url_set=%s model=%s; using fallback answer",
                bool(self.api_key),
                bool(self.base_url),
                self.model,
            )
            return self._build_sanitized_fallback_answer(
                question=question,
                retrieval_outcome=retrieval_outcome,
                context=context,
                allowed_patent_ids=allowed_patent_ids,
            )
        prompt, prompt_metadata = self._build_prompt_with_metadata(
            question=question,
            retrieval_outcome=retrieval_outcome,
            context=context,
        )
        _LOGGER.info(
            "patent answer builder prompt prepared prompt_chars=%s evidence_count=%s evidence_chars=%s allowed_patent_ids=%s",
            len(prompt),
            int(prompt_metadata.get("evidence_item_count", 0)),
            int(prompt_metadata.get("evidence_chars", 0)),
            allowed_patent_ids,
        )
        min_distinct_citations = _resolve_min_distinct_citations(context=context, allowed_patent_ids=allowed_patent_ids)
        _LOGGER.info(
            "patent answer builder request start model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s allowed_patent_ids=%s evidence_count=%s evidence_chars=%s prompt_chars=%s",
            self.model,
            self.base_url,
            self.timeout_seconds,
            describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client).get("client_owner"),
            describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client).get("shared_client_id"),
            allowed_patent_ids,
            int(prompt_metadata.get("evidence_item_count", 0)),
            int(prompt_metadata.get("evidence_chars", 0)),
            len(prompt),
        )
        try:
            request_url = f"{self.base_url.rstrip('/')}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = self._build_request_payload(
                prompt=prompt,
                allowed_patent_ids=allowed_patent_ids,
                stream=False,
                min_distinct_citations=min_distinct_citations,
            )
            payload_body = json.dumps(payload, ensure_ascii=False)
            _LOGGER.info(
                "patent answer builder request payload ready model=%s stream=%s message_count=%s payload_chars=%s allowed_patent_ids=%s",
                self.model,
                False,
                len(payload.get("messages") or []),
                len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
                allowed_patent_ids,
            )
            request_started = time.perf_counter()
            request = None
            if hasattr(self._client, "build_request"):
                request = self._client.build_request(
                    "POST",
                    request_url,
                    headers=headers,
                    content=payload_body.encode("utf-8"),
                )
                _LOGGER.info(
                    "patent answer builder request object built method=%s url=%s elapsed_ms=%.3f content_length=%s",
                    getattr(request, "method", "POST"),
                    str(getattr(request, "url", request_url)),
                    (time.perf_counter() - request_started) * 1000,
                    str(getattr(request, "headers", {}).get("content-length") or ""),
                )
            _LOGGER.info(
                "patent answer builder request dispatch start timeout_seconds=%s elapsed_ms=%.3f transport=%s",
                self.timeout_seconds,
                (time.perf_counter() - request_started) * 1000,
                "send" if request is not None and hasattr(self._client, "send") else "post",
            )
            request_timeout = build_patent_request_timeout(
                http_client=self._client,
                timeout_seconds=self.timeout_seconds,
            )
            dispatch_started = time.perf_counter()
            if request is not None and hasattr(self._client, "send"):
                try:
                    response = self._client.send(request, stream=False, timeout=request_timeout)
                except TypeError as exc:
                    if "timeout" not in str(exc):
                        record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                        raise
                    response = self._client.send(request, stream=False)
                except Exception as exc:
                    record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                    raise
            else:
                try:
                    response = self._client.post(
                        request_url,
                        headers=headers,
                        content=payload_body.encode("utf-8"),
                        timeout=request_timeout,
                    )
                except TypeError as exc:
                    if "content" not in str(exc):
                        record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                        raise
                    response = self._client.post(
                        request_url,
                        headers=headers,
                        json=payload,
                        timeout=request_timeout,
                    )
                except Exception as exc:
                    record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                    raise
            record_patent_dispatch_success(http_client=self._client, started_at=dispatch_started)
            _LOGGER.info(
                "patent answer builder request dispatch returned status_code=%s elapsed_ms=%.3f",
                getattr(response, "status_code", ""),
                (time.perf_counter() - request_started) * 1000,
            )
            _LOGGER.info(
                "patent answer builder llm response headers received status_code=%s elapsed_ms=%.3f content_length=%s",
                getattr(response, "status_code", ""),
                (time.perf_counter() - request_started) * 1000,
                str(response.headers.get("content-length") or ""),
            )
            response.raise_for_status()
            payload = response.json()
            choices = list(payload.get("choices") or [])
            message = dict((choices[0] or {}).get("message") or {}) if choices else {}
            content = str(message.get("content") or "").strip()
            _LOGGER.info(
                "patent answer builder llm response body parsed response_chars=%s elapsed_ms=%.3f",
                len(content),
                (time.perf_counter() - request_started) * 1000,
            )
            if content:
                _LOGGER.info(
                    "patent answer builder llm response received chars=%s elapsed_ms=%.3f",
                    len(content),
                    (time.perf_counter() - request_started) * 1000,
                )
                return sanitize_patent_id_citations(content, allowed_patent_ids=allowed_patent_ids)[0]
            _LOGGER.warning("patent answer builder returned empty content; using fallback answer")
            return self._build_sanitized_fallback_answer(
                question=question,
                retrieval_outcome=retrieval_outcome,
                context=context,
                allowed_patent_ids=allowed_patent_ids,
            )
        except Exception as exc:
            _LOGGER.warning("patent answer builder request failed; using fallback answer: %s", exc)
            return self._build_sanitized_fallback_answer(
                question=question,
                retrieval_outcome=retrieval_outcome,
                context=context,
                allowed_patent_ids=allowed_patent_ids,
            )

    def stream(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        context = dict(context or {})
        allowed_patent_ids = _normalize_patent_id_list(
            list(context.get("allowed_patent_ids") or []) or list(retrieval_outcome.references)
        )
        if not self.api_key or not self.base_url or not self.model:
            _LOGGER.warning(
                "patent answer builder missing llm config api_key_set=%s base_url_set=%s model=%s; using fallback streamed answer",
                bool(self.api_key),
                bool(self.base_url),
                self.model,
            )
            fallback = self._build_sanitized_fallback_answer(
                question=question,
                retrieval_outcome=retrieval_outcome,
                context=context,
                allowed_patent_ids=allowed_patent_ids,
            )
            if fallback:
                yield fallback
            return
        prompt, prompt_metadata = self._build_prompt_with_metadata(
            question=question,
            retrieval_outcome=retrieval_outcome,
            context=context,
        )
        _LOGGER.info(
            "patent answer builder stream prompt prepared prompt_chars=%s evidence_count=%s evidence_chars=%s allowed_patent_ids=%s",
            len(prompt),
            int(prompt_metadata.get("evidence_item_count", 0)),
            int(prompt_metadata.get("evidence_chars", 0)),
            allowed_patent_ids,
        )
        min_distinct_citations = _resolve_min_distinct_citations(context=context, allowed_patent_ids=allowed_patent_ids)
        _LOGGER.info(
            "patent answer builder stream start model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s allowed_patent_ids=%s evidence_count=%s evidence_chars=%s prompt_chars=%s",
            self.model,
            self.base_url,
            self.timeout_seconds,
            describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client).get("client_owner"),
            describe_patent_transport(http_client=self._client, owns_http_client=self._owns_http_client).get("shared_client_id"),
            allowed_patent_ids,
            int(prompt_metadata.get("evidence_item_count", 0)),
            int(prompt_metadata.get("evidence_chars", 0)),
            len(prompt),
        )
        streamed_any = False
        stream_started = time.perf_counter()
        first_chunk_logged = False
        first_payload_logged = False
        first_line_logged = False
        chunk_count = 0
        answer_chars = 0
        request_url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_request_payload(
            prompt=prompt,
            allowed_patent_ids=allowed_patent_ids,
            stream=True,
            min_distinct_citations=min_distinct_citations,
        )
        payload_body = json.dumps(payload, ensure_ascii=False)
        _LOGGER.info(
            "patent answer builder stream request payload ready model=%s stream=%s message_count=%s payload_chars=%s allowed_patent_ids=%s",
            self.model,
            True,
            len(payload.get("messages") or []),
            len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            allowed_patent_ids,
        )
        try:
            request = None
            if hasattr(self._client, "build_request"):
                request = self._client.build_request(
                    "POST",
                    request_url,
                    headers=headers,
                    content=payload_body.encode("utf-8"),
                )
                _LOGGER.info(
                    "patent answer builder stream request object built method=%s url=%s elapsed_ms=%.3f content_length=%s",
                    getattr(request, "method", "POST"),
                    str(getattr(request, "url", request_url)),
                    (time.perf_counter() - stream_started) * 1000,
                    str(getattr(request, "headers", {}).get("content-length") or ""),
                )
            _LOGGER.info(
                "patent answer builder stream request dispatch start timeout_seconds=%s elapsed_ms=%.3f transport=%s",
                self.timeout_seconds,
                (time.perf_counter() - stream_started) * 1000,
                "send" if request is not None and hasattr(self._client, "send") else "stream",
            )
            request_timeout = build_patent_request_timeout(
                http_client=self._client,
                timeout_seconds=self.timeout_seconds,
                stream=True,
            )
            dispatch_started = time.perf_counter()
            if request is not None and hasattr(self._client, "send"):
                try:
                    response_cm = self._client.send(request, stream=True, timeout=request_timeout)
                except TypeError as exc:
                    if "timeout" not in str(exc):
                        record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                        raise
                    response_cm = self._client.send(request, stream=True)
                except Exception as exc:
                    record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                    raise
                response_context = closing(response_cm)
            else:
                try:
                    response_cm = self._client.stream(
                        "POST",
                        request_url,
                        headers=headers,
                        content=payload_body.encode("utf-8"),
                        timeout=request_timeout,
                    )
                except TypeError as exc:
                    if "content" not in str(exc):
                        record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                        raise
                    response_cm = self._client.stream(
                        "POST",
                        request_url,
                        headers=headers,
                        json=payload,
                        timeout=request_timeout,
                    )
                except Exception as exc:
                    record_patent_dispatch_error(http_client=self._client, started_at=dispatch_started, exc=exc)
                    raise
                response_context = response_cm
            with response_context as response:
                record_patent_dispatch_success(http_client=self._client, started_at=dispatch_started)
                _LOGGER.info(
                    "patent answer builder stream request dispatch returned status_code=%s elapsed_ms=%.3f",
                    getattr(response, "status_code", ""),
                    (time.perf_counter() - stream_started) * 1000,
                )
                response.raise_for_status()
                _LOGGER.info(
                    "patent answer builder stream response headers received status_code=%s elapsed_ms=%.3f content_type=%s",
                    getattr(response, "status_code", ""),
                    (time.perf_counter() - stream_started) * 1000,
                    str(response.headers.get("content-type") or ""),
                )
                for line in response.iter_lines():
                    if line is None:
                        continue
                    line_text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
                    stripped = line_text.strip()
                    if not stripped:
                        continue
                    if not first_line_logged:
                        first_line_logged = True
                        _LOGGER.info(
                            "patent answer builder stream first response line received line_chars=%s elapsed_ms=%.3f",
                            len(stripped),
                            (time.perf_counter() - stream_started) * 1000,
                        )
                    if stripped.startswith("data:"):
                        stripped = stripped[5:].strip()
                    if not stripped:
                        continue
                    if stripped == "[DONE]":
                        break
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        _LOGGER.debug("patent answer builder ignored non-json stream line: %s", _truncate(stripped))
                        continue
                    if not first_payload_logged:
                        first_payload_logged = True
                        _LOGGER.info(
                            "patent answer builder stream first payload received payload_chars=%s elapsed_ms=%.3f",
                            len(stripped),
                            (time.perf_counter() - stream_started) * 1000,
                        )
                    for fragment in _extract_stream_fragments(payload):
                        if not fragment:
                            continue
                        streamed_any = True
                        chunk_count += 1
                        answer_chars += len(fragment)
                        if not first_chunk_logged:
                            first_chunk_logged = True
                            _LOGGER.info(
                                "patent answer builder stream first chunk chunk_chars=%s elapsed_ms=%.3f",
                                len(fragment),
                                (time.perf_counter() - stream_started) * 1000,
                            )
                        yield fragment
            if streamed_any:
                _LOGGER.info(
                    "patent answer builder stream completed chunk_count=%s answer_chars=%s elapsed_ms=%.3f",
                    chunk_count,
                    answer_chars,
                    (time.perf_counter() - stream_started) * 1000,
                )
                return
            _LOGGER.warning("patent answer builder stream returned no content; using fallback answer")
        except Exception as exc:
            if streamed_any:
                _LOGGER.warning("patent answer builder stream failed after partial content: %s", exc)
                return
            _LOGGER.warning("patent answer builder stream failed; using fallback answer: %s", exc)
        fallback = self._build_sanitized_fallback_answer(
            question=question,
            retrieval_outcome=retrieval_outcome,
            context=context,
            allowed_patent_ids=allowed_patent_ids,
        )
        if fallback:
            yield fallback

    @staticmethod
    def from_env(*, http_client: Any | None = None) -> "PatentAnswerBuilder":
        return PatentAnswerBuilder(
            api_key=str(
                os.getenv("PATENT_OPENAI_API_KEY")
                or os.getenv("LLM_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or os.getenv("DASHSCOPE_API_KEY")
                or ""
            ).strip(),
            base_url=str(
                os.getenv("PATENT_OPENAI_BASE_URL")
                or os.getenv("LLM_BASE_URL")
                or os.getenv("OPENAI_BASE_URL")
                or os.getenv("DASHSCOPE_BASE_URL")
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).strip(),
            model=str(
                os.getenv("PATENT_OPENAI_MODEL")
                or os.getenv("LLM_MODEL")
                or os.getenv("OPENAI_MODEL")
                or os.getenv("DASHSCOPE_MODEL")
                or "deepseek-v3.1"
            ).strip(),
            timeout_seconds=float(str(os.getenv("PATENT_OPENAI_TIMEOUT_SECONDS") or "30").strip()),
            http_client=http_client,
        )

    def _build_prompt(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any] | None,
    ) -> str:
        return self._build_prompt_with_metadata(
            question=question,
            retrieval_outcome=retrieval_outcome,
            context=context,
        )[0]

    def _build_prompt_with_metadata(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any] | None,
    ) -> tuple[str, dict[str, int]]:
        context = dict(context or {})
        allowed_patent_ids = _normalize_patent_id_list(
            list(context.get("allowed_patent_ids") or []) or list(retrieval_outcome.references)
        )
        min_distinct_citations = _resolve_min_distinct_citations(
            context=context,
            allowed_patent_ids=allowed_patent_ids,
        )
        lines = [
            "你是一名最终的专利答案润色与校验专家。",
            "",
            "请基于以下材料生成最终答案：",
            "",
            f"1. 原始问题：{question}",
        ]
        summary = dict(context.get("summary_for_llm") or context.get("summary") or {})
        short_summary = str(summary.get("short_summary") or "").strip()
        if short_summary:
            lines.extend(["", f"2. 会话摘要（仅用于承接上文指代）：{short_summary}"])
        stage1_deep_answer = str(context.get("stage1_deep_answer") or "").strip()
        if stage1_deep_answer:
            lines.extend(
                [
                    "",
                    "3. 阶段1预分析（仅供结构、比较维度和核验线索参考，不能直接当作事实来源）：",
                    stage1_deep_answer,
                ]
            )
        turns_source = context.get("recent_turns_for_llm") or context.get("chat_history") or []
        turns = [str(item.get("content") or "").strip() for item in list(turns_source)[-4:] if isinstance(item, dict)]
        if turns:
            lines.extend(["", "4. 最近对话（仅用于承接当前问题，不可覆盖专利证据）："])
            lines.extend(f"- {item}" for item in turns if item)
        if allowed_patent_ids:
            lines.extend(["", f"5. 允许引用的专利白名单：{', '.join(allowed_patent_ids)}"])
        else:
            lines.extend(["", "5. 允许引用的专利白名单：无"])
        lines.append("最终答案中的引用格式必须严格使用 `(patent_id=公开号)`，并且只能引用上面的白名单公开号。")
        lines.append("只有白名单允许引用；图谱候选专利和图谱事实只能作为结构化辅助线索。")
        graph_kb = dict(context.get("graph_kb") or {})
        if graph_kb:
            lines.extend(["", "6. 图谱结构化辅助线索（不可直接当作文献引用，也不能把这些候选专利当作引用白名单）："])
            graph_mode = str(graph_kb.get("mode") or "").strip()
            if graph_mode:
                lines.append(f"- 图谱模式：{graph_mode}")
            graph_candidates = _normalize_patent_id_list(graph_kb.get("stage4_graph_candidate_patent_ids"))
            if graph_candidates:
                lines.append(f"- 图谱候选专利（非引用白名单）：{', '.join(graph_candidates)}")
            fact_block = str(graph_kb.get("stage4_fact_block") or "").strip()
            if fact_block:
                lines.append("- 图谱事实：")
                lines.extend(f"  {line}" for line in str(fact_block).splitlines() if str(line).strip())
        if min_distinct_citations > 0:
            lines.append(f"最终答案至少引用 {min_distinct_citations} 个不同公开号。")
        evidence_lines, evidence_metadata = _build_stage4_evidence_section(
            retrieval_outcome=retrieval_outcome,
            allowed_patent_ids=allowed_patent_ids,
        )
        lines.extend(evidence_lines)
        lines.extend(
            [
                "",
                "写作与引用要求：",
                "- 答案必须基于“检索证据”生成，而不是直接照搬“阶段1预分析”。",
                "- 阶段1预分析只能作为结构、比较维度和核验线索；若与证据冲突，以证据为准。",
                "- 如果表格与正文片段同时存在，容量、倍率、循环等数值优先采用表格证据；配比、工艺窗口等结构化参数也应优先使用表格，正文片段用于补充条件、对象和机理。",
                "- 每个核心要点都应尽量同时给出：实质技术结论、对应证据、必要的机理解释或工程含义。",
                "- 具体数值、工艺参数、性能结论必须来自给定证据；证据不足时可以保守表述，但不能伪造引用。",
                "- 要明确区分背景/法律套话与实质技术证据，优先保留真正支持结论的片段。",
                "- 每个有证据支撑的关键结论后面都要补一个 `(patent_id=公开号)`，不能输出白名单之外的公开号。",
                "- 不要机械地在每句话后重复标注同一公开号；同一逻辑块优先在段落末或要点末标注 1 次。",
                "- 输出使用清晰、规整的 Markdown；避免只罗列碎片证据。",
            ]
        )
        if min_distinct_citations > 0:
            lines.append(f"整段答案必须覆盖至少 {min_distinct_citations} 个不同公开号。")
        return "\n".join(lines), evidence_metadata

    def _build_request_payload(
        self,
        *,
        prompt: str,
        allowed_patent_ids: list[str],
        stream: bool,
        min_distinct_citations: int,
    ) -> dict[str, Any]:
        min_citation_clause = (
            f"最终答案至少引用 {int(min_distinct_citations)} 个不同公开号；"
            if int(min_distinct_citations) > 0
            else ""
        )
        return {
            "model": self.model,
            "temperature": 0.2,
            "stream": bool(stream),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是专利分析助手。只能基于给定证据回答，不要虚构未出现的事实；"
                        "阶段1预分析只能作为结构和核验线索，不能直接当作事实来源；"
                        "如果表格存在，必须优先采用表格中的容量、倍率、循环、配比和工艺窗口等数值，再用正文片段补充机理与条件；"
                        "要区分背景/法律套话与真正的技术证据；"
                        "每个核心要点尽量同时包含实质技术结论、关键证据和必要的机理或工程含义；"
                        "不要机械地在每句话后重复标注同一公开号，同一逻辑块优先在段落末或要点末标注 1 次；"
                        "引用必须使用 `(patent_id=公开号)`，不能使用 DOI；"
                        f"{min_citation_clause}"
                        f"最终答案里只允许引用这些公开号：{', '.join(allowed_patent_ids) if allowed_patent_ids else '无'}。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }

    def _build_sanitized_fallback_answer(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any],
        allowed_patent_ids: list[str],
    ) -> str:
        fallback = build_fallback_patent_answer(question=question, retrieval_outcome=retrieval_outcome, context=context)
        return sanitize_patent_id_citations(fallback, allowed_patent_ids=allowed_patent_ids)[0]
