# Multi-Chat Background Streaming Design

**Date:** 2026-04-04

## Summary

Support up to 5 simultaneous generating conversations within the current frontend instance, meaning the currently loaded browser page / SPA runtime.

Conversation A may continue streaming in the background while the user creates or switches to B/C/D/E and asks new questions there. A background generation must not be stopped by in-app conversation switching. The same conversation still allows only one active generation at a time within the current frontend instance.

When 5 conversations are already generating or dispatching from this frontend instance, the 6th send attempt is blocked in the frontend with an explicit message instead of sending a request.

---

## Goals

- Allow cross-conversation concurrent streaming with a hard cap of 5 active or dispatching conversations within the current frontend instance.
- Preserve existing streaming UX inside a single conversation: send becomes stop while that conversation is active.
- Allow stopping a background-generating conversation directly from the sidebar.
- Keep existing ask/ask_stream and persistence contracts compatible.

## Non-Goals

- No support for multiple active generations inside the same conversation in the same frontend instance.
- No guarantee of a global per-user 5-way cap across tabs, devices, or sessions in this phase.
- No new backend cancel API in this phase.
- No attempt to restore a live SSE connection after full page refresh.
- No change to persisted backend message schema or ask_stream payload shape.

---

## Current State

The frontend currently models streaming as a single global runtime instance:

- one abort controller
- one streaming chat id
- one client-generated `streamRequestId`
- one target index
- one global `isStreaming`

That global state disables new chat creation, chat switching, pinning, uploads, mode switching, and effectively all cross-conversation activity during a stream.

The frontend `streamRequestId` is a client-only runtime identifier used to find the target assistant message in local state. It is not part of backend persistence identity. Backend persistence and idempotency instead rely on server trace identity (`trace_id`) and keys shaped like `conversation_id:trace_id:operation`.

The backend path does not appear to enforce one active conversation per user. It enforces service or process concurrency limits and per-conversation ordered persistence.

---

## Design Decisions

1. Different conversations may stream concurrently within one frontend instance; a single conversation may not.
2. The frontend is the source of truth for the 5-conversation current-instance cap.
3. The 6th concurrent attempt is rejected before request dispatch with a clear user-facing message.
4. Sidebar entries for generating conversations show both generating state and a stop action.
5. Runtime-only streaming state stays out of persisted chat data and out of backend payloads.
6. Backend config defaults should be raised so a single frontend instance can realistically achieve 5 concurrent thinking-mode streams under normal conditions, but this is still service-instance capacity, not a per-user distributed guarantee.

## Implementation Integrity

This feature must be implemented as real end-to-end behavior, not as a placeholder shell.

Explicitly disallowed:

- UI-only busy badges without real concurrent stream runtime support
- stop buttons that only change local text or icon state without aborting the real request for that chat
- fake capacity enforcement that only changes button state but still allows a 6th request to be sent
- per-chat busy maps that coexist with shared global buffering or flush state and therefore do not truly support concurrent streams
- test coverage that only checks copy, structure, or mocked flags while the actual runtime behavior is still single-stream

Required standard:

- a chat marked busy must correspond to a real dispatching or streaming request owned by that chat in the current frontend instance
- a stopped chat must have had its real frontend request aborted
- a 6th blocked send must be prevented before network dispatch
- switching among chats while background streams are active must preserve correct content routing and message growth in real runtime behavior
- verification must demonstrate actual working multi-chat concurrency rather than only state-shape refactors

---

## Frontend State Model

Replace the single global stream runtime with a per-chat runtime map keyed by chat id.

Each runtime record contains:

- `phase`: `dispatching` or `streaming`
- `clientStreamRequestId`
- `abortController`
- `targetMessageIndex`
- `pendingContent`
- `flushFrame`
- `startedAt`
- `serverTraceId` (optional, filled only if surfaced by stream metadata later)

Derived selectors:

- `isChatBusy(chatId)`
- `isChatStreaming(chatId)`
- `currentChatIsBusy`
- `activeBusyChatIds`
- `activeBusyCount`
- `hasBusyCapacity`

Rules:

