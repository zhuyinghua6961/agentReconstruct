import test from 'node:test'
import assert from 'node:assert/strict'

import { authApi } from './auth.js'

function createStorage(initial = {}) {
  const data = new Map(Object.entries(initial))
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null
    },
    setItem(key, value) {
      data.set(key, String(value))
    },
    removeItem(key) {
      data.delete(key)
    },
  }
}

test('authApi.updateUsername turns network failures into recoverable error payload', async () => {
  const originalFetch = global.fetch
  const originalLocalStorage = global.localStorage
  const originalWindow = global.window

  global.localStorage = createStorage({ 'agentcode.auth.token.v1': 'demo-token' })
  global.window = { location: { pathname: '/profile', href: '/profile' } }
  global.fetch = async () => {
    throw new Error('offline')
  }

  try {
    const result = await authApi.updateUsername('alice-renamed')
    assert.equal(result.success, false)
    assert.equal(result.error, 'offline')
  } finally {
    global.fetch = originalFetch
    global.localStorage = originalLocalStorage
    global.window = originalWindow
  }
})

test('authApi.updateUsername converts non-json error responses into structured failure payload', async () => {
  const originalFetch = global.fetch
  const originalLocalStorage = global.localStorage
  const originalWindow = global.window

  global.localStorage = createStorage({ token: 'demo-token' })
  global.window = { location: { pathname: '/profile', href: '/profile' } }
  global.fetch = async () => ({
    ok: false,
    status: 502,
    async json() {
      throw new Error('bad json')
    },
  })

  try {
    const result = await authApi.updateUsername('alice-renamed')
    assert.equal(result.success, false)
    assert.equal(result.error, 'HTTP 502')
  } finally {
    global.fetch = originalFetch
    global.localStorage = originalLocalStorage
    global.window = originalWindow
  }
})
