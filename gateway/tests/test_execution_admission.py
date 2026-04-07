from dataclasses import replace
import threading

from app.integrations.redis.service import (
    GatewayRedisRuntime,
    GatewayRedisRuntimeStatus,
    RedisService,
)
from app.core.config import GatewaySettings
from app.integrations.redis.service import bootstrap_redis_runtime
from app.services.execution_admission import (
    AdmissionExecutionOutcome,
    ExecutionAdmissionDispatcher,
    ExecutionAdmissionWorker,
    build_admission_worker_owner_id,
    build_admission_status,
    main,
    run_admission_worker,
)
from app.services.execution_queue_status import ExecutionQueueStatusStore
from app.services.execution_slot_leases import ExecutionSlotLeaseStore


def _settings_with_admission_overrides(**overrides) -> GatewaySettings:
    base = GatewaySettings.from_env()
    return replace(base, admission=replace(base.admission, **overrides))


def test_build_admission_status_exposes_task_api_defaults(monkeypatch):
    monkeypatch.delenv("GATEWAY_ADMISSION_ENABLED", raising=False)
    monkeypatch.delenv("GATEWAY_RUNTIME_ROLE", raising=False)
    monkeypatch.delenv("REDIS_ENABLED", raising=False)

    settings = GatewaySettings.from_env()
    redis_runtime = bootstrap_redis_runtime(settings.redis)
    payload = build_admission_status(
        settings=settings,
        redis_runtime=redis_runtime,
        queue_status_store=ExecutionQueueStatusStore(
            redis_service=RedisService.from_prefix(client=None, key_prefix="gateway")
        ),
        slot_lease_store=ExecutionSlotLeaseStore(
            redis_service=RedisService.from_prefix(client=None, key_prefix="gateway")
        ),
    )

    assert payload["enabled"] is False
    assert payload["runtime_role"] == "web"
    assert payload["worker_script_supported"] is True
    assert payload["request_path_cutover_enabled"] is True
    assert payload["backend_specific_ceilings"]["fast_or_patent"] == 20
    assert payload["backend_specific_ceilings"]["thinking"] == 5
    assert payload["per_user_max_active"] == 5
    assert payload["thinking_min_slots"] == 1
    assert payload["queue_max_size"] == 200
    assert payload["queue_metrics"]["backlog"] == 0
    assert payload["slot_metrics"]["active_leases"] == 0
    assert payload["shared_state_ready"] is True


def test_run_admission_worker_rejects_wrong_runtime_role(monkeypatch):
    monkeypatch.setenv("GATEWAY_RUNTIME_ROLE", "web")
    monkeypatch.setenv("GATEWAY_ADMISSION_ENABLED", "1")

    settings = GatewaySettings.from_env()
    redis_runtime = bootstrap_redis_runtime(settings.redis)

    assert run_admission_worker(settings=settings, redis_runtime=redis_runtime) == 2


def test_run_admission_worker_exits_cleanly_when_disabled(monkeypatch):
    monkeypatch.setenv("GATEWAY_RUNTIME_ROLE", "admission_worker")
    monkeypatch.setenv("GATEWAY_ADMISSION_ENABLED", "0")

    settings = GatewaySettings.from_env()
    redis_runtime = bootstrap_redis_runtime(settings.redis)

    assert run_admission_worker(settings=settings, redis_runtime=redis_runtime) == 0


class _DeadRedis:
    def ping(self):
        raise RuntimeError("redis down")


def test_build_admission_status_reports_degraded_shared_state_when_redis_unavailable(monkeypatch):
    monkeypatch.setenv("GATEWAY_RUNTIME_ROLE", "admission_worker")
    monkeypatch.setenv("GATEWAY_ADMISSION_ENABLED", "1")

    settings = GatewaySettings.from_env()
    dead_client = _DeadRedis()
    redis_runtime = GatewayRedisRuntime(
        client=dead_client,
        service=RedisService.from_prefix(client=dead_client, key_prefix="gateway"),
        status=GatewayRedisRuntimeStatus(
            enabled=True,
            available=True,
            dependency_available=True,
            client_source="host_port",
            key_prefix="gateway",
        ),
    )

    payload = build_admission_status(
        settings=settings,
        redis_runtime=redis_runtime,
        queue_status_store=ExecutionQueueStatusStore(
            redis_service=RedisService.from_prefix(client=None, key_prefix="gateway")
        ),
        slot_lease_store=ExecutionSlotLeaseStore(
            redis_service=RedisService.from_prefix(client=None, key_prefix="gateway")
        ),
    )

    assert payload["redis"]["available"] is True
    assert payload["redis"]["live_available"] is False
    assert payload["shared_state_ready"] is False
    assert "shared_redis_unavailable" in payload["degraded_reasons"]


