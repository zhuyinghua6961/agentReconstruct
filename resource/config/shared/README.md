# Shared Config

Shared env files provide repository-wide defaults. Loaders read legacy service files first,
then shared files, then `resource/config/services/<service>` files. Later env files override
earlier env files; process environment variables override every env file.

`*.shared.env` files are commit-safe and must contain only non-secret defaults.
`*.secret.env` files are local-only and must not be committed. `*.secret.env.example` files
are commit-safe templates with blank placeholder values.

## File Ownership

- `infrastructure.shared.env`: service hosts/ports, gateway backend URLs, and common
  MySQL/Redis/MinIO non-secret defaults.
- `infrastructure.secret.env.example`: local template for shared infrastructure secrets.
- `model-endpoints.shared.env`: unified `LLM_*`, `EMBEDDING_*`, `RERANK_*`, and `OCR_*`
  endpoint/model/timeout defaults, plus legacy aliases during migration.
- `model-endpoints.secret.env.example`: local template for model API keys.
- `graph.shared.env`: namespaced Neo4j URLs, usernames, and database names for fastQA,
  patent, and public-service, plus legacy aliases during migration.
- `graph.secret.env.example`: local template for Neo4j passwords.

Service-local files should keep behavior, capacity, path, cache, and service-specific tuning
only. Local overrides belong in `resource/config/services/<service>/config.env` or process env.
