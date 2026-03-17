# Multi-Mode Gateway Task Breakdown

## Goal

Build a gateway that becomes the only frontend entrypoint and the only owner of:
- file-context resolution
- requested-mode to actual-backend routing
- gateway-facing QA contract

The current `fast` backend remains:
- `fast` mode executor
- public infrastructure backend

The `thinking` backend remains:
- `thinking` mode executor only

## Workstreams

### W0. Contract Freeze

#### Task W0.1
Freeze frontend -> gateway ask request contract:
- `question`
- `conversation_id`
- `chat_history`
- `requested_mode`
- `pdf_context`
- `options`

Acceptance:
- request fields are documented
- defaulting rules are documented
- backward compatibility with legacy `mode` field is defined

#### Task W0.2
Freeze gateway -> backend normalized execution contract:
- `requested_mode`
- `actual_mode`
- `route`
- `turn_mode`
- `allow_kb_verification`
- `used_files`
- `execution_files`
- `trace_id`

Acceptance:
- backend no longer needs raw frontend file semantics to execute

#### Task W0.3
Freeze gateway-facing SSE contract:
- `metadata`
- `step`
- `content`
- `done`
- `error`

Acceptance:
- `requested_mode`, `actual_mode`, `route`, `trace_id` appear in canonical events

### W1. Gateway Skeleton

#### Task W1.1
Create gateway application skeleton:
- config loader
- backend registry
- HTTP client factory
- auth passthrough helpers
- trace-id middleware

Acceptance:
- gateway can start independently
- backend base URLs are configurable

#### Task W1.2
Add health endpoints:
- gateway self health
- backend upstream health aggregation

Acceptance:
- can distinguish gateway down vs upstream down

#### Task W1.3
Add route groups:
- public proxy group
- QA routing group
- internal debug / observability group if needed

Acceptance:
- route ownership is explicit in code layout

### W2. Public API Proxy

#### Task W2.1
Proxy public APIs to `fast`:
- auth
- conversations
- files
- uploads
- PDF preview
- translate
- summarize
- kb/system/quota/admin

Acceptance:
- gateway path shape matches frontend expectations
- file upload proxy supports multipart

#### Task W2.2
Preserve auth and trace headers:
- `Authorization`
- request id / trace id
- relevant forwarded headers

Acceptance:
- `fast` sees authenticated user identity unchanged

#### Task W2.3
Proxy file and PDF responses correctly:
- binary passthrough
- `Content-Disposition` passthrough
- no accidental buffering or body rewrite

Acceptance:
- PDF preview remains inline
- upload / download behavior matches direct `fast` access

### W3. File Context Resolver

#### Task W3.1
Move raw file-context parsing into gateway:
- parse `selected_ids`
- parse `newly_uploaded_ids`
- parse `all_available_ids`
- parse `last_focus_ids`
- parse `last_turn_route`

Acceptance:
- gateway can build normalized file context without backend help

#### Task W3.2
Implement file-intent detection:
- explicit `#1`, `#2` references
- singular references like “这篇文献”
- plural references like “这些文件”
- latest / recent upload references
- table-specific cues
- mixed-task cues

Acceptance:
- selected files alone do not force file routing

#### Task W3.3
Load conversation file metadata from shared source:
- MySQL-backed conversation file rows
- file status
- file type
- parse/index status
- file meta

Acceptance:
- gateway uses metadata only
- gateway does not parse full file content

#### Task W3.4
Produce normalized resolver result:
- `needs_clarification`
- `clarification_message`
- `selected_file_ids`
- `used_files`
- `execution_files`
- `route`
- `turn_mode`
- `allow_kb_verification`

Acceptance:
- gateway can fully decide route without backend re-interpretation

### W4. QA Router

#### Task W4.1
Implement actual routing rules:
- plain QA -> selected mode backend
- file-aware QA -> `fast`
- mixed QA -> `fast`
- ambiguous file selection -> gateway clarification response

Acceptance:
- `requested_mode=thinking` plus selected file but plain question still routes to `thinking`

#### Task W4.2
Normalize outbound backend request body:
- strip raw `pdf_context` from long-term backend contract
- send normalized execution payload

Acceptance:
- route decision becomes gateway-owned

#### Task W4.3
Return explicit routing metadata to frontend:
- `requested_mode`
- `actual_mode`
- `route`

Acceptance:
- frontend can explain why a turn ran on `fast`

### W5. SSE Streaming Proxy

#### Task W5.1
Proxy upstream SSE without buffering:
- chunk passthrough
- heartbeat passthrough
- long timeout

Acceptance:
- frontend sees progressive tokens and steps

#### Task W5.2
Normalize gateway-level stream errors:
- upstream timeout
- upstream unavailable
- invalid request
- clarification required

Acceptance:
- error codes match frozen contract

#### Task W5.3
Preserve completion metadata:
- references
- used files
- file selection
- timings

Acceptance:
- mixed/file turns still populate frontend file-focus state correctly

### W6. Backend Decoupling

#### Task W6.1
Make `fast` backend trust normalized gateway payload first.

Acceptance:
- if `route` and `execution_files` are present, `fast` does not need to re-resolve raw frontend context

#### Task W6.2
Keep backend-side file resolver as temporary fallback.

Acceptance:
- direct backend access still works during migration

#### Task W6.3
Plan removal of duplicate backend resolver logic.

Acceptance:
- removal criteria documented:
  - frontend only calls gateway
  - direct backend routes disabled or internalized
  - parity tests pass

### W7. Observability and Safety

#### Task W7.1
Add structured logs:
- `trace_id`
- `conversation_id`
- `requested_mode`
- `actual_mode`
- `route`
- resolver strategy

#### Task W7.2
Add upstream metrics:
- route counts
- clarification counts
- SSE duration
- upstream error counts

#### Task W7.3
Add guardrails:
- backend timeout policy
- max concurrent streams
- circuit-breaker or fail-fast policy if upstream unavailable

### W8. Validation Matrix

#### Task W8.1
Plain QA matrix
- no files, fast
- no files, thinking
- no files, patent

#### Task W8.2
File-aware QA matrix
- selected PDF + explicit file question
- selected table + explicit table question
- uploaded file + no explicit selection

#### Task W8.3
Mixed-turn continuity matrix
- file turn -> plain turn
- plain turn -> file turn
- file turn -> ambiguous follow-up

#### Task W8.4
Proxy regression matrix
- upload pdf
- upload excel
- list files
- download file
- view pdf inline
- SSE ask_stream passthrough

## Recommended Delivery Order

### Milestone M1
Complete:
- W0
- W1
- W2

Outcome:
- gateway can proxy all public APIs to `fast`

### Milestone M2
Complete:
- W3
- W4
- W5

Outcome:
- gateway can make QA routing decisions correctly

### Milestone M3
Complete:
- W6
- W7
- W8

Outcome:
- duplicate routing logic begins to move out of backend safely

## Immediate Next Tasks

1. Create gateway directory and runtime config skeleton.
2. Implement backend registry and public route proxy.
3. Implement file-context resolver module in gateway.
4. Implement QA route decision service.
5. Implement SSE passthrough proxy for `ask_stream`.
6. Add focused tests for:
- selected file but plain QA
- explicit file QA forcing `fast`
- ambiguous file reference clarification
- file turn followed by plain `thinking` turn
