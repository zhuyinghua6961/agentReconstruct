from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import DefaultDict


_COUNTERS: DefaultDict[str, DefaultDict[str, int]] = defaultdict(lambda: defaultdict(int))
_LOCK = Lock()


def increment_cache_metric(namespace: str, metric: str, value: int = 1) -> None:
    ns = str(namespace or "").strip() or "unknown"
    key = str(metric or "").strip()
    if not key:
        return
    delta = int(value or 0)
    if delta == 0:
        return
    with _LOCK:
        _COUNTERS["all"][key] += delta
        _COUNTERS[ns][key] += delta


def snapshot_cache_metrics() -> dict[str, dict[str, int]]:
    with _LOCK:
        return {
            namespace: {metric: int(count) for metric, count in counters.items()}
            for namespace, counters in _COUNTERS.items()
        }


def reset_cache_metrics() -> None:
    with _LOCK:
        _COUNTERS.clear()

