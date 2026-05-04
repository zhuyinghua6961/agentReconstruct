# QA Step Timing Reliability Design

**Goal:** Make processing steps and per-step duration display reliable across fastQA, highThinkingQA, and patent QA, including live streaming, refresh recovery, task replay, and persisted conversation history.

**Status:** Approved

---

## Background

The frontend already renders a "处理过程" panel and can display duration labels next to steps. Backend services also emit timing payloads. The unstable behavior comes from inconsistent contracts and an incomplete persistence path:

- fastQA and patent emit generation-stage timings as `done.timings` with keys `stage1`, `stage2`, `stage25`, `stage3`, `stage4` in milliseconds.
- highThinkingQA emits `done.timings` with keys such as `step1_parallel`, `step2_pre_answer`, `step3_retrieval`, `step4_synthesis`, `step5_check_revise`, and `total` in seconds.
- The frontend currently maps per-step duration mostly by generation-stage keys and Chinese stage titles, so highThinkingQA step events like `step1` do not reliably match timing keys like `step1_parallel`.
- The gateway refresh-survivable task path receives upstream `done.timings`, but terminal task persistence sends only `answer_text`, `steps`, and `failure` to public-service authority APIs. The authoritative assistant record can therefore lose timings after refresh or recovery.
- patent stage 2.5 can be returned as `status: "skipped"`, but frontend step status normalization does not preserve `skipped`, so skipped steps can become processing-like in persisted or normalized state.

This design treats `done.timings` as the terminal source of truth and makes all recovery and history paths preserve it.

## Current Evidence

### Backend Timing Emission

fastQA generation-driven KB QA emits stage timing metadata before stage 4 and terminal timings on `done`:

- `fastQA/app/modules/qa_kb/orchestrators/generation.py` emits `metadata.stage_timings_ms` while streaming.
- The same orchestrator emits terminal `done.timings`.

patent staged QA records the same stage keys in milliseconds:

- `patent/server/patent/orchestrators/generation.py` records stage timings in `_timed()`.
- `patent/server/patent/result_builder.py` includes `timings` in the terminal `done` event.

highThinkingQA records seconds:

- `highThinkingQA/agent_core/graph.py` records `step1_parallel`, `step2_pre_answer`, `step3_retrieval`, `step4_synthesis`, `step5_check_revise`, loop totals, counters, and `total`.
- `highThinkingQA/server/services/ask_service.py` puts `state.timings` into the terminal `done` event.

### Persistence Gap

The direct conversation summary path already understands `timings`, but the refresh-survivable task terminal path does not:

- `gateway/app/services/qa_tasks.py` terminalizes completed tasks without passing `payload.timings`.
- `gateway/app/services/conversation_persistence.py::terminal_task_assistant()` has no `timings` parameter.
- `public-service/backend/app/modules/conversation/task_schemas.py::AuthorityTaskAssistantTerminalRequest` has no `timings` field.
- `public-service/backend/app/modules/conversation/service.py::terminal_authority_task_assistant()` stores `metadata.steps` but not `metadata.timings`.

This means a live browser may temporarily show durations from the replayed `done` event, but the authoritative assistant message can later overwrite or reload without timings.

### Frontend Gap

The frontend already has useful pieces:

- `frontend-vue/src/utils/stageTimings.js` normalizes generation-stage millisecond timings and highThinking second timings.
- `frontend-vue/src/services/api.js` and `frontend-vue/src/stores/chatStore.js` preserve timings when a message already contains `message.timings`, `metadata.timings`, or `metadata.stage_timings_ms`.
- `frontend-vue/src/views/Home.vue` stores `data.timings` on `done`.

The remaining gaps are:

- `mergeRoutingMetadata()` does not preserve `timings` or `stage_timings_ms` from metadata events.
- `getStepTimingDurationLabel()` does not map highThinking step event keys to highThinking timing keys.
- step status normalization lacks a stable `skipped` status.

## Design Goals

