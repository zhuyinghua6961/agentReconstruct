# Gateway Forwarding Protocol

## 1. Document Purpose

This document freezes the forwarding contract for the multi-backend architecture.

It covers three boundaries:
- frontend -> gateway
- gateway -> backend
- backend -> frontend, via gateway passthrough

It is written for three audiences:
- frontend developers
- gateway developers
- backend developers for `public`, `fast`, `thinking`, and later `patent`

This is not just an API list. It also defines:
- routing ownership
- request normalization
- stream behavior
- header propagation
- clarification behavior
- compatibility rules
- error normalization

## 2. Status Legend

To avoid protocol drift, every rule in this document is interpreted as one of two states.

### 2.1 Implemented now

Already present in current gateway code or covered by tests.

### 2.2 Required next

Not fully implemented yet, but the protocol is frozen and future code must follow it.

Unless explicitly stated otherwise, this document defines the target contract and should be treated as authoritative.

## 3. Architecture Roles

### 3.1 Frontend

The frontend must only call the gateway.

The frontend must not:
- call `fast` directly
- call `thinking` directly
- decide final backend execution target by itself
- infer file-routing rules by itself

The frontend may only choose:
- selected mode
- raw user question
- optional conversation and file context

### 3.2 Gateway

The gateway is the only owner of:
- frontend-facing route surface
- mode path interpretation
- request trace propagation
- file-context resolution
- requested-mode to actual-backend override
- clarification response when file target is ambiguous
- SSE passthrough behavior
- upstream backend selection

The gateway must not:
- run retrieval
- parse PDFs
- run model inference
- persist conversation data
- rewrite final answers semantically

### 3.3 Public backend role

Owns shared infrastructure capabilities:
- auth
- conversations
- uploaded-file metadata
- file upload
- file delete
- file download / preview
- translate
- summarize
- system health / kb info

Current transition reality:
- `public` and `fast` may point to the same physical backend
- this does not change the protocol boundary

### 3.4 QA backends

`fast`, `thinking`, and future `patent` are execution backends for QA turns.

They should gradually become execution-only.

Long-term rule:
- gateway decides route
- backend executes route
- backend does not re-decide frontend file context from scratch

## 4. Terminology

### 4.1 Requested mode

Mode selected by the frontend or implied by the path.

Allowed values:
- `fast`
- `thinking`
- `patent`

### 4.2 Actual mode

Final execution target chosen by gateway after routing logic.

Example:
- frontend selected `thinking`
- question is file-aware
- gateway routes to `fast`
- requested mode = `thinking`
- actual mode = `fast`

### 4.3 Route

Execution route category.

Allowed values:
- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

### 4.4 Turn mode

Semantic turn type decided by gateway.

Allowed values:
- `kb_only`
- `file_only`
- `mixed`

### 4.5 File-aware QA

A turn that depends on uploaded file context.

Examples:
- “请总结这篇文献”
- “第 2 个文件的结论是什么”
- “开路电压_V 的分布是什么”

### 4.6 Mixed QA

A turn that combines uploaded file context with broader knowledge or KB verification.

Examples:
- “结合知识库补充分析这篇文献的局限性”
- “结合外部知识解释这个表格结果”

### 4.7 Clarification

A gateway-generated refusal to guess when file selection is ambiguous.

Example:
- user says “总结这篇文献”
- current conversation has multiple candidate files
- gateway returns clarification instead of arbitrarily choosing one

## 5. Canonical Route Surface

## 5.1 Public capability routes

These routes always go to backend role `public`.

