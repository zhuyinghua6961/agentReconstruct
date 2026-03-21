# Public-Service Integration Plan

## Goal

Make `gateway` ready to use `public-service` as the `public` backend role without changing `public-service` or `fastapi-version`.

This phase is limited to `gateway` code:
- expand transparent public proxy coverage
- add `/api/v1/*` compatibility routes where the frontend already depends on them
- keep request/response passthrough semantics unchanged
- add tests for the new route surface

## Current Readiness

`public-service` is already a better upstream fit than `fastapi-version` for the public backend role:
- it supports canonical upload routes such as `/api/upload_pdf`
- it exposes `/api/health`
- it keeps conversation file metadata under `/api/conversations/{conversation_id}/files`
- it supports most public APIs on both `/api/*` and `/api/v1/*`

The main blockers are currently inside `gateway`, not inside `public-service`:
- `gateway` only proxies a subset of the public route boundary
- `gateway` does not currently expose `/api/v1/*` routes, while its own frontend calls them heavily
- several public-service routes exist but are unreachable through `gateway`

## Route Categories

### Ready To Proxy Now

These can be exposed by gateway immediately because `public-service` already supports them:
- auth: login/register/me/password/security questions
- conversations: CRUD, messages, title update, file metadata/download/delete
- uploads: `upload_pdf`, `upload_excel`, `clear_pdf`
- documents: `translate`, `summarize_pdf`, `extract_pdf_text`, `check_pdf`, `view_pdf`, `literature_content`, `reference_preview`
- system: `health`, `kb_info`, `refresh_kb`, `clear_cache`, `background_status`
- quota: all `/api/quota/*`
- admin: all `/api/admin/*`

### Gateway Compatibility Work Needed

Gateway must provide these compatibility entrypoints because the current frontend uses them:
- `/api/v1/auth/*`
- `/api/v1/conversations/*`
- `/api/v1/upload_pdf`
- `/api/v1/upload_excel`
- `/api/v1/clear_pdf`
- `/api/v1/translate`
- `/api/v1/kb_info`
- `/api/v1/refresh_kb`
- `/api/v1/clear_cache`
- `/api/v1/literature_content`
- `/api/v1/reference_preview`
- `/api/v1/quota/*`
- `/api/v1/ask`
- `/api/v1/ask_stream`
- `/api/v1/{mode}/ask`
- `/api/v1/{mode}/ask_stream`

Admin stays on `/api/admin/*` in this phase because `public-service` does not expose `/api/v1/admin/*`.

## Known Non-Gateway Gaps

These are real differences, but they cannot be solved in this phase because only `gateway` may change:
- `public-service` requires auth for `view_pdf`, while `fastapi-version` allows optional auth
- `public-service` restricts `kb_info`, `refresh_kb`, `clear_cache`, and `background_status` more strictly
- `literature_content` and `reference_preview` still depend on runtime agent availability upstream

Gateway should document these differences, not mask them.

## Implementation Plan

### Phase 1
- add missing public proxy routes for the full public-service route surface
- add `/api/v1/*` compatibility aliases for public and QA endpoints
- keep upstream path passthrough exact for public routes

### Phase 2
- add tests for new public proxy methods and paths
- add tests for `/api/v1` QA aliases
- keep existing canonical `/api/*` behavior unchanged

### Phase 3
- run targeted gateway tests
- report remaining upstream behavior differences separately from gateway routing readiness

## Success Criteria

Gateway is ready for public-service integration when:
- all public-service routes intended for public traffic are reachable through gateway
- the current frontend no longer depends on routes missing from gateway
- gateway preserves auth headers, query strings, multipart bodies, and PDF inline headers
- QA routing still forwards to `/api/{mode}/ask` and `/api/{mode}/ask_stream` correctly while accepting `/api/v1/*` aliases
