# Patent

Phase 1 currently bootstraps the standalone `patent` FastAPI service scaffold under `patent/` only.

Current scaffold includes:

- package metadata and shared environment example
- patent-local start, test, and lint scripts
- a minimal `server_fastapi.app:create_app` factory with seeded runtime component state
- an initial health smoke test for the app factory defaults

External rollout gates still apply before durable patent traffic can be enabled outside this directory:

- gateway routing and persistence behavior must be updated for patent mode
- public-service authority contracts must accept the patent source/mode values
- production durable mode should remain disabled until those dependencies are ready
