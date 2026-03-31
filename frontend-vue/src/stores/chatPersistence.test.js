import test from 'node:test'
import assert from 'node:assert/strict'

async function loadChatPersistenceUtils() {
  try {
    return await import('./chatPersistence.js')
  } catch {
    return {}
  }
}

function buildPersistedChatPayload() {
  return [
    {
      id: '42',
      title: '测试会话',
      synced: false,
      syncStatus: 'local',
      createdAt: '2026-03-31T10:00:00.000Z',
      updatedAt: '2026-03-31T10:05:00.000Z',
      messages: [
        {
          role: 'user',
          content: '第一个问题',
          timestamp: '2026-03-31T10:00:00.000Z',
        },
        {
          role: 'assistant',
          content: '未完成回答片段',
          timestamp: '2026-03-31T10:00:05.000Z',
          isComplete: false,
          stepsCollapsed: true,
          streamRequestId: 'stream_123',
          references: [{ doi: '10.1000/demo', title: 'Demo Ref' }],
          referenceLinks: [{ doi: '10.1000/demo', pdfUrl: '/demo.pdf' }],
          doiLocations: {
            '10.1000/demo': [{ page: 3, section: 'intro', chunk_id: 'c1' }],
          },
          steps: [{ step: 'retrieve', status: 'success', title: '检索', detail: 'done' }],
        },
      ],
    },
  ]
}

test('restorePersistedChats removes runtime-only streaming fields while preserving durable message data', async () => {
  const { restorePersistedChats } = await loadChatPersistenceUtils()

  assert.equal(typeof restorePersistedChats, 'function')

  const restored = restorePersistedChats(buildPersistedChatPayload())

  assert.equal(restored.length, 1)
  assert.equal(restored[0].messages.length, 2)
  assert.equal(restored[0].messages[0].content, '第一个问题')
  assert.equal(restored[0].messages[1].content, '未完成回答片段')
  assert.equal(restored[0].messages[1].isComplete, false)
  assert.equal(restored[0].messages[1].stepsCollapsed, true)
  assert.deepEqual(restored[0].messages[1].references, [{ doi: '10.1000/demo', title: 'Demo Ref' }])
  assert.deepEqual(restored[0].messages[1].referenceLinks, [{ doi: '10.1000/demo', pdfUrl: '/demo.pdf' }])
  assert.deepEqual(restored[0].messages[1].doiLocations, {
    '10.1000/demo': [{ page: 3, section: 'intro', chunk_id: 'c1' }],
  })
  assert.deepEqual(restored[0].messages[1].steps, [{ step: 'retrieve', status: 'success', title: '检索', detail: 'done' }])
  assert.equal('streamRequestId' in restored[0].messages[1], false)
})

test('prepareChatsForPersistence keeps message order and durable fields but strips runtime-only state', async () => {
  const { prepareChatsForPersistence } = await loadChatPersistenceUtils()

  assert.equal(typeof prepareChatsForPersistence, 'function')

  const prepared = prepareChatsForPersistence(buildPersistedChatPayload())

  assert.equal(prepared.length, 1)
  assert.equal(prepared[0].messages.length, 2)
  assert.equal(prepared[0].messages[0].content, '第一个问题')
  assert.equal(prepared[0].messages[1].content, '未完成回答片段')
  assert.equal(prepared[0].messages[1].stepsCollapsed, true)
  assert.equal(prepared[0].messages[1].isComplete, false)
  assert.deepEqual(prepared[0].messages[1].references, [{ doi: '10.1000/demo', title: 'Demo Ref' }])
  assert.deepEqual(prepared[0].messages[1].referenceLinks, [{ doi: '10.1000/demo', pdfUrl: '/demo.pdf' }])
  assert.deepEqual(prepared[0].messages[1].doiLocations, {
    '10.1000/demo': [{ page: 3, section: 'intro', chunk_id: 'c1' }],
  })
  assert.deepEqual(prepared[0].messages[1].steps, [{ step: 'retrieve', status: 'success', title: '检索', detail: 'done' }])
  assert.equal('streamRequestId' in prepared[0].messages[1], false)
})

test('prepareChatsForPersistence serializes Date timestamps into durable ISO strings', async () => {
  const { prepareChatsForPersistence } = await loadChatPersistenceUtils()

  assert.equal(typeof prepareChatsForPersistence, 'function')

  const prepared = prepareChatsForPersistence([
    {
      id: 'date-chat',
      messages: [
        {
          role: 'user',
          content: 'date payload',
          timestamp: new Date('2026-03-31T10:00:00.000Z'),
        },
      ],
    },
  ])

  assert.equal(prepared[0].messages[0].timestamp, '2026-03-31T10:00:00.000Z')
})

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

