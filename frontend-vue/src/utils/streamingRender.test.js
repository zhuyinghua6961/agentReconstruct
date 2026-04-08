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
