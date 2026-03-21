# fastapi-version Backend Adaptation

## Role in Target Architecture

`fastapi-version` should adapt to two roles:
- `public` backend role
- `fast` QA execution backend

It should not define the external protocol. It should consume the gateway protocol.

## Current Strengths

Already present:
- auth APIs
- conversation CRUD and file APIs
- document APIs including `literature_content` and `reference_preview`
- quota APIs
- admin APIs
- file-context and route resolution logic
- SSE-based QA pipeline
- query-token auth compatibility for preview/download

## Current Gaps Against Gateway Standard

### 1. No canonical mode routes yet
Current QA routes are:
- `/api/v1/ask`
- `/api/v1/ask_stream`
- `/ask`
- `/ask_stream`

Target contract requires gateway-owned mode routing. `fastapi-version` should accept standardized execution requests rather than expose frontend-facing mode selection directly.

### 2. `ask` is not a true JSON ask route
Current `/api/v1/ask` is wired to the same SSE flow as `ask_stream`.

Target state:
- either provide true JSON ask
- or let gateway synthesize JSON ask from stream

### 3. Backend still owns file-context routing
Current backend enriches payload with:
- `route_hint`
- `turn_mode`
- `used_files`
- `execution_files`
- backend-side file-context resolution

Target state:
- gateway decides route
- backend executes route
- backend-side file resolver becomes compatibility fallback only

### 4. Legacy ask fields remain first-class
Current request model still uses:
- `use_pdf`
- `pdf_path`
- `use_generation_driven`
- `route_hint`

Target state:
- these fields remain compatibility-only
- standardized gateway execution fields become primary

### 5. Response contract is incomplete relative to gateway target
Current stream `done` events already carry useful fields such as:
- `route`
- `used_files`
- `trace_id`
- `file_selection`

But canonical target still needs stable handling for:
- `requested_mode`
- `actual_mode`
- canonical `metadata` shape
- canonical JSON ask response shape

## Adaptation Requirements

### Required first
1. Accept gateway execution payload as primary input.
2. Trust `route`, `turn_mode`, and `actual_mode` from gateway.
3. Keep backend-side routing logic only as fallback during migration.
4. Preserve `trace_id` unchanged.
5. Keep public APIs stable under gateway proxying.

### Required second
1. Normalize `done` payload to canonical gateway expectations.
2. Make JSON ask behavior explicit.
3. Stop letting legacy fields alter routing when canonical fields are present.

## What Should Stay in fastapi-version

Should remain owned here:
- auth
- conversation persistence
- file metadata and storage integration
- PDF preview and file delivery
- literature helper APIs
- quota/admin/public infrastructure
- fast-mode execution
- file QA execution

Should gradually move out of primary responsibility here:
- frontend-facing routing semantics
- raw `pdf_context` interpretation
- mode arbitration

## Suggested Adaptation Order

1. Consume canonical gateway execution payload.
2. Gate legacy routing logic behind compatibility checks.
3. Normalize stream and JSON output fields.
4. Keep public APIs stable while gateway takes ownership of route compatibility.