def test_run_admission_worker_fails_closed_when_shared_redis_unavailable(monkeypatch):
    monkeypatch.setenv("GATEWAY_RUNTIME_ROLE", "admission_worker")
    monkeypatch.setenv("GATEWAY_ADMISSION_ENABLED", "1")

    settings = GatewaySettings.from_env()
    dead_client = _DeadRedis()
    redis_runtime = GatewayRedisRuntime(
        client=dead_client,
        service=RedisService.from_prefix(client=dead_client, key_prefix="gateway"),
        status=GatewayRedisRuntimeStatus(
            enabled=True,
            available=True,
            dependency_available=True,
            client_source="host_port",
            key_prefix="gateway",
        ),
    )

    assert run_admission_worker(settings=settings, redis_runtime=redis_runtime) == 3


def test_admission_main_poll_interval_preserves_full_settings_contract(monkeypatch):
    monkeypatch.setenv("GATEWAY_RUNTIME_ROLE", "admission_worker")
    monkeypatch.setenv("GATEWAY_ADMISSION_ENABLED", "0")
    captured: dict[str, object] = {}

    def _fake_setup_logging(debug: bool) -> None:
        captured["debug"] = debug

    def _fake_bootstrap(redis_settings):
        captured["redis"] = redis_settings
        return object()

    def _fake_run_admission_worker(*, settings, redis_runtime):
        captured["settings"] = settings
        captured["redis_runtime"] = redis_runtime
        return 0

    monkeypatch.setattr("app.services.execution_admission.setup_logging", _fake_setup_logging)
    monkeypatch.setattr("app.integrations.redis.service.bootstrap_redis_runtime", _fake_bootstrap)
    monkeypatch.setattr("app.services.execution_admission.run_admission_worker", _fake_run_admission_worker)

    exit_code = main(["--poll-interval", "7"])

    settings = captured["settings"]
    assert exit_code == 0
    assert settings.admission.poll_interval_seconds == 7
    assert settings.route_classifier.provider == GatewaySettings.from_env().route_classifier.provider


def _memory_queue_store() -> ExecutionQueueStatusStore:
    return ExecutionQueueStatusStore(redis_service=RedisService.from_prefix(client=None, key_prefix="gateway"))


def _memory_slot_store() -> ExecutionSlotLeaseStore:
    return ExecutionSlotLeaseStore(redis_service=RedisService.from_prefix(client=None, key_prefix="gateway"))


def _queued_record(
    request_id: str,
    *,
    actual_mode: str,
    enqueued_at: str,
    target_backend: str | None = None,
    backend_capacity_key: str | None = None,
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "status": "queued",
        "cancel_allowed": True,
        "requested_mode": actual_mode,
        "actual_mode": actual_mode,
        "route": "kb_qa",
        "target_backend": target_backend or actual_mode,
        "backend_capacity_key": backend_capacity_key,
        "transport_kind": "sse",
        "enqueued_at": enqueued_at,
        "execution_snapshot": {"question": request_id},
    }


def test_dispatcher_prefers_fast_or_patent_before_thinking(monkeypatch):
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_thinking", actual_mode="thinking", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    queue_store.put_request(
        _queued_record("req_fast", actual_mode="fast", enqueued_at="2026-03-30T10:00:01+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
        thinking_starvation_seconds=300,
    )

    picked = dispatcher.pick_next_request(now_epoch=1774864805.0)

    assert picked is not None
    assert picked["request_id"] == "req_fast"


def test_dispatcher_promotes_old_thinking_request_after_starvation_threshold():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_old_thinking", actual_mode="thinking", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    queue_store.put_request(
        _queued_record("req_fast", actual_mode="fast", enqueued_at="2026-03-30T10:03:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
        thinking_starvation_seconds=60,
    )

    picked = dispatcher.pick_next_request(now_epoch=1774865100.0)

    assert picked is not None
    assert picked["request_id"] == "req_old_thinking"