- Preserve terminal timing data through live stream, replay, terminal task persistence, retry sync, and history reload.
- Keep one canonical terminal source of truth: `done.timings`.
- Support partial live display when metadata events include `stage_timings_ms` or `timings`, without relying on partial metadata as final truth.
- Keep backend generation timing units unchanged to avoid broad backend rewrites.
- Make frontend mapping explicit enough that fastQA, highThinkingQA, and patent all show per-step durations consistently.
- Keep the implementation testable with focused gateway, public-service, and frontend unit or structure tests.

## Non-Goals

- Changing answer generation behavior, retrieval stages, prompts, ranking, caching, or citation behavior.
- Adding a new streaming event type.
- Replacing the existing `steps` event shape.
- Showing continuously ticking elapsed timers for active in-progress steps.
- Redesigning the process panel UI beyond making status and duration labels stable.
- Converting all services to a single timing unit internally.

## Timing Contract

### Terminal Contract

Every successful QA terminal `done` event produced by fastQA, highThinkingQA, and patent QA is expected to include:

```json
{
  "type": "done",
  "final_answer": "...",
  "timings": {
    "stage1": 1234.5
  }
}
```

For these three services, preserving and displaying timings depends on this expected contract. Consumers must still be defensive: if `timings` is absent, not an object, or contains invalid values, streaming and terminal persistence continue normally and the UI simply hides duration labels. Implementers should not add validation that rejects or fails an otherwise valid terminal answer because timings are missing.

`timings` must be a JSON object when present. Values used for display must be finite, non-negative numbers. Unknown or invalid values are ignored by the frontend display model.

### Mode Families

Generation-stage family, used by fastQA and patent:

| Key | Unit | Meaning |
| --- | --- | --- |
| `stage1` | ms | planning / pre-answer / retrieval planning |
| `stage2` | ms | retrieval and reranking / patent retrieval |
| `stage25` | ms | evidence expansion / context compression |
| `stage3` | ms | evidence loading / prompt or evidence assembly |
| `stage4` | ms | final answer generation |

Thinking-step family, used by highThinkingQA:

| Key | Unit | Meaning |
| --- | --- | --- |
| `step1_parallel` | seconds | direct answer and decomposition parallel phase |
| `step2_pre_answer` | seconds | sub-question pre-answer phase |
| `step3_retrieval` | seconds | retrieval phase |
| `step4_synthesis` | seconds | synthesis draft phase |
| `step5_check_revise` | seconds | checker/reviser phase |
| `step5_check_total` | seconds | checker accumulated time |
| `step5_revise_total` | seconds | reviser accumulated time |
| `total` | seconds | total pipeline time |

Counter keys such as `step5_issue_total` and `step5_revise_rounds` remain allowed in `timings`, but they must not render as duration rows.

### Metadata Contract

Streaming metadata events may include either:

- `stage_timings_ms`: partial generation-stage timing data, primarily from fastQA and patent.
- `timings`: partial or final timing-like data.

The frontend should normalize either field into `metadata.timings` for display. Terminal `done.timings` wins over earlier metadata values.

### Persistence Contract

For refresh-survivable tasks, terminal persistence must carry `timings` end to end:

1. Gateway task worker extracts `done.timings`.
2. Gateway calls `conversation_persistence_service.terminal_task_assistant(..., timings=...)`.
3. Gateway terminal sync retry payload includes `timings`.
4. public-service task terminal request accepts `timings`.
5. public-service stores `metadata.timings` on the assistant placeholder that becomes the terminal assistant message.
6. Conversation detail and frontend normalization expose the same timings back to the UI.

If timings are absent or invalid, persistence should store no timings or an empty object. Missing timings must not block answer terminalization.

## Frontend Behavior

### Step-to-Timing Mapping

The process panel should compute a candidate timing key list for each step.

Generation-stage candidates:

| Step key or title | Timing key |
| --- | --- |
| `stage1`, `阶段一` | `stage1` |
| `stage2`, `阶段二` | `stage2` |
| `stage25`, `阶段二点五`, `阶段2.5`, `阶段2点5` | `stage25` |
| `stage3`, `阶段三` | `stage3` |
| `stage4`, `阶段四` | `stage4` |

