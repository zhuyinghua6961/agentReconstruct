from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, Iterator

from fastapi import Request
from fastapi.responses import StreamingResponse


def encode_sse(payload: dict[str, Any], *, event: str | None = None) -> str:
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    data = json.dumps(payload, ensure_ascii=False)
    for line in data.splitlines() or [""]:
        lines.append(f"data: {line}")
    lines.append("")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class _SyncQueueItem:
    kind: str
    payload: Any = None


_SYNC_DONE = "done"
_SYNC_EVENT = "event"
_SYNC_ERROR = "error"


async def _invoke_callback(callback: Callable[[], Awaitable[None] | None] | None) -> None:
    if callback is None:
        return
    maybe = callback()
    if asyncio.iscoroutine(maybe):
        await maybe


def _close_iterator(iterator: Any) -> None:
    close = getattr(iterator, "close", None)
    if close is not None:
        close()


def _start_sync_producer(
    *,
    iterator: Iterator[dict[str, Any]],
    queue: asyncio.Queue[_SyncQueueItem],
    loop: asyncio.AbstractEventLoop,
    stop_event: Event,
) -> Thread:
    def _publish(item: _SyncQueueItem) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def _run() -> None:
        try:
            for item in iterator:
                if stop_event.is_set():
                    break
                _publish(_SyncQueueItem(kind=_SYNC_EVENT, payload=item))
        except Exception as exc:  # pragma: no cover
            _publish(_SyncQueueItem(kind=_SYNC_ERROR, payload=exc))
        finally:
            try:
                _close_iterator(iterator)
            except Exception:
                pass
            _publish(_SyncQueueItem(kind=_SYNC_DONE))

    thread = Thread(target=_run, daemon=True, name="fastqa-sse-sync-producer")
    thread.start()
    return thread


async def iter_sse(
    *,
    request: Request,
    source: AsyncIterator[dict[str, Any]] | Iterable[dict[str, Any]],
    heartbeat_sec: int = 15,
    on_disconnect: Callable[[], Awaitable[None] | None] | None = None,
) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    last_emit = loop.time()
    disconnect_notified = False

    if hasattr(source, "__aiter__"):
        iterator = source.__aiter__()
        try:
            while True:
                if await request.is_disconnected():
                    disconnect_notified = True
                    break
                try:
                    item = await asyncio.wait_for(iterator.__anext__(), timeout=heartbeat_sec)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    now = loop.time()
                    if now - last_emit >= heartbeat_sec:
                        last_emit = now
                        yield ": heartbeat\n\n"
                    continue
                last_emit = loop.time()
                yield encode_sse(item)
        finally:
            if disconnect_notified:
                await _invoke_callback(on_disconnect)
            close = getattr(iterator, "aclose", None)
            if close is not None:
                await close()
            await _invoke_callback(None if disconnect_notified else on_disconnect)
        return

    iterator = iter(source)
    queue: asyncio.Queue[_SyncQueueItem] = asyncio.Queue()
    stop_event = Event()
    producer = _start_sync_producer(iterator=iterator, queue=queue, loop=loop, stop_event=stop_event)

    try:
        while True:
            if await request.is_disconnected():
                disconnect_notified = True
                stop_event.set()
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_sec)
            except asyncio.TimeoutError:
                now = loop.time()
                if now - last_emit >= heartbeat_sec:
                    last_emit = now
                    yield ": heartbeat\n\n"
                continue

            if item.kind == _SYNC_DONE:
                break
            if item.kind == _SYNC_ERROR:
                raise item.payload

            last_emit = loop.time()
            yield encode_sse(item.payload)
    finally:
        stop_event.set()
        if disconnect_notified:
            await _invoke_callback(on_disconnect)
        await asyncio.to_thread(producer.join, 0.5)
        await _invoke_callback(None if disconnect_notified else on_disconnect)


def sse_response(
    *,
    request: Request,
    source: AsyncIterator[dict[str, Any]] | Iterable[dict[str, Any]],
    heartbeat_sec: int = 15,
    on_disconnect: Callable[[], Awaitable[None] | None] | None = None,
) -> StreamingResponse:
    return StreamingResponse(
        iter_sse(request=request, source=source, heartbeat_sec=heartbeat_sec, on_disconnect=on_disconnect),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
