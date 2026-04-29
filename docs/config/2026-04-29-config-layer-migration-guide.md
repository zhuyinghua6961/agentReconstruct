# Config Layer Migration Guide

## New Hierarchy

Shared config lives in `resource/config/shared`:

- `infrastructure.shared.env`: service ports, gateway backend URLs, Redis/MySQL/MinIO non-secret defaults.
- `model-endpoints.shared.env`: unified `LLM_*`, `EMBEDDING_*`, `RERANK_*`, and `OCR_*` endpoint/model/timeouts.
- `graph.shared.env`: namespaced Neo4j URLs, usernames, and database names.
- `*.secret.env.example`: commit-safe blank templates for local-only secret env files.

Service behavior config lives in `resource/config/services/<service>`:

- `config.shared.env`: service behavior, capacity, paths, cache, stage, and route tuning.
- `config.secret.env.example`: service-local secret placeholders only.
- `config.env`: untracked local override with highest env-file precedence.

## Precedence

Process environment wins over every env file. Without explicit `*_ENV_FILES`, loaders read:

1. legacy service/root env files as fallback,
2. shared public and secret files,
3. service `config.shared.env`,
4. service `config.secret.env`,
5. service `.env`,
6. service `config.env`.

Later env files override earlier env files. Explicit `*_ENV_FILE(S)` remains an escape hatch for rollback.

## Secret Policy

Commit only `*.shared.env` and `*.secret.env.example`. Do not commit real `*.secret.env`, `.env`, API keys, passwords, or tokens.

## Default-On Graph Rollback

fastQA and patent graph QA are default-on, and routine service config files no longer expose graph disable switches. These old flags remain supported as hidden emergency rollback env vars:

- `FASTQA_GRAPH_KB_ENABLED`
- `FASTQA_GRAPH_KB_V2_ENABLED`
- `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED`
- `FASTQA_GRAPH_COMMUNITY_ROUTE_ENABLED`
- `FASTQA_GRAPH_PRECISE_NUMERIC_ENABLED`
- `PATENT_GRAPH_KB_ENABLED`
- `PATENT_GRAPH_KB_V2_ENABLED`
- `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED`

Leave them unset in normal deployments. For emergency rollback, set the relevant flag to `false` in a local secret/env override or process environment. If Neo4j is missing or unavailable while graph remains enabled, graph components report degraded and RAG/vector fallback remains available.

## Service Ports

Canonical ports are in `infrastructure.shared.env`:

- gateway: `GATEWAY_PORT=8101`
- public-service: `PUBLIC_SERVICE_PORT=8102`
- fastQA: `FASTQA_PORT=8008`
- highThinkingQA: `HIGHTHINKINGQA_PORT=8009`
- patent: `PATENT_PORT=8010`

Legacy aliases such as `FASTAPI_PORT`, `BACKEND_PORT`, and highThinkingQA `APP_PORT` are compatibility fallbacks only.

## Model Config

Use shared `LLM_*` by default. Service-specific variables such as `PATENT_OPENAI_*` remain overrides. Legacy `OPENAI_*` and `DASHSCOPE_*` aliases still work after `LLM_*` is absent.

## Rollback

For a single service rollback, set explicit `*_ENV_FILES` to the previous known-good env list. For graph-only emergency rollback, set the hidden graph flag for that service to `false`; for dependency-only degradation tests, leave graph enabled and clear Neo4j credentials or point to an unavailable local endpoint.
