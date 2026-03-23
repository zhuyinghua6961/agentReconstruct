import threading
import time

from server.runtime.ordered_task_dispatcher import OrderedTaskDispatcher


def test_ordered_task_dispatcher_preserves_fifo_per_key():
    dispatcher = OrderedTaskDispatcher(max_workers=4)
    results = []
    results_lock = threading.Lock()
    release_first = threading.Event()

    def append(value: str, *, wait_for_release: bool = False):
        if wait_for_release:
            release_first.wait(timeout=1)
        with results_lock:
            results.append(value)

    first = dispatcher.submit(
        key="conversation:1:2",
        fn=append,
        kwargs={"value": "user", "wait_for_release": True},
    )
    second = dispatcher.submit(
        key="conversation:1:2",
        fn=append,
        kwargs={"value": "assistant"},
    )

    time.sleep(0.05)
    release_first.set()

    first.result(timeout=1)
    second.result(timeout=1)

    assert results == ["user", "assistant"]


def test_ordered_task_dispatcher_allows_parallelism_across_keys():
    dispatcher = OrderedTaskDispatcher(max_workers=4)
    started = []
    release = threading.Event()
    started_lock = threading.Lock()

    def worker(label: str):
        with started_lock:
            started.append(label)
        release.wait(timeout=1)
        return label

    future_a = dispatcher.submit(key="conversation:1:1", fn=worker, kwargs={"label": "a"})
    future_b = dispatcher.submit(key="conversation:2:2", fn=worker, kwargs={"label": "b"})

    time.sleep(0.05)
    release.set()

    assert sorted(started) == ["a", "b"]
    assert future_a.result(timeout=1) == "a"
    assert future_b.result(timeout=1) == "b"
