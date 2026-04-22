from __future__ import annotations

from threading import Event, Thread
import time

from app.integrations.llm.upstream_gate import SharedStage2UpstreamGate, Stage2UpstreamGateCancelled


def test_shared_stage2_upstream_gate_serializes_across_threads():
    gate = SharedStage2UpstreamGate(
        name="chat",
        limit=3,
        logger=None,
        limit_provider=lambda: 1,
        poll_interval_seconds=0.01,
    )
    order: list[str] = []
    holder_started = Event()
    release_holder = Event()
    waiter_finished = Event()

    def _holder() -> None:
        with gate.enter(trace_label="claim_1", request_limit=3):
            order.append("holder_entered")
            holder_started.set()
            release_holder.wait(1.0)
        order.append("holder_released")

    def _waiter() -> None:
        holder_started.wait(1.0)
        with gate.enter(trace_label="claim_2", request_limit=3):
            order.append("waiter_entered")
        waiter_finished.set()

    holder_thread = Thread(target=_holder)
    waiter_thread = Thread(target=_waiter)
    holder_thread.start()
    waiter_thread.start()

    holder_started.wait(1.0)
    time.sleep(0.05)
    assert order == ["holder_entered"]
    assert waiter_finished.is_set() is False

    release_holder.set()
    holder_thread.join(1.0)
    waiter_thread.join(1.0)

    assert order == ["holder_entered", "holder_released", "waiter_entered"]
    assert waiter_finished.is_set() is True


def test_shared_stage2_upstream_gate_cancels_waiting_enter():
    gate = SharedStage2UpstreamGate(
        name="rerank",
        limit=1,
        logger=None,
        limit_provider=lambda: 1,
        poll_interval_seconds=0.01,
    )
    holder_started = Event()
    release_holder = Event()
    cancel_waiter = Event()
    waiter_error: list[str] = []

    def _holder() -> None:
        with gate.enter(trace_label="claim_1", request_limit=1):
            holder_started.set()
            release_holder.wait(1.0)

    def _waiter() -> None:
        holder_started.wait(1.0)
        try:
            with gate.enter(
                trace_label="claim_2",
                request_limit=1,
                should_cancel=lambda: cancel_waiter.is_set(),
            ):
                raise AssertionError("waiter should not acquire the gate after cancellation")
        except Stage2UpstreamGateCancelled as exc:
            waiter_error.append(str(exc))

    holder_thread = Thread(target=_holder)
    waiter_thread = Thread(target=_waiter)
    holder_thread.start()
    waiter_thread.start()

    holder_started.wait(1.0)
    time.sleep(0.05)
    cancel_waiter.set()
    waiter_thread.join(1.0)
    release_holder.set()
    holder_thread.join(1.0)

    assert waiter_error == ["stage2 rerank gate wait cancelled"]