Canonical paths:
- `POST /api/auth/login`
- `POST /api/auth/register`
- `GET /api/auth/me`
- `PUT /api/auth/password`
- `POST /api/auth/password`
- `POST /api/auth/forgot-password/initiate`
- `POST /api/auth/forgot-password/verify`
- `GET /api/auth/security-questions`
- `POST /api/auth/security-questions`
- `GET /api/conversations`
- `POST /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `DELETE /api/conversations/{conversation_id}`
- `POST /api/conversations/{conversation_id}/messages`
- `PUT /api/conversations/{conversation_id}/title`
- `GET /api/conversations/{conversation_id}/files`
- `GET /api/conversations/{conversation_id}/files/{file_id}`
- `DELETE /api/conversations/{conversation_id}/files/{file_id}`
- `GET /api/conversations/{conversation_id}/files/{file_id}/download`
- `POST /api/upload_pdf`
- `POST /api/upload_excel`
- `POST /api/translate`
- `POST /api/summarize_pdf/{doi}`
- `GET /api/extract_pdf_text/{doi}`
- `GET /api/check_pdf/{doi}`
- `GET /api/view_pdf/{doi}`
- `HEAD /api/view_pdf/{doi}`
- `GET /api/literature_content`
- `POST /api/reference_preview`
- `GET /api/quota/my`
- `GET /api/quota/configs`
- `POST /api/quota/configs`
- `PUT /api/quota/configs/{quota_type}`
- `GET /api/quota/users/{user_id}`
- `POST /api/quota/reset/{user_id}/{quota_type}`
- `GET /api/admin/users`
- `POST /api/admin/users`
- `DELETE /api/admin/users/{user_id}`
- `PUT /api/admin/users/{user_id}/password`
- `PUT /api/admin/users/{user_id}/status`
- `PUT /api/admin/users/{user_id}/type`
- `POST /api/admin/users/batch-import`
- `GET /api/admin/users/import-template`
- `GET /api/health`
- `GET /api/kb_info`
- `POST /api/refresh_kb`
- `POST /api/clear_cache`
- `POST /api/clear_pdf`

Status:
- gateway already proxies only a subset of these under `/api/...`
- `/api/v1/...` compatibility for gateway is still required next
- several routes listed above are protocol-required but not implemented in the current gateway route table yet

## 5.2 QA routes

Canonical mode-routed QA paths:
- `POST /api/fast/ask`
- `POST /api/fast/ask_stream`
- `POST /api/thinking/ask`
- `POST /api/thinking/ask_stream`
- `POST /api/patent/ask`
- `POST /api/patent/ask_stream`

Compatibility aliases:
- `POST /api/ask`
- `POST /api/ask_stream`

Alias behavior:
- alias requested mode is `fast`
- alias does not remove gateway routing logic
- alias may still produce `actual_mode = fast`

Status:
- implemented now

## 5.3 Versioning rule

Canonical frontend-facing gateway routes should be `/api/...`, not `/api/v1/...`.

However, the copied frontend currently still uses `/api/v1/...` in multiple places.

Therefore the protocol requires a compatibility period.

Required next:
- gateway accepts both `/api/...` and `/api/v1/...`
- both forms normalize to the same internal route ownership
- backend services are not responsible for frontend path versioning

Recommended migration:
1. gateway supports both `/api/...` and `/api/v1/...`
2. frontend migrates all calls to `/api/...`
3. compatibility aliases are removed later

## 5.4 Current frontend dependency inventory

The copied frontend code currently references the following gateway-facing families.

Auth and account:
- `/api/v1/auth/login`
- `/api/v1/auth/register`
- `/api/v1/auth/me`
- `/api/v1/auth/password`

Conversation and files:
- `/api/v1/conversations`
- `/api/v1/conversations/{conversation_id}`
- `/api/v1/conversations/{conversation_id}/messages`
- `/api/v1/conversations/{conversation_id}/title`
- `/api/v1/conversations/{conversation_id}/files`
- `/api/v1/conversations/{conversation_id}/files/{file_id}`
- `/api/v1/conversations/{conversation_id}/files/{file_id}/download`

QA and literature:
- `/api/v1/ask_stream`
- `/api/v1/view_pdf/{doi}`
- `/api/v1/check_pdf/{doi}`
- `/api/v1/summarize_pdf/{doi}`
- `/api/v1/literature_content?doi=...`
- `/api/v1/reference_preview`

Uploads and translate:
- `/api/v1/upload_pdf`
- `/api/v1/upload_excel`
- `/api/v1/translate`

Quota and admin:
- `/api/v1/quota/...`
- `/api/admin/...`

This inventory matters because protocol completeness must cover current frontend dependencies, not only idealized target routes.

## 5.5 Review findings against current gateway implementation

The current document review found these material gaps between frontend expectations and gateway implementation.

Protocol-required but not yet implemented in current gateway routing table:
- `PUT /api/auth/password`
- `PUT /api/conversations/{conversation_id}/title`
- `/api/literature_content`
- `/api/reference_preview`
- `/api/quota/...`
- `/api/admin/...`
- `/api/refresh_kb`
- `/api/clear_cache`
- `/api/clear_pdf`

Compatibility-required but not yet implemented:
- `/api/v1/...` aliases for current frontend
- token-in-query support for browser-opened preview/download URLs

These are not optional cleanup items. They are real integration blockers.

## 6. Transport and Header Rules

## 6.1 Request transport

Frontend -> gateway transport:
- HTTP/1.1 or HTTP/2
- JSON for normal ask
- JSON request body plus SSE response for stream ask
- multipart form-data for file upload

Gateway -> backend transport:
- HTTP passthrough
- no MQ replacement of the browser-facing HTTP or SSE protocol itself
- queue-backed execution admission may still happen after gateway route resolution and before backend execution starts
- for immediately admitted stream requests, SSE passthrough must stay streaming and not be buffered

## 6.2 Required request headers

For authenticated requests:
- `Authorization: Bearer <token>`

For JSON requests:
- `Content-Type: application/json`

Optional but recommended:
- `X-Trace-Id: <opaque-string>`
- `Accept: application/json`
- `Accept: text/event-stream` for stream calls

## 6.2.1 Auth transport compatibility rule

Canonical rule:
- authenticated requests should use `Authorization: Bearer <token>`

Compatibility rule required during migration:
- direct browser-opened file preview and file download URLs may carry `?token=<token>`

Reason:
- browser navigation and embedded PDF viewers do not always send custom `Authorization` headers

Security rule:
- query-token support is compatibility-only and should be treated as temporary
- gateway should never emit token values in logs
- long-term preferred method remains `Authorization` header

## 6.3 Gateway header propagation rules

Gateway must forward upstream:
- `Authorization`
- `Content-Type`
- `Accept`
- `X-Trace-Id`

Gateway must filter hop-by-hop headers such as:
- `Host`
- `Connection`
- `Content-Length`
- `Transfer-Encoding`
- `Keep-Alive`
- `Upgrade`

Gateway must ensure:
- at most one `X-Trace-Id` is forwarded upstream
- request trace id remains stable across the whole chain

Status:
- duplicate trace-id forwarding bug already fixed in current gateway

## 6.4 Response headers from gateway

Gateway should preserve upstream response headers except hop-by-hop response headers.

Gateway must add:
- `X-Gateway-Backend: public|fast|thinking|patent`

For document preview/download routes, gateway must preserve:
- `Content-Type`
- `Content-Disposition`
- `Cache-Control`

## 7. Frontend -> Gateway Ask Contract

Applies to:
- `POST /api/{mode}/ask`
- `POST /api/{mode}/ask_stream`
- `POST /api/ask`
- `POST /api/ask_stream`

## 7.1 Request body schema

```json
{
  "question": "请总结这篇文献的结论",
  "conversation_id": 101,
  "chat_history": [
    {"role": "user", "content": "上一轮问题"},
    {"role": "assistant", "content": "上一轮回答"}
  ],
  "requested_mode": "thinking",
  "pdf_context": {
    "selected_ids": [33],
    "newly_uploaded_ids": [33],
    "all_available_ids": [11, 22, 33],
    "last_focus_ids": [33],
    "last_turn_route": "pdf_qa"
  },
  "options": {},
  "mode": "thinking"
}
```

## 7.2 Field specification

### `question`
- type: `string`
- required: yes
- min length: `1`
- max length: `4000`
- semantics: raw user question, unmodified by frontend except normal trimming

### `conversation_id`
- type: `integer | string | null`
- required: no
- semantics: conversation identity for persistence and file-context lookup
- gateway should pass through if present

### `chat_history`
- type: `array`
- required: no
- max items: `20`
- item schema:
  - `role`: `user | assistant | system`
  - `content`: `string`, `1..4000`
- semantics: recent visible history only, not full conversation dump

### `requested_mode`
- type: `fast | thinking | patent`
- required: no
- default: `fast`
- semantics: user-selected mode

### `pdf_context`
- type: `object`
- required: no
- default: `{}`
- semantics: advisory file context for gateway routing only

### `options`
- type: `object`
- required: no
- default: `{}`
- semantics: reserved extension field; gateway forwards unchanged

### `mode`
- type: `fast | thinking | patent | null`
- required: no
- semantics: optional echo of path mode, mainly for compatibility/migration

## 7.3 `pdf_context` schema

### `selected_ids`
- type: `integer[]`
- meaning: files currently selected in UI
- rule: presence alone must not force file routing

### `newly_uploaded_ids`
- type: `integer[]`
- meaning: recently uploaded files in the current conversation
- gateway may use this for “latest uploaded” references

### `all_available_ids`
- type: `integer[]`
- meaning: currently available file ids known to frontend
- gateway may use as candidate ordering input

### `last_focus_ids`
- type: `integer[]`
- meaning: files actually used in previous file-oriented turn
- gateway may use this to resolve “这篇文献” in follow-up turns

### `last_turn_route`
- type: `kb_qa | pdf_qa | tabular_qa | hybrid_qa | string`
- meaning: previous resolved route from the last assistant turn
- gateway may use this only as a routing hint, not as a hard command

## 7.4 Request normalization rules

Gateway must enforce:
- invalid `mode` path returns `400 mode_not_supported`
- if body `mode` exists and conflicts with path mode, return `400 bad_request`
- if `requested_mode` exists and conflicts with path mode, path mode wins, but mismatch should be logged
- if `question` is empty after trim, return `400 bad_request`
- unknown keys may be ignored unless security-sensitive

Recommended gateway logging fields:
- `trace_id`
- `requested_mode`
- `path_mode`
- `conversation_id`
- `has_pdf_context`
- `selected_ids_count`

## 7.5 Field precedence and conflict rules

Priority order for mode interpretation:
1. path mode from `/api/{mode}/...`
2. body `mode` if present and equal to path mode
3. body `requested_mode`
4. default `fast`

Conflict handling:
- path mode invalid -> `400 mode_not_supported`
- body `mode` conflicts with path mode -> `400 bad_request`
- body `requested_mode` conflicts with path mode -> request may still proceed using path mode, but mismatch must be logged
- alias path `/api/ask*` sets path mode to `fast`

Legacy-field rule:
- gateway may ignore deprecated fields such as `use_pdf`, `pdf_path`, `use_generation_driven`
- ignored legacy fields must not change routing decisions

## 8. Gateway File-Context Resolution Contract

The gateway may use conversation-file metadata from a provider.

Current provider modes:
- `noop`
- `public_http`

### 8.1 Provider contract

Gateway asks provider for:
- conversation file list
- file type
- file name
- file status
- parse/index status
- file metadata such as table columns

The provider must not:
- download file content
- parse file content
- infer answer semantics

### 8.2 Metadata lookup source

When using `public_http`, gateway calls:
- `GET /api/conversations/{conversation_id}/files`

Auth and trace rules:
- gateway forwards `Authorization`
- gateway forwards `X-Trace-Id`
- gateway accepts standard public backend JSON wrappers

Status:
- implemented now with tests

### 8.3 File-intent decision rules

Gateway should consider these as file-intent evidence:
- explicit references like `#1`
- ordinal references like `第2个文件`
- singular references like `这篇文献`
- plural references like `这些文件`
- latest-upload references like `最新上传`
- table words such as `列`, `字段`, `筛选`, `统计`
- column-name match from file metadata
- filename match from selected file metadata

