import test from 'node:test'
import assert from 'node:assert/strict'

import {
  beginTaskAttach,
  consumePendingStreamContent,
  deriveRecoveredReplayCursor,
  endTaskAttach,
  shouldClearRecoveredActiveTask,
} from './taskRecoveryRuntime.js'

test('beginTaskAttach suppresses duplicate attaches for the same chat/task until the first one finishes', () => {
  const lockMap = new Map()

  assert.equal(
    beginTaskAttach(lockMap, {
      chatId: '42',
      taskId: 'task_live_42',
      replaceMessagesFromServer: true,
    }),
    true,
  )

  assert.equal(
    beginTaskAttach(lockMap, {
      chatId: '42',
      taskId: 'task_live_42',
      replaceMessagesFromServer: false,
    }),
    false,
  )

  endTaskAttach(lockMap, {
    chatId: '42',
    taskId: 'task_live_42',
  })

  assert.equal(
    beginTaskAttach(lockMap, {
      chatId: '42',
      taskId: 'task_live_42',
      replaceMessagesFromServer: false,
    }),
    true,
  )
})

test('endTaskAttach releases the original lock so a newer live task can attach after stale task refresh', () => {
  const lockMap = new Map()

  assert.equal(
    beginTaskAttach(lockMap, {
      chatId: '42',
      taskId: 'task_stale_a',
      replaceMessagesFromServer: true,
    }),
    true,
  )

  endTaskAttach(lockMap, {
    chatId: '42',
    taskId: 'task_stale_a',
  })

  assert.equal(
    beginTaskAttach(lockMap, {
      chatId: '42',
      taskId: 'task_live_b',
      replaceMessagesFromServer: false,
    }),
    true,
  )
})

test('consumePendingStreamContent keeps buffered content when the assistant placeholder is not ready yet', () => {
  assert.deepEqual(
    consumePendingStreamContent({
      existingContent: '',
      pendingContent: 'partial',
      targetFound: false,
    }),
    {
      nextContent: '',
      remainingPending: 'partial',
    },
  )

  assert.deepEqual(
    consumePendingStreamContent({
      existingContent: 'hello ',
      pendingContent: 'world',
      targetFound: true,
    }),
    {
      nextContent: 'hello world',
      remainingPending: '',
    },
  )
})

test('shouldClearRecoveredActiveTask keeps live active_task after fallback refresh and clears only terminal truth', () => {
  assert.equal(
    shouldClearRecoveredActiveTask({
      active_task: {
        task_id: 'task_running_1',
        status: 'running',
        last_seq: 6,
      },
    }, 4),
    false,
  )

  assert.equal(
    shouldClearRecoveredActiveTask({
      active_task: null,
    }, 6),
    true,
  )

  assert.equal(
    shouldClearRecoveredActiveTask({
      active_task: {
        task_id: 'task_done_1',
        status: 'completed',
        last_seq: 7,
      },
    }, 6),
    true,
  )
})

test('shouldClearRecoveredActiveTask clears stale live active_task when matching assistant message is already terminal', () => {
  assert.equal(
    shouldClearRecoveredActiveTask({
      active_task: {
        task_id: 'task_running_2',
        status: 'running',
        last_seq: 8,
      },
      messages: [
        {
          role: 'assistant',
          content: 'final answer',
          status: 'completed',
          metadata: {
            task_id: 'task_running_2',
            terminal_status: 'completed',
            done_seen: true,
            last_seq: 8,
          },
        },
      ],
    }, 8),
    true,
  )
})

test('deriveRecoveredReplayCursor prefers the persisted assistant last_seq when it is ahead of active_task', () => {
  assert.deepEqual(
    deriveRecoveredReplayCursor(
      {
        active_task: {
          task_id: 'task_running_3',
          status: 'running',
          last_seq: 1,
        },
        messages: [
          {
            role: 'assistant',
            content: 'partial answer',
            metadata: {
              task_id: 'task_running_3',
              last_seq: 4,
            },
          },
        ],
      },
      0,
    ),
    {
      taskId: 'task_running_3',
      lastSeq: 4,
    },
  )
})
