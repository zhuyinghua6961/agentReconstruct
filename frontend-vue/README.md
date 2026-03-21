# frontend-vue

Vue 3 frontend for the gateway-based agentCode system.

## Scope

- Canonical UI layer for the current repository.
- Uses the gateway as the single browser-facing backend entrypoint.
- Supports:
  - Session list and multi-chat switching
  - Streaming answer consumption from `/api/v1/{mode}/ask_stream` (fallback alias: `/api/v1/ask_stream`)
  - Reference DOI panel + literature detail loading (`/api/v1/literature_content`)
  - Batched reference preview (`/api/v1/reference_preview`) for title/PDF availability
  - Open cited paper PDF via `/api/v1/view_pdf/<doi>`
  - KB info / refresh / cache clear
  - PDF and Excel upload
  - Local session persistence (`localStorage`)

## Quick Start

```bash
cd /home/cqy/worktrees/highThinking/frontend-vue
npm install
npm run dev
```

Default dev server:
- Local: `http://127.0.0.1:5173`
- IP access: `http://<server-ip>:5173`

## Backend Integration

- Dev proxy is configured in `vite.config.js`:
  - `/api/*` -> `http://127.0.0.1:8101`
- Optional env:
  - `VITE_API_BASE_URL` for direct backend URL
  - `VITE_PROXY_TARGET` to override the dev proxy target

Notes:

- This frontend is intended to run with the gateway on `8101`.
- If using `VITE_API_BASE_URL` for cross-origin calls, make sure the gateway explicitly allows the frontend origin.

## Quality Gate

From the frontend directory:

```bash
npm run build
```