test('chat store reload path restores durable content and does not revive stale streaming state', async () => {
  const storage = installLocalStorageMock({
    lfp_chats: JSON.stringify(buildPersistedChatPayload()),
    lfp_current_chat_id: '42',
  })

  const { createPinia, setActivePinia } = await import('pinia')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()
  await store.loadChats()

  assert.equal(store.isStreaming, false)
  assert.equal(store.currentMessages.length, 2)
  assert.equal(store.currentMessages[1].content, '未完成回答片段')
  assert.equal(store.currentMessages[1].isComplete, false)
  assert.equal(store.currentMessages[1].stepsCollapsed, true)
  assert.equal('streamRequestId' in store.currentMessages[1], false)

  store.persistLocalState()
  const persistedChats = JSON.parse(storage.get('lfp_chats'))
  assert.equal('streamRequestId' in persistedChats[0].messages[1], false)
  assert.equal(persistedChats[0].messages[0].content, '第一个问题')
  assert.equal(persistedChats[0].messages[1].content, '未完成回答片段')
})

test('synced chat reload lets explicit server completion state override stale local recovery state', async () => {
  const storage = installLocalStorageMock({
    lfp_chats: JSON.stringify([
      {
        ...buildPersistedChatPayload()[0],
        synced: true,
        syncStatus: 'synced',
      },
    ]),
    lfp_current_chat_id: '42',
  })

  const { createPinia, setActivePinia } = await import('pinia')
  const { api } = await import('../services/api.js')
  const { useChatStore } = await import('./chatStore.js')

  const originalGetConversationDetail = api.getConversationDetail
  api.getConversationDetail = async () => ({
    updated_at: '2026-03-31T10:06:00.000Z',
    message_count: 2,
    messages: [
      {
        role: 'user',
        content: '第一个问题',
        timestamp: '2026-03-31T10:00:00.000Z',
      },
      {
        role: 'assistant',
        content: '未完成回答片段',
        timestamp: '2026-03-31T10:00:05.000Z',
        isComplete: true,
        stepsCollapsed: false,
        references: [{ doi: '10.1000/demo', title: 'Demo Ref' }],
        referenceLinks: [{ doi: '10.1000/demo', pdfUrl: '/demo.pdf' }],
        doiLocations: {
          '10.1000/demo': [{ page: 3, section: 'intro', chunk_id: 'c1' }],
        },
        steps: [{ step: 'retrieve', status: 'success', title: '检索', detail: 'done' }],
      },
    ],
  })

  try {
    setActivePinia(createPinia())
    const store = useChatStore()
    await store.loadChats()
    localStorage.setItem('lfp_user_id', '1')
    await store.switchChat('42')

    assert.equal(store.currentMessages[1].content, '未完成回答片段')
    assert.equal(store.currentMessages[1].isComplete, true)
    assert.equal(store.currentMessages[1].stepsCollapsed, false)
    assert.equal('streamRequestId' in store.currentMessages[1], false)

    store.persistLocalState()
    const persistedChats = JSON.parse(storage.get('lfp_chats'))
    assert.equal(persistedChats[0].messages[1].isComplete, true)
    assert.equal(persistedChats[0].messages[1].stepsCollapsed, false)
  } finally {
    api.getConversationDetail = originalGetConversationDetail
  }
})

test('synced chat reload preserves local unfinished state when server detail omits completion fields', async () => {
  installLocalStorageMock({
    lfp_chats: JSON.stringify([
      {
        ...buildPersistedChatPayload()[0],
        synced: true,
        syncStatus: 'synced',
      },
    ]),
    lfp_current_chat_id: '42',
  })

  const { createPinia, setActivePinia } = await import('pinia')
  const { useChatStore } = await import('./chatStore.js')

  const originalFetch = global.fetch
  global.fetch = async (url) => {
    const urlString = String(url)
    if (urlString.endsWith('/api/conversations/42')) {
      return {
        ok: true,
        async json() {
          return {
            success: true,
            data: {
              conversation_id: 42,
              title: '测试会话',
              updated_at: '2026-03-31T10:06:00.000Z',
              message_count: 2,
              messages: [
                {
                  role: 'user',
                  content: '第一个问题',
                  timestamp: '2026-03-31T10:00:00.000Z',
                },
                {
                  role: 'assistant',
                  content: '未完成回答片段',
                  timestamp: '2026-03-31T10:00:05.000Z',
                  references: [{ doi: '10.1000/demo', title: 'Demo Ref' }],
                  referenceLinks: [{ doi: '10.1000/demo', pdfUrl: '/demo.pdf' }],
                  doiLocations: {
                    '10.1000/demo': [{ page: 3, section: 'intro', chunk_id: 'c1' }],
                  },
                  steps: [{ step: 'retrieve', status: 'success', title: '检索', detail: 'done' }],
                },
              ],
              uploaded_files: [],
            },
          }
        },
      }
    }
    throw new Error(`unexpected fetch url: ${urlString}`)
  }

  try {
    setActivePinia(createPinia())
    const store = useChatStore()
    await store.loadChats()
    localStorage.setItem('lfp_user_id', '1')
    await store.switchChat('42')

    assert.equal(store.currentMessages[1].content, '未完成回答片段')
    assert.equal(store.currentMessages[1].isComplete, false)
    assert.equal(store.currentMessages[1].stepsCollapsed, true)
    assert.equal('streamRequestId' in store.currentMessages[1], false)
  } finally {
    global.fetch = originalFetch
  }
})
