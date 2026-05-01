import test from 'node:test'
import assert from 'node:assert/strict'

async function loadMessageRenderMemoUtils() {
  try {
    return await import('./messageRenderMemo.js')
  } catch {
    return {}
  }
}

function createTrackedArray(items, counters, counterKey) {
  return new Proxy([...items], {
    get(target, prop, receiver) {
      if (prop === 'map') {
        counters[counterKey] += 1
      }
      return Reflect.get(target, prop, receiver)
    },
  })
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

test('buildMessageRenderMemoKey reuses completed historical message signature without rewalking deep arrays', async () => {
  const { buildMessageRenderMemoKey } = await loadMessageRenderMemoUtils()

  assert.equal(typeof buildMessageRenderMemoKey, 'function')

  const counters = {
    stepsMapCalls: 0,
    referencesMapCalls: 0,
    referenceLinksMapCalls: 0,
  }

  const message = {
    role: 'assistant',
    content: '稳定历史答案',
    isComplete: true,
    stepsCollapsed: false,
    steps: createTrackedArray([{ step: 'retrieve', status: 'success', title: '检索' }], counters, 'stepsMapCalls'),
    references: createTrackedArray([{ doi: '10.1000/demo', title: 'demo' }], counters, 'referencesMapCalls'),
    referenceLinks: createTrackedArray([{ doi: '10.1000/demo', pdfUrl: '/demo.pdf' }], counters, 'referenceLinksMapCalls'),
  }

  const first = buildMessageRenderMemoKey(message)
  const second = buildMessageRenderMemoKey(message)

  assert.equal(first, second)
  assert.equal(counters.stepsMapCalls, 1)
  assert.equal(counters.referencesMapCalls, 1)
  assert.equal(counters.referenceLinksMapCalls, 1)
})

test('buildMessageRenderMemoKey does not force historical deep recompute after unrelated streaming updates', async () => {
  const { buildMessageRenderMemoKey } = await loadMessageRenderMemoUtils()

  assert.equal(typeof buildMessageRenderMemoKey, 'function')

  const counters = {
    stepsMapCalls: 0,
  }

  const historical = {
    role: 'assistant',
    content: '历史答案',
    isComplete: true,
    steps: createTrackedArray([{ step: 'retrieve', status: 'success' }], counters, 'stepsMapCalls'),
  }
  const streaming = {
    role: 'assistant',
    content: '流式答案',
    isComplete: false,
    steps: [{ step: 'draft', status: 'processing' }],
  }

  const historicalFirst = buildMessageRenderMemoKey(historical)
  const streamingFirst = buildMessageRenderMemoKey(streaming)
  const streamingSecond = buildMessageRenderMemoKey({
    ...streaming,
    content: '流式答案更新',
  })
  const historicalSecond = buildMessageRenderMemoKey(historical)

  assert.notEqual(streamingFirst, streamingSecond)
  assert.equal(historicalFirst, historicalSecond)
  assert.equal(counters.stepsMapCalls, 1)
})

test('buildMessageRenderMemoKey reuses cached historical signature without deep re-walking nested fields', async () => {
  const { buildMessageRenderMemoKey } = await loadMessageRenderMemoUtils()

  assert.equal(typeof buildMessageRenderMemoKey, 'function')

  let titleReads = 0
  const step = {}
  Object.defineProperty(step, 'title', {
    enumerable: true,
    get() {
      titleReads += 1
      return '检索'
    },
  })

  const message = {
    role: 'assistant',
    content: '历史答案',
    queryMode: '快速模式',
    isComplete: true,
    stepsCollapsed: false,
    steps: [step],
    references: [{ doi: '10.1000/demo' }],
    referenceLinks: [{ doi: '10.1000/demo', pdfUrl: '/demo.pdf' }],
    doiLocations: { '10.1000/demo': [{ page: 3, section: 'intro' }] },
  }

  const first = buildMessageRenderMemoKey(message)
  const second = buildMessageRenderMemoKey(message)

  assert.equal(first, second)
  assert.equal(titleReads, 1)
})

test('buildMessageRenderMemoKey changes for the active streaming message when same-object content mutates', async () => {
  const { buildMessageRenderMemoKey } = await loadMessageRenderMemoUtils()

  assert.equal(typeof buildMessageRenderMemoKey, 'function')

  const message = {
    role: 'assistant',
    content: '答案',
    isComplete: false,
    steps: [{ step: 'retrieve', status: 'processing' }],
  }

  const first = buildMessageRenderMemoKey(message)
  message.content = '答案更新'
  const second = buildMessageRenderMemoKey(message)

  assert.notEqual(first, second)
})

test('buildMessageRenderMemoKey changes when terminal render flags change without changing content', async () => {
  const { buildMessageRenderMemoKey } = await loadMessageRenderMemoUtils()

  assert.equal(typeof buildMessageRenderMemoKey, 'function')

  const base = {
    role: 'assistant',
    content: '## 标题\n正文',
    isComplete: true,
    metadata: {
      done_seen: false,
      streaming_terminal_event: '',
      terminal_status: '',
    },
  }

  const first = buildMessageRenderMemoKey(base)
  const second = buildMessageRenderMemoKey({
    ...base,
    metadata: {
      ...base.metadata,
      done_seen: true,
      streaming_terminal_event: 'done',
      terminal_status: 'completed',
    },
  })

  assert.notEqual(first, second)
})

test('buildMessageRenderMemoKey changes when timing metadata changes without changing content', async () => {
  const { buildMessageRenderMemoKey } = await loadMessageRenderMemoUtils()

  assert.equal(typeof buildMessageRenderMemoKey, 'function')

  const message = {
    role: 'assistant',
    content: '答案',
    isComplete: true,
    metadata: {},
  }

  const first = buildMessageRenderMemoKey(message)
  message.metadata.timings = { stage1: 10, stage2: 20 }
  const second = buildMessageRenderMemoKey(message)

  assert.notEqual(first, second)
})