def test_dispatcher_claim_next_request_marks_record_admitted_and_creates_lease():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_claim", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )

    result = dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )

    admitted = queue_store.get_request("req_claim")

    assert result.outcome == "claimed"
    assert result.request_id == "req_claim"
    assert result.lease is not None
    assert admitted is not None
    assert admitted["status"] == "admitted"
    assert admitted["cancel_allowed"] is False
    assert admitted["lease_owner_id"] == "worker-a"
    assert slot_store.describe()["active_leases"] == 1


def test_claim_request_enforces_capacity_atomically_under_concurrent_workers():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    settings = _settings_with_admission_overrides(
        max_concurrent=1,
        fast_or_patent_max_concurrent=1,
        thinking_max_concurrent=1,
    )
    queue_store.put_request(
        _queued_record("req_atomic_1", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    queue_store.put_request(
        _queued_record("req_atomic_2", actual_mode="fast", enqueued_at="2026-03-30T10:00:01+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    original_acquire = slot_store.acquire
    barrier = threading.Barrier(2)

    def delayed_acquire(**kwargs):
        try:
            barrier.wait(timeout=1)
        except threading.BrokenBarrierError:
            pass
        return original_acquire(**kwargs)

    slot_store.acquire = delayed_acquire  # type: ignore[method-assign]
    results: dict[str, object] = {}

    def run_claim(request_id: str, owner_id: str):
        results[request_id] = dispatcher.claim_request(
            request_id,
            owner_id=owner_id,
            admitted_at="2026-03-30T10:01:00+00:00",
            lease_ttl_seconds=30,
        )

    thread_1 = threading.Thread(target=run_claim, args=("req_atomic_1", "worker-a"), daemon=True)
    thread_2 = threading.Thread(target=run_claim, args=("req_atomic_2", "worker-b"), daemon=True)
    thread_1.start()
    thread_2.start()
    thread_1.join(timeout=5)
    thread_2.join(timeout=5)

    assert not thread_1.is_alive()
    assert not thread_2.is_alive()
    outcomes = sorted(result.outcome for result in results.values())
    assert outcomes == ["capacity_exhausted", "claimed"]
    assert slot_store.describe()["active_leases"] == 1


def test_dispatcher_respects_capacity_limits_before_claiming():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_blocked", actual_mode="thinking", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    slot_store.acquire(
        request_id="req_existing",
        capacity_key="thinking",
        owner_id="worker-z",
        ttl_seconds=30,
        acquired_at="2026-03-30T10:00:00+00:00",
    )
    monkeypatch_settings = _settings_with_admission_overrides(
        max_concurrent=10,
        fast_or_patent_max_concurrent=10,
        thinking_max_concurrent=1,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=monkeypatch_settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )

    result = dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )

    assert result.outcome == "capacity_exhausted"
    assert queue_store.get_request("req_blocked")["status"] == "queued"


def test_dispatcher_skips_capacity_blocked_starved_thinking_and_claims_fast():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("think_old", actual_mode="thinking", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    queue_store.put_request(
        _queued_record("fast_new", actual_mode="fast", enqueued_at="2026-03-30T10:03:00+00:00"),
        ttl_seconds=900,
    )
    slot_store.acquire(
        request_id="req_existing",
        capacity_key="thinking",
        owner_id="worker-z",
        ttl_seconds=30,
        acquired_at="2026-03-30T10:00:00+00:00",
    )
    settings = _settings_with_admission_overrides(
        max_concurrent=10,
        fast_or_patent_max_concurrent=10,
        thinking_max_concurrent=1,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
        thinking_starvation_seconds=60,
    )

    result = dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:03:10+00:00",
        lease_ttl_seconds=30,
        now_epoch=1774864990.0,
    )

    assert result.outcome == "claimed"
    assert result.request_id == "fast_new"
    assert queue_store.get_request("think_old")["status"] == "queued"


def test_dispatcher_reserves_thinking_min_slot_before_high_tier_when_none_active():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("think_first", actual_mode="thinking", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    queue_store.put_request(
        _queued_record("fast_second", actual_mode="fast", enqueued_at="2026-03-30T10:00:01+00:00"),
        ttl_seconds=900,
    )
    slot_store.acquire(
        request_id="req_existing_fast",
        capacity_key="fast_or_patent",
        owner_id="worker-z",
        ttl_seconds=30,
        acquired_at="2026-03-30T10:00:00+00:00",
    )
    settings = _settings_with_admission_overrides(
        max_concurrent=2,
        fast_or_patent_max_concurrent=20,
        thinking_max_concurrent=5,
        thinking_min_slots=1,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=settings,
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
        thinking_starvation_seconds=300,
    )

    result = dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )

    assert result.outcome == "claimed"
    assert result.request_id == "think_first"


def test_dispatcher_fails_patent_request_when_readiness_checker_rejects():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record(
            "req_patent",
            actual_mode="patent",
            target_backend="patent",
            backend_capacity_key="fast_or_patent",
            enqueued_at="2026-03-30T10:00:00+00:00",
        ),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
        readiness_checker=lambda record: (False, "patent_not_ready"),
    )

    result = dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )

    failed = queue_store.get_request("req_patent")

    assert result.outcome == "failed"
    assert result.reason == "patent_not_ready"
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["failure_reason"] == "patent_not_ready"
    assert slot_store.describe()["active_leases"] == 0