Gateway should consider these as mixed-intent evidence:
- `结合知识库`
- `结合外部知识`
- `knowledge base`

Gateway should not force file routing only because:
- `selected_ids` exists
- user asks a generic topic containing words like `文献` or `论文`

### 8.4 Clarification rules

If the question contains singular file intent and multiple candidate files remain, gateway must clarify instead of guessing.

Clarification examples:
- “总结这篇文献” with 3 active candidate files
- “这个文件的结论是什么” with no unique active target

Gateway may auto-resolve only if one of these is true:
- exactly one selected file
- exactly one last-focus file from previous file turn
- exactly one newly uploaded file when asking for latest uploaded
- exactly one candidate file remains


### 8.5 Examples of expected gateway decisions

Example A:
- selected mode: `thinking`
- selected files: `[11]`
- question: `磷酸铁锂电压范围是多少？`
- output: `route=kb_qa`, `turn_mode=kb_only`, `actual_mode=thinking`

Example B:
- selected mode: `thinking`
- selected files: `[11]`
- question: `请总结这篇文献`
- output: `route=pdf_qa`, `turn_mode=file_only`, `actual_mode=fast`

Example C:
- selected mode: `thinking`
- selected files: `[33]` where file 33 is Excel with column `开路电压_V`
- question: `开路电压_V 的分布是什么？`
- output: `route=tabular_qa`, `turn_mode=file_only`, `actual_mode=fast`

