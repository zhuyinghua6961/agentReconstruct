"""Service layer."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


_LAZY_MODULES = {
    "ask_service",
    "chat_persistence",
    "conversation_authority_client",
    "conversation_context_service",
    "documents_service",
    "redis_client",
    "stage_cache",
}


def __getattr__(name: str) -> ModuleType:
    if name not in _LAZY_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module


__all__ = sorted(_LAZY_MODULES)
