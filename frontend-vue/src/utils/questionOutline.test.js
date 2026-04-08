import test from 'node:test'
import assert from 'node:assert/strict'

async function loadQuestionOutlineUtils() {
  try {
    return await import('./questionOutline.js')
  } catch {
    return {}
  }
}

test('buildQuestionOutlineSignature ignores assistant content growth', async () => {
  const { buildQuestionOutlineSignature } = await loadQuestionOutlineUtils()

  assert.equal(typeof buildQuestionOutlineSignature, 'function')

  const baseMessages = [
    { role: 'user', content: '第一个问题' },
    { role: 'assistant', content: '第一段答案' },
  ]

  const first = buildQuestionOutlineSignature(baseMessages)
  const second = buildQuestionOutlineSignature([
    baseMessages[0],
    { ...baseMessages[1], content: '第一段答案继续增长' },
  ])

  assert.equal(first, second)
})

test('buildQuestionOutlineSignature changes when a new user turn is appended', async () => {
  const { buildQuestionOutlineSignature } = await loadQuestionOutlineUtils()

  assert.equal(typeof buildQuestionOutlineSignature, 'function')

  const first = buildQuestionOutlineSignature([
    { role: 'user', content: '第一个问题' },
    { role: 'assistant', content: '回答' },
  ])
  const second = buildQuestionOutlineSignature([
    { role: 'user', content: '第一个问题' },
    { role: 'assistant', content: '回答' },
    { role: 'user', content: '第二个问题' },
  ])

  assert.notEqual(first, second)
})

test('buildQuestionOutlineItems only contains user messages with stable absolute indexes', async () => {
  const { buildQuestionOutlineItems } = await loadQuestionOutlineUtils()

  assert.equal(typeof buildQuestionOutlineItems, 'function')

  const items = buildQuestionOutlineItems([
    { role: 'system', content: 'system' },
    { role: 'user', content: '第一个问题' },
    { role: 'assistant', content: '回答一' },
    { role: 'user', content: '第二个问题会更长一些，需要被截断展示以避免过长过宽影响布局' },
  ])

  assert.deepEqual(items.map((item) => item.outlineIndex), [1, 2])
  assert.deepEqual(items.map((item) => item.messageIndex), [1, 3])
  assert.equal(items[0].anchorId, 'question-1')
  assert.match(items[1].preview, /^第二个问题会更长一些/)
  assert.ok(items[1].preview.length <= 51)
})

test('getLastQuestionOutlineItem returns the newest user turn and null for empty outlines', async () => {
  const { buildQuestionOutlineItems, getLastQuestionOutlineItem } = await loadQuestionOutlineUtils()

  assert.equal(typeof buildQuestionOutlineItems, 'function')
  assert.equal(typeof getLastQuestionOutlineItem, 'function')

  const items = buildQuestionOutlineItems([
    { role: 'user', content: '第一个问题' },
    { role: 'assistant', content: '回答一' },
    { role: 'user', content: '第二个问题' },
    { role: 'assistant', content: '回答二' },
    { role: 'user', content: '第三个问题' },
  ])

  assert.equal(getLastQuestionOutlineItem([]), null)
  assert.deepEqual(getLastQuestionOutlineItem(items), items[2])
  assert.equal(getLastQuestionOutlineItem(items)?.messageIndex, 4)
})