Example D:
- selected mode: `thinking`
- selected files: `[11, 33]` where one is PDF and one is table
- question: `结合知识库解释这些文件的一致性`
- output: `route=hybrid_qa`, `turn_mode=mixed`, `actual_mode=fast`

Example E:
- selected mode: `thinking`
- candidate files: `[11, 22, 33]`
- question: `总结这篇文献`
- output: clarification, no backend execution

Example F:
- selected mode: `patent`
- selected files: `[11]`
- question: `请总结这篇文献`
- output: `route=pdf_qa`, `turn_mode=file_only`, `actual_mode=fast`, `requested_mode=patent`

Example G:
- selected mode: `patent`
- selected files: `[11]`
- question: `请结合知识库总结这篇文献`
- output: `route=hybrid_qa`, `turn_mode=mixed`, `actual_mode=fast`, `requested_mode=patent`

## 9. Gateway Decision Output Contract

For every ask turn, gateway produces these routing outputs.

### 9.1 `route`
Allowed values:
- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

### 9.2 `turn_mode`
Allowed values:
- `kb_only`
- `file_only`
- `mixed`

### 9.3 `actual_mode`
Allowed values:
- `fast`
- `thinking`
- `patent`

### 9.4 Decision matrix

#### Plain QA
Condition:
- no resolved file intent