def test_dispatcher_can_requeue_admitted_request_after_transient_failure():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_requeue", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    claim = dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )

    result = dispatcher.requeue_request(
        "req_requeue",
        owner_id="worker-a",
        requeued_at="2026-03-30T10:00:06+00:00",
        reason="backend_busy",
    )

    record = queue_store.get_request("req_requeue")

    assert claim.outcome == "claimed"
    assert result.outcome == "requeued"
    assert record is not None
    assert record["status"] == "queued"
    assert record["cancel_allowed"] is True
    assert record["last_dispatch_error"] == "backend_busy"
    assert "lease_owner_id" not in record
    assert slot_store.describe()["active_leases"] == 0


def test_dispatcher_requeue_rejects_wrong_owner_and_keeps_admitted_state():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_wrong_owner", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )

    result = dispatcher.requeue_request(
        "req_wrong_owner",
        owner_id="worker-b",
        requeued_at="2026-03-30T10:00:06+00:00",
        reason="backend_busy",
    )

    assert result.outcome == "lease_owner_mismatch"
    assert queue_store.get_request("req_wrong_owner")["status"] == "admitted"
    assert slot_store.describe()["active_leases"] == 1


def test_dispatcher_can_complete_request_and_persist_result():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_done", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    claim = dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )

    result = dispatcher.complete_request(
        "req_done",
        owner_id="worker-a",
        terminal_status="completed",
        completed_at="2026-03-30T10:00:09+00:00",
        result_payload={"answer": "ok"},
    )

    record = queue_store.get_request("req_done")

    assert claim.outcome == "claimed"
    assert result.outcome == "completed"
    assert record is not None
    assert record["status"] == "completed"
    assert record["completed_at"] == "2026-03-30T10:00:09+00:00"
    assert queue_store.get_result("req_done") == {"answer": "ok"}
    assert slot_store.describe()["active_leases"] == 0


def test_dispatcher_complete_leaves_request_admitted_when_result_write_fails():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_result_fail", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )
    queue_store.put_result = lambda *args, **kwargs: False  # type: ignore[assignment]

    result = dispatcher.complete_request(
        "req_result_fail",
        owner_id="worker-a",
        terminal_status="completed",
        completed_at="2026-03-30T10:00:09+00:00",
        result_payload={"answer": "ok"},
    )

    assert result.outcome == "result_store_failed"
    assert queue_store.get_request("req_result_fail")["status"] == "admitted"
    assert slot_store.describe()["active_leases"] == 1


def test_dispatcher_complete_rejects_wrong_owner_and_keeps_admitted_state():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_done_wrong_owner", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )

    result = dispatcher.complete_request(
        "req_done_wrong_owner",
        owner_id="worker-b",
        terminal_status="completed",
        completed_at="2026-03-30T10:00:09+00:00",
        result_payload={"answer": "ok"},
    )

    assert result.outcome == "lease_owner_mismatch"
    assert queue_store.get_request("req_done_wrong_owner")["status"] == "admitted"
    assert slot_store.describe()["active_leases"] == 1


def test_dispatcher_complete_rejects_request_that_has_already_been_requeued():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_requeue_then_complete", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )
    dispatcher.requeue_request(
        "req_requeue_then_complete",
        owner_id="worker-a",
        requeued_at="2026-03-30T10:00:06+00:00",
        reason="backend_busy",
    )

    result = dispatcher.complete_request(
        "req_requeue_then_complete",
        owner_id="worker-a",
        terminal_status="completed",
        completed_at="2026-03-30T10:00:09+00:00",
        result_payload={"answer": "ok"},
    )

    record = queue_store.get_request("req_requeue_then_complete")

    assert result.outcome == "not_admitted"
    assert record is not None
    assert record["status"] == "queued"
    assert "completed_at" not in record
    assert queue_store.get_result("req_requeue_then_complete") is None


