from __future__ import annotations

import re

from app.modules.graph_kb.models import GuardrailResult
from app.modules.graph_kb.schema_registry import SchemaRegistry


_WRITE_CLAUSE_RE = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|CALL)\b", re.IGNORECASE)
_LABEL_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_REL_RE = re.compile(r"\[:([A-Za-z_][A-Za-z0-9_]*)\]")
_DYNAMIC_REL_RE = re.compile(r"-\[\s*[A-Za-z_][A-Za-z0-9_]*\s*\]->")
_TYPE_IN_RE = re.compile(r"type\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\)\s+IN\s*\[([^\]]*)\]", re.IGNORECASE)
_STRING_LITERAL_RE = re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")


def _contains_write_clause(cypher: str) -> bool:
    return bool(_WRITE_CLAUSE_RE.search(str(cypher or "")))


def _ensure_limit(cypher: str) -> str:
    normalized = str(cypher or "").strip()
    if re.search(r"\bLIMIT\s+(?:\d+|\$[A-Za-z_][A-Za-z0-9_]*)\b", normalized, re.IGNORECASE):
        return normalized
    return f"{normalized} LIMIT 20"


def _find_unapproved_tokens(cypher: str, registry: SchemaRegistry) -> tuple[str, ...]:
    labels = {item for item in _LABEL_RE.findall(cypher) if item}
    relations = {item for item in _REL_RE.findall(cypher) if item}
    allowed_labels = set(registry.allowed_labels)
    allowed_relations = set(registry.allowed_relations)
    issues: list[str] = []
    if any(label not in allowed_labels for label in labels):
        issues.append("label_not_allowed")
    if any(rel not in allowed_relations for rel in relations):
        issues.append("relation_not_allowed")
    if _DYNAMIC_REL_RE.search(cypher):
        type_lists = _TYPE_IN_RE.findall(cypher)
        dynamic_relations = {
            item
            for type_list in type_lists
            for item in _STRING_LITERAL_RE.findall(type_list)
            if item
        }
        if not dynamic_relations:
            issues.append("dynamic_relation_unbounded")
        elif any(rel not in allowed_relations for rel in dynamic_relations):
            issues.append("dynamic_relation_not_allowed")
    return tuple(issues)


def inspect_cypher(*, cypher: str, registry: SchemaRegistry) -> GuardrailResult:
    normalized = str(cypher or "").strip()
    if _contains_write_clause(normalized):
        return GuardrailResult(verdict="reject", issues=("write_clause",), normalized_cypher=normalized)
    issues = _find_unapproved_tokens(normalized, registry)
    if issues:
        return GuardrailResult(verdict="reject", issues=issues, normalized_cypher=normalized)
    return GuardrailResult(verdict="allow", issues=(), normalized_cypher=_ensure_limit(normalized))
