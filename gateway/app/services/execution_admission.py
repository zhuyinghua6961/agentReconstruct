"""Infra-only admission worker scaffolding.

This module intentionally does not intercept the live request path yet.
It provides a dedicated worker role, status helpers, and Redis-backed
foundation objects so admission infrastructure can be deployed before
existing ask/ask_stream routes are cut over.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import inspect
import logging
import os
import signal
import socket
import threading
import time
from typing import Any, Callable

from app.core.config import GatewaySettings
from app.core.logging import setup_logging
from app.integrations.redis.service import GatewayRedisRuntime
from app.services.execution_queue_status import ExecutionQueueStatusStore
from app.services.execution_slot_leases import ExecutionSlotLeaseStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdmissionDispatchResult:
    outcome: str
    request_id: str | None = None
    reason: str = ""
    request: dict[str, Any] | None = None
    lease: dict[str, Any] | None = None


@dataclass(frozen=True)
class AdmissionExecutionOutcome:
    outcome: str
    reason: str = ""
    result_payload: Any | None = None
    terminal_status: str = "completed"


def build_admission_worker_owner_id(
    runtime_role: str,
    *,
    hostname: str | None = None,
    pid: int | None = None,
) -> str:
    normalized_role = str(runtime_role or "").strip() or "admission_worker"
    normalized_host = str(hostname or socket.gethostname() or "unknown-host").strip() or "unknown-host"
    normalized_pid = int(pid if pid is not None else os.getpid())
    return f"{normalized_role}:{normalized_host}:{normalized_pid}"


class ExecutionAdmissionDispatcher:
    def __init__(
        self,
        *,
        settings: GatewaySettings,
        queue_status_store: ExecutionQueueStatusStore,
        slot_lease_store: ExecutionSlotLeaseStore,
        readiness_checker: Callable[[dict[str, Any]], tuple[bool, str]] | None = None,
        thinking_starvation_seconds: int = 120,
    ) -> None:
        self.settings = settings
        self.queue_status_store = queue_status_store
        self.slot_lease_store = slot_lease_store
        self.readiness_checker = readiness_checker
        self.thinking_starvation_seconds = max(0, int(thinking_starvation_seconds))

    def capacity_key_for_record(self, record: dict[str, Any]) -> str:
        explicit = str(record.get("backend_capacity_key") or "").strip()
        if explicit:
            return explicit
        actual_mode = str(record.get("actual_mode") or "").strip().lower()
        return "thinking" if actual_mode == "thinking" else "fast_or_patent"

    def queue_priority_for_record(self, record: dict[str, Any], *, now_epoch: float | None = None) -> tuple[int, float, str]:
        queued_epoch = self.queue_status_store._queued_epoch(record) or float("inf")
        request_id = str(record.get("request_id") or "").strip()
        capacity_key = self.capacity_key_for_record(record)
        if capacity_key != "thinking":
            return (0, queued_epoch, request_id)
        age_seconds = 0 if queued_epoch == float("inf") else max(0, int((now_epoch or time.time()) - queued_epoch))
        bucket = 0 if age_seconds >= self.thinking_starvation_seconds else 1
        return (bucket, queued_epoch, request_id)

    def pick_next_request(self, *, now_epoch: float | None = None) -> dict[str, Any] | None:
        queued = self.queue_status_store.list_requests(status="queued")
        if not queued:
            return None
        return min(
            queued,
            key=lambda record: self.queue_priority_for_record(record, now_epoch=now_epoch),
        )

    def claim_next_request(
        self,
        *,
        owner_id: str,
        admitted_at: str,
        lease_ttl_seconds: int,
        now_epoch: float | None = None,
    ) -> AdmissionDispatchResult:
        queued = sorted(
            self.queue_status_store.list_requests(status="queued"),
            key=lambda record: self.queue_priority_for_record(record, now_epoch=now_epoch),
        )
        if not queued:
            return AdmissionDispatchResult(outcome="no_queued")
        saw_capacity_exhausted = False
        saw_not_ready = False
        last_not_ready_reason = ""
        for record in queued:
            ready, reason = self._ready_for_dispatch(record)
            if not ready:
                if str(record.get("target_backend") or "").strip().lower() == "patent":
                    return self.claim_request(
                        str(record.get("request_id") or "").strip(),
                        owner_id=owner_id,
                        admitted_at=admitted_at,
                        lease_ttl_seconds=lease_ttl_seconds,
                    )
                saw_not_ready = True
                last_not_ready_reason = reason
                continue
            if not self._capacity_available(self.capacity_key_for_record(record)):
                saw_capacity_exhausted = True
                continue
            return self.claim_request(
                str(record.get("request_id") or "").strip(),
                owner_id=owner_id,
                admitted_at=admitted_at,
                lease_ttl_seconds=lease_ttl_seconds,
            )
        if saw_capacity_exhausted:
            return AdmissionDispatchResult(outcome="capacity_exhausted")
        if saw_not_ready:
            return AdmissionDispatchResult(outcome="not_ready", reason=last_not_ready_reason)
        return AdmissionDispatchResult(outcome="no_queued")

    def claim_request(
        self,
        request_id: str,
        *,
        owner_id: str,
        admitted_at: str,
        lease_ttl_seconds: int,
    ) -> AdmissionDispatchResult:
        record = self.queue_status_store.get_request(request_id)
        if not isinstance(record, dict):
            return AdmissionDispatchResult(outcome="not_found", request_id=request_id)
        if str(record.get("status") or "").strip().lower() != "queued":
            return AdmissionDispatchResult(outcome="not_queued", request_id=request_id, request=record)
        capacity = self.capacity_key_for_record(record)
        if not self._capacity_available(capacity):
            return AdmissionDispatchResult(outcome="capacity_exhausted", request_id=request_id, request=record)
        ready, reason = self._ready_for_dispatch(record)
        if not ready:
            if str(record.get("target_backend") or "").strip().lower() == "patent":
                failed = self._store_terminal_request(
                    record,
                    terminal_status="failed",
                    timestamp_field="failed_at",
                    timestamp_value=admitted_at,
                    extra={"failure_reason": reason or "backend_not_ready"},
                )
                return AdmissionDispatchResult(
                    outcome="failed",
                    request_id=request_id,
                    reason=reason or "backend_not_ready",
                    request=failed,
                )
            return AdmissionDispatchResult(outcome="not_ready", request_id=request_id, reason=reason, request=record)

        lease = self.slot_lease_store.acquire(
            request_id=request_id,
            capacity_key=capacity,
            owner_id=owner_id,
            ttl_seconds=lease_ttl_seconds,
            acquired_at=admitted_at,
            metadata={
                "actual_mode": record.get("actual_mode"),
                "target_backend": record.get("target_backend"),
            },
        )
        if lease is None:
            return AdmissionDispatchResult(outcome="lease_conflict", request_id=request_id, request=record)

        updated = dict(record)
        updated["status"] = "admitted"
        updated["cancel_allowed"] = False
        updated["admitted_at"] = str(admitted_at)
        updated["lease_owner_id"] = str(owner_id)
        updated["backend_capacity_key"] = capacity
        updated["dispatch_attempts"] = int(record.get("dispatch_attempts") or 0) + 1
        ttl_seconds = self._request_ttl_seconds(record)
        stored = self.queue_status_store.put_request(updated, ttl_seconds=ttl_seconds)
        if not stored:
            self.slot_lease_store.release(request_id, owner_id=owner_id)
            return AdmissionDispatchResult(outcome="store_write_failed", request_id=request_id, request=record)
        return AdmissionDispatchResult(
            outcome="claimed",
            request_id=request_id,
            request=updated,
            lease=lease,
        )

    def requeue_request(
        self,
        request_id: str,
        *,
        owner_id: str,
        requeued_at: str,
        reason: str,
    ) -> AdmissionDispatchResult:
        record = self.queue_status_store.get_request(request_id)
        if not isinstance(record, dict):
            return AdmissionDispatchResult(outcome="not_found", request_id=request_id)
        if str(record.get("status") or "").strip().lower() != "admitted":
            return AdmissionDispatchResult(outcome="not_admitted", request_id=request_id, request=record)
        lease = self.slot_lease_store.get(request_id)
        allow_missing_lease = (not isinstance(lease, dict)) and self._record_owned_by(record, owner_id)
        if not isinstance(lease, dict) and not allow_missing_lease:
            return AdmissionDispatchResult(outcome="lease_missing", request_id=request_id, request=record)
        if isinstance(lease, dict) and str(lease.get("owner_id") or "").strip() != str(owner_id or "").strip():
            return AdmissionDispatchResult(outcome="lease_owner_mismatch", request_id=request_id, request=record)
        updated = dict(record)
        updated["status"] = "queued"
        updated["cancel_allowed"] = True
        updated["requeued_at"] = str(requeued_at)
        updated["last_dispatch_error"] = str(reason or "")
        updated.pop("lease_owner_id", None)
        ttl_seconds = self._request_ttl_seconds(record)
        if not self.queue_status_store.put_request(updated, ttl_seconds=ttl_seconds):
            return AdmissionDispatchResult(outcome="store_write_failed", request_id=request_id, request=record)
        if not allow_missing_lease:
            released = self.slot_lease_store.release(request_id, owner_id=owner_id)
            if not released and self.slot_lease_store.get(request_id) is not None:
                return AdmissionDispatchResult(outcome="lease_release_failed", request_id=request_id, request=updated)
        return AdmissionDispatchResult(outcome="requeued", request_id=request_id, reason=reason, request=updated)

    def complete_request(
        self,
        request_id: str,
        *,
        owner_id: str,
        terminal_status: str,
        completed_at: str,
        result_payload: Any | None = None,
        failure_reason: str | None = None,
    ) -> AdmissionDispatchResult:
        record = self.queue_status_store.get_request(request_id)
        if not isinstance(record, dict):
            return AdmissionDispatchResult(outcome="not_found", request_id=request_id)
        if str(record.get("status") or "").strip().lower() != "admitted":
            return AdmissionDispatchResult(outcome="not_admitted", request_id=request_id, request=record)
        normalized_status = self._normalize_terminal_status(terminal_status)
        if normalized_status is None:
            return AdmissionDispatchResult(outcome="invalid_terminal_status", request_id=request_id, request=record)
        lease = self.slot_lease_store.get(request_id)
        allow_missing_lease = (not isinstance(lease, dict)) and self._record_owned_by(record, owner_id)
        if not isinstance(lease, dict) and not allow_missing_lease:
            return AdmissionDispatchResult(outcome="lease_missing", request_id=request_id, request=record)
        if isinstance(lease, dict) and str(lease.get("owner_id") or "").strip() != str(owner_id or "").strip():
            return AdmissionDispatchResult(outcome="lease_owner_mismatch", request_id=request_id, request=record)
        ttl_seconds = self._request_ttl_seconds(record)
        if result_payload is not None:
            result_ttl = min(
                max(60, int(self.settings.admission.post_admit_attach_ttl_seconds)),
                max(60, ttl_seconds),
            )
            if not self.queue_status_store.put_result(request_id, result_payload, ttl_seconds=result_ttl):
                return AdmissionDispatchResult(outcome="result_store_failed", request_id=request_id, request=record)
        updated = dict(record)
        updated["status"] = normalized_status
        updated["cancel_allowed"] = False
        self._clear_terminal_timestamps(updated)
        updated[self._terminal_timestamp_field(normalized_status)] = str(completed_at)
        if normalized_status == "failed":
            updated["failure_reason"] = str(failure_reason or "execution_failed")
        else:
            updated.pop("failure_reason", None)
        if not self.queue_status_store.put_request(updated, ttl_seconds=ttl_seconds):
            if result_payload is not None:
                if not self.queue_status_store.delete_result(request_id):
                    return AdmissionDispatchResult(outcome="result_cleanup_failed", request_id=request_id, request=record)
            return AdmissionDispatchResult(outcome="store_write_failed", request_id=request_id, request=record)
        if not allow_missing_lease:
            released = self.slot_lease_store.release(request_id, owner_id=owner_id)
            if not released and self.slot_lease_store.get(request_id) is not None:
                return AdmissionDispatchResult(outcome="lease_release_failed", request_id=request_id, request=updated)
        return AdmissionDispatchResult(
            outcome=normalized_status,
            request_id=request_id,
            request=updated,
        )

    def _capacity_available(self, capacity_key: str) -> bool:
        slot_metrics = self.slot_lease_store.describe()
        active_total = int(slot_metrics.get("active_leases") or 0)
        capacity_counts = dict(slot_metrics.get("capacity_counts") or {})
        if active_total >= int(self.settings.admission.max_concurrent):
            return False
        limit = self._capacity_limit(capacity_key)
        if limit is None:
            return True
        return int(capacity_counts.get(capacity_key) or 0) < int(limit)

    def _capacity_limit(self, capacity_key: str) -> int | None:
        if capacity_key == "thinking":
            return int(self.settings.admission.thinking_max_concurrent)
        if capacity_key == "fast_or_patent":
            return int(self.settings.admission.fast_or_patent_max_concurrent)
        return None

    def _ready_for_dispatch(self, record: dict[str, Any]) -> tuple[bool, str]:
        if self.readiness_checker is None:
            return True, ""
        ready, reason = self.readiness_checker(record)
        return bool(ready), str(reason or "")

    def _request_ttl_seconds(self, record: dict[str, Any]) -> int:
        ttl_seconds = self.queue_status_store.request_ttl_seconds(str(record.get("request_id") or ""))
        if ttl_seconds is None or ttl_seconds <= 0:
            ttl_seconds = int(self.settings.admission.queued_ttl_seconds)
        return max(60, int(ttl_seconds))

    def _normalize_terminal_status(self, terminal_status: str) -> str | None:
        normalized = str(terminal_status or "").strip().lower()
        if normalized in {"completed", "failed", "cancelled", "expired"}:
            return normalized
        return None

    def _terminal_timestamp_field(self, terminal_status: str) -> str:
        mapping = {
            "completed": "completed_at",
            "failed": "failed_at",
            "cancelled": "cancelled_at",
            "expired": "expired_at",
        }
        return mapping.get(str(terminal_status or "").strip().lower(), "completed_at")

    def _clear_terminal_timestamps(self, record: dict[str, Any]) -> None:
        for field_name in ("completed_at", "failed_at", "cancelled_at", "expired_at"):
            record.pop(field_name, None)

    def _record_owned_by(self, record: dict[str, Any], owner_id: str) -> bool:
        return str(record.get("lease_owner_id") or "").strip() == str(owner_id or "").strip()

    def _store_terminal_request(
        self,
        record: dict[str, Any],
        *,
        terminal_status: str,
        timestamp_field: str,
        timestamp_value: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        updated = dict(record)
        updated["status"] = str(terminal_status or "failed").strip().lower() or "failed"
        updated["cancel_allowed"] = False
        updated[str(timestamp_field)] = str(timestamp_value)
        if extra:
            updated.update(extra)
        self.queue_status_store.put_request(
            updated,
            ttl_seconds=self._request_ttl_seconds(record),
        )
        return updated


class ExecutionAdmissionWorker:
    def __init__(
        self,
        *,
        dispatcher: ExecutionAdmissionDispatcher,
        owner_id: str,
        executor: Callable[..., AdmissionExecutionOutcome | dict[str, Any] | str | None],
        lease_ttl_seconds: int | None = None,
        timestamp_factory: Callable[[], str] | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.owner_id = str(owner_id or "").strip() or "admission-worker"
        self.executor = executor
        default_ttl = max(30, int(dispatcher.settings.admission.poll_interval_seconds) * 4)
        self.lease_ttl_seconds = max(1, int(lease_ttl_seconds or default_ttl))
        self.timestamp_factory = timestamp_factory or self._default_timestamp
        self._processed_cycles = 0
        self._claimed_requests = 0
        self._completed_requests = 0
        self._requeued_requests = 0
        self._failed_requests = 0
        self._executor_errors = 0
        self._last_result: AdmissionDispatchResult | None = None

    def run_dispatch_cycle(self, *, now_epoch: float | None = None) -> AdmissionDispatchResult:
        self._processed_cycles += 1
        claim = self.dispatcher.claim_next_request(
            owner_id=self.owner_id,
            admitted_at=self._timestamp(),
            lease_ttl_seconds=self.lease_ttl_seconds,
            now_epoch=now_epoch,
        )
        if claim.outcome != "claimed":
            self._remember_result(claim)
            return claim

        self._claimed_requests += 1
        request = dict(claim.request or {})
        lease = dict(claim.lease or {})
        request_id = str(claim.request_id or "").strip()

        try:
            execution = self._normalize_execution_outcome(self._invoke_executor(request, lease, request_id))
        except Exception as exc:
            logger.exception("gateway admission worker executor raised request_id=%s", request_id)
            self._executor_errors += 1
            final = self.dispatcher.requeue_request(
                request_id,
                owner_id=self.owner_id,
                requeued_at=self._timestamp(),
                reason=f"executor_exception:{type(exc).__name__}",
            )
            self._remember_result(final)
            return final

        normalized_outcome = str(execution.outcome or "").strip().lower()
        if normalized_outcome == "requeue":
            final = self.dispatcher.requeue_request(
                request_id,
                owner_id=self.owner_id,
                requeued_at=self._timestamp(),
                reason=execution.reason or "executor_requeue",
            )
        elif normalized_outcome == "failed":
            final = self.dispatcher.complete_request(
                request_id,
                owner_id=self.owner_id,
                terminal_status="failed",
                completed_at=self._timestamp(),
                result_payload=execution.result_payload,
                failure_reason=execution.reason or "execution_failed",
            )
        else:
            terminal_status, failure_reason = self._effective_terminal_status(execution)
            final = self.dispatcher.complete_request(
                request_id,
                owner_id=self.owner_id,
                terminal_status=terminal_status,
                completed_at=self._timestamp(),
                result_payload=execution.result_payload,
                failure_reason=failure_reason,
            )
        self._remember_result(final)
        return final

    def describe(self) -> dict[str, Any]:
        last_result = None
        if self._last_result is not None:
            last_result = {
                "outcome": self._last_result.outcome,
                "request_id": self._last_result.request_id,
                "reason": self._last_result.reason,
            }
        return {
            "owner_id": self.owner_id,
            "lease_ttl_seconds": self.lease_ttl_seconds,
            "processed_cycles": self._processed_cycles,
            "claimed_requests": self._claimed_requests,
            "completed_requests": self._completed_requests,
            "requeued_requests": self._requeued_requests,
            "failed_requests": self._failed_requests,
            "executor_errors": self._executor_errors,
            "last_result": last_result,
        }

    def _default_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _timestamp(self) -> str:
        return str(self.timestamp_factory())

    def _normalize_execution_outcome(
        self,
        value: AdmissionExecutionOutcome | dict[str, Any] | str | None,
    ) -> AdmissionExecutionOutcome:
        if isinstance(value, AdmissionExecutionOutcome):
            outcome = value
        elif isinstance(value, dict):
            outcome = AdmissionExecutionOutcome(
                outcome=str(value.get("outcome") or ""),
                reason=str(value.get("reason") or ""),
                result_payload=value.get("result_payload"),
                terminal_status=str(value.get("terminal_status") or "completed"),
            )
        elif isinstance(value, str):
            outcome = AdmissionExecutionOutcome(outcome=value)
        elif value is None:
            outcome = AdmissionExecutionOutcome(outcome="completed")
        else:
            outcome = AdmissionExecutionOutcome(outcome="failed", reason="invalid_executor_outcome")

        normalized = str(outcome.outcome or "").strip().lower()
        if normalized in {"completed", "requeue", "failed"}:
            return AdmissionExecutionOutcome(
                outcome=normalized,
                reason=outcome.reason,
                result_payload=outcome.result_payload,
                terminal_status=outcome.terminal_status,
            )
        return AdmissionExecutionOutcome(
            outcome="failed",
            reason=outcome.reason or f"invalid_executor_outcome:{normalized or 'missing'}",
            result_payload=outcome.result_payload,
            terminal_status="failed",
        )

    def _invoke_executor(
        self,
        request: dict[str, Any],
        lease: dict[str, Any],
        request_id: str,
    ) -> AdmissionExecutionOutcome | dict[str, Any] | str | None:
        renew_lease = self._lease_renew_callback(request_id)
        try:
            signature = inspect.signature(self.executor)
        except (TypeError, ValueError):
            return self.executor(request, lease)

        parameters = list(signature.parameters.values())
        positional_count = sum(
            1
            for parameter in parameters
            if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
        accepts_varargs = any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters)
        if accepts_varargs or positional_count >= 3:
            return self.executor(request, lease, renew_lease)
        return self.executor(request, lease)

    def _lease_renew_callback(self, request_id: str) -> Callable[..., dict[str, Any] | None]:
        def _renew_lease(*, ttl_seconds: int | None = None, renewed_at: str | None = None) -> dict[str, Any] | None:
            effective_ttl = max(1, int(ttl_seconds or self.lease_ttl_seconds))
            return self.dispatcher.slot_lease_store.renew(
                request_id=request_id,
                owner_id=self.owner_id,
                ttl_seconds=effective_ttl,
                renewed_at=renewed_at or self._timestamp(),
            )

        return _renew_lease

    def _effective_terminal_status(self, execution: AdmissionExecutionOutcome) -> tuple[str, str | None]:
        requested = str(execution.terminal_status or "completed").strip().lower() or "completed"
        if requested in {"completed", "failed", "cancelled", "expired"}:
            if requested == "failed":
                return ("failed", execution.reason or "execution_failed")
            return (requested, execution.reason if requested == "failed" else None)
        return ("failed", f"invalid_terminal_status:{requested}")

    def _remember_result(self, result: AdmissionDispatchResult) -> None:
        self._last_result = result
        if result.outcome == "completed":
            self._completed_requests += 1
        elif result.outcome == "requeued":
            self._requeued_requests += 1
        elif result.outcome == "failed":
            self._failed_requests += 1


def build_admission_status(
    *,
    settings: GatewaySettings,
    redis_runtime: GatewayRedisRuntime,
    queue_status_store: ExecutionQueueStatusStore | None = None,
    slot_lease_store: ExecutionSlotLeaseStore | None = None,
) -> dict[str, Any]:
    queue_metrics = queue_status_store.describe() if queue_status_store is not None else {}
    lease_metrics = slot_lease_store.describe() if slot_lease_store is not None else {}
    redis_status = dict(redis_runtime.status.to_dict())
    redis_status["live_available"] = bool(redis_runtime.service.probe())
    shared_state_required = bool(settings.admission.enabled)
    shared_state_ready = (not shared_state_required) or bool(redis_status["live_available"])
    degraded_reasons: list[str] = []
    if shared_state_required and not bool(redis_status["live_available"]):
        degraded_reasons.append("shared_redis_unavailable")
    return {
        "enabled": bool(settings.admission.enabled),
        "runtime_role": settings.admission.runtime_role,
        "dispatcher_enabled": bool(settings.admission.dispatcher_enabled),
        "is_admission_worker": bool(settings.admission.is_admission_worker),
        "poll_interval_seconds": int(settings.admission.poll_interval_seconds),
        "interactive_execution_max_concurrent": int(settings.admission.max_concurrent),
        "backend_specific_ceilings": {
            "fast_or_patent": int(settings.admission.fast_or_patent_max_concurrent),
            "thinking": int(settings.admission.thinking_max_concurrent),
        },
        "queued_ttl_seconds": int(settings.admission.queued_ttl_seconds),
        "post_admit_attach_ttl_seconds": int(settings.admission.post_admit_attach_ttl_seconds),
        "redis": redis_status,
        "queue_metrics": {
            "backlog": int(queue_metrics.get("queued_requests") or 0),
            "oldest_queued_age_seconds": queue_metrics.get("oldest_queued_age_seconds"),
        },
        "slot_metrics": {
            "active_leases": int(lease_metrics.get("active_leases") or 0),
            "capacity_counts": dict(lease_metrics.get("capacity_counts") or {}),
        },
        "shared_state_required": shared_state_required,
        "shared_state_ready": shared_state_ready,
        "degraded_reasons": degraded_reasons,
        "worker_script_supported": True,
        "request_path_cutover_enabled": False,
    }


def run_admission_worker(
    *,
    settings: GatewaySettings,
    redis_runtime: GatewayRedisRuntime,
    executor: Callable[..., AdmissionExecutionOutcome | dict[str, Any] | str | None] | None = None,
    worker_owner_id: str | None = None,
) -> int:
    if not settings.admission.is_admission_worker:
        logger.error("gateway admission worker refused to start with runtime_role=%s", settings.admission.runtime_role)
        return 2

    if not settings.admission.enabled or not settings.admission.dispatcher_enabled:
        logger.info("gateway admission worker exiting because admission is disabled")
        return 0

    if not redis_runtime.service.probe():
        logger.error("gateway admission worker requires shared redis but the runtime probe failed")
        return 3

    stop_event = threading.Event()

    def _handle_signal(signum, _frame) -> None:
        logger.info("gateway admission worker received signal=%s", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    status = build_admission_status(settings=settings, redis_runtime=redis_runtime)
    logger.info("gateway admission worker started infra-only mode status=%s", status)

    interval = max(1, int(settings.admission.poll_interval_seconds))
    worker: ExecutionAdmissionWorker | None = None
    if executor is not None:
        dispatcher = ExecutionAdmissionDispatcher(
            settings=settings,
            queue_status_store=ExecutionQueueStatusStore(redis_service=redis_runtime.service),
            slot_lease_store=ExecutionSlotLeaseStore(redis_service=redis_runtime.service),
        )
        worker = ExecutionAdmissionWorker(
            dispatcher=dispatcher,
            owner_id=worker_owner_id or build_admission_worker_owner_id(settings.admission.runtime_role),
            executor=executor,
        )
    while not stop_event.is_set():
        if worker is None:
            logger.debug("gateway admission worker heartbeat role=%s redis_available=%s", settings.admission.runtime_role, redis_runtime.service.available)
        else:
            cycle_result = worker.run_dispatch_cycle()
            logger.debug(
                "gateway admission worker cycle role=%s outcome=%s request_id=%s state=%s",
                settings.admission.runtime_role,
                cycle_result.outcome,
                cycle_result.request_id,
                worker.describe(),
            )
        if stop_event.wait(interval):
            break

    logger.info("gateway admission worker stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gateway admission infra-only worker")
    parser.add_argument("--poll-interval", type=int, default=0)
    args = parser.parse_args(argv)

    settings = GatewaySettings.from_env()
    if args.poll_interval > 0:
        settings = replace(
            settings,
            admission=replace(
                settings.admission,
                poll_interval_seconds=max(1, int(args.poll_interval)),
            ),
        )

    setup_logging(settings.debug)
    from app.integrations.redis.service import bootstrap_redis_runtime

    redis_runtime = bootstrap_redis_runtime(settings.redis)
    return run_admission_worker(settings=settings, redis_runtime=redis_runtime)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
