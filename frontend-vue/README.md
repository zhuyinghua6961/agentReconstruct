# frontend-vue

Vue 3 separated frontend for the agentCode backend API.

## Scope

- Decoupled UI layer for `/api/v1/*` endpoints.
- Supports:
  - Session list and multi-chat switching
  - Streaming answer consumption from `/api/v1/ask_stream`
  - Reference DOI panel + literature detail loading (`/api/v1/literature_content`)
  - Batched reference preview (`/api/v1/reference_preview`) for title/PDF availability
    - Uses `POST /api/v1/reference_preview` for scalable batch payload
    - Supports server-side limit (`max_items`) with truncation metadata
  - Open cited paper PDF via `/api/v1/view_pdf/<doi>`
  - KB info / refresh / cache clear
  - PDF and Excel upload
  - Optional "use uploaded PDF" ask mode
  - Local session persistence (`localStorage`)

## Folder Layout

```text
src/
  api/
  features/
    chat/
      components/
      composables/
    controls/
      components/
      composables/
    references/
      components/
      composables/
        useReferencePanelState.js
  styles/
```

## Quick Start

```bash
cd frontend-vue
npm install
npm run dev
```

Default dev server:
- Local: `http://127.0.0.1:5174`
- IP access: `http://<server-ip>:5174` (server binds to `0.0.0.0`)

## Backend Integration

- Dev proxy is configured in `vite.config.js`:
  - `/api/*` -> `http://127.0.0.1:8008`
- Optional env:
  - `VITE_API_BASE_URL` for direct backend URL (if not using proxy)

## Backend Suggested Env

For separated deployment, backend can run in API-only mode:

```bash
export WEB_API_ONLY=1
export CORS_ORIGINS="http://127.0.0.1:5174,http://localhost:5174"
```

If frontend is accessed via server IP, append IP origin explicitly, for example:

```bash
export CORS_ORIGINS="http://127.0.0.1:5174,http://localhost:5174,http://192.168.1.20:5174"
```

## Quality Gate

From project root:

```bash
python3 backend/tools/verify_frontend_build.py
```
