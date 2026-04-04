from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from server.patent.retrieval_models import PatentEvidence, PatentRetrievalOutcome, PatentTableSupplement

_LOGGER = logging.getLogger("patent.answering")
_PATENT_ID_CITATION_RE = re.compile(r"\(\s*patent_id\s*=\s*([A-Za-z0-9._/\-]+)\s*\)", re.IGNORECASE)
_CLAUSE_BOUNDARIES = "\n。！？!?；;，,"


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


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

    def __post_init__(self) -> None:
        self._client = httpx.Client(timeout=self.timeout_seconds, transport=self.transport)

    def close(self) -> None:
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
        prompt = self._build_prompt(question=question, retrieval_outcome=retrieval_outcome, context=context)
        _LOGGER.info(
            "patent answer builder request start model=%s allowed_patent_ids=%s evidence_count=%s prompt_chars=%s",
            self.model,
            allowed_patent_ids,
            len(list(retrieval_outcome.evidences)),
            len(prompt),
        )
        try:
            response = self._client.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "temperature": 0.2,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是专利分析助手。只能基于给定证据回答，不要虚构未出现的事实；"
                                "如果表格存在，必须把表格数据纳入分析；"
                                "要区分背景/法律套话与真正的技术证据；"
                                "引用必须使用 `(patent_id=公开号)`，不能使用 DOI；"
                                f"最终答案里只允许引用这些公开号：{', '.join(allowed_patent_ids) if allowed_patent_ids else '无'}。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
            choices = list(payload.get("choices") or [])
            message = dict((choices[0] or {}).get("message") or {}) if choices else {}
            content = str(message.get("content") or "").strip()
            if content:
                _LOGGER.info("patent answer builder llm response received chars=%s", len(content))
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
        prompt = self._build_prompt(question=question, retrieval_outcome=retrieval_outcome, context=context)
        streamed_any = False
        try:
            with self._client.stream(
                "POST",
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=self._build_request_payload(prompt=prompt, allowed_patent_ids=allowed_patent_ids, stream=True),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line is None:
                        continue
                    line_text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
                    stripped = line_text.strip()
                    if not stripped:
                        continue
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
                    for fragment in _extract_stream_fragments(payload):
                        if not fragment:
                            continue
                        streamed_any = True
                        yield fragment
            if streamed_any:
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
    def from_env() -> "PatentAnswerBuilder":
        use_shared_env = _env_flag("PATENT_OPENAI_USE_SHARED_ENV", default=False)
        return PatentAnswerBuilder(
            api_key=str(
                os.getenv("PATENT_OPENAI_API_KEY")
                or ((os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")) if use_shared_env else "")
                or ""
            ).strip(),
            base_url=str(
                os.getenv("PATENT_OPENAI_BASE_URL")
                or ((os.getenv("OPENAI_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL")) if use_shared_env else "")
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).strip(),
            model=str(
                os.getenv("PATENT_OPENAI_MODEL")
                or ((os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL")) if use_shared_env else "")
                or "deepseek-v3.1"
            ).strip(),
            timeout_seconds=float(str(os.getenv("PATENT_OPENAI_TIMEOUT_SECONDS") or "30").strip()),
        )

    def _build_prompt(
        self,
        *,
        question: str,
        retrieval_outcome: PatentRetrievalOutcome,
        context: dict[str, Any] | None,
    ) -> str:
        context = dict(context or {})
        allowed_patent_ids = _normalize_patent_id_list(
            list(context.get("allowed_patent_ids") or []) or list(retrieval_outcome.references)
        )
        lines = [f"用户问题: {question}"]
        summary = dict(context.get("summary_for_llm") or context.get("summary") or {})
        short_summary = str(summary.get("short_summary") or "").strip()
        if short_summary:
            lines.append(f"会话摘要: {short_summary}")
        stage1_deep_answer = str(context.get("stage1_deep_answer") or "").strip()
        if stage1_deep_answer:
            lines.append(f"阶段1预分析: {stage1_deep_answer}")
        turns_source = context.get("recent_turns_for_llm") or context.get("chat_history") or []
        turns = [str(item.get("content") or "").strip() for item in list(turns_source)[-4:] if isinstance(item, dict)]
        if turns:
            lines.append("最近对话:")
            lines.extend(f"- {item}" for item in turns if item)
        if allowed_patent_ids:
            lines.append(f"允许引用的专利白名单: {', '.join(allowed_patent_ids)}")
        lines.append("最终答案中的引用格式必须严格使用 `(patent_id=公开号)`，并且只能引用上面的白名单公开号。")
        lines.append("检索证据:")
        for index, (_, patent_evidences) in enumerate(_group_evidences_by_patent(list(retrieval_outcome.evidences)), start=1):
            evidence = patent_evidences[0]
            lines.append(f"{index}. 专利: {evidence.title} ({evidence.canonical_patent_id})")
            if evidence.abstract_text:
                lines.append(f"   摘要: {evidence.abstract_text}")
            for snippet in patent_evidences[:3]:
                if snippet.matched_section_label and snippet.matched_snippet:
                    lines.append(f"   命中片段[{snippet.matched_section_label}]: {snippet.matched_snippet}")
            for table in evidence.table_supplements[:2]:
                lines.append(f"   表格: {_table_summary(table)}")
            reference_summary = _reference_summary(retrieval_outcome=retrieval_outcome, patent_id=evidence.canonical_patent_id)
            if reference_summary:
                lines.append(f"   原文定位: {reference_summary}")
        lines.append("请输出简洁但完整的专利分析，明确区分背景/法律套话与实质技术证据。")
        lines.append("每个有证据支撑的关键结论后面都要补一个 `(patent_id=公开号)`，不能输出白名单之外的公开号。")
        return "\n".join(lines)

    def _build_request_payload(
        self,
        *,
        prompt: str,
        allowed_patent_ids: list[str],
        stream: bool,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "temperature": 0.2,
            "stream": bool(stream),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是专利分析助手。只能基于给定证据回答，不要虚构未出现的事实；"
                        "如果表格存在，必须把表格数据纳入分析；"
                        "要区分背景/法律套话与真正的技术证据；"
                        "引用必须使用 `(patent_id=公开号)`，不能使用 DOI；"
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
