import test from 'node:test'
import assert from 'node:assert/strict'

function installLocalStorageMock(initial = {}) {
  const data = new Map(Object.entries(initial).map(([key, value]) => [String(key), String(value)]))
  global.localStorage = {
    getItem(key) {
      return data.has(String(key)) ? data.get(String(key)) : null
    },
    setItem(key, value) {
      data.set(String(key), String(value))
    },
    removeItem(key) {
      data.delete(String(key))
    },
    clear() {
      data.clear()
    },
  }
  return data
}

test('chat store restores failed terminal assistant messages as complete durable messages', async () => {
  installLocalStorageMock({
    lfp_chats: JSON.stringify([
      {
        id: '42',
        title: '失败会话',
        synced: false,
        syncStatus: 'local',
        createdAt: '2026-03-31T10:00:00.000Z',
        updatedAt: '2026-03-31T10:05:00.000Z',
        messages: [
          {
            role: 'assistant',
            content: '',
            timestamp: '2026-03-31T10:00:05.000Z',
            terminalStatus: 'failed',
            status: 'failed',
            failureMessage: '模型超时',
            failureCode: 'UPSTREAM_TIMEOUT',
            retriable: true,
            doneSeen: false,
            metadata: {
              terminal_status: 'failed',
              status: 'failed',
              failure_message: '模型超时',
              failure_code: 'UPSTREAM_TIMEOUT',
              retriable: true,
              done_seen: false,
            },
          },
        ],
      },
    ]),
    lfp_current_chat_id: '42',
  })

  const { createPinia, setActivePinia } = await import('pinia')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()
  await store.loadChats()

  assert.equal(store.currentMessages.length, 1)
  assert.equal(store.currentMessages[0].terminalStatus, 'failed')
  assert.equal(store.currentMessages[0].failureMessage, '模型超时')
  assert.equal(store.currentMessages[0].doneSeen, false)
  assert.equal(store.currentMessages[0].isComplete, true)
})

test('chat store restores canceled terminal assistant messages as complete durable messages', async () => {
  installLocalStorageMock({
    lfp_chats: JSON.stringify([
      {
        id: '43',
        title: '取消会话',
        synced: false,
        syncStatus: 'local',
        createdAt: '2026-03-31T10:00:00.000Z',
        updatedAt: '2026-03-31T10:05:00.000Z',
        messages: [
          {
            role: 'assistant',
            content: '',
            timestamp: '2026-03-31T10:00:05.000Z',
            terminalStatus: 'canceled',
            status: 'canceled',
            failureMessage: '用户取消',
            failureCode: 'ASK_CANCELLED',
            retriable: false,
            doneSeen: false,
            metadata: {
              terminal_status: 'canceled',
              status: 'canceled',
              failure_message: '用户取消',
              failure_code: 'ASK_CANCELLED',
              retriable: false,
              done_seen: false,
            },
          },
        ],
      },
    ]),
    lfp_current_chat_id: '43',
  })

  const { createPinia, setActivePinia } = await import('pinia')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()
  await store.loadChats()

  assert.equal(store.currentMessages.length, 1)
  assert.equal(store.currentMessages[0].terminalStatus, 'canceled')
  assert.equal(store.currentMessages[0].failureMessage, '用户取消')
  assert.equal(store.currentMessages[0].retriable, false)
  assert.equal(store.currentMessages[0].isComplete, true)
})
