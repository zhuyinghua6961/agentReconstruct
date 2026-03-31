import test from 'node:test'
import assert from 'node:assert/strict'

async function loadScrollFollowUtils() {
  try {
    return await import('./scrollFollow.js')
  } catch {
    return {}
  }
}

test('isNearBottom returns true when viewport is within threshold', async () => {
  const { isNearBottom } = await loadScrollFollowUtils()

  assert.equal(typeof isNearBottom, 'function')

  assert.equal(isNearBottom({
    scrollTop: 880,
    clientHeight: 400,
    scrollHeight: 1360,
    thresholdPx: 120,
  }), true)
})

test('isNearBottom returns false when viewport is far from bottom', async () => {
  const { isNearBottom } = await loadScrollFollowUtils()

  assert.equal(typeof isNearBottom, 'function')

  assert.equal(isNearBottom({
    scrollTop: 600,
    clientHeight: 400,
    scrollHeight: 1360,
    thresholdPx: 120,
  }), false)
})

test('shouldAutoScroll respects force mode and near-bottom state', async () => {
  const { shouldAutoScroll } = await loadScrollFollowUtils()

  assert.equal(typeof shouldAutoScroll, 'function')

  assert.equal(shouldAutoScroll({ force: true, nearBottom: false }), true)
  assert.equal(shouldAutoScroll({ force: false, nearBottom: true }), true)
  assert.equal(shouldAutoScroll({ force: false, nearBottom: false }), false)
})
