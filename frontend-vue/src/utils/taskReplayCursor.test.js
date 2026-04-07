import test from 'node:test'
import assert from 'node:assert/strict'

import {
  advanceTaskReplayCursor,
  isRecoverableTaskStatus,
  normalizeTaskReplayCursor,
  shouldFallBackToConversationTruth,
} from './taskReplayCursor.js'

test('normalizeTaskReplayCursor keeps backend last_seq for recoverable queued tasks', () => {
  const cursor = normalizeTaskReplayCursor(
    {
      task_id: 'task_queue_1',
      status: 'queued',
      last_seq: 4,
      replay_available: true,
    },
    2,
  )

  assert.deepEqual(cursor, {
    taskId: 'task_queue_1',
    status: 'queued',
    lastSeq: 4,
    recoverable: true,
    replayAvailable: true,
    terminal: false,
  })
})

test('normalizeTaskReplayCursor falls back to cached last seq when summary omits it', () => {
  const cursor = normalizeTaskReplayCursor(
    {
      task_id: 'task_running_1',
      status: 'running',
      replay_available: true,
    },
    7,
  )

  assert.equal(cursor.lastSeq, 7)
  assert.equal(cursor.status, 'running')
  assert.equal(cursor.recoverable, true)
})

test('advanceTaskReplayCursor moves forward to the highest consumed event sequence', () => {
  const initial = normalizeTaskReplayCursor(
    {
      task_id: 'task_replay_1',
      status: 'admitted',
      last_seq: 3,
      replay_available: true,
    },
    0,
  )

  const next = advanceTaskReplayCursor(initial, [
    { seq: 4, type: 'state', status: 'running' },
    { seq: 6, type: 'content', delta: 'partial' },
    { seq: 5, type: 'step', step: 'outline' },
  ])

  assert.equal(next.lastSeq, 6)
  assert.equal(next.status, 'running')
  assert.equal(next.recoverable, true)
})

test('terminal statuses are not recoverable and should fall back to conversation truth', () => {
  assert.equal(isRecoverableTaskStatus('queued'), true)
  assert.equal(isRecoverableTaskStatus('admitted'), true)
  assert.equal(isRecoverableTaskStatus('running'), true)
  assert.equal(isRecoverableTaskStatus('completed'), false)
  assert.equal(isRecoverableTaskStatus('failed'), false)
  assert.equal(isRecoverableTaskStatus('canceled'), false)

  const cursor = normalizeTaskReplayCursor(
    {
      task_id: 'task_done_1',
      status: 'completed',
      last_seq: 9,
      replay_available: false,
    },
    2,
  )

  assert.equal(shouldFallBackToConversationTruth(cursor), true)
})