Output:
- `route = kb_qa`
- `turn_mode = kb_only`
- `actual_mode = requested_mode`

#### File-only QA
Condition:
- resolved file intent and no KB-combination hint

Output:
- `turn_mode = file_only`
- `actual_mode = fast` for all requested modes (`fast`, `thinking`, `patent`)
- route depends on resolved file types

#### Mixed QA
Condition:
- resolved file intent plus KB-combination hint

Output:
- `turn_mode = mixed`
- `actual_mode = fast` for all requested modes (`fast`, `thinking`, `patent`)
- `allow_kb_verification = true`

#### Route selection by file types
- only PDF -> `pdf_qa`
- only table -> `tabular_qa`
- PDF + table -> `hybrid_qa`

Status:
- implemented now in gateway tests

## 10. Gateway -> Backend Execution Payload

Backends should receive normalized payloads only.

```json
{
  "question": "请总结这篇文献的结论",
  "conversation_id": 101,
  "chat_history": [],
  "requested_mode": "thinking",
  "actual_mode": "fast",
  "route": "pdf_qa",
  "turn_mode": "file_only",
  "allow_kb_verification": false,
  "used_files": [
    {
      "file_id": 33,
      "file_type": "pdf",
      "file_name": "paper.pdf",
      "selected_reason": "selected_single",
      "source": "gateway_file_context",
      "file_meta": {}
    }
  ],
  "execution_files": [
    {
      "file_id": 33,
      "file_type": "pdf",
      "file_name": "paper.pdf",
      "selected_reason": "selected_single",
      "source": "gateway_file_context",
      "file_meta": {}
    }
  ],
  "trace_id": "req_abc123",
  "options": {}
}
```

## 10.1 Field meanings

### `requested_mode`
What the user selected.

### `actual_mode`
What the gateway actually routed to.

### `route`
Execution route already decided by gateway.

### `turn_mode`
Whether the turn is KB-only, file-only, or mixed.

### `allow_kb_verification`
Boolean flag for backends that support file answer plus KB cross-check.

### `used_files`
Gateway-resolved file metadata for frontend-visible provenance.

### `execution_files`
Files the backend should actually execute against.

Current expectation:
- `execution_files` usually equals `used_files`
- future versions may allow them to differ

### `trace_id`
Stable correlation id for logs and client-visible debugging.

## 10.2 Backend obligations

