import test from 'node:test'
import assert from 'node:assert/strict'

async function loadStreamingTargetUtils() {
  try {
    return await import('./streamingTarget.js')
  } catch {
    return {}
  }
}

test('resolveStreamingTarget uses cached target index when it still matches request id', async () => {
  const { resolveStreamingTarget } = await loadStreamingTargetUtils()

  assert.equal(typeof resolveStreamingTarget, 'function')

  const messages = [
    { role: 'user', content: 'q1' },
    { role: 'assistant', content: '', streamRequestId: 'stream_a' },
    { role: 'user', content: 'q2' },
  ]

  const target = resolveStreamingTarget({
    messages,
    requestId: 'stream_a',
    cachedTargetIndex: 1,
  })

  assert.equal(target.index, 1)
  assert.equal(target.message, messages[1])
  assert.equal(target.resolvedBy, 'cached_index')
})

test('resolveStreamingTarget falls back to request id scan when cached index becomes stale', async () => {
  const { resolveStreamingTarget } = await loadStreamingTargetUtils()

  assert.equal(typeof resolveStreamingTarget, 'function')

  const messages = [
    { role: 'assistant', content: '', streamRequestId: 'other' },
    { role: 'user', content: 'q1' },
    { role: 'assistant', content: '', streamRequestId: 'stream_a' },
  ]

  const target = resolveStreamingTarget({
    messages,
    requestId: 'stream_a',
    cachedTargetIndex: -1,
  })

  assert.equal(target.index, 2)
  assert.equal(target.message, messages[2])
  assert.equal(target.resolvedBy, 'request_id_scan')
})

test('resolveStreamingTarget falls back to request id scan when cached index becomes stale', async () => {
  const { resolveStreamingTarget } = await loadStreamingTargetUtils()

  assert.equal(typeof resolveStreamingTarget, 'function')

  const messages = [
    { role: 'assistant', content: '', streamRequestId: 'other' },
    { role: 'user', content: 'q1' },
    { role: 'assistant', content: '', streamRequestId: 'stream_a' },
  ]

  const target = resolveStreamingTarget({
    messages,
    requestId: 'stream_a',
    cachedTargetIndex: 1,
  })

  assert.equal(target.index, 2)
  assert.equal(target.message, messages[2])
  assert.equal(target.resolvedBy, 'request_id_scan')
})

test('resolveStreamingTarget uses request id scan when no cached target index is available', async () => {
  const { resolveStreamingTarget } = await loadStreamingTargetUtils()

  assert.equal(typeof resolveStreamingTarget, 'function')

  const messages = [
    { role: 'assistant', content: '', streamRequestId: 'other' },
    { role: 'user', content: 'q1' },
    { role: 'assistant', content: '', streamRequestId: 'stream_a' },
  ]

  const target = resolveStreamingTarget({
    messages,
    requestId: 'stream_a',
    cachedTargetIndex: -1,
  })

  assert.equal(target.index, 2)
  assert.equal(target.message, messages[2])
  assert.equal(target.resolvedBy, 'request_id_scan')
})

test('resolveStreamingTarget returns last assistant when request id is missing', async () => {
  const { resolveStreamingTarget } = await loadStreamingTargetUtils()

  assert.equal(typeof resolveStreamingTarget, 'function')

  const messages = [
    { role: 'user', content: 'q1' },
    { role: 'assistant', content: 'a1' },
    { role: 'user', content: 'q2' },
    { role: 'bot', content: 'a2' },
  ]

  const target = resolveStreamingTarget({
    messages,
    requestId: '',
    cachedTargetIndex: -1,
  })

  assert.equal(target.index, 3)
  assert.equal(target.message, messages[3])
  assert.equal(target.resolvedBy, 'last_assistant_scan')
})

test('resolveStreamingTarget returns null when no assistant-like message exists', async () => {
  const { resolveStreamingTarget } = await loadStreamingTargetUtils()

  assert.equal(typeof resolveStreamingTarget, 'function')

  const target = resolveStreamingTarget({
    messages: [{ role: 'user', content: 'only user' }],
    requestId: 'stream_a',
    cachedTargetIndex: 0,
  })

  assert.equal(target, null)
})

test('resolveStreamingTarget does not fall back to a previous assistant when strict request matching is enabled', async () => {
  const { resolveStreamingTarget } = await loadStreamingTargetUtils()

  assert.equal(typeof resolveStreamingTarget, 'function')

  const messages = [
    { role: 'user', content: 'q1' },
    { role: 'assistant', content: 'previous answer' },
    { role: 'user', content: 'q2' },
  ]

  const target = resolveStreamingTarget({
    messages,
    requestId: 'stream_pending',
    cachedTargetIndex: -1,
    strictRequestMatch: true,
  })

  assert.equal(target, null)
})