highThinking candidates:

| Step key | Timing keys, in priority order |
| --- | --- |
| `step1` | `step1_parallel` |
| `step2` | `step2_pre_answer` |
| `step3` | `step3_retrieval` |
| `step4` | `step4_synthesis` |
| `step5` | `step5_check_revise`, then `step5_check_total`, then `step5_revise_total` |
| `step5_check` | `step5_check_total`, then `step5_check_revise` |
| `step5_revise` | `step5_revise_total`, then `step5_check_revise` |

The displayed duration should come from the first valid matching timing entry. `step5` is treated as an aggregate visible step when present. `step5_check` and `step5_revise` are separate visible steps and should prefer their specific accumulated timing before falling back to the aggregate `step5_check_revise`.

### Status Normalization

Frontend step normalization should preserve `skipped` as a stable display status. A skipped step is terminal and should not be counted as processing. Existing success/error behavior remains unchanged.

If the existing UI does not need a distinct skipped icon immediately, skipped may render with success-like terminal styling, but the normalized data must not degrade to `processing`.

### Display Timing

The UI should show duration labels when timing data exists. The feature is not required to show live ticking elapsed time for an active step. During streaming, partial timing metadata may show completed earlier stage durations. On `done`, the terminal timings replace or complete the prior model.

## Backend Behavior

### gateway

Gateway task execution should capture `timings` when processing upstream `done` events. It must pass timings through:

- immediate terminal task assistant write,
- queued terminal sync fallback when terminal write fails,
- terminal sync retry from stored queue records.

Cancellation and failure terminalization may pass empty timings unless a valid timing snapshot is already available. Completed terminalization must not drop valid `done.timings`.

### public-service

The task terminal authority API should accept an optional timings object with an empty-object default. Service terminalization should write `metadata.timings` when the dict is present and non-empty. Repeated terminal calls must keep the first terminal record authoritative, consistent with current idempotency behavior.

The non-task assistant terminal path already preserves timings and should not be regressed.

### fastQA, highThinkingQA, patent

The existing timing emission should remain compatible. No stage naming rewrite is required in these services for the first implementation. Backend changes are limited to preserving and forwarding existing `done.timings` through gateway/public-service terminal persistence.

## Error Handling

- Invalid timing payloads are ignored for display and should not break streaming or persistence.
- Missing timings are allowed and should simply hide duration labels.
- Terminal persistence failures should continue to use the existing terminal sync retry mechanism, now including timings in the retry payload.
- Late progress after terminal state must not overwrite terminal `metadata.timings`.

## Testing Requirements

### gateway

Add or extend task worker tests to prove:

- upstream `done.timings` appears in the public-service `assistant-terminal` payload;
- terminal sync fallback stores `terminal_sync_payload.timings`;
- terminal sync retry replays `timings` to public-service.

### public-service

Add task terminal service/API tests to prove:

- `AuthorityTaskAssistantTerminalRequest` accepts `timings`;
- `terminal_authority_task_assistant()` stores `metadata.timings`;
- repeated terminal calls do not overwrite the original terminal timings.

### frontend

Add tests to prove:

- `mergeRoutingMetadata()` preserves `timings` and `stage_timings_ms` as `metadata.timings`;
- highThinking step keys map to highThinking timing keys for per-step duration labels;
- `skipped` status remains terminal after API/store/Home normalization;
- existing `stageTimings` tests continue to pass for generation and highThinking timing families.

## Acceptance Criteria

- fastQA completed answers show per-stage duration labels live and after refresh/history reload.
- patent completed answers show per-stage duration labels live and after refresh/history reload.
- patent skipped stage 2.5 does not remain visually processing after normalization or reload.
- highThinking completed answers show duration labels for visible steps despite timing keys using detailed suffixes.
- Refresh-survivable task terminal records in public-service include `metadata.timings` when upstream `done.timings` was present.
- Terminal sync retry preserves timings after an initial terminal write failure.
- No backend answer generation logic changes are required.
