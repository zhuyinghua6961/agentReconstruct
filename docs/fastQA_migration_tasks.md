# FastQA Migration Tasks

## Phase 0: Freeze Boundary

1. Freeze Phase 1 scope as `kb_qa` only.
2. Exclude `ask_gateway`, `qa_pdf`, `qa_tabular`, `file_context`, auth, quota, conversation, uploads.
3. Freeze gateway-facing ask / ask_stream SSE contract.

## Phase 1: Create Skeleton

1. Done: prepare `fastQA/` directory structure for `core`, `integrations`, `modules`, `tests`, `scripts`.
2. Done: add `resource/config/services/fastQA/` env templates.
3. Done: define `resource/state/dev/fastQA` and `resource/runtime/dev/fastQA` path contract.

## Phase 2: Copy Minimal QA Closure

1. Done: copy `core`: env/config/logging/prompts/sse.
2. In progress: copy `integrations`.
   Completed now: `redis`, runtime bootstrap health wiring.
   Deferred: `llm`, `embedding`, `vector_db`, `neo4j`.
3. Pending: copy `modules/retrieval`.
4. Pending: copy `modules/storage/paper_storage.py`.
5. Done: copy `modules/qa_cache`.
6. Pending: copy `modules/generation_pipeline`.
7. In progress: copy `modules/qa_kb`.
   Completed now: request/metadata models, placeholder stream service.
   Pending: real generation-driven execution path.

## Phase 3: Rebuild Thin Service Layer

1. Done: rebuild a thin FastAPI app in `fastQA`.
2. Done: expose `health`, `ask`, `ask_stream`, plus `/api/fast/*` compatibility aliases.
3. Done: add a request adapter from gateway payload into `QaKbRequest`.
4. Done: reject unsupported file / hybrid execution fields explicitly.

## Phase 4: Rebind Paths

1. Done for current skeleton: config loader honors `SERVICE_*_ROOT`.
2. Done for currently owned paths: runtime/config/cache-health paths resolve under `resource/`.
3. Pending for future pipeline modules: remove source-root path inference from retrieval / generation runtime code.

## Phase 5: Focused Verification

1. Done: add tests for config/root rebinding.
2. Done: add tests for ask / ask_stream SSE compatibility.
3. Done for current baseline: `39` focused tests green.
4. Pending: add tests proving no public-service modules are imported on the real `kb_qa` hot path.
5. Pending: add live HTTP regression once the real execution closure is wired.

## Next Slice

1. Migrate minimal `integrations/llm` with DashScope-native-first behavior preserved.
2. Migrate minimal `generation_pipeline` runtime bootstrap and stage orchestrator.
3. Rebind retrieval/vector-db dependencies to `resource/`.
4. Replace placeholder `qa_kb_service.iter_phase1_placeholder_events()` with real `kb_qa`.
