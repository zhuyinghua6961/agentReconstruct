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
          metadata: {
            timings: { stage1: 10, stage2: 20 },
          },
        },
        {
          role: 'assistant',
          content: '',
          timestamp: '2026-03-31T10:00:08.000Z',
          terminalStatus: 'failed',
          status: 'failed',
          failureMessage: '模型超时',
          failureCode: 'UPSTREAM_TIMEOUT',
          retriable: true,
          doneSeen: false,
          isComplete: true,
          metadata: {
            terminal_status: 'failed',
            failure_message: '模型超时',
            failure_code: 'UPSTREAM_TIMEOUT',
            retriable: true,
            done_seen: false,
          },
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
  assert.equal(restored[0].messages.length, 3)
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
  assert.equal(restored[0].messages[2].terminalStatus, 'failed')
  assert.equal(restored[0].messages[2].failureMessage, '模型超时')
  assert.equal(restored[0].messages[2].metadata.terminal_status, 'failed')
  assert.equal(restored[0].messages[2].metadata.failure_message, '模型超时')
  assert.equal(restored[0].messages[2].doneSeen, false)
})

test('prepareChatsForPersistence keeps message order and durable fields but strips runtime-only state', async () => {
  const { prepareChatsForPersistence } = await loadChatPersistenceUtils()

  assert.equal(typeof prepareChatsForPersistence, 'function')

  const prepared = prepareChatsForPersistence(buildPersistedChatPayload())

  assert.equal(prepared.length, 1)
  assert.equal(prepared[0].messages.length, 3)
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
  assert.equal(prepared[0].messages[2].terminalStatus, 'failed')
  assert.equal(prepared[0].messages[2].failureCode, 'UPSTREAM_TIMEOUT')
  assert.equal(prepared[0].messages[2].metadata.failure_code, 'UPSTREAM_TIMEOUT')
  assert.equal(prepared[0].messages[2].retriable, true)
  assert.equal(prepared[0].messages[2].doneSeen, false)
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

test('prepareChatsForPersistence strips chat-level busy runtime snapshots', async () => {
  const { prepareChatsForPersistence } = await loadChatPersistenceUtils()

  assert.equal(typeof prepareChatsForPersistence, 'function')

  const prepared = prepareChatsForPersistence([
    {
      id: 'busy-chat',
      title: '运行中会话',
      busyRuntime: {
        phase: 'streaming',
        requestId: 'stream_busy',
        abortController: { fake: true },
        pendingContent: 'partial answer',
      },
      messages: [
        {
          role: 'assistant',
          content: 'partial answer',
          timestamp: '2026-03-31T10:00:00.000Z',
        },
      ],
    },
  ])

  assert.equal('busyRuntime' in prepared[0], false)
  assert.equal(prepared[0].messages[0].content, 'partial answer')
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
  assert.equal(store.currentMessages.length, 3)
  assert.equal(store.currentMessages[1].content, '未完成回答片段')
  assert.equal(store.currentMessages[1].isComplete, false)
  assert.equal(store.currentMessages[1].stepsCollapsed, true)
  assert.deepEqual(store.currentMessages[1].metadata.timings, { stage1: 10, stage2: 20 })
  assert.deepEqual(store.currentMessages[1].timings, { stage1: 10, stage2: 20 })
  assert.equal('streamRequestId' in store.currentMessages[1], false)
  assert.equal(store.currentMessages[2].terminalStatus, 'failed')
  assert.equal(store.currentMessages[2].failureMessage, '模型超时')
  assert.equal(store.currentMessages[2].isComplete, true)

  store.persistLocalState()
  const persistedChats = JSON.parse(storage.get('lfp_chats'))
  assert.equal('streamRequestId' in persistedChats[0].messages[1], false)
  assert.equal(persistedChats[0].messages[0].content, '第一个问题')
  assert.equal(persistedChats[0].messages[1].content, '未完成回答片段')
  assert.equal(persistedChats[0].messages[2].metadata.terminal_status, 'failed')
})

test('chat store reload path preserves skipped QA steps with timings', async () => {
  installLocalStorageMock({
    lfp_chats: JSON.stringify([
      {
        id: 'chat-skipped',
        title: 'Skipped timing',
        messages: [
          { role: 'user', content: 'q' },
          {
            role: 'assistant',
            content: 'a',
            steps: [
              {
                step: 'stage25',
                title: '阶段二点五',
                status: 'skipped',
                message: '阶段二点五：已跳过MD原文扩展',
              },
            ],
            metadata: { timings: { stage25: 0 } },
          },
        ],
      },
    ]),
    lfp_current_chat_id: 'chat-skipped',
  })

  const { createPinia, setActivePinia } = await import('pinia')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()
  await store.loadChats()

  const restored = store.currentMessages[1]
  assert.equal(restored.steps[0].status, 'skipped')
  assert.deepEqual(restored.metadata.timings, { stage25: 0 })
})

test('chat store reload merges terminal metadata timings over stale top-level partial timings', async () => {
  installLocalStorageMock({
    lfp_chats: JSON.stringify([
      {
        id: 'timing-chat',
        messages: [
          {
            role: 'assistant',
            content: 'answer',
            timings: { stage1: 1000 },
            metadata: {
              stage_timings_ms: { stage1: 1000 },
              timings: { stage1: 1100, stage2: 2200 },
            },
          },
        ],
      },
    ]),
    lfp_current_chat_id: 'timing-chat',
  })

  const { createPinia, setActivePinia } = await import('pinia')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()
  await store.loadChats()

  assert.deepEqual(store.currentMessages[0].metadata.timings, { stage1: 1100, stage2: 2200 })
  assert.deepEqual(store.currentMessages[0].timings, { stage1: 1100, stage2: 2200 })
})

test('chat store loadChats clears runtime-only busy state before restoring durable content', async () => {
  installLocalStorageMock({
    lfp_chats: JSON.stringify([
      {
        id: 'runtime-chat',
        title: '运行中恢复',
        synced: false,
        syncStatus: 'local',
        createdAt: '2026-03-31T10:00:00.000Z',
        updatedAt: '2026-03-31T10:05:00.000Z',
        messages: [
          {
            role: 'assistant',
            content: 'local partial answer',
            timestamp: '2026-03-31T10:00:05.000Z',
            isComplete: false,
          },
        ],
      },
    ]),
    lfp_current_chat_id: 'runtime-chat',
  })

  const { createPinia, setActivePinia } = await import('pinia')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()

  assert.deepEqual(
    store.startChatBusyRuntime('runtime-chat', { phase: 'dispatching', requestId: 'stream_runtime' }),
    { ok: true, reason: '' }
  )
  assert.equal(store.activeBusyCount, 1)
  assert.equal(store.isStreaming, true)

  await store.loadChats()

  assert.equal(store.activeBusyCount, 0)
  assert.equal(store.isChatBusy('runtime-chat'), false)
  assert.equal(store.isStreaming, false)
  assert.equal(store.currentMessages.length, 1)
  assert.equal(store.currentMessages[0].content, 'local partial answer')
  assert.equal(store.currentMessages[0].isComplete, false)
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
