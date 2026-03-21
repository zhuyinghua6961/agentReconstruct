from __future__ import annotations

import atexit
import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Hashable


class OrderedTaskDispatcher:
    def __init__(self, *, max_workers: int = 4, logger: Any = None) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="fastqa-bg",
        )
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._tails: dict[Hashable, Future] = {}
        atexit.register(self.shutdown)

    def submit(
        self,
        *,
        key: Hashable,
        fn: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> Future:
        kwargs = kwargs or {}
        with self._lock:
            prev_future = self._tails.get(key)

            def _runner():
                if prev_future is not None:
                    try:
                        prev_future.result()
                    except Exception:
                        pass
                return fn(*args, **kwargs)

            future = self._executor.submit(_runner)
            self._tails[key] = future

        def _cleanup(done: Future) -> None:
            with self._lock:
                if self._tails.get(key) is done:
                    self._tails.pop(key, None)
            try:
                exc = done.exception()
            except Exception as callback_exc:
                self._logger.warning("background task callback failed: %s", callback_exc)
                return
            if exc is not None:
                self._logger.warning("background task failed (key=%s): %s", key, exc)

        future.add_done_callback(_cleanup)
        return future

    def shutdown(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=False)
        except Exception:
            pass


_DISPATCHER_LOCK = threading.Lock()
_DEFAULT_DISPATCHER: OrderedTaskDispatcher | None = None


def get_default_dispatcher() -> OrderedTaskDispatcher:
    global _DEFAULT_DISPATCHER
    if _DEFAULT_DISPATCHER is not None:
        return _DEFAULT_DISPATCHER
    with _DISPATCHER_LOCK:
        if _DEFAULT_DISPATCHER is None:
            workers_raw = str(os.getenv("CHAT_PERSIST_ASYNC_WORKERS", "4")).strip() or "4"
            try:
                max_workers = max(1, int(workers_raw))
            except Exception:
                max_workers = 4
            _DEFAULT_DISPATCHER = OrderedTaskDispatcher(max_workers=max_workers)
    return _DEFAULT_DISPATCHER
