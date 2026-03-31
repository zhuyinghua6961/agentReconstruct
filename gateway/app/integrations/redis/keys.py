from __future__ import annotations

from dataclasses import dataclass


def _normalize_segment(value: object) -> str:
    text = str(value or "").strip()
    return text.strip(":")


@dataclass(frozen=True)
class RedisKeyFactory:
    prefix: str

    def join(self, *segments: object) -> str:
        items: list[str] = []
        base = _normalize_segment(self.prefix)
        if base:
            items.append(base)
        for segment in segments:
            normalized = _normalize_segment(segment)
            if normalized:
                items.append(normalized)
        return ":".join(items)

    def cache(self, *segments: object) -> str:
        return self.join("cache", *segments)

    def lock(self, *segments: object) -> str:
        return self.join("lock", *segments)

    def stream(self, *segments: object) -> str:
        return self.join("stream", *segments)

    def pending(self, *segments: object) -> str:
        return self.join("pending", *segments)

    def admission(self, *segments: object) -> str:
        return self.join("admission", *segments)

    def relay(self, *segments: object) -> str:
        return self.join("relay", *segments)

    def result(self, *segments: object) -> str:
        return self.join("result", *segments)


def build_key_factory(prefix: str) -> RedisKeyFactory:
    return RedisKeyFactory(prefix=_normalize_segment(prefix))


__all__ = ["RedisKeyFactory", "build_key_factory"]
