# Repository Guidelines

## Project Structure & Module Organization
- `frontend-vue/`: Vue 3 + Vite frontend.
- `server_fastapi/`: FastAPI app, routers, auth deps, gunicorn config.
- `server/`: backend services, repositories, schemas, runtime helpers, storage, database access.
- `agent_core/`: reasoning pipeline, synthesis, checker, reviser.
- `ingest/`, `retriever/`, `prompts/`: ingestion, retrieval, and prompt templates.
- `scripts/`: gunicorn lifecycle helpers for the FastAPI service.

## Build, Test, and Development Commands
- `bash scripts/start_fastapi_gunicorn.sh`: start FastAPI with gunicorn on `8008`.
- `bash scripts/status_fastapi_gunicorn.sh`: show gunicorn status and listener.
- `bash scripts/stop_fastapi_gunicorn.sh`: stop gunicorn.
- `cd frontend-vue && npm run dev`: start Vite dev server on `5174`.
- `pytest tests/test_ask_service_executor.py tests/test_run_agent_overlap.py tests/test_checker_precheck.py -q`: focused backend regression suite.

## Coding Style & Naming Conventions
- Python: 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes.
- Vue/JS: existing component structure and naming should be preserved.
- Keep API-facing compatibility logic in backend adapters such as `server/services/ask_service.py`.

## Testing Guidelines
- Put backend tests under `tests/` using `test_<name>.py`.
- Prefer focused regression tests around SSE contracts, citation formatting, and PDF delivery behavior.
- Mock external model calls; do not rely on live DashScope/OpenAI services in tests.

## Commit & Pull Request Guidelines
- Use Conventional Commit prefixes: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.
- Keep each commit scoped to one concern.
- PRs should include touched modules, commands run, and any API contract changes.

## Security & Configuration Tips
- Secrets belong in `config.secret.env` or local `.env`, never in committed code.
- Generated runtime directories such as `cache/`, `vectordb/`, `uploads/`, and `.runtime/` are ignored by Git.
