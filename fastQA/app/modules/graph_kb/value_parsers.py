from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParsedGraphValue:
    original: str
    value: float | None = None
    unit: str = ""
    confidence: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


_FLOAT_RE = r"([0-9]+(?:\.[0-9]+)?(?:e[+\-]?[0-9]+)?)"
_PLACEHOLDER_DOI_RE = re.compile(r"^[A-Za-z_]+\d*_10\.\d{1,9}/", re.IGNORECASE)
_RATE_RE = re.compile(r"\b([0-9]+(?:\.[0-9]+)?\s*C)\b", re.IGNORECASE)
_CYCLES_RE = re.compile(r"after\s+([0-9]+)\s+cycles|([0-9]+)\s+cycles", re.IGNORECASE)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_placeholder(value: str) -> bool:
    text = _text(value)
    return bool(_PLACEHOLDER_DOI_RE.search(text))


def _empty(original: str, warning: str = "unparsed") -> ParsedGraphValue:
    warnings = (warning,) if warning else ()
    return ParsedGraphValue(original=_text(original), value=None, confidence=0.0, warnings=warnings)


def _parse_float(value: str) -> float:
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed


def _rate_context(text: str) -> dict[str, Any]:
    match = _RATE_RE.search(text.replace("_", " "))
    return {"rate": match.group(1).replace(" ", "")} if match else {}


def _cycle_context(text: str) -> dict[str, Any]:
    match = _CYCLES_RE.search(text)
    if match is None:
        return {}
    cycles = next((group for group in match.groups() if group), "")
    return {"cycles": int(cycles)} if cycles else {}


def parse_capacity(value: str) -> ParsedGraphValue:
    text = _text(value)
    if _is_placeholder(text):
        return _empty(text, "placeholder")
    match = re.search(_FLOAT_RE + r"\s*(?:mA\s*h\s*g[⁻\-]?\s*[1¹]|mAh\s*/?\s*g(?:\s*\-?[1¹])?)", text, re.IGNORECASE)
    if match is None:
        return _empty(text)
    return ParsedGraphValue(
        original=text,
        value=_parse_float(match.group(1)),
        unit="mAh/g",
        confidence=0.9,
        context=_rate_context(text),
    )


def parse_density(value: str) -> ParsedGraphValue:
    text = _text(value)
    if _is_placeholder(text):
        return _empty(text, "placeholder")
    match = re.search(_FLOAT_RE + r"\s*g\s*(?:/|\s+)\s*cm\s*(?:\^?3|³|-3)", text, re.IGNORECASE)
    if match is None:
        return _empty(text)
    return ParsedGraphValue(original=text, value=_parse_float(match.group(1)), unit="g/cm3", confidence=0.9)


def parse_conductivity(value: str) -> ParsedGraphValue:
    text = _text(value)
    if _is_placeholder(text):
        return _empty(text, "placeholder")
    match = re.search(_FLOAT_RE + r"\s*S\s*/\s*cm", text, re.IGNORECASE)
    if match is None:
        return _empty(text)
    return ParsedGraphValue(original=text, value=_parse_float(match.group(1)), unit="S/cm", confidence=0.85)


def parse_retention(value: str) -> ParsedGraphValue:
    text = _text(value)
    if _is_placeholder(text):
        return _empty(text, "placeholder")
    match = re.search(_FLOAT_RE + r"\s*%", text, re.IGNORECASE)
    if match is None:
        return _empty(text)
    return ParsedGraphValue(
        original=text,
        value=_parse_float(match.group(1)),
        unit="%",
        confidence=0.85,
        context=_cycle_context(text),
    )
