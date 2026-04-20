from __future__ import annotations

import re

from server.patent.graph_kb.models import PatentGuardrailResult, PatentSchemaRegistry


_WRITE_CLAUSE_RE = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|CALL)\b", re.IGNORECASE)
_LABEL_RE = re.compile(r"(?<!\[):([A-Za-z_][A-Za-z0-9_]*)")
_REL_RE = re.compile(r"\[:([A-Za-z_][A-Za-z0-9_]*)\]")
_LIMIT_RE = re.compile(r"\bLIMIT\s+(?:\d+|\$[A-Za-z_][A-Za-z0-9_]*)\b", re.IGNORECASE)
_DEFAULT_LIMIT = 200


def _contains_write_clause(cypher: str) -> bool:
    return bool(_WRITE_CLAUSE_RE.search(str(cypher or "")))


def _ensure_limit(cypher: str) -> str:
    normalized = str(cypher or "").strip()
    if not normalized:
        return normalized
    if _LIMIT_RE.search(normalized):
        return normalized
    return f"{normalized} LIMIT {_DEFAULT_LIMIT}"


def _find_unapproved_tokens(cypher: str, registry: PatentSchemaRegistry) -> tuple[str, ...]:
    labels = {item for item in _LABEL_RE.findall(cypher) if item}
    relations = {item for item in _REL_RE.findall(cypher) if item}
    allowed_labels = set(registry.allowed_labels)
    allowed_relations = set(registry.allowed_relations)

    issues: list[str] = []
    if any(label not in allowed_labels for label in labels):
        issues.append("label_not_allowed")
    if any(relation not in allowed_relations for relation in relations):
        issues.append("relation_not_allowed")
    return tuple(issues)


def inspect_patent_cypher(
    *,
    cypher: str,
    registry: PatentSchemaRegistry,
) -> PatentGuardrailResult:
    normalized = str(cypher or "").strip()
    if _contains_write_clause(normalized):
        return PatentGuardrailResult(
            verdict="reject",
            issues=("write_clause",),
            normalized_cypher=normalized,
        )

    issues = _find_unapproved_tokens(normalized, registry)
    if issues:
        return PatentGuardrailResult(
            verdict="reject",
            issues=issues,
            normalized_cypher=normalized,
        )

    return PatentGuardrailResult(
        verdict="allow",
        issues=(),
        normalized_cypher=_ensure_limit(normalized),
    )