def test_dispatcher_requeue_does_not_report_success_when_request_write_fails():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_requeue_fail", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )
    queue_store.put_request = lambda *args, **kwargs: False  # type: ignore[assignment]

    result = dispatcher.requeue_request(
        "req_requeue_fail",
        owner_id="worker-a",
        requeued_at="2026-03-30T10:00:06+00:00",
        reason="backend_busy",
    )

    assert result.outcome == "store_write_failed"
    assert queue_store.get_request("req_requeue_fail")["status"] == "admitted"
    assert slot_store.describe()["active_leases"] == 1


def test_dispatcher_complete_does_not_report_success_when_request_write_fails():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_complete_fail", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )
    queue_store.put_request = lambda *args, **kwargs: False  # type: ignore[assignment]

    result = dispatcher.complete_request(
        "req_complete_fail",
        owner_id="worker-a",
        terminal_status="completed",
        completed_at="2026-03-30T10:00:09+00:00",
        result_payload={"answer": "ok"},
    )

    assert result.outcome == "store_write_failed"
    assert queue_store.get_request("req_complete_fail")["status"] == "admitted"
    assert queue_store.get_result("req_complete_fail") is None
    assert slot_store.describe()["active_leases"] == 1


def test_dispatcher_complete_reports_result_cleanup_failure_when_compensation_fails():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_cleanup_fail", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    dispatcher.claim_next_request(
        owner_id="worker-a",
        admitted_at="2026-03-30T10:00:05+00:00",
        lease_ttl_seconds=30,
    )
    queue_store.put_request = lambda *args, **kwargs: False  # type: ignore[assignment]
    queue_store.delete_result = lambda request_id: False  # type: ignore[assignment]

    result = dispatcher.complete_request(
        "req_cleanup_fail",
        owner_id="worker-a",
        terminal_status="completed",
        completed_at="2026-03-30T10:00:09+00:00",
        result_payload={"answer": "ok"},
    )

    assert result.outcome == "result_cleanup_failed"
    assert queue_store.get_request("req_cleanup_fail")["status"] == "admitted"
    assert slot_store.describe()["active_leases"] == 1


def test_worker_cycle_returns_idle_when_no_request_is_queued():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-a",
        executor=lambda request, lease: AdmissionExecutionOutcome(outcome="completed"),
        timestamp_factory=lambda: "2026-03-30T10:00:00+00:00",
    )

    result = worker.run_dispatch_cycle()

    assert result.outcome == "no_queued"
    assert worker.describe()["processed_cycles"] == 1
    assert worker.describe()["claimed_requests"] == 0
    assert worker.describe()["last_result"]["outcome"] == "no_queued"


def test_worker_cycle_completes_claimed_request_when_executor_succeeds():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_worker_complete", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-a",
        executor=lambda request, lease: AdmissionExecutionOutcome(
            outcome="completed",
            result_payload={"answer": request["request_id"]},
        ),
        timestamp_factory=lambda: "2026-03-30T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()
    record = queue_store.get_request("req_worker_complete")

    assert result.outcome == "completed"
    assert record is not None
    assert record["status"] == "completed"
    assert queue_store.get_result("req_worker_complete") == {"answer": "req_worker_complete"}
    assert slot_store.describe()["active_leases"] == 0
    assert worker.describe()["completed_requests"] == 1


def test_worker_cycle_requeues_request_when_executor_returns_transient_failure():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_worker_requeue", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-a",
        executor=lambda request, lease: AdmissionExecutionOutcome(
            outcome="requeue",
            reason="backend_busy",
        ),
        timestamp_factory=lambda: "2026-03-30T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()
    record = queue_store.get_request("req_worker_requeue")

    assert result.outcome == "requeued"
    assert record is not None
    assert record["status"] == "queued"
    assert record["last_dispatch_error"] == "backend_busy"
    assert slot_store.describe()["active_leases"] == 0
    assert worker.describe()["requeued_requests"] == 1


def test_worker_cycle_fails_request_when_executor_returns_terminal_failure():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_worker_failed", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-a",
        executor=lambda request, lease: AdmissionExecutionOutcome(
            outcome="failed",
            reason="backend_unavailable",
        ),
        timestamp_factory=lambda: "2026-03-30T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()
    record = queue_store.get_request("req_worker_failed")

    assert result.outcome == "failed"
    assert record is not None
    assert record["status"] == "failed"
    assert record["failure_reason"] == "backend_unavailable"
    assert record["failed_at"] == "2026-03-30T10:00:05+00:00"
    assert "completed_at" not in record
    assert slot_store.describe()["active_leases"] == 0
    assert worker.describe()["failed_requests"] == 1


