import test from 'node:test'
import assert from 'node:assert/strict'

function installLocalStorageRecorder(initial = {}) {
  const data = new Map(Object.entries(initial).map(([key, value]) => [String(key), String(value)]))
  const setItemCalls = []

  global.localStorage = {
    getItem(key) {
      return data.has(String(key)) ? data.get(String(key)) : null
    },
    setItem(key, value) {
      const normalizedKey = String(key)
      const normalizedValue = String(value)
      setItemCalls.push({ key: normalizedKey, value: normalizedValue })
      data.set(normalizedKey, normalizedValue)
    },
    removeItem(key) {
      data.delete(String(key))
    },
    clear() {
      data.clear()
      setItemCalls.length = 0
    },
  }

  return {
    data,
    setItemCalls,
    countWrites(key) {
      return setItemCalls.filter((item) => item.key === String(key)).length
    },
    resetCalls() {
      setItemCalls.length = 0
    },
  }
}

function installTimerRecorder() {
  const originalSetTimeout = global.setTimeout
  const originalClearTimeout = global.clearTimeout
  let nextTimerId = 1
  const pending = new Map()
  const cleared = []

  global.setTimeout = (callback, delay, ...args) => {
    const timerId = nextTimerId
    nextTimerId += 1
    pending.set(timerId, {
      callback: () => callback(...args),
      delay,
    })
    return timerId
  }

  global.clearTimeout = (timerId) => {
    cleared.push(timerId)
    pending.delete(timerId)
  }

  return {
    pending,
    cleared,
    restore() {
      global.setTimeout = originalSetTimeout
      global.clearTimeout = originalClearTimeout
    },
    runAllPending() {
      const timers = Array.from(pending.entries())
      pending.clear()
      timers.forEach(([, timer]) => {
        timer.callback()
      })
    },
  }
}

test('chat store force persist clears pending streaming debounce and persists only once', async () => {
  const storage = installLocalStorageRecorder()
  const timers = installTimerRecorder()

  const originalFetch = global.fetch
  global.fetch = async () => {
    throw new Error('fetch should not be called in persistence timing test')
  }

  try {
    const { createPinia, setActivePinia } = await import('pinia')
    const { useChatStore } = await import('./chatStore.js')

    setActivePinia(createPinia())
    const store = useChatStore()

    store.createChat()
    storage.resetCalls()

    store.setStreaming(true)
    await store.addBotMessage({ content: 'streaming answer fragment' })

    assert.equal(storage.countWrites('lfp_chats'), 0)
    assert.equal(timers.pending.size, 1)

    const pendingTimerId = Array.from(timers.pending.keys())[0]

    store.setStreaming(false)

    assert.equal(storage.countWrites('lfp_chats'), 1)
    assert.ok(timers.cleared.includes(pendingTimerId))
    assert.equal(timers.pending.size, 0)

    timers.runAllPending()

    assert.equal(storage.countWrites('lfp_chats'), 1)
  } finally {
    timers.restore()
    global.fetch = originalFetch
  }
})
