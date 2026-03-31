import test from 'node:test'
import assert from 'node:assert/strict'

async function loadStreamPersistPolicy() {
  try {
    return await import('./streamPersistPolicy.js')
  } catch {
    return {}
  }
}

test('resolveChatPersistPolicy uses coarse debounce during streaming', async () => {
  const { resolveChatPersistPolicy, STREAM_PERSIST_DEBOUNCE_MS } = await loadStreamPersistPolicy()

  assert.equal(typeof resolveChatPersistPolicy, 'function')
  assert.equal(STREAM_PERSIST_DEBOUNCE_MS, 1200)

  assert.deepEqual(
    resolveChatPersistPolicy({ force: false, isStreaming: true }),
    { mode: 'debounced', debounceMs: 1200 }
  )
})

test('resolveChatPersistPolicy persists immediately when not streaming or when force is true', async () => {
  const { resolveChatPersistPolicy } = await loadStreamPersistPolicy()

  assert.equal(typeof resolveChatPersistPolicy, 'function')

  assert.deepEqual(
    resolveChatPersistPolicy({ force: false, isStreaming: false }),
    { mode: 'immediate', debounceMs: 0 }
  )
  assert.deepEqual(
    resolveChatPersistPolicy({ force: true, isStreaming: true }),
    { mode: 'immediate', debounceMs: 0 }
  )
})

test('shouldForcePersistForStreamingTransition only forces on terminal streaming edge', async () => {
  const { shouldForcePersistForStreamingTransition } = await loadStreamPersistPolicy()

  assert.equal(typeof shouldForcePersistForStreamingTransition, 'function')

  assert.equal(
    shouldForcePersistForStreamingTransition({ previousIsStreaming: true, nextIsStreaming: false }),
    true
  )
  assert.equal(
    shouldForcePersistForStreamingTransition({ previousIsStreaming: false, nextIsStreaming: true }),
    false
  )
  assert.equal(
    shouldForcePersistForStreamingTransition({ previousIsStreaming: false, nextIsStreaming: false }),
    false
  )
})
