# Public Service Consistency Hardening Spec

## Scope

This spec covers the current `public-service` backend hardening work for:

- admin batch import quota correctness
- upload processing failure handling
- upload processing cache amplification
- multi-instance conversation write consistency
- multi-instance quota check/finalize consistency
- outbox publish correctness under multi-instance races
- upload processing recovery across restarts and multiple instances

The target is not a full platform redesign. The target is to close the known correctness gaps while preserving the migrated API surface and existing tests.

## Confirmed Problems

### 1. Admin Import Quota Bypass On User Lookup Error

Current behavior:

- `AdminUsersImportService._precheck_excel_upload_quota()` swallows lookup exceptions and returns `(None, None)`.
- When actor lookup fails, the import path silently skips quota enforcement.

Risk:

- DB/auth transient failures can turn into quota bypass.
- Multi-instance deployment makes this more visible under partial dependency failures.

Required fix:

- actor lookup failure must return a quota/db failure payload, not a bypass.
- admin import must also release any acquired quota lease on every early-return validation path, not only the success path.

### 2. Upload Worker Continues Heavy Parsing After State Write Failure

Current behavior:

- `UploadProcessingWorker._set_state()` only logs warnings on repeated failure.
- `_run_task()` continues parsing even if the file state can no longer be persisted.

Risk:

- wasted CPU and disk I/O
- stale processing against already-deleted or no-longer-visible files

Required fix:

- make state transition failures actionable
- fail fast on unrecoverable initial transition failure

### 3. Upload Processing State Updates Over-Refresh Conversation Caches

Current behavior:

- `update_uploaded_file_processing_state()` persists JSON, refreshes primary conversation list cache, refreshes detail cache, then re-reads file detail.
- upload worker performs multiple state transitions per file.

Risk:

- unnecessary DB + JSON + Redis churn
- amplified cost under concurrent uploads

Required fix:

- stop refreshing conversation list cache for file-processing-only state changes
- keep detail visibility for file status updates

### 4. Conversation JSON Writes Are Not Cross-Instance Safe

Current behavior:

- `ConversationJsonStore.conversation_lock()` uses process lock + local file lock only.
- this protects one process or one host path, but not multiple service instances across hosts.

Risk:

- concurrent writes from different instances can overwrite JSON documents
- cache invalidation remains shared, but source-of-truth writes do not have distributed mutual exclusion

Required fix:

- add Redis-backed distributed lock for conversation write critical sections
- keep local lock/file lock as same-host supplement
- degrade gracefully when Redis is unavailable
- make Redis release/renew compare token atomically so one instance cannot delete or extend another instance's lock after expiry races
- renew long-lived locks while the critical section is still running, otherwise slow MinIO sync can outlive the TTL and reopen cross-instance write races

## 5. Quota Precheck/Finalize Has Cross-Instance Race Window

Current behavior:

- request path uses `precheck_quota()` and later `finalize_quota()`
- these are separated and not guarded by a shared lease

Risk:

- concurrent requests across instances can both pass precheck before either finalizes
- quota can overshoot limit in burst scenarios

Required fix:

- add Redis-backed per-user/per-quota lease covering `precheck -> finalize`
- release lock in all success/failure/skip branches
- wire upload and admin-import call paths to the new lease lifecycle; otherwise they leak leases or bypass serialization
- renew long-running quota leases while upload/import work is still in flight

## 6. Outbox Can Publish Stale Or Corrupted Conversation JSON

Current behavior:

- outbox tasks reuse the canonical conversation JSON local path
- when the current local file no longer matches the enqueued `content_hash`, the worker only logs the mismatch and still uploads
- the worker uploads before `mark_chat_json_sync_ok(expected_version=...)` confirms that the task is still current

Risk:

- stale or corrupted local content can overwrite the canonical MinIO object
- an older task can race with a newer write and still publish to remote before being marked stale in MySQL

Required fix:

- fail closed on content hash mismatch instead of uploading anyway
- serialize outbox publish with the same conversation distributed lock used by foreground writes
- keep the remote publish + sync-index update in one serialized critical section

## 7. Outbox Processing Timeout Can Cause Duplicate Consumers

Current behavior:

- the outbox worker marks tasks as `processing`
- reclaim logic times out long-running tasks
- there is no heartbeat while a slow upload is in flight

Risk:

- multiple instances can reclaim and process the same outbox task concurrently
- remote publish cost is amplified under slow storage or network stalls

Required fix:

- add periodic processing heartbeat updates while a task is still actively being handled
- keep reclaim timeout as a recovery mechanism, but not as the normal way to finish slow uploads

## 8. Upload Processing Is Not Restart-Safe Or Cross-Instance Safe

Current behavior:

- upload processing is queued only into an in-memory thread pool
- service startup does not rescan and resubmit files left in `uploaded/parsing/indexing`
- worker de-duplication is process-local only

Risk:

- successful uploads can remain permanently stuck after process restart
- multiple instances can parse the same file concurrently during startup recovery or duplicate submission

Required fix:

- rescan pending upload-processing files on startup and resubmit them
- add Redis-backed per-file processing lease so only one instance executes a given file task at a time

## 9. Lease Loss Is Logged But Not Enforced

Current behavior:

- Redis lease renewal failure sets an internal `lost` flag and logs a warning
- foreground code paths do not fail closed when the lease is already lost

Risk:

- the service can continue a critical section after distributed mutual exclusion is already gone
- the lock then degrades from correctness protection to best-effort telemetry

Required fix:

- expose lease health checks to callers
- fail closed before committing protected state transitions when the lease has already been lost

## Non-Goals In This Iteration

- replace upload processing thread pool with a durable distributed task queue
- make documents LLM summarization streaming
- redesign quota storage to fully transactional DB reservations

These remain future work, but are not blockers for the current hardening batch.

## Design

### A. Redis Lock Primitive

Introduce Redis lock support in `public-service` by migrating the existing lightweight lock manager pattern:

- `RedisLockHandle`
- `RedisLockManager.acquire(key, ttl_seconds)`
- `RedisLockManager.release(handle)`

Behavior:

- use `SET key token NX EX ttl`
- release only if the stored token matches
- use atomic compare-and-delete / compare-and-expire when Redis scripting is available
- if Redis is unavailable, caller may fall back to local-only behavior where explicitly allowed

Lease support:

- wrap acquired locks in a lightweight renewer thread
- extend TTL periodically at roughly `ttl / 3`
- log and mark the lease as degraded if renewal fails mid-flight

### B. Conversation Distributed Lock

`ConversationJsonStore` will accept optional `redis_service`.

Lock acquisition order:

1. acquire Redis distributed lock when Redis is available
2. acquire process-local lock
3. acquire local file lock

Release order:

1. release file lock
2. release process-local lock
3. release Redis distributed lock

Rationale:

- a single global order avoids deadlock
- Redis protects cross-instance consistency
- local locks still prevent same-host races and preserve current semantics

Config:

- `CONVERSATION_LOCK_TTL_SECONDS`
- `CONVERSATION_LOCK_WAIT_SECONDS`
- `CONVERSATION_LOCK_RETRY_INTERVAL_MS`

Fallback policy:

- if Redis is unavailable, continue with local lock behavior so the service still works in degraded mode
- if Redis is available but the lock cannot be acquired within wait timeout, fail the write path with a clear error
- if a held Redis lease is lost mid-critical-section, fail closed before committing protected updates wherever feasible

### C. Quota Lease

Extend `QuotaGrant` to carry optional lock handle metadata.

`precheck_quota()`:

- acquire per-user/per-quota Redis lock
- perform user exemption lookup and quota check inside that lease
- if any exception occurs after acquisition, release lock before raising

`finalize_quota()`:

- always release the lease
- if result should not count, release only
- if result should count, increment quota then release

Call-path requirement:

- admin import and upload endpoints cannot return early after precheck without explicitly finalizing or releasing the lease

Config:

- `QUOTA_LOCK_TTL_SECONDS`
- `QUOTA_LOCK_WAIT_SECONDS`
- `QUOTA_LOCK_RETRY_INTERVAL_MS`

Fallback policy:

- if Redis is unavailable, keep current behavior to avoid hard breakage in non-distributed environments
- when Redis is available, use the lease

### D. Upload Processing State Efficiency

`ConversationService.update_uploaded_file_processing_state()` will:

- persist document and detail cache refresh
- skip primary conversation list cache refresh because file parse/index state does not change list payload

`UploadProcessingWorker` will:

- treat failed initial state transition as terminal for that task
- stop before parse/index work if state persistence is unavailable
- acquire a Redis-backed per-file processing lease when Redis is available
- allow startup recovery to resubmit pending files without cross-instance duplicate execution

### E. Outbox Publish Hardening

`ChatJsonOutboxWorker` will:

- acquire the same conversation distributed lock before rechecking version/hash and publishing remote JSON
- reject content-hash mismatch instead of uploading anyway
- heartbeat the `processing_started_at` timestamp while a slow upload is still active

### F. Upload Processing Recovery

`ConversationService` and runtime startup will:

- scan uploaded files that still appear pending in conversation JSON
- resubmit those files into the processing worker on startup
- rely on the per-file Redis lease to avoid duplicate cross-instance processing

### G. Admin Import Quota Strictness

`AdminUsersImportService._precheck_excel_upload_quota()` will:

- return DB failure on actor lookup error
- never treat auth/db failure as quota exemption

## Test Plan

### Unit

- admin import returns `DB_UNAVAILABLE` on actor lookup failure
- conversation lock uses Redis lock when available and releases it
- quota precheck/finalize acquires and releases lease
- upload worker stops before parse when initial state update fails
- processing state update no longer refreshes primary list cache

### Integration

- real MySQL + Redis + MinIO upload flow still succeeds
- unbound upload still creates no local/MinIO orphan
- conversation outbox still recovers with real MySQL + Redis + MinIO

### Regression

- full `public-service` backend pytest suite under conda env `agent`

## Acceptance Criteria

- no known silent quota bypass path remains
- no known unbound upload orphan path remains
- conversation write critical sections are guarded across instances when Redis is available
- quota precheck/finalize is serialized per `(user_id, quota_type)` when Redis is available
- upload worker avoids heavy parsing when state persistence already failed
- all tests pass, including live MySQL/Redis/MinIO integration coverage