- Capacity accounting counts both `dispatching` and `streaming` records. This prevents race-sending a 6th request before earlier dispatches fully attach.
- A runtime entry is created immediately before request dispatch once local capacity and same-chat checks pass.
- A runtime entry is removed on done, error, or local stop.
- These entries are never serialized to localStorage.
- Existing message-level `streamRequestId` may remain as the stored field name if that minimizes churn, but it must be treated as a client-only stream identifier.

---

## Home View Behavior

Update the main chat page so that actions are scoped to the current conversation instead of the whole app.

Allowed while some other conversation is busy:

- create a new chat
- switch to another chat
- pin or unpin chats
- inspect history
- send a question in an idle conversation if `activeBusyCount < 5`

Disabled only when the current conversation is busy:

- that conversation's upload button
- that conversation's ask mode switch
- that conversation's send action becomes stop

Sidebar behavior:

- any busy conversation shows a generating badge
- any busy conversation exposes a stop action in the sidebar
- stopping from the sidebar affects only that chat's runtime record and request

Overflow behavior:

- if current conversation is idle but `activeBusyCount >= 5`, sending is blocked locally
- the UI shows `最多同时生成 5 个会话，请先停止或等待其中一个完成。`
- no network request is sent for the blocked 6th attempt
- the draft input remains intact

---

## Streaming Isolation Rules

Streaming updates must always be routed by `(chatId, clientStreamRequestId)` in the frontend runtime and never by current active chat alone.

Implications:

- background chat A can continue receiving chunks while B is visible
- A's chunks update only A's assistant message and sidebar state
- B must not auto-scroll because A received content
- question outline, hidden-history reveal state, and current scroll behavior remain tied to the visible chat only
- when returning to A, already accumulated content is shown immediately without reconnecting the stream

---

## Single-Conversation Rule Scope

The rule "one active generation per conversation" applies only inside the current frontend instance.

Explicitly not guaranteed in this phase:

- another browser tab may start another request for the same conversation
- another device may do the same
- the backend will not reject those duplicate concurrent same-conversation requests by itself in this phase

UI behavior required in this phase:

- the current frontend instance never starts a second active request for a chat it already marks busy
- if duplicate work from another tab or device later appears in persisted history, the current instance treats it as external state, not as a contract violation

---

## Stop Semantics And Persistence Truth

- Stopping the current conversation via the send/stop button aborts only that conversation's active request from this frontend instance.
- Stopping a background conversation from the sidebar aborts only that conversation's active request from this frontend instance.
- This phase continues to use frontend fetch abort only.
- Because no backend cancel endpoint exists, abort is best-effort for upstream computation.
- If the backend still completes and persists an assistant terminal message after local abort, persisted backend state is the long-term source of truth after reload or cross-device read.
- In the current live page, once the user stops locally, the chat is shown as locally canceled and no further chunks from that aborted request are expected to be rendered.

---

## Busy Chat Rehydration Rule

For synced chats, any code path that fetches conversation detail and then overwrites local chat state is not valid for a locally busy chat.

Required behavior in this phase:

- if the target chat is locally `dispatching` or `streaming`, switching to that chat must render existing local message state first
- server detail refresh for that chat must be skipped while the chat remains busy in the current frontend instance
- after the chat leaves busy state, normal detail refresh may resume
- this rule applies not only to `chatStore.switchChat()` but also to any detail-refresh path triggered by file-status polling, file upload completion, file removal follow-up refresh, or any other conversation-detail sync flow that could replace `chat.messages`

This rule exists to preserve:

- the in-flight assistant message
- local canceled presentation
- `(chatId, clientStreamRequestId)` targeting

---

## Navigation Scope

In this document, "background survival" and "navigation does not stop generation" refer only to in-app conversation switching inside the Home chat page.

Explicitly outside this guarantee:

- route navigation away from Home
- full SPA teardown
- tab close
- full page refresh

Once the current frontend instance ends, runtime stream state ends with it.

---

## Delete Semantics

- A chat that is busy in the current frontend instance cannot be deleted from the UI in this phase.
- Delete affordances for busy chats should be disabled or hidden.
- Deleting a different idle chat while other chats are busy is allowed and must not disturb existing busy runtime records.

