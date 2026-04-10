import test from 'node:test'
import assert from 'node:assert/strict'

async function loadStreamingRenderUtils() {
  try {
    return await import('./streamingRender.js')
  } catch {
    return {}
  }
}

test('createStreamingHtmlRenderer throttles recomputation for rapidly changing content', async () => {
  const { createStreamingHtmlRenderer } = await loadStreamingRenderUtils()

  assert.equal(typeof createStreamingHtmlRenderer, 'function')

  let now = 1000
  let callCount = 0
  const render = createStreamingHtmlRenderer({
    minIntervalMs: 120,
    now: () => now,
    formatter: (text) => {
      callCount += 1
      return `<p>${text}</p>`
    },
  })

  const message = { content: 'A' }
  assert.equal(render(message), '<p>A</p>')
  assert.equal(callCount, 1)

  message.content = 'AB'
  now += 30
  assert.equal(render(message), '<p>A</p>')
  assert.equal(callCount, 1)

  now += 150
  assert.equal(render(message), '<p>AB</p>')
  assert.equal(callCount, 2)
})

test('createStreamingHtmlRenderer reuses cached html when content does not change', async () => {
  const { createStreamingHtmlRenderer } = await loadStreamingRenderUtils()

  assert.equal(typeof createStreamingHtmlRenderer, 'function')

  let callCount = 0
  const render = createStreamingHtmlRenderer({
    formatter: (text) => {
      callCount += 1
      return `<p>${text}</p>`
    },
  })

  const message = { content: 'stable' }
  assert.equal(render(message), '<p>stable</p>')
  assert.equal(render(message), '<p>stable</p>')
  assert.equal(callCount, 1)
})

test('createStreamingHtmlRenderer updates more frequently under the default adaptive budget when renders stay cheap', async () => {
  const { createStreamingHtmlRenderer } = await loadStreamingRenderUtils()

  assert.equal(typeof createStreamingHtmlRenderer, 'function')

  let now = 1000
  let callCount = 0
  const render = createStreamingHtmlRenderer({
    now: () => now,
    measureNow: () => now,
    formatter: (text) => {
      callCount += 1
      now += 4
      return `<p>${text}</p>`
    },
  })

  const message = { content: 'A' }
  assert.equal(render(message), '<p>A</p>')
  assert.equal(callCount, 1)

  message.content = 'AB'
  now += 10
  assert.equal(render(message), '<p>A</p>')
  assert.equal(callCount, 1)

  now += 24
  assert.equal(render(message), '<p>AB</p>')
  assert.equal(callCount, 2)
})

test('createStreamingHtmlRenderer rerenders with the terminal formatter once a message completes even if content is unchanged', async () => {
  const { createStreamingHtmlRenderer } = await loadStreamingRenderUtils()

  assert.equal(typeof createStreamingHtmlRenderer, 'function')

  let streamingCalls = 0
  let terminalCalls = 0
  const render = createStreamingHtmlRenderer({
    formatter: (text) => {
      streamingCalls += 1
      return `<stream>${text}</stream>`
    },
    terminalFormatter: (text) => {
      terminalCalls += 1
      return `<final>${text}</final>`
    },
  })

  const message = { content: '## 标题', isComplete: false, metadata: {} }

  assert.equal(render(message), '<stream>## 标题</stream>')
  assert.equal(streamingCalls, 1)
  assert.equal(terminalCalls, 0)

  message.isComplete = true
  message.metadata.done_seen = true

  assert.equal(render(message), '<final>## 标题</final>')
  assert.equal(streamingCalls, 1)
  assert.equal(terminalCalls, 1)
})
