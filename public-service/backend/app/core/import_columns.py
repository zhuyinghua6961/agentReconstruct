from __future__ import annotations

from typing import Any, Mapping


def normalize_column_name(value: object) -> str:
    return str(value or "").strip().lower()


def resolve_column_aliases(
    columns: list[Any],
    required_aliases: Mapping[str, tuple[str, ...]],
    optional_aliases: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    normalized = {normalize_column_name(col): col for col in columns}
    resolved: dict[str, Any] = {}
    missing: list[str] = []

    for canonical, aliases in required_aliases.items():
        matched = _match_column(normalized, aliases)
        if matched is None:
            missing.append(str(aliases[0]))
        else:
            resolved[canonical] = matched

    if optional_aliases:
        for canonical, aliases in optional_aliases.items():
            matched = _match_column(normalized, aliases)
            if matched is not None:
                resolved[canonical] = matched

    return resolved, missing


def _match_column(normalized: dict[str, Any], aliases: tuple[str, ...]) -> Any | None:
    for alias in aliases:
        normalized_alias = normalize_column_name(alias)
        if normalized_alias in normalized:
            return normalized[normalized_alias]
    return None
