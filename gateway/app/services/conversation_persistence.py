"""Persist QA conversation turns into the public-service authority."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import os
from typing import Any

import httpx
from fastapi import Request

from app.core.config import GatewaySettings
from app.core.trace import TRACE_ID_HEADER, get_trace_id
from app.services.sse_frames import SSEFrameBuffer, parse_sse_json_frame


def _conversation_id_int(value: Any) -> int | None:
    try:
        conversation_id = int(value)
    except Exception:
        return None
    return conversation_id if conversation_id > 0 else None


def _coerce_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list):
        return []
    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(steps, start=1):
        if not isinstance(item, dict):
            continue
        step_key = str(item.get("step") or f"step_{idx}").strip() or f"step_{idx}"
        normalized.append(
            {
                "step": step_key,
                "title": str(item.get("title") or "").strip(),
                "message": str(item.get("message") or item.get("content") or step_key).strip() or step_key,
                "status": str(item.get("status") or "processing").strip() or "processing",
                "data": item.get("data") if isinstance(item.get("data"), dict) else {},
            }
        )
    return normalized


def _normalize_positive_int_list(values: Any) -> list[int]:
    if not isinstance(values, list):
        return []
    normalized: list[int] = []
    seen: set[int] = set()
    for item in values:
        try:
            value = int(item)
        except Exception:
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _coerce_context_hints(context_hints: Any) -> dict[str, Any]:
    if not isinstance(context_hints, dict):
        return {
            "selected_file_ids": [],
            "last_turn_route_hint": None,
        }
    return {
        "selected_file_ids": _normalize_positive_int_list(context_hints.get("selected_file_ids")),
        "last_turn_route_hint": str(context_hints.get("last_turn_route_hint") or "").strip() or None,
    }


@dataclass
class StreamSummary:
    assistant_content: str = ""
    query_mode: str = ""
    references: list[Any] | None = None
    reference_objects: list[Any] | None = None
    reference_links: list[Any] | None = None
    pdf_links: list[Any] | None = None
    doi_locations: dict[str, Any] | list[Any] | None = None
    route: str = ""
    used_files: list[Any] | None = None
    timings: dict[str, Any] | None = None
    trace_id: str = ""
    file_selection: dict[str, Any] | None = None
    steps: list[dict[str, Any]] | None = None
    done_seen: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "source": "gateway_ask_stream",
            "query_mode": self.query_mode,
            "references": list(self.references or []),
            "reference_objects": list(self.reference_objects or []),
            "reference_links": list(self.reference_links or []),
            "pdf_links": list(self.pdf_links or []),
            "doi_locations": self.doi_locations if isinstance(self.doi_locations, dict) else {},
            "steps": _coerce_steps(self.steps),
            "route": self.route,
            "used_files": list(self.used_files or []),
            "timings": dict(self.timings or {}),
            "trace_id": self.trace_id,
            "file_selection": dict(self.file_selection or {}),
            "done_seen": self.done_seen,
        }


class ConversationPersistenceService:
    def __init__(self, settings: GatewaySettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def set_transport(self, transport: httpx.AsyncBaseTransport | None) -> None:
        self._transport = transport

    async def persist_task_user_message(
        self,
        *,
        request: Request,
        conversation_id: int | str | None,
        user_id: int | str | None,
        task_id: str,
        content: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        selected_file_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        cid = _conversation_id_int(conversation_id)
        uid = _conversation_id_int(user_id)
        if cid is None or uid is None:
            raise ValueError("conversation_id_and_user_id_required")
        source_service = self._source_service_for_mode(actual_mode)
        payload = {
            "conversation_id": cid,
            "user_id": uid,
            "trace_id": str(task_id or "").strip(),
            "source_service": source_service,
            "route": str(route or "").strip() or "kb_qa",
            "requested_mode": str(requested_mode or "").strip() or actual_mode,
            "actual_mode": str(actual_mode or "").strip(),
            "idempotency_key": f"{cid}:{str(task_id or '').strip()}:user",
            "message": {
                "role": "user",
                "content": str(content or "").strip(),
            },
            "context_hints": {
                "selected_file_ids": list(selected_file_ids or []),
                "last_turn_route_hint": str(route or "").strip() or None,
            },
        }
        return await self._post_internal_json(
            request=request,
            path=f"/internal/conversations/{cid}/messages/user",
            payload=payload,
        )

    async def create_task_turn(
        self,
        *,
        request: Request,
        conversation_id: int | str | None,
        user_id: int | str | None,
        task_id: str,
        content: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        selected_file_ids: list[int] | None = None,
        status: str = "queued",
        last_seq: int = 0,
    ) -> dict[str, Any]:
        cid = _conversation_id_int(conversation_id)
        uid = _conversation_id_int(user_id)
        if cid is None or uid is None:
            raise ValueError("conversation_id_and_user_id_required")
        source_service = self._source_service_for_mode(actual_mode)
        payload = {
            "conversation_id": cid,
            "user_id": uid,
            "task_id": str(task_id or "").strip(),
            "trace_id": str(task_id or "").strip(),
            "source_service": source_service,
            "route": str(route or "").strip() or "kb_qa",
            "requested_mode": str(requested_mode or "").strip() or actual_mode,
            "actual_mode": str(actual_mode or "").strip(),
            "message": {
                "role": "user",
                "content": str(content or "").strip(),
            },
            "context_hints": {
                "selected_file_ids": list(selected_file_ids or []),
                "last_turn_route_hint": str(route or "").strip() or None,
            },
            "status": str(status or "queued").strip() or "queued",
            "last_seq": max(0, int(last_seq)),
        }
        return await self._post_internal_json(
            request=request,
            path=f"/internal/conversations/{cid}/tasks/{str(task_id or '').strip()}/create-turn",
            payload=payload,
        )

    async def start_task_assistant(
        self,
        *,
        request: Request,
        conversation_id: int | str | None,
        user_id: int | str | None,
        task_id: str,
        route: str,
        requested_mode: str,
        actual_mode: str,
        status: str = "queued",
        last_seq: int = 0,
    ) -> dict[str, Any]:
        cid = _conversation_id_int(conversation_id)
        uid = _conversation_id_int(user_id)
        if cid is None or uid is None:
            raise ValueError("conversation_id_and_user_id_required")
        source_service = self._source_service_for_mode(actual_mode)
        payload = {
            "conversation_id": cid,
            "user_id": uid,
            "task_id": str(task_id or "").strip(),
            "trace_id": str(task_id or "").strip(),
            "source_service": source_service,
            "route": str(route or "").strip() or "kb_qa",
            "requested_mode": str(requested_mode or "").strip() or actual_mode,
            "actual_mode": str(actual_mode or "").strip(),
            "status": str(status or "queued").strip() or "queued",
            "last_seq": max(0, int(last_seq)),
        }
        return await self._post_internal_json(
            request=request,
            path=f"/internal/conversations/{cid}/tasks/{str(task_id or '').strip()}/assistant-start",
            payload=payload,
        )

    async def rollback_task_creation(
        self,
        *,
        request: Request,
        conversation_id: int | str | None,
        user_id: int | str | None,
        task_id: str,
        user_message_id: str | None = None,
        assistant_message_id: str | None = None,
        preserve_user_message: bool = False,
    ) -> dict[str, Any]:
        cid = _conversation_id_int(conversation_id)
        uid = _conversation_id_int(user_id)
        if cid is None or uid is None:
            raise ValueError("conversation_id_and_user_id_required")
        payload = {
            "conversation_id": cid,
            "user_id": uid,
            "task_id": str(task_id or "").strip(),
            "user_message_id": str(user_message_id or "").strip(),
            "assistant_message_id": str(assistant_message_id or "").strip(),
            "preserve_user_message": bool(preserve_user_message),
        }
        return await self._post_internal_json(
            request=request,
            path=f"/internal/conversations/{cid}/tasks/{str(task_id or '').strip()}/rollback-create",
            payload=payload,
        )

    async def progress_task_assistant(
        self,
        *,
        request: Request,
        conversation_id: int | str | None,
        user_id: int | str | None,
        task_id: str,
        status: str,
        content_delta: str = "",
        steps: list[dict[str, Any]] | None = None,
        last_seq: int = 0,
    ) -> dict[str, Any]:
        cid = _conversation_id_int(conversation_id)
        uid = _conversation_id_int(user_id)
        if cid is None or uid is None:
            raise ValueError("conversation_id_and_user_id_required")
        payload = {
            "conversation_id": cid,
            "user_id": uid,
            "task_id": str(task_id or "").strip(),
            "status": str(status or "running").strip() or "running",
            "content_delta": str(content_delta or ""),
            "steps": list(steps or []),
            "last_seq": max(0, int(last_seq)),
        }
        return await self._post_internal_json(
            request=request,
            path=f"/internal/conversations/{cid}/tasks/{str(task_id or '').strip()}/assistant-progress",
            payload=payload,
        )

    async def terminal_task_assistant(
        self,
        *,
        request: Request,
        conversation_id: int | str | None,
        user_id: int | str | None,
        task_id: str,
        terminal_status: str,
        last_seq: int = 0,
        answer_text: str = "",
        steps: list[dict[str, Any]] | None = None,
        failure: dict[str, Any] | None = None,
        timings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cid = _conversation_id_int(conversation_id)
        uid = _conversation_id_int(user_id)
        if cid is None or uid is None:
            raise ValueError("conversation_id_and_user_id_required")
        payload = {
            "conversation_id": cid,
            "user_id": uid,
            "task_id": str(task_id or "").strip(),
            "terminal_status": str(terminal_status or "failed").strip() or "failed",
            "last_seq": max(0, int(last_seq)),
            "answer_text": str(answer_text or ""),
            "steps": list(steps or []),
            "failure": dict(failure or {}),
            "timings": dict(timings or {}),
        }
        return await self._post_internal_json(
            request=request,
            path=f"/internal/conversations/{cid}/tasks/{str(task_id or '').strip()}/assistant-terminal",
            payload=payload,
        )

    async def persist_user_message(
        self,
        *,
        request: Request,
        conversation_id: int | str | None,
        content: str,
        context_hints: dict[str, Any] | None = None,
    ) -> None:
        cid = _conversation_id_int(conversation_id)
        if cid is None:
            return
        if not str(content or "").strip():
            return
        await self._add_message(
            request=request,
            conversation_id=cid,
            role="user",
            content=str(content).strip(),
            metadata={"source": "gateway_ask_stream", "context_hints": _coerce_context_hints(context_hints)},
        )

    async def persist_assistant_summary(
        self,
        *,
        request: Request,
        conversation_id: int | str | None,
        summary: StreamSummary,
    ) -> None:
        cid = _conversation_id_int(conversation_id)
        if cid is None or not summary.done_seen:
            return
        content = str(summary.assistant_content or "").strip()
        if not content:
            return
        await self._add_message(
            request=request,
            conversation_id=cid,
            role="assistant",
            content=content,
            metadata=summary.to_metadata(),
        )

    def new_stream_summary(self) -> StreamSummary:
        return StreamSummary(
            references=[],
            reference_objects=[],
            reference_links=[],
            pdf_links=[],
            doi_locations={},
            used_files=[],
            timings={},
            file_selection={},
            steps=[],
        )

    async def extract_stream(
        self,
        *,
        body_iter: AsyncIterator[bytes],
        summary: StreamSummary,
    ) -> AsyncIterator[bytes]:
        frame_buffer = SSEFrameBuffer()
        step_order: list[str] = []
        step_map: dict[str, dict[str, Any]] = {}
        state = {"thinking_count": 0}
        async for chunk in body_iter:
            if not chunk:
                continue
            for frame in frame_buffer.feed(chunk):
                self._apply_sse_frame(
                    frame=frame,
                    summary=summary,
                    step_order=step_order,
                    step_map=step_map,
                    state=state,
                )
            yield chunk
        buffer = frame_buffer.flush()
        if buffer is not None:
            self._apply_sse_frame(
                frame=buffer,
                summary=summary,
                step_order=step_order,
                step_map=step_map,
                state=state,
            )

    def _apply_sse_frame(
        self,
        *,
        frame: str,
        summary: StreamSummary,
        step_order: list[str],
        step_map: dict[str, dict[str, Any]],
        state: dict[str, int],
    ) -> None:
        payload, _prefix_lines = parse_sse_json_frame(frame)
        if not isinstance(payload, dict):
            return
        event_type = str(payload.get("type") or "").strip().lower()
        if event_type == "content":
            summary.assistant_content += str(payload.get("content") or "")
            return
        if event_type == "metadata":
            mode = str(payload.get("query_mode") or payload.get("mode") or "").strip()
            if mode:
                summary.query_mode = mode
            summary.route = str(payload.get("route") or summary.route or "")
            summary.trace_id = str(payload.get("trace_id") or summary.trace_id or "")
            return
        if event_type == "thinking":
            state["thinking_count"] += 1
            step_key = f"thinking_{state['thinking_count']}"
            self._upsert_step(
                step_order=step_order,
                step_map=step_map,
                step_key=step_key,
                payload={
                    "step": step_key,
                    "title": "",
                    "message": str(payload.get("content") or payload.get("message") or step_key).strip() or step_key,
                    "status": "success",
                    "data": {},
                },
            )
            summary.steps = [step_map[key] for key in step_order]
            return
        if event_type == "step":
            step_key = str(payload.get("step") or f"step_{len(step_order) + 1}").strip() or f"step_{len(step_order) + 1}"
            self._upsert_step(
                step_order=step_order,
                step_map=step_map,
                step_key=step_key,
                payload={
                    "step": step_key,
                    "title": str(payload.get("title") or "").strip(),
                    "message": str(payload.get("message") or payload.get("content") or step_key).strip() or step_key,
                    "status": str(payload.get("status") or "processing").strip() or "processing",
                    "data": payload.get("data") if isinstance(payload.get("data"), dict) else {},
                },
            )
            summary.steps = [step_map[key] for key in step_order]
            return
        if event_type == "done":
            summary.done_seen = True
            summary.assistant_content = str(payload.get("final_answer") or summary.assistant_content or "")
            mode = str(payload.get("query_mode") or (payload.get("metadata") or {}).get("query_mode") or summary.query_mode or "").strip()
            if mode:
                summary.query_mode = mode
            refs = payload.get("references")
            if isinstance(refs, list):
                summary.references = refs
            reference_objects = payload.get("reference_objects")
            if isinstance(reference_objects, list):
                summary.reference_objects = reference_objects
            ref_links = payload.get("reference_links")
            if isinstance(ref_links, list):
                summary.reference_links = ref_links
            pdf_links = payload.get("pdf_links")
            if isinstance(pdf_links, list):
                summary.pdf_links = pdf_links
            doi_locations = payload.get("doi_locations")
            if isinstance(doi_locations, dict):
                summary.doi_locations = doi_locations
            elif isinstance(doi_locations, list):
                summary.doi_locations = {}
            summary.route = str(payload.get("route") or summary.route or "")
            used_files = payload.get("used_files")
            if isinstance(used_files, list):
                summary.used_files = used_files
            timings = payload.get("timings")
            if isinstance(timings, dict):
                summary.timings = timings
            summary.trace_id = str(payload.get("trace_id") or summary.trace_id or "")
            file_selection = payload.get("file_selection")
            if isinstance(file_selection, dict):
                summary.file_selection = file_selection
            if not summary.steps:
                summary.steps = [step_map[key] for key in step_order]

    def _upsert_step(
        self,
        *,
        step_order: list[str],
        step_map: dict[str, dict[str, Any]],
        step_key: str,
        payload: dict[str, Any],
    ) -> None:
        if step_key not in step_map:
            step_order.append(step_key)
            step_map[step_key] = payload
            return
        step_map[step_key] = {**step_map[step_key], **payload}

    async def _add_message(
        self,
        *,
        request: Request,
        conversation_id: int,
        role: str,
        content: str,
        metadata: dict[str, Any],
    ) -> None:
        headers = self._forward_headers(request)
        payload = {
            "message": {
                "role": role,
                "content": content,
                "metadata": metadata,
            }
        }
        async with httpx.AsyncClient(
            timeout=float(self._settings.request_timeout_seconds),
            transport=self._transport,
        ) as client:
            response = await client.post(
                f"{self._settings.endpoints.public}/api/v1/conversations/{conversation_id}/messages",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

    async def _post_internal_json(
        self,
        *,
        request: Request,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=float(self._settings.request_timeout_seconds),
            transport=self._transport,
        ) as client:
            response = await client.post(
                f"{self._settings.endpoints.public}{path}",
                headers=self._internal_headers(request),
                json=payload,
            )
            response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("conversation_internal_invalid_response")
        return data

    def _forward_headers(self, request: Request) -> dict[str, str]:
        headers: dict[str, str] = {}
        authorization = str(request.headers.get("authorization") or "").strip()
        if authorization:
            headers["Authorization"] = authorization
        headers["Content-Type"] = "application/json"
        return headers

    def _internal_headers(self, request: Request) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Internal-Service-Name": "gateway",
            "X-Internal-Service-Token": self._internal_token(),
        }
        trace_id = get_trace_id(request)
        if trace_id:
            headers[TRACE_ID_HEADER] = trace_id
        return headers

    def _internal_token(self) -> str:
        token = str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip()
        if token:
            return token
        if str(self._settings.environment or "").strip().lower() == "test":
            return "authority-test-token"
        return ""

    def _source_service_for_mode(self, actual_mode: str) -> str:
        normalized = str(actual_mode or "").strip().lower()
        if normalized == "thinking":
            return "highThinkingQA"
        if normalized == "patent":
            return "patentQA"
        return "fastQA"
