from __future__ import annotations

import re
from typing import Any


_TOKEN_RE = re.compile(r"LiFePO4(?:/C)?|LFP|NCM|graphite|solvothermal|hydrothermal|solid-state|synthesis|milling", re.IGNORECASE)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _first_token(values: tuple[str, ...], *, method: bool = False) -> str:
    for value in values:
        text = _clean(value)
        if not text:
            continue
        matches = _TOKEN_RE.findall(text)
        if method:
            for match in matches:
                lowered = match.lower()
                if lowered in {"solvothermal", "hydrothermal", "solid-state", "synthesis", "milling"}:
                    return match
        elif matches:
            return matches[0]
        if not method:
            return text.split()[0]
    return ""


def build_community_label(
    *,
    community_id: int | str | None,
    titles: tuple[str, ...] = (),
    materials: tuple[str, ...] = (),
    methods: tuple[str, ...] = (),
) -> str:
    _ = community_id
    material = _first_token(tuple(materials or ()) + tuple(titles or ()))
    method = _first_token(tuple(methods or ()), method=True)
    if material and method:
        return f"{material} {method} literature cluster"
    if material:
        return f"{material} related literature cluster"
    if method:
        return f"{method} related literature cluster"
    return "related literature cluster"
