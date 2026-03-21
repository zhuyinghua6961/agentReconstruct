# Repository Guidelines

## Project Structure & Module Organization
`app/` contains the FastAPI gateway backend. Keep HTTP entrypoints in `app/routers/`, request and response schemas in `app/models/`, shared configuration in `app/core/`, provider integrations in `app/providers/`, and routing or proxy logic in `app/services/`. Backend tests live in `tests/` and follow the runtime modules they cover, for example `tests/test_route_decision.py`. Protocol notes and design references belong in `docs/`. The canonical frontend is no longer inside `gateway/`; it now lives at the repository root in `frontend-vue/`.

## Build, Test, and Development Commands
Run the gateway locally with `conda run --no-capture-output -n agent gunicorn -k uvicorn.workers.UvicornWorker app.main:app --chdir /home/cqy/worktrees/highThinking/gateway --bind 0.0.0.0:8101 --workers 1 --timeout 600`. Install backend dependencies with `pip install -e .[dev]` from the repository root. Execute backend tests with `conda run --no-capture-output -n agent pytest -q tests -p no:cacheprovider`. For the active frontend, use `cd /home/cqy/worktrees/highThinking/frontend-vue && npm install`, then `npm run dev` for local development, `npm run build` for a production bundle, and `npm run preview` to smoke-test the built app.

## Coding Style & Naming Conventions
Follow existing style rather than introducing a second one. Python uses 4-space indentation, type-aware FastAPI service code, and `snake_case` for modules, functions, and variables. Keep classes in `PascalCase`. Vue and JavaScript frontend files now live in the repository-root `frontend-vue/` directory and continue to use 2-space indentation, `camelCase` for functions and stores, and `PascalCase` for component files. Prefer small router handlers that delegate behavior to `app/services/`.

## Testing Guidelines
Use `pytest` for backend coverage. Name tests `test_<behavior>.py` and keep each test focused on one routing or proxying contract. Mock upstream HTTP with `httpx.MockTransport` when validating forwarding, auth headers, or SSE passthrough behavior. No formal coverage threshold is defined here, but new gateway routes, mode-selection rules, and provider integrations should ship with regression tests.

## Commit & Pull Request Guidelines
Git history is not available in this workspace, so use short imperative commit subjects with a scope when helpful, for example `gateway: route file-aware asks to fast backend`. Keep commits focused and avoid mixing backend and unrelated frontend refactors without a reason. PRs should explain the user-visible change, note any required environment variables, link related issues, and include screenshots or request/response examples when UI or API behavior changes.

## Security & Configuration Tips
Do not hardcode backend URLs or secrets. Configure services through environment variables such as `PUBLIC_BACKEND_BASE_URL`, `FAST_BACKEND_BASE_URL`, `THINKING_BACKEND_BASE_URL`, `PATENT_BACKEND_BASE_URL`, and `GATEWAY_CONVERSATION_FILE_PROVIDER`. When changing forwarding behavior, verify auth and trace headers still pass through correctly.
