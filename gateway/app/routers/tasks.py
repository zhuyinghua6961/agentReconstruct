"""Public QA task routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.auth import AuthContext, require_auth_context
from app.models.ask import AskRequest
from app.services.qa_tasks import QATaskService


router = APIRouter(tags=["tasks"])


@router.post("/api/v1/tasks")
async def create_task(
    payload: AskRequest,
    request: Request,
    auth_context: AuthContext = Depends(require_auth_context),
):
    if not bool(getattr(request.app.state.settings, "refresh_survivable_qa_tasks_enabled", False)):
        raise HTTPException(status_code=404, detail="task_api_disabled")
    service = QATaskService(request)
    return await service.create_task(payload, auth_context=auth_context)


@router.get("/api/v1/tasks/{task_id}")
async def get_task(
    task_id: str,
    request: Request,
    auth_context: AuthContext = Depends(require_auth_context),
):
    service = QATaskService(request)
    await service.reconcile_pending_terminal_tasks(task_ids={task_id})
    return service.get_task(task_id, auth_context=auth_context)


@router.get("/api/v1/tasks/{task_id}/events")
async def get_task_events(
    task_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    auth_context: AuthContext = Depends(require_auth_context),
):
    service = QATaskService(request)
    await service.reconcile_pending_terminal_tasks(task_ids={task_id})
    accept = str(request.headers.get("accept") or "").lower()
    if "text/event-stream" in accept:
        return service.stream_task_events(task_id, after_seq=after_seq, auth_context=auth_context)
    return service.get_task_events(task_id, after_seq=after_seq, auth_context=auth_context)


@router.post("/api/v1/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    request: Request,
    auth_context: AuthContext = Depends(require_auth_context),
):
    service = QATaskService(request)
    await service.reconcile_pending_terminal_tasks(task_ids={task_id})
    return await service.cancel_task(task_id, auth_context=auth_context)
