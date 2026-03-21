# Repository Guidelines

## Project Structure & Module Organization
- `frontend-vue/`: canonical Vue 3 + Vite frontend for the gateway-based system.
- `gateway/`: FastAPI gateway backend, routing and proxy layer.
- `public-service/`: standalone public capability backend.
- `fastQA/`: fast-mode QA backend.
- `highThinkingQA/`: thinking-mode QA backend.
- `resource/`: shared resource, config, and runtime roots.
- `scripts/`: top-level service lifecycle helpers.

## Build, Test, and Development Commands
- `cd frontend-vue && npm run dev`: start the canonical Vite frontend on `5173`.
- `cd frontend-vue && npm run build`: build the canonical frontend bundle.
- `bash scripts/start_all.sh`: start the active backend stack.
- `bash scripts/status_all.sh`: inspect active backend processes.
- `bash scripts/stop_all.sh`: stop the active backend processes.

## Coding Style & Naming Conventions
- Python: 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes.
- Vue/JS: preserve existing component structure and naming.
- Keep gateway behavior in `gateway/app/`, public capabilities in `public-service/backend/app/`, and mode-specific QA logic in the QA service directories.

## Testing Guidelines
- Gateway backend tests live under `gateway/tests/`.
- Public-service backend tests live under `public-service/backend/tests/`.
- Frontend validation should at minimum include `cd frontend-vue && npm run build`.

## Commit & Pull Request Guidelines
- Use Conventional Commit prefixes: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.
- Keep each commit scoped to one concern.
- PRs should include touched services, commands run, and any API or routing contract changes.

## Security & Configuration Tips
- Secrets belong in `config.secret.env` or local `.env`, never in committed code.
- Generated runtime directories such as `.runtime/`, `resource/runtime/`, `resource/state/`, `frontend-vue/dist/`, and local backup folders under `archive/` are ignored by Git.