def test_worker_cycle_requeues_request_when_executor_raises_exception():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_worker_raise", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )

    def _boom(request, lease):
        raise RuntimeError("executor exploded")

    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-a",
        executor=_boom,
        timestamp_factory=lambda: "2026-03-30T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()
    record = queue_store.get_request("req_worker_raise")

    assert result.outcome == "requeued"
    assert record is not None
    assert record["status"] == "queued"
    assert record["last_dispatch_error"] == "executor_exception:RuntimeError"
    assert slot_store.describe()["active_leases"] == 0
    assert worker.describe()["executor_errors"] == 1


def test_worker_cycle_exposes_lease_renew_callback_to_executor():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_worker_renew", actual_mode="thinking", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    captured: dict[str, object] = {}

    def _executor(request, lease, renew_lease):
        captured["renewed"] = renew_lease(
            ttl_seconds=120,
            renewed_at="2026-03-30T10:00:06+00:00",
        )
        return AdmissionExecutionOutcome(outcome="completed")

    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-a",
        executor=_executor,
        timestamp_factory=lambda: "2026-03-30T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()

    assert result.outcome == "completed"
    assert captured["renewed"] is not None
    assert captured["renewed"]["last_renewed_at"] == "2026-03-30T10:00:06+00:00"
    assert captured["renewed"]["lease_ttl_seconds"] == 120


def test_build_admission_worker_owner_id_uses_role_host_and_pid():
    assert build_admission_worker_owner_id("admission_worker", hostname="host-a", pid=3210) == "admission_worker:host-a:3210"


def test_worker_cycle_requeues_when_owned_lease_disappears_mid_execution():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_worker_missing_lease_requeue", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )

    def _executor(request, lease):
        slot_store.release(request["request_id"], owner_id="worker-a")
        return AdmissionExecutionOutcome(outcome="requeue", reason="backend_busy")

    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-a",
        executor=_executor,
        timestamp_factory=lambda: "2026-03-30T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()
    record = queue_store.get_request("req_worker_missing_lease_requeue")

    assert result.outcome == "requeued"
    assert record is not None
    assert record["status"] == "queued"
    assert record["last_dispatch_error"] == "backend_busy"
    assert slot_store.describe()["active_leases"] == 0


def test_worker_cycle_completes_when_owned_lease_disappears_mid_execution():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_worker_missing_lease_complete", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )

    def _executor(request, lease):
        slot_store.release(request["request_id"], owner_id="worker-a")
        return AdmissionExecutionOutcome(outcome="completed", result_payload={"answer": "ok"})

    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-a",
        executor=_executor,
        timestamp_factory=lambda: "2026-03-30T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()
    record = queue_store.get_request("req_worker_missing_lease_complete")

    assert result.outcome == "completed"
    assert record is not None
    assert record["status"] == "completed"
    assert queue_store.get_result("req_worker_missing_lease_complete") == {"answer": "ok"}
    assert slot_store.describe()["active_leases"] == 0


def test_worker_cycle_downgrades_invalid_terminal_status_to_failed():
    queue_store = _memory_queue_store()
    slot_store = _memory_slot_store()
    queue_store.put_request(
        _queued_record("req_worker_invalid_terminal", actual_mode="fast", enqueued_at="2026-03-30T10:00:00+00:00"),
        ttl_seconds=900,
    )
    dispatcher = ExecutionAdmissionDispatcher(
        settings=GatewaySettings.from_env(),
        queue_status_store=queue_store,
        slot_lease_store=slot_store,
    )
    worker = ExecutionAdmissionWorker(
        dispatcher=dispatcher,
        owner_id="worker-a",
        executor=lambda request, lease: AdmissionExecutionOutcome(
            outcome="completed",
            terminal_status="queued",
        ),
        timestamp_factory=lambda: "2026-03-30T10:00:05+00:00",
    )

    result = worker.run_dispatch_cycle()
    record = queue_store.get_request("req_worker_invalid_terminal")

    assert result.outcome == "failed"
    assert record is not None
    assert record["status"] == "failed"
    assert record["failure_reason"] == "invalid_terminal_status:queued"
    assert slot_store.describe()["active_leases"] == 0
