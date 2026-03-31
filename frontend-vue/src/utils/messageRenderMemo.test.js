import test from 'node:test'
import assert from 'node:assert/strict'

async function loadMessageRenderMemoUtils() {
  try {
    return await import('./messageRenderMemo.js')
  } catch {
    return {}
  }
}

test('buildMessageRenderMemoKey ignores timestamp-only changes', async () => {
  const { buildMessageRenderMemoKey } = await loadMessageRenderMemoUtils()

  assert.equal(typeof buildMessageRenderMemoKey, 'function')

  const base = {
    role: 'assistant',
    content: '答案',
    queryMode: '快速模式',
    isComplete: false,
    steps: [{ step: 'retrieve', status: 'success', title: '检索' }],
    references: [{ doi: '10.1000/demo' }],
    referenceLinks: [{ doi: '10.1000/demo', pdfUrl: '/demo.pdf' }],
    doiLocations: { '10.1000/demo': [{ page: 3, section: 'intro' }] },
    timestamp: '2026-03-31T13:00:00+08:00',
  }

  const first = buildMessageRenderMemoKey(base)
  const second = buildMessageRenderMemoKey({
    ...base,
    timestamp: '2026-03-31T13:05:00+08:00',
  })

  assert.equal(first, second)
})

test('buildMessageRenderMemoKey changes when render-relevant content changes', async () => {
  const { buildMessageRenderMemoKey } = await loadMessageRenderMemoUtils()

  assert.equal(typeof buildMessageRenderMemoKey, 'function')

  const base = {
    role: 'assistant',
    content: '答案',
    isComplete: false,
    steps: [{ step: 'retrieve', status: 'success' }],
  }

  const first = buildMessageRenderMemoKey(base)
  const second = buildMessageRenderMemoKey({
    ...base,
    content: '答案更新',
  })

  assert.notEqual(first, second)
})
