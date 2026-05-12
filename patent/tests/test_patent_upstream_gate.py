from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from server.patent.upstream_gate import PatentPlanningUpstreamGate, PatentPlanningUpstreamGateCancelled


def test_patent_upstream_gate_from_env_ignores_disabled_switch(monkeypatch):
    monkeypatch.setenv("PATENT_PLANNING_UPSTREAM_GATE_ENABLED", "false")
    monkeypatch.setenv("PATENT_PLANNING_UPSTREAM_GATE_LIMIT", "3")

    gate = PatentPlanningUpstreamGate.from_env()

    assert gate is not None
    assert gate.limit == 3


def test_patent_upstream_gate_enforces_configured_concurrency():
    gate = PatentPlanningUpstreamGate(name="planning", limit=1, poll_interval_seconds=0.01)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def _first_worker():
        with gate.enter(trace_label="first"):
            first_entered.set()
            release_first.wait(timeout=1.0)

    thread = threading.Thread(target=_first_worker)
    thread.start()
    assert first_entered.wait(timeout=1.0) is True

    with threading.Lock():
        pass

    def _second_worker():
        with gate.enter(trace_label="second"):
            second_entered.set()

    second = threading.Thread(target=_second_worker)
    second.start()
    time.sleep(0.05)

    assert second_entered.is_set() is False
    release_first.set()
    thread.join(timeout=1.0)
    second.join(timeout=1.0)
    assert second_entered.is_set() is True


def test_patent_upstream_gate_derives_effective_concurrency_from_ready_lanes():
    ready_lanes = 1
    gate = PatentPlanningUpstreamGate(
        name="planning",
        limit=3,
        limit_provider=lambda: ready_lanes,
        poll_interval_seconds=0.01,
    )
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def _first_worker():
        with gate.enter(trace_label="first"):
            first_entered.set()
            release_first.wait(timeout=1.0)

    thread = threading.Thread(target=_first_worker)
    thread.start()
    assert first_entered.wait(timeout=1.0) is True

    def _second_worker():
        with gate.enter(trace_label="second"):
            second_entered.set()

    second = threading.Thread(target=_second_worker)
    second.start()
    time.sleep(0.05)

    assert second_entered.is_set() is False
    release_first.set()
    thread.join(timeout=1.0)
    second.join(timeout=1.0)
    assert second_entered.is_set() is True


def test_patent_upstream_gate_cancelled_wait_exits_cleanly():
    cancel = threading.Event()
    gate = PatentPlanningUpstreamGate(name="planning", limit=1, poll_interval_seconds=0.01)
    first_entered = threading.Event()
    release_first = threading.Event()
    error_holder: list[Exception] = []

    def _first_worker():
        with gate.enter(trace_label="first"):
            first_entered.set()
            release_first.wait(timeout=1.0)

    thread = threading.Thread(target=_first_worker)
    thread.start()
    assert first_entered.wait(timeout=1.0) is True

    def _second_worker():
        try:
            with gate.enter(trace_label="second", should_cancel=cancel.is_set):
                raise AssertionError("should not enter")
        except Exception as exc:  # pragma: no branch - test captures exact type below
            error_holder.append(exc)

    second = threading.Thread(target=_second_worker)
    second.start()
    time.sleep(0.05)
    cancel.set()
    second.join(timeout=1.0)
    release_first.set()
    thread.join(timeout=1.0)

    assert len(error_holder) == 1
    assert isinstance(error_holder[0], PatentPlanningUpstreamGateCancelled)


def test_patent_upstream_gate_bypasses_wait_when_dynamic_limit_is_zero():
    calls: list[dict[str, object]] = []
    gate = PatentPlanningUpstreamGate(
        name="planning",
        limit=2,
        limit_provider=lambda: 0,
        poll_interval_seconds=0.01,
    )
    base_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: calls.append(kwargs) or {"ok": True},
            )
        )
    )

    proxy = gate.proxy_client(base_client=base_client, trace_label="stage1")
    result = proxy.chat.completions.create(model="planner-model")

    assert result == {"ok": True}
    assert calls == [{"model": "planner-model"}]
    assert gate.snapshot()["in_flight"] == 0
