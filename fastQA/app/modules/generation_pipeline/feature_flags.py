#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feature-flag helpers for QA retrieval pipeline."""

from __future__ import annotations

import os
from typing import Optional


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


def env_bool(name: str, default: bool = False, *, raw: Optional[str] = None) -> bool:
    """Parse env bool from common textual forms."""
    value = raw if raw is not None else os.getenv(name)
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def env_int(
    name: str,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
    raw: Optional[str] = None,
) -> int:
    """Parse env int with optional clamp."""
    value = raw if raw is not None else os.getenv(name)
    try:
        parsed = int(str(value).strip()) if value is not None else int(default)
    except Exception:
        parsed = int(default)

    if minimum is not None and parsed < minimum:
        parsed = minimum
    if maximum is not None and parsed > maximum:
        parsed = maximum
    return parsed


__all__ = ["env_bool", "env_int"]