---

## Refresh Behavior

- Full page refresh clears all runtime busy records.
- No live SSE connection is restored.
- Any partially accumulated assistant content already saved in local chat state remains visible as static content.
- After refresh, the UI does not claim any chat is still generating, even if server-side work from pre-refresh requests may still be finishing.
- Because of that, the 5-way cap is only guaranteed for the lifetime of the current frontend instance and may temporarily diverge from backend reality after refresh; backend saturation may still yield ordinary busy or 429 responses on later sends.

---

## Backend Alignment

No external API shape changes are required.

Required config alignment for practical capacity:

- `highThinkingQA/config.py` and corresponding shared env: raise `ASK_STREAM_MAX_CONCURRENT` from 2 to at least 5
- `highThinkingQA/config.py` and corresponding shared env: raise `ASK_EXECUTOR_MAX_WORKERS` from 4 to at least 5
- `gateway/app/core/config.py`: if gateway admission is enabled in deployment, raise `INTERACTIVE_EXECUTION_MAX_CONCURRENT` and `INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT` to at least 5 for this rollout

Assumed backend invariants kept unchanged:

- ask/ask_stream request payloads keep current shape
- persistence remains keyed by conversation id
- idempotency remains based on `conversation_id + trace_id + operation`
- ordered persistence per conversation remains intact

---

## Error Handling

- If frontend blocks the 6th request, render a local non-terminal warning and keep the draft input intact.
- If frontend thinks capacity exists but backend still rejects due to service saturation, render the existing backend busy/quota/routing error flow on that target assistant message.
- If a background stream finishes after the user has navigated away from that chat, its terminal state is still applied to its own message.
- If local abort and backend persistence later disagree, the current page keeps the local stopped presentation until rehydration; after reload, persisted backend truth may replace it.

---

## Acceptance Criteria

- Up to 5 conversations can be busy simultaneously within one frontend instance.
- Switching away from a busy conversation does not stop it.
- A busy conversation can be stopped from the sidebar.
- The 6th concurrent attempt is blocked locally with no request sent.
- A conversation cannot start a second concurrent generation from the same frontend instance while it already has one active generation.
- Switching back to a busy synced chat does not overwrite its local in-flight assistant message from server detail refresh.
- Current-chat scrolling and outline behavior are unaffected by background chat chunks.
- Runtime-only stream state is not persisted.
- Leaving the Home route or refreshing is explicitly outside the background-stream survival guarantee.
- Busy chats cannot be deleted.
- Spec language does not claim a cross-tab or cross-device per-user cap.
- The shipped behavior is real: concurrent chats actually dispatch and receive independent streams, stop actually aborts the matching request, and the 6th send is actually prevented before dispatch.

---

## Implementation Targets

Primary change areas:

- `frontend-vue/src/stores/chatStore.js`
- `frontend-vue/src/views/Home.vue`
- `frontend-vue/src/stores/chatPersistence.js`
- helper utilities around stream targeting and lifecycle if needed
- `highThinkingQA/config.py`
- `highThinkingQA/config.shared.env`
- `resource/config/services/highThinkingQA/config.shared.env`
- if applicable in deployment, gateway admission env/config values consumed by `gateway/app/core/config.py`

---

## Test Coverage

Frontend tests should cover:

- two chats busy simultaneously
- five chats busy simultaneously
- local rejection of the 6th send
- same-chat second send becomes stop rather than a new request
- sidebar stop for a background chat
- no auto-scroll bleed from background chat updates
- runtime state omitted from persistence
- refresh clears busy runtime state
- switching to a busy synced chat does not server-overwrite the local in-flight assistant message
- file-status and file-refresh flows do not server-overwrite the local in-flight assistant message for a busy chat
- busy chats cannot be deleted

Backend/config tests should cover:

- updated default concurrency settings in highThinkingQA shared config
- no contract change to ask_stream payload handling
- no mistaken coupling between frontend `clientStreamRequestId` and backend `trace_id` semantics
- per-conversation persistence ordering remains unchanged for concurrent different-conversation writes
