# QA Observability Beijing Time Design

**Context**

The QA stack currently emits mixed timestamp formats and timezones across `gateway`, `gateway admission`, `public-service`, `fastQA`, and `patent`. The slow-path diagnosis for ordinary QA requests is also incomplete because task lifecycle and stage-level LLM boundaries are not logged consistently.

**Goal**

Unify runtime log timestamps to Beijing time and add fine-grained event logs that make it possible to reconstruct the request path from task submission through upstream LLM completion.

**Scope**

- `gateway`
- `gateway admission`
- `public-service`
- `fastQA`
- `patent`

Out of scope:

- Changing persisted task API timestamp fields from UTC to Beijing time
- Converting existing logs to JSON
- Changing business behavior or routing

**Design**

1. Logging time normalization

- Replace UTC/default `asctime` formatters with explicit Beijing time formatters in each service.
- Use a single ISO-like textual format with milliseconds and offset: `YYYY-MM-DDTHH:MM:SS.mmm+08:00`.
- Keep current text-log style so existing grep workflows continue to work.

2. Event-style timing logs

- Add explicit event logs at important lifecycle boundaries.
- Include stable correlation identifiers whenever available:
  - `trace_id`
  - `task_id`
  - `conversation_id`
  - `requested_mode`
  - `actual_mode`
  - `route`
  - `phase`
  - `event`
  - `elapsed_ms`
- Keep log messages grep-friendly instead of nesting JSON payloads.

3. Gateway lifecycle visibility

- Log task create lifecycle:
  - task accepted
  - quota precheck completed
  - queue record written
  - authority create-turn persisted
  - task queued
- Log direct proxy lifecycle:
  - ask/ask_stream start
  - route resolution completed
  - quota precheck completed
  - upstream stream opened
  - first metadata/step/content
  - terminal finalize

4. Gateway admission visibility

- Log worker dispatch lifecycle:
  - candidate claimed
  - request admitted
  - request running
  - upstream stream opened
  - first downstream metadata
  - first step
  - first content
  - terminal completion/requeue/failure

5. Public-service lifecycle visibility

- Extend authority task internal API logs with richer context:
  - create-turn
  - assistant-progress
  - assistant-terminal
- Include route/mode/trace and payload-size details where useful.

6. fastQA and patent stage visibility

- Add finer stage logs around stage1/stage4:
  - stage start
  - prompt prepared
  - LLM request start
  - first LLM response chunk or full response received
  - parse/postprocess completed
  - stage completed
- Preserve existing stage timing summaries.

**Testing**

- Add formatter tests proving Beijing time formatting in the five services.
- Extend log-oriented unit tests to assert key timing/event messages appear in:
  - `gateway`
  - `public-service`
  - `fastQA`
  - `patent`

**Risks**

- Log volume will increase, especially for streaming paths.
- To control noise, only first-occurrence boundaries should be logged for metadata/step/content events.

