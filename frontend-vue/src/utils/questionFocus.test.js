import test from 'node:test'
import assert from 'node:assert/strict'

async function loadQuestionFocusUtils() {
  try {
    return await import('./questionFocus.js')
  } catch {
    return {}
  }
}

test('focusQuestionItem uses the requested scroll behavior and can skip transient highlight', async () => {
  const { focusQuestionItem } = await loadQuestionFocusUtils()

  assert.equal(typeof focusQuestionItem, 'function')

  const calls = []
  const target = {
    scrollIntoView(options) {
      calls.push({ type: 'scroll', options })
    },
  }
  const userMessageElements = new Map([[9, target]])
  let activeQuestionMessageIndex = null
  let highlightedQuestionMessageIndex = null
  let nextTickCalls = 0

  const result = await focusQuestionItem({
    item: { messageIndex: 9 },
    userMessageElements,
    revealHiddenHistory: () => false,
    nextTick: async () => {
      nextTickCalls += 1
    },
    setActiveQuestionMessageIndex: (value) => {
      activeQuestionMessageIndex = value
    },
    setHighlightedQuestionMessageIndex: (value) => {
      highlightedQuestionMessageIndex = value
    },
    scheduleHighlightReset: () => {
      calls.push({ type: 'highlight-reset' })
    },
    behavior: 'auto',
    highlight: false,
  })

  assert.equal(result, true)
  assert.equal(activeQuestionMessageIndex, 9)
  assert.equal(highlightedQuestionMessageIndex, null)
  assert.equal(nextTickCalls, 1)
  assert.deepEqual(calls, [
    { type: 'scroll', options: { behavior: 'auto', block: 'start' } },
  ])
})

test('focusQuestionItem reveals hidden history before scrolling and keeps manual highlight behavior', async () => {
  const { focusQuestionItem } = await loadQuestionFocusUtils()

  assert.equal(typeof focusQuestionItem, 'function')

  const steps = []
  const target = {
    scrollIntoView(options) {
      steps.push(['scroll', options])
    },
  }
  const userMessageElements = new Map([[5, target]])
  let activeQuestionMessageIndex = null
  let highlightedQuestionMessageIndex = null

  const result = await focusQuestionItem({
    item: { messageIndex: 5 },
    userMessageElements,
    revealHiddenHistory: () => {
      steps.push(['reveal'])
      return true
    },
    nextTick: async () => {
      steps.push(['nextTick'])
    },
    setActiveQuestionMessageIndex: (value) => {
      activeQuestionMessageIndex = value
    },
    setHighlightedQuestionMessageIndex: (value) => {
      highlightedQuestionMessageIndex = value
      steps.push(['highlight', value])
    },
    scheduleHighlightReset: () => {
      steps.push(['highlight-reset'])
    },
    behavior: 'smooth',
    highlight: true,
  })

  assert.equal(result, true)
  assert.equal(activeQuestionMessageIndex, 5)
  assert.equal(highlightedQuestionMessageIndex, 5)
  assert.deepEqual(steps, [
    ['reveal'],
    ['nextTick'],
    ['nextTick'],
    ['scroll', { behavior: 'smooth', block: 'start' }],
    ['highlight', 5],
    ['highlight-reset'],
  ])
})
