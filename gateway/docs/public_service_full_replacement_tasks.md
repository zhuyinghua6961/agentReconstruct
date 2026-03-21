# Public-Service Full Replacement Tasks

## P0 Must Do

### P0-1 Contract Alignment
- [x] audit auth request/response contracts against gateway frontend
- [x] audit conversation request/response contracts against gateway frontend
- [x] audit upload response contracts against gateway frontend
- [x] gateway public proxy surface aligned to public-service route surface
- [x] gateway `/api/v1/*` compatibility routes added
- [x] make `reference_preview` accept frontend `doi` payload
- [x] verify `view_pdf` auth + query-token browser-open flow
- [x] verify quota/admin response shapes against frontend usage

### P0-2 Critical Flow Validation
- [x] login/register/me/password/security questions
- [x] create/list/detail/update/delete conversation
- [x] upload PDF with conversation binding
- [x] upload Excel with conversation binding
- [x] list/download/delete conversation file
- [x] reference preview
- [x] literature content
- [x] view PDF with auth
- [x] kb_info / refresh_kb / clear_cache / background_status

### P0-3 Test Coverage
- [x] add/expand public-service contract tests for mismatched endpoints
- [x] gateway proxy route tests expanded
- [x] add regression tests for frontend-compatible `reference_preview`
- [x] run targeted gateway + public-service test suites

## P1 Should Do
- [ ] audit all endpoints still depending on `runtime.agent`
- [ ] classify acceptable temporary coupling vs required decoupling
- [ ] normalize operational endpoint semantics
- [ ] document standalone deployment requirements for DB/Redis/storage
- [ ] verify health and status payloads are service-oriented

## P2 Hardening
- [ ] define rollback procedure from public-service back to old public backend
- [ ] define cutover checklist
- [ ] define observability checklist
- [ ] define deprecation plan for fastapi-version public ownership

## Immediate Execution Order
1. `reference_preview` request compatibility
2. upload / file metadata contract verification
3. `view_pdf` auth browser-open verification
4. quota/admin contract verification
5. critical flow regression run
