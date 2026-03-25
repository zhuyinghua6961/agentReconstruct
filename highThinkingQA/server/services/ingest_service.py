"""Ingest job service for HTTP APIs."""

# Deprecated: retained only for the retired highThinkingQA ingest HTTP surface.


from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _to_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


@dataclass
class _Job:
    job_id: str
    status: str
    created_at: str
    request: dict[str, Any]
    started_at: str | None = None
    finished_at: str | None = None
    stats: dict[str, Any] | None = None
    error: str | None = None


class IngestService:
    """Manage ingest jobs and bridge to ingest pipeline."""

    _ALLOWED_PARSE_METHODS = {"vlm_api", "paddleocr_client"}

    def __init__(self, *, max_keep_jobs: int = 200) -> None:
        self._max_keep_jobs = max(20, int(max_keep_jobs))
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._job_order: list[str] = []
        self._running_job_id: str | None = None

    def _normalize_request(self, payload: Any) -> tuple[dict[str, Any] | None, str | None]:
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return None, "request body must be a JSON object"

        parse_method = str(payload.get("parse_method") or "vlm_api").strip()
        if parse_method not in self._ALLOWED_PARSE_METHODS:
            methods = ", ".join(sorted(self._ALLOWED_PARSE_METHODS))
            return None, f"parse_method must be one of: {methods}"

        max_papers_raw = payload.get("max_papers")
        max_papers: int | None
        if max_papers_raw is None:
            max_papers = None
        else:
            try:
                max_papers = int(max_papers_raw)
            except Exception:
                return None, "max_papers must be integer"
            if max_papers <= 0:
                return None, "max_papers must be positive"

        start_raw = payload.get("start", 0)
        try:
            start = int(start_raw)
        except Exception:
            return None, "start must be integer"
        if start < 0:
            return None, "start must be >= 0"

        end_raw = payload.get("end")
        if end_raw is None:
            end = None
        else:
            try:
                end = int(end_raw)
            except Exception:
                return None, "end must be integer"
            if end <= start:
                return None, "end must be greater than start"

        request_data = {
            "parse_method": parse_method,
            "skip_parsed": _to_bool(payload.get("skip_parsed"), default=True),
            "max_papers": max_papers,
            "start": start,
            "end": end,
            "run_async": _to_bool(payload.get("run_async"), default=True),
        }
        return request_data, None

    def _trim_jobs_if_needed(self) -> None:
        while len(self._job_order) > self._max_keep_jobs:
            old_job_id = self._job_order.pop(0)
            self._jobs.pop(old_job_id, None)

    def _job_snapshot(self, job: _Job) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "status": job.status,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "request": deepcopy(job.request),
            "stats": deepcopy(job.stats) if isinstance(job.stats, dict) else None,
            "error": job.error,
        }

    def _is_busy(self) -> bool:
        for item in self._jobs.values():
            if str(item.status) in {"queued", "running"}:
                return True
        return False

    def _run_job(self, *, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            self._running_job_id = job_id
            job.status = "running"
            job.started_at = _now_iso()
            params = deepcopy(job.request)

        try:
            from ingest.pipeline import run_pipeline

            stats = run_pipeline(
                parse_method=str(params.get("parse_method") or "vlm_api"),
                skip_parsed=bool(params.get("skip_parsed", True)),
                max_papers=params.get("max_papers"),
                start=int(params.get("start", 0)),
                end=params.get("end"),
            )
            with self._lock:
                current = self._jobs.get(job_id)
                if current:
                    current.status = "succeeded"
                    current.finished_at = _now_iso()
                    current.stats = deepcopy(stats) if isinstance(stats, dict) else {"raw_result": stats}
                    current.error = None
        except Exception as exc:  # pragma: no cover - runtime path
            with self._lock:
                current = self._jobs.get(job_id)
                if current:
                    current.status = "failed"
                    current.finished_at = _now_iso()
                    current.error = str(exc)
        finally:
            with self._lock:
                if self._running_job_id == job_id:
                    self._running_job_id = None

    def create_ingest_job(self, *, payload: Any) -> dict[str, Any]:
        request_data, error = self._normalize_request(payload)
        if error:
            return {"success": False, "error": error, "code": "VALIDATION_ERROR"}
        assert request_data is not None

        with self._lock:
            if self._is_busy():
                return {
                    "success": False,
                    "error": "another ingest job is running",
                    "code": "INGEST_BUSY",
                }
            job_id = uuid.uuid4().hex
            job = _Job(
                job_id=job_id,
                status="queued",
                created_at=_now_iso(),
                request=deepcopy(request_data),
            )
            self._jobs[job_id] = job
            self._job_order.append(job_id)
            self._trim_jobs_if_needed()

        run_async = bool(request_data.get("run_async", True))
        if run_async:
            thread = threading.Thread(
                target=self._run_job,
                kwargs={"job_id": job_id},
                daemon=True,
                name=f"ingest-job-{job_id[:8]}",
            )
            thread.start()
            with self._lock:
                snapshot = self._job_snapshot(self._jobs[job_id])
            return {"success": True, "data": snapshot}

        self._run_job(job_id=job_id)
        with self._lock:
            snapshot = self._job_snapshot(self._jobs[job_id])
        return {"success": True, "data": snapshot}

    def get_job(self, *, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id))
            if not job:
                return {"success": False, "error": "ingest_job_not_found", "code": "NOT_FOUND"}
            return {"success": True, "data": self._job_snapshot(job)}


ingest_service = IngestService()