Backends must:
- trust `actual_mode`, `route`, and `turn_mode`
- not re-parse raw frontend `pdf_context`
- not silently switch `actual_mode` again
- preserve `trace_id`
- preserve event order for streams
- return protocol-compliant JSON or SSE

Backends may:
- enrich `metadata`
- add backend-specific timings
- add route-specific evidence structures

Backends must not:
- drop `trace_id`
- overwrite `requested_mode`
- return a different semantic route without explicit protocol revision

## 11. Success Response Contract

## 11.1 Non-streaming success response

Applies to:
- `POST /api/{mode}/ask`

Recommended shape:

```json
{
  "success": true,
  "data": {
    "final_answer": "结论如下...",
    "metadata": {
      "requested_mode": "thinking",
      "actual_mode": "fast",
      "route": "pdf_qa",
      "mode": "fast",
      "query_mode": "pdf_qa",
      "conversation_id": 101,
      "used_files": []
    },
    "references": [],
    "reference_links": [],
    "pdf_links": [],
    "timings": {},
    "trace_id": "req_abc123"
  },
  "trace_id": "req_abc123"
}
```

Required fields:
- top-level `success`
- `data.final_answer`
- `trace_id` either top-level or in `data`, preferably both

Recommended metadata fields:
- `requested_mode`
- `actual_mode`
- `route`
- `query_mode`
- `conversation_id`

## 11.2 Streaming response contract

Applies to:
- `POST /api/{mode}/ask_stream`

Response headers should include:
- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `X-Gateway-Backend`

All frames use:

```text
data: {json}\n\n
```

Gateway should not coalesce the full stream before sending downstream.

### 11.2.1 Required event types
- `metadata`
- `content`
- `done`

### 11.2.2 Optional event types
- `thinking`
- `step`
- `error`

### 11.2.3 Recommended common fields
Each event should include when practical:
- `type`
- `trace_id`
- `seq`
- `ts`

Current frontend can work without `seq` and `ts`, but protocol recommends adding them.

### 11.2.3.1 Event ordering rules

Stream ordering constraints:
- `metadata` should be first when available
- `done` and `error` are mutually exclusive terminal events
- no `content` event should appear after terminal `done` or `error`
- `step` may repeat the same `step` key with updated `status`
- stream consumers must tolerate missing optional event types

HTTP status expectations:
- successful stream: `200 text/event-stream`
- clarification stream: `200 text/event-stream`
- validation failure before stream open: `4xx application/json`
- upstream non-SSE failure: gateway returns JSON with upstream status

### 11.2.4 `metadata` event

Purpose:
- announce resolved route and mode before answer content

Recommended shape:

```json
{
  "type": "metadata",
  "requested_mode": "thinking",
  "actual_mode": "fast",
  "route": "pdf_qa",
  "mode": "fast",
  "query_mode": "pdf_qa",
  "trace_id": "req_abc123"
}
```

### 11.2.5 `thinking` event

Purpose:
- lightweight progress text for UI step rendering

Recommended shape:

```json
{
  "type": "thinking",
  "content": "正在定位目标文献",
  "trace_id": "req_abc123"
}
```

### 11.2.6 `step` event

Purpose:
- structured progress state

Recommended shape:

```json
{
  "type": "step",
  "step": "retrieve",
  "title": "检索相关内容",
  "message": "正在召回文献片段",
  "status": "processing",
  "data": {},
  "trace_id": "req_abc123"
}
```

Allowed normalized statuses:
- `processing`
- `success`
- `error`

### 11.2.7 `content` event

Purpose:
- incremental answer text

Recommended shape:

```json
{
  "type": "content",
  "content": "这是流式输出片段",
  "trace_id": "req_abc123"
}
```

### 11.2.8 `done` event

Purpose:
- mark answer completion and provide final structured metadata

Recommended shape:

```json
{
  "type": "done",
  "final_answer": "完整答案",
  "route": "pdf_qa",
  "used_files": [],
  "reference_links": [],
  "pdf_links": [],
  "doi_locations": [],
  "timings": {},
  "metadata": {
    "requested_mode": "thinking",
    "actual_mode": "fast",
    "route": "pdf_qa",
    "query_mode": "pdf_qa"
  },
  "trace_id": "req_abc123"
}
```

`done` should be the last successful event.


