import test from 'node:test'
import assert from 'node:assert/strict'

async function loadMessageWindowingUtils() {
  try {
    return await import('./messageWindowing.js')
  } catch {
    return {}
  }
}

function buildMessages(count) {
  return Array.from({ length: count }, (_, index) => ({
    role: index % 2 === 0 ? 'user' : 'assistant',
    content: `message-${index}`,
  }))
}

test('buildVisibleMessageWindow returns visible entries with stable absolute indexes', async () => {
  const { buildVisibleMessageWindow } = await loadMessageWindowingUtils()

  assert.equal(typeof buildVisibleMessageWindow, 'function')

  const result = buildVisibleMessageWindow({
    messages: buildMessages(8),
    visibleCount: 4,
  })

  assert.equal(result.hiddenCount, 4)
  assert.deepEqual(
    result.visibleMessages.map((entry) => entry.absoluteMessageIndex),
    [4, 5, 6, 7]
  )
  assert.equal(result.visibleMessages[0].message.content, 'message-4')
})

test('buildVisibleMessageWindow preserves stable identity when older history is expanded', async () => {
  const { buildVisibleMessageWindow } = await loadMessageWindowingUtils()

  assert.equal(typeof buildVisibleMessageWindow, 'function')

  const messages = buildMessages(10)
  const first = buildVisibleMessageWindow({
    messages,
    visibleCount: 4,
    revealedCount: 0,
  })
  const second = buildVisibleMessageWindow({
    messages,
    visibleCount: 4,
    revealedCount: 3,
  })

  assert.deepEqual(
    first.visibleMessages.map((entry) => entry.absoluteMessageIndex),
    [6, 7, 8, 9]
  )
  assert.deepEqual(
    second.visibleMessages.map((entry) => entry.absoluteMessageIndex),
    [3, 4, 5, 6, 7, 8, 9]
  )
})

test('resolveHiddenHistoryReveal targets the correct batch to expose a hidden message', async () => {
  const { resolveHiddenHistoryReveal } = await loadMessageWindowingUtils()

  assert.equal(typeof resolveHiddenHistoryReveal, 'function')

  const result = resolveHiddenHistoryReveal({
    totalMessages: 20,
    visibleCount: 6,
    revealedCount: 0,
    batchSize: 5,
    targetAbsoluteIndex: 8,
  })

  assert.deepEqual(result, {
    needsReveal: true,
    nextRevealedCount: 6,
  })
})

test('resolveHiddenHistoryReveal returns no-op when target is already visible', async () => {
  const { resolveHiddenHistoryReveal } = await loadMessageWindowingUtils()

  assert.equal(typeof resolveHiddenHistoryReveal, 'function')

  const result = resolveHiddenHistoryReveal({
    totalMessages: 20,
    visibleCount: 6,
    revealedCount: 4,
    batchSize: 5,
    targetAbsoluteIndex: 17,
  })

  assert.deepEqual(result, {
    needsReveal: false,
    nextRevealedCount: 4,
  })
})
