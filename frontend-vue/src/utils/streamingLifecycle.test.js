import test from 'node:test'
import assert from 'node:assert/strict'

async function loadStreamingLifecycleUtils() {
  try {
    return await import('./streamingLifecycle.js')
  } catch {
    return {}
  }
}

test('shouldIgnoreLateStreamError returns true after a done terminal event', async () => {
  const { shouldIgnoreLateStreamError } = await loadStreamingLifecycleUtils()

  assert.equal(typeof shouldIgnoreLateStreamError, 'function')
  assert.equal(
    shouldIgnoreLateStreamError({
      isComplete: true,
      content: '答案已经完整输出',
      metadata: {
        done_seen: true,
        streaming_terminal_event: 'done',
      },
    }),
    true,
  )
})

test('shouldIgnoreLateStreamError returns false before stream completion', async () => {
  const { shouldIgnoreLateStreamError } = await loadStreamingLifecycleUtils()

  assert.equal(typeof shouldIgnoreLateStreamError, 'function')
  assert.equal(
    shouldIgnoreLateStreamError({
      isComplete: false,
      content: '输出中',
      metadata: {},
    }),
    false,
  )
})

test('shouldIgnoreLateStreamError returns false for terminal errors', async () => {
  const { shouldIgnoreLateStreamError } = await loadStreamingLifecycleUtils()

  assert.equal(typeof shouldIgnoreLateStreamError, 'function')
  assert.equal(
    shouldIgnoreLateStreamError({
      isComplete: true,
      content: '处理失败',
      metadata: {
        streaming_terminal_event: 'error',
      },
    }),
    false,
  )
})

test('shouldIgnoreLateStreamError returns true when done_seen is the only terminal marker', async () => {
  const { shouldIgnoreLateStreamError } = await loadStreamingLifecycleUtils()

  assert.equal(typeof shouldIgnoreLateStreamError, 'function')
  assert.equal(
    shouldIgnoreLateStreamError({
      isComplete: true,
      content: '专利答案已经结束',
      metadata: {
        done_seen: true,
      },
    }),
    true,
  )
})

test('shouldIgnoreLateStreamError normalizes uppercase done terminal events', async () => {
  const { shouldIgnoreLateStreamError } = await loadStreamingLifecycleUtils()

  assert.equal(typeof shouldIgnoreLateStreamError, 'function')
  assert.equal(
    shouldIgnoreLateStreamError({
      isComplete: true,
      content: 'thinking done',
      metadata: {
        streaming_terminal_event: 'DONE',
      },
    }),
    true,
  )
})

test('shouldIgnoreLateStreamContent returns true after a done terminal event', async () => {
  const { shouldIgnoreLateStreamContent } = await loadStreamingLifecycleUtils()

  assert.equal(typeof shouldIgnoreLateStreamContent, 'function')
  assert.equal(
    shouldIgnoreLateStreamContent({
      isComplete: true,
      content: '最终答案',
      metadata: {
        done_seen: true,
        streaming_terminal_event: 'done',
      },
    }),
    true,
  )
})

test('shouldIgnoreLateStreamContent returns false before completion or for non-done terminals', async () => {
  const { shouldIgnoreLateStreamContent } = await loadStreamingLifecycleUtils()

  assert.equal(typeof shouldIgnoreLateStreamContent, 'function')
  assert.equal(
    shouldIgnoreLateStreamContent({
      isComplete: false,
      content: '输出中',
      metadata: {},
    }),
    false,
  )
  assert.equal(
    shouldIgnoreLateStreamContent({
      isComplete: true,
      content: '已取消',
      metadata: {
        streaming_terminal_event: 'canceled',
      },
    }),
    false,
  )
})

test('late-stream guards also recognize top-level completed markers from recovered messages', async () => {
  const { shouldIgnoreLateStreamContent, shouldIgnoreLateStreamError } = await loadStreamingLifecycleUtils()

  assert.equal(typeof shouldIgnoreLateStreamContent, 'function')
  assert.equal(typeof shouldIgnoreLateStreamError, 'function')
  assert.equal(
    shouldIgnoreLateStreamContent({
      isComplete: true,
      status: 'completed',
      content: '恢复出的最终答案',
    }),
    true,
  )
  assert.equal(
    shouldIgnoreLateStreamError({
      isComplete: true,
      doneSeen: true,
      content: '恢复出的最终答案',
    }),
    true,
  )
})