Field notes:
- `reference_links` and `pdf_links` should contain clickable objects rather than raw strings when available
- recommended item shape: `{"doi": "10.xxxx/...", "pdf_url": "/api/view_pdf/...", "title": "optional"}`
- `doi_locations` may be used by frontend to mark inline DOI locations after answer completion

### 11.2.9 `error` event

Purpose:
- terminal failure inside a stream

Recommended shape:

```json
{
  "type": "error",
  "code": "upstream_error",
  "error": "upstream_error",
  "message": "backend failed",
  "retriable": false,
  "trace_id": "req_abc123"
}
```

## 12. Clarification Contract

If gateway cannot uniquely resolve a file target, it must not call the backend.

## 12.1 JSON clarification response

Status code:
- `400`

Body:

```json
{
  "success": false,
  "code": "FILE_SELECTION_CLARIFICATION_REQUIRED",
  "error": "file_selection_clarification_required",
  "message": "当前对话中有多个候选文件，请明确指定文件",
  "trace_id": "req_abc123",
  "requested_mode": "thinking",
  "actual_mode": "thinking",
  "route": "kb_qa"
}
```

## 12.2 SSE clarification response

Gateway should return a short synthetic stream:
1. `metadata`
2. `error`

Gateway must not open an upstream stream in this case.

Status:
- implemented now

## 13. Error Contract

## 13.1 Gateway-generated errors

Recommended stable codes:
- `mode_not_supported`
- `bad_request`
- `file_selection_clarification_required`
- `upstream_error`
- `upstream_timeout`

## 13.2 Non-streaming error body

Recommended shape:

```json
{
  "success": false,
  "code": "upstream_error",
  "error": "upstream_error",
  "message": "upstream_error",
  "trace_id": "req_abc123"
}
```

## 13.3 Streaming upstream failure handling

If upstream `ask_stream` returns non-SSE error content:
- gateway should stop streaming mode
- gateway should return JSON error body instead
- response should include `backend` and `trace_id`

Status:
- implemented now for non-SSE upstream stream failure handling

## 13.4 Auth and permission failures

Gateway should preserve upstream status codes such as:
- `401`
- `403`
- `404`

Gateway should avoid rewriting auth semantics.

Frontend may handle these by:
- redirecting to login on `401`
- showing disabled / forbidden state on `403`


## 13.5 Ask-stream status matrix

| Scenario | HTTP status | Content-Type | Body shape |
| --- | --- | --- | --- |
| Normal stream success | 200 | `text/event-stream` | SSE |
| Gateway clarification | 200 | `text/event-stream` | synthetic SSE |
| Invalid mode path | 400 | `application/json` | JSON error |
| Body/path mode conflict | 400 | `application/json` | JSON error |
| Empty question | 400 | `application/json` | JSON error |
| Upstream auth failure | preserve upstream | usually `application/json` | JSON passthrough or normalized JSON |
| Upstream non-SSE stream failure | preserve upstream | `application/json` | gateway JSON error |
| Gateway timeout before stream open | 504 recommended | `application/json` | JSON error |

This matrix should be used directly by frontend integration and gateway tests.

## 14. Public Proxy Contract

For all public capability routes, gateway acts as a transparent proxy with limited normalization.

Gateway must:
- forward method, path, query, body
- preserve auth header
- preserve inline PDF headers
- attach `X-Gateway-Backend: public`

Gateway must not:
- rewrap successful public JSON payloads
- convert public route payload shape
- invent its own pagination shape

This matters because current frontend already expects many legacy payload shapes from the public backend.


## 14.1 Public query and body passthrough rules

Gateway should preserve public-route query strings unchanged. Important examples:
- `/api/conversations?page=1&page_size=30`
- `/api/conversations/{id}/files?include_deleted=true`
- `/api/literature_content?doi=10.xxx/...`
- `/api/admin/users?page=1&page_size=10`
- `/api/admin/users/import-template?format=xlsx`

Gateway should preserve public-route body shapes unchanged for:
- auth payloads
- conversation creation and title update
- quota config writes
- admin writes
- upload multipart forms

Protocol rule:
- public route compatibility belongs in gateway, not in frontend-specific backend forks

## 15. File Metadata Contract for Gateway Routing

When gateway fetches conversation files, it expects rows with enough metadata for routing.

