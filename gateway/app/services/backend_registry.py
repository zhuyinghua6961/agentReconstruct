"""Configured upstream backend registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.config import GatewaySettings

BackendRole = Literal["public", "fast", "thinking", "patent"]


@dataclass(frozen=True)
class BackendTarget:
    name: BackendRole
    base_url: str


class BackendRegistry:
    def __init__(self, settings: GatewaySettings) -> None:
        self._targets: dict[BackendRole, BackendTarget] = {
            "public": BackendTarget(name="public", base_url=settings.endpoints.public),
            "fast": BackendTarget(name="fast", base_url=settings.endpoints.fast),
            "thinking": BackendTarget(name="thinking", base_url=settings.endpoints.thinking),
            "patent": BackendTarget(name="patent", base_url=settings.endpoints.patent),
        }

    def get(self, name: BackendRole | str) -> BackendTarget:
        key = str(name or "").strip().lower()
        if key not in self._targets:
            raise KeyError(f"unknown backend: {name}")
        return self._targets[key]  # type: ignore[index]

    def get_public(self) -> BackendTarget:
        return self._targets["public"]

    def get_mode_backend(self, mode: str) -> BackendTarget:
        key = str(mode or "").strip().lower()
        if key not in {"fast", "thinking", "patent"}:
            raise KeyError(f"unknown mode backend: {mode}")
        return self._targets[key]  # type: ignore[index]

    def all(self) -> dict[str, BackendTarget]:
        return dict(self._targets)