Minimum useful fields:
- `file_id`
- `file_type`
- `file_name`

Recommended fields:
- `file_status`
- `parse_status`
- `index_status`
- `processing_stage`
- `file_meta.columns`
- `display_no`
- `file_no`
- `storage_ref`
- `local_path`

Accepted payload wrappers for provider normalization:
- `{"data": {"files": [...]}}`
- `{"files": [...]}`
- `{"data": [...]}`
- `[...]`

Status:
- implemented now in `public_http` provider normalization


Additional normalization rules:
- rows without a usable positive `file_id` must be ignored
- deleted files should not be used as active routing candidates
- rows should be sorted consistently by `display_no`, then `file_no`, then `file_id` when these fields exist

## 16. Frontend Rendering Contract

Frontend is allowed to depend on these stream or final fields:
- `metadata.query_mode`
- `metadata.actual_mode`
- `metadata.route`
- `content.content`
- `done.final_answer`
- `done.used_files`
- `done.reference_links`
- `done.pdf_links`
- `done.doi_locations`
- `done.timings`
- `done.trace_id`
- `step.status`

Frontend must not depend on:
- backend-internal prompt stage names
- backend-specific undocumented event types
- backend-specific raw retrieval payloads
- `selected_ids` being echoed back unchanged


Current frontend compatibility notes:
- some modules read token from `localStorage.token`
- some modules read token from `localStorage.agentcode.auth.token.v1`
- gateway must stay agnostic to frontend token storage keys; it only consumes request headers or compatibility query token

## 17. Timeout, Retry, and Cancel Rules

## 17.1 Timeouts

Gateway configuration currently defines:
- normal request timeout
- SSE request timeout

Protocol expectations:
- public routes use normal timeout
- `ask_stream` uses longer timeout
- timeout values should be centralized in gateway config

## 17.2 Retries

Gateway should not automatically retry non-idempotent ask requests.

Reason:
- retries may duplicate persistence
- retries may double-run model generation

Gateway may later retry selected safe public GET routes, but this is outside the current ask protocol.

## 17.3 Cancellation

Frontend may cancel `ask_stream` using request abort.

Gateway behavior should be:
- stop downstream stream promptly
- close upstream stream promptly
- avoid leaking sockets

This behavior should be tested during live integration.

## 18. Compatibility and Migration Constraints

### 18.1 Frontend current state

The copied frontend currently still references `/api/v1/...`.

Therefore, before frontend migration is finished, gateway should support:
- `/api/v1/ask_stream`
- `/api/v1/ask`
- `/api/v1/conversations/...`
- other currently used `/api/v1/...` public routes

### 18.2 Long-term direction

Long term, the frontend should use only:
- `/api/...`
- `/api/{mode}/ask`
- `/api/{mode}/ask_stream`

### 18.3 Backend compatibility

Backends should not expose frontend-facing route differences to the browser.

Gateway is the compatibility boundary.

## 19. Conformance Checklist

A frontend change is compliant only if:
- it sends ask requests to gateway, not directly to backends
- it does not infer actual backend on its own
- it handles `actual_mode` separately from selected mode
- it handles clarification responses
- it handles both JSON and SSE error shapes

A gateway change is compliant only if:
- it does not break `X-Trace-Id` continuity
- it keeps public routes on `public`
- it keeps file-aware and mixed QA on `fast`
- it does not buffer SSE to completion
- it does not duplicate file-routing logic in frontend
- it covers all frontend-required public compatibility routes or explicitly documents unsupported ones

A backend change is compliant only if:
- it accepts normalized execution payload
- it preserves `trace_id`
- it emits protocol-compliant JSON or SSE
- it does not silently override gateway routing decisions

## 20. Immediate Follow-up Tasks

1. Add `/api/v1/...` compatibility routes in gateway.
2. Update frontend ask APIs from `/api/v1/ask_stream` to `/api/{mode}/ask_stream`.
3. Add protocol tests for body-path mode mismatch.
4. Add protocol tests for clarification JSON and clarification SSE.
5. Add protocol tests for upstream `401`, `403`, and timeout normalization.
6. Add a mock-public-backend plan so frontend can run before public backend is ready.
7. Add backend compliance cases for `fast` and `thinking` against this document.
