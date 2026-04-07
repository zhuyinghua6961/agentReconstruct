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
}

async function createStoreWithChats(chatCount = 1) {
  installLocalStorageMock()
  const { createPinia, setActivePinia } = await import('pinia')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()
  const originalNow = Date.now
  let seed = 1760000000000
  Date.now = () => {
    seed += 1
    return seed
  }
  try {
    for (let index = 0; index < chatCount; index += 1) {
      const chat = store.createChat()
      const persistedChat = store.chats.find((item) => item.id === chat.id)
      persistedChat.title = `Chat ${index + 1}`
      persistedChat.messages = [
        {
          role: 'user',
          content: `question ${index + 1}`,
          timestamp: new Date().toISOString(),
        },
      ]
    }
  } finally {
    Date.now = originalNow
  }
  return store
}

test('starting one busy chat marks only that chat busy', async () => {
  const store = await createStoreWithChats(2)
  const [chatA, chatB] = store.chats

  assert.equal(typeof store.startChatBusyRuntime, 'function')
  assert.equal(typeof store.isChatBusy, 'function')
  assert.equal(typeof store.isChatStreaming, 'function')

  const result = store.startChatBusyRuntime(chatA.id, { phase: 'dispatching' })

  assert.deepEqual(result, { ok: true, reason: '' })
  assert.equal(store.isChatBusy(chatA.id), true)
  assert.equal(store.isChatBusy(chatB.id), false)
  assert.equal(store.isChatStreaming(chatA.id), false)
  assert.equal(store.activeBusyCount, 1)
  assert.equal(store.isStreaming, true)
  assert.equal(store.hasBusyCapacity, true)
})

test('different chats can be busy simultaneously while the same chat cannot start twice', async () => {
  const store = await createStoreWithChats(3)
  const [chatA, chatB] = store.chats

  assert.deepEqual(store.startChatBusyRuntime(chatA.id, { phase: 'dispatching' }), { ok: true, reason: '' })
  assert.deepEqual(store.startChatBusyRuntime(chatB.id, { phase: 'dispatching' }), { ok: true, reason: '' })
  assert.equal(store.activeBusyCount, 2)

  const duplicateAttempt = store.startChatBusyRuntime(chatA.id, { phase: 'dispatching' })

  assert.deepEqual(duplicateAttempt, { ok: false, reason: 'chat_busy' })
  assert.equal(store.activeBusyCount, 2)
  assert.equal(store.isChatBusy(chatA.id), true)
  assert.equal(store.isChatBusy(chatB.id), true)
})

test('chat store exposes buildAutoTitleFromText for the recoverable send flow', async () => {
  const store = await createStoreWithChats(1)

  assert.equal(typeof store.buildAutoTitleFromText, 'function')
  assert.equal(store.buildAutoTitleFromText('这是一个用于生成标题的超长问题描述'), '这是一个用于生成标题的超长问题描述')
})

test('dispatching and streaming phases both count toward active busy capacity', async () => {
  const store = await createStoreWithChats(1)
  const [chatA] = store.chats

  store.startChatBusyRuntime(chatA.id, { phase: 'dispatching' })
  assert.equal(store.activeBusyCount, 1)
  assert.equal(store.isChatStreaming(chatA.id), false)

  assert.equal(typeof store.markChatBusyStreaming, 'function')
  store.markChatBusyStreaming(chatA.id, { requestId: 'stream_a' })

  assert.equal(store.activeBusyCount, 1)
  assert.equal(store.isChatBusy(chatA.id), true)
  assert.equal(store.isChatStreaming(chatA.id), true)
  assert.equal(store.hasBusyCapacity, true)
})

test('the sixth busy chat attempt is rejected before dispatch', async () => {
  const store = await createStoreWithChats(6)
  const firstFiveIds = store.chats.slice(0, 5).map((chat) => chat.id)
  const sixthId = store.chats[5].id

  firstFiveIds.forEach((chatId) => {
    assert.deepEqual(store.startChatBusyRuntime(chatId, { phase: 'dispatching' }), { ok: true, reason: '' })
  })

  assert.equal(store.activeBusyCount, 5)
  assert.equal(store.hasBusyCapacity, false)

  const rejected = store.startChatBusyRuntime(sixthId, { phase: 'dispatching' })

  assert.deepEqual(rejected, { ok: false, reason: 'capacity_reached' })
  assert.equal(store.isChatBusy(sixthId), false)
  assert.equal(store.activeBusyCount, 5)
})

test('finishing or stopping a busy chat frees capacity immediately', async () => {
  const store = await createStoreWithChats(6)
  const [chatA, chatB, chatC, chatD, chatE, chatF] = store.chats

  ;[chatA, chatB, chatC, chatD, chatE].forEach((chat) => {
    assert.deepEqual(store.startChatBusyRuntime(chat.id, { phase: 'dispatching' }), { ok: true, reason: '' })
  })

  assert.equal(typeof store.finishChatBusyRuntime, 'function')
  store.finishChatBusyRuntime(chatC.id)

  assert.equal(store.activeBusyCount, 4)
  assert.equal(store.hasBusyCapacity, true)
  assert.equal(store.isChatBusy(chatC.id), false)
  assert.deepEqual(store.startChatBusyRuntime(chatF.id, { phase: 'dispatching' }), { ok: true, reason: '' })

  store.finishChatBusyRuntime(chatB.id)

  assert.equal(store.activeBusyCount, 4)
  assert.equal(store.isChatBusy(chatB.id), false)
})

test('requesting stop keeps the chat busy until the caller finishes cleanup', async () => {
  const store = await createStoreWithChats(2)
  const [chatA, chatB] = store.chats

  store.startChatBusyRuntime(chatA.id, { phase: 'dispatching', requestId: 'stream_a' })
  assert.equal(store.isChatBusy(chatA.id), true)
  assert.equal(store.isChatStopRequested(chatA.id), false)

  assert.equal(store.requestChatBusyStop(chatA.id), true)
  assert.equal(store.isChatBusy(chatA.id), true)
  assert.equal(store.isChatStopRequested(chatA.id), true)
  assert.equal(store.activeBusyCount, 1)

  assert.deepEqual(
    store.startChatBusyRuntime(chatB.id, { phase: 'dispatching', requestId: 'stream_b' }),
    { ok: true, reason: '' }
  )
  assert.equal(store.activeBusyCount, 2)

  store.finishChatBusyRuntime(chatA.id)
  assert.equal(store.isChatBusy(chatA.id), false)
  assert.equal(store.activeBusyCount, 1)
})

test('busy runtime migrates with a chat when a temp conversation is promoted to a server id', async () => {
  const store = await createStoreWithChats(1)
  const tempChatId = store.currentChatId
  store.currentChat.messages = []
  store.currentChat.title = '新对话'

  const { api } = await import('../services/api.js')
  const originalCreateConversation = api.createConversation
  api.createConversation = async () => ({
    conversation_id: 9001,
    title: 'Promoted Chat',
    created_at: '2026-04-04T00:00:00.000Z',
    updated_at: '2026-04-04T00:00:00.000Z',
  })

  try {
    store.setUserId(99)
    assert.deepEqual(
      store.startChatBusyRuntime(tempChatId, { phase: 'dispatching', requestId: 'stream_temp' }),
      { ok: true, reason: '' }
    )

    await store.addUserMessage('promote this chat')

    assert.equal(store.currentChatId, '9001')
    assert.equal(store.isChatBusy(tempChatId), false)
    assert.equal(store.isChatBusy('9001'), true)
    assert.deepEqual(
      store.startChatBusyRuntime('9001', { phase: 'dispatching', requestId: 'stream_duplicate' }),
      { ok: false, reason: 'chat_busy' }
    )
    assert.equal(store.finishChatBusyRuntime('9001'), true)
    assert.equal(store.activeBusyCount, 0)
  } finally {
    api.createConversation = originalCreateConversation
  }
})

test('deleting or clearing chats also clears their busy runtime entries', async () => {
  const store = await createStoreWithChats(3)
  const [chatA, chatB, chatC] = store.chats

  store.startChatBusyRuntime(chatA.id, { phase: 'dispatching' })
  store.startChatBusyRuntime(chatB.id, { phase: 'dispatching' })
  assert.equal(store.activeBusyCount, 2)

  await store.deleteChat(chatA.id)
  assert.equal(store.isChatBusy(chatA.id), false)
  assert.equal(store.activeBusyCount, 1)

  store.startChatBusyRuntime(chatC.id, { phase: 'dispatching' })
  assert.equal(store.activeBusyCount, 2)

  store.clearAllChats()
  assert.equal(store.activeBusyCount, 0)
  assert.equal(store.isStreaming, false)
})

test('first-send temp chat promotion stays bound to the originating chat when currentChatId changes mid-flight', async () => {
  installLocalStorageMock()
  const { createPinia, setActivePinia } = await import('pinia')
  const { api } = await import('../services/api.js')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()
  store.setUserId(99)
  store.chats.splice(0, store.chats.length, {
    id: 'temp_a',
    title: '新对话',
    messages: [],
    createdAt: '2026-04-04T00:00:00.000Z',
    updatedAt: '2026-04-04T00:00:00.000Z',
    synced: false,
    syncStatus: 'local',
    pdf_list: [],
    excel_list: [],
    uploaded_files: [],
    isPinned: false,
  }, {
    id: 'temp_b',
    title: '第二个草稿',
    messages: [],
    createdAt: '2026-04-04T00:00:01.000Z',
    updatedAt: '2026-04-04T00:00:01.000Z',
    synced: false,
    syncStatus: 'local',
    pdf_list: [],
    excel_list: [],
    uploaded_files: [],
    isPinned: false,
  })
  store.currentChatId = 'temp_a'

  let resolveCreateConversation
  const createConversationPromise = new Promise((resolve) => {
    resolveCreateConversation = resolve
  })
  const originalCreateConversation = api.createConversation
  api.createConversation = async () => createConversationPromise

  try {
    assert.deepEqual(
      store.startChatBusyRuntime('temp_a', { phase: 'dispatching', requestId: 'stream_a' }),
      { ok: true, reason: '' }
    )

    const addMessagePromise = store.addUserMessage('question for chat a', { chatId: 'temp_a' })
    store.currentChatId = 'temp_b'
    resolveCreateConversation({
      conversation_id: 9002,
      title: 'Chat A',
      created_at: '2026-04-04T00:00:02.000Z',
      updated_at: '2026-04-04T00:00:02.000Z',
    })

    const resultChat = await addMessagePromise
    const promotedChat = store.chats.find((chat) => chat.id === '9002')
    const untouchedChat = store.chats.find((chat) => chat.id === 'temp_b')

    assert.equal(resultChat?.id, '9002')
    assert.equal(store.currentChatId, 'temp_b')
    assert.equal(promotedChat?.messages.length, 1)
    assert.equal(promotedChat?.messages[0].content, 'question for chat a')
    assert.equal(untouchedChat?.messages.length, 0)

    await store.addBotMessage({ content: '', isComplete: false }, { chatId: resultChat.id })
    assert.equal(promotedChat?.messages.length, 2)
    assert.equal(promotedChat?.messages[1].role, 'assistant')
    assert.equal(untouchedChat?.messages.length, 0)

    assert.equal(store.isChatBusy('temp_a'), false)
    assert.equal(store.isChatBusy('9002'), true)
  } finally {
    api.createConversation = originalCreateConversation
  }
})

test('switchChat refreshes synced idle chats from the server but preserves busy synced chats local in-flight messages', async () => {
  installLocalStorageMock()
  const { createPinia, setActivePinia } = await import('pinia')
  const { api } = await import('../services/api.js')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()
  store.setUserId(7)
  store.chats.splice(0, store.chats.length, {
    id: '101',
    title: 'Synced Chat',
    synced: true,
    syncStatus: 'synced',
    createdAt: '2026-04-04T00:00:00.000Z',
    updatedAt: '2026-04-04T00:00:00.000Z',
    pdf_list: [],
    excel_list: [],
    uploaded_files: [],
    isPinned: false,
    messages: [
      { role: 'user', content: 'question', timestamp: '2026-04-04T00:00:01.000Z' },
      {
        role: 'assistant',
        content: 'local partial answer',
        timestamp: '2026-04-04T00:00:02.000Z',
        isComplete: false,
        streamRequestId: 'stream_local',
      },
    ],
  })

  const originalGetConversationDetail = api.getConversationDetail
  api.getConversationDetail = async () => ({
    updated_at: '2026-04-04T00:01:00.000Z',
    message_count: 2,
    messages: [
      { role: 'user', content: 'question', timestamp: '2026-04-04T00:00:01.000Z' },
      {
        role: 'assistant',
        content: 'server final answer',
        timestamp: '2026-04-04T00:00:02.000Z',
        isComplete: true,
      },
    ],
    pdf_list: [{ file_id: 5, pdf_title: 'server.pdf' }],
    excel_list: [],
    uploaded_files: [],
  })

  try {
    await store.switchChat('101')
    assert.equal(store.currentMessages[1].content, 'server final answer')
    assert.equal(store.currentMessages[1].isComplete, true)
    assert.equal(store.currentChat.pdf_list.length, 1)

    store.currentChat.messages = [
      { role: 'user', content: 'question', timestamp: '2026-04-04T00:00:01.000Z' },
      {
        role: 'assistant',
        content: 'local partial answer',
        timestamp: '2026-04-04T00:00:02.000Z',
        isComplete: false,
        streamRequestId: 'stream_local',
      },
    ]

    assert.deepEqual(
      store.startChatBusyRuntime('101', { phase: 'streaming', requestId: 'stream_local' }),
      { ok: true, reason: '' }
    )

    await store.switchChat('101')
    assert.equal(store.currentMessages[1].content, 'local partial answer')
    assert.equal(store.currentMessages[1].isComplete, false)
    assert.equal(store.currentChat.pdf_list.length, 1)

    store.finishChatBusyRuntime('101')
    await store.switchChat('101')
    assert.equal(store.currentMessages[1].content, 'server final answer')
    assert.equal(store.currentMessages[1].isComplete, true)
  } finally {
    api.getConversationDetail = originalGetConversationDetail
  }
})

test('switchChat preserves messages when the chat was busy at switch start even if busy clears before server detail returns', async () => {
  installLocalStorageMock()
  const { createPinia, setActivePinia } = await import('pinia')
  const { api } = await import('../services/api.js')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()
  store.setUserId(7)
  store.chats.splice(0, store.chats.length, {
    id: '102',
    title: 'Busy Snapshot Chat',
    synced: true,
    syncStatus: 'synced',
    createdAt: '2026-04-04T00:00:00.000Z',
    updatedAt: '2026-04-04T00:00:00.000Z',
    pdf_list: [],
    excel_list: [],
    uploaded_files: [],
    isPinned: false,
    messages: [
      { role: 'user', content: 'question', timestamp: '2026-04-04T00:00:01.000Z' },
      {
        role: 'assistant',
        content: 'local busy answer',
        timestamp: '2026-04-04T00:00:02.000Z',
        isComplete: false,
        streamRequestId: 'stream_busy',
      },
    ],
  })

  let resolveDetail
  const detailPromise = new Promise((resolve) => {
    resolveDetail = resolve
  })
  const originalGetConversationDetail = api.getConversationDetail
  api.getConversationDetail = async () => detailPromise

  try {
    store.startChatBusyRuntime('102', { phase: 'streaming', requestId: 'stream_busy' })

    const switchPromise = store.switchChat('102')
    store.finishChatBusyRuntime('102')
    resolveDetail({
      updated_at: '2026-04-04T00:01:00.000Z',
      message_count: 2,
      messages: [
        { role: 'user', content: 'question', timestamp: '2026-04-04T00:00:01.000Z' },
        {
          role: 'assistant',
          content: 'server final answer',
          timestamp: '2026-04-04T00:00:02.000Z',
          isComplete: true,
        },
      ],
      pdf_list: [],
      excel_list: [],
      uploaded_files: [],
    })
    await switchPromise

    assert.equal(store.currentMessages[1].content, 'local busy answer')
    assert.equal(store.currentMessages[1].isComplete, false)

    await store.switchChat('102')
    assert.equal(store.currentMessages[1].content, 'server final answer')
    assert.equal(store.currentMessages[1].isComplete, true)
  } finally {
    api.getConversationDetail = originalGetConversationDetail
  }
})

test('switchChat preserves local messages when the chat becomes busy before server detail returns', async () => {
  installLocalStorageMock()
  const { createPinia, setActivePinia } = await import('pinia')
  const { api } = await import('../services/api.js')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  const store = useChatStore()
  store.setUserId(7)
  store.chats.splice(0, store.chats.length, {
    id: '103',
    title: 'Late Busy Chat',
    synced: true,
    syncStatus: 'synced',
    createdAt: '2026-04-04T00:00:00.000Z',
    updatedAt: '2026-04-04T00:00:00.000Z',
    pdf_list: [],
    excel_list: [],
    uploaded_files: [],
    isPinned: false,
    messages: [
      { role: 'user', content: 'question', timestamp: '2026-04-04T00:00:01.000Z' },
      {
        role: 'assistant',
        content: 'local partial answer',
        timestamp: '2026-04-04T00:00:02.000Z',
        isComplete: false,
        streamRequestId: 'stream_late_busy',
      },
    ],
  })

  let resolveDetail
  const detailPromise = new Promise((resolve) => {
    resolveDetail = resolve
  })
  const originalGetConversationDetail = api.getConversationDetail
  api.getConversationDetail = async () => detailPromise

  try {
    const switchPromise = store.switchChat('103')
    store.startChatBusyRuntime('103', { phase: 'streaming', requestId: 'stream_late_busy' })
    resolveDetail({
      updated_at: '2026-04-04T00:01:00.000Z',
      message_count: 2,
      messages: [
        { role: 'user', content: 'question', timestamp: '2026-04-04T00:00:01.000Z' },
        {
          role: 'assistant',
          content: 'server final answer',
          timestamp: '2026-04-04T00:00:02.000Z',
          isComplete: true,
        },
      ],
      pdf_list: [],
      excel_list: [],
      uploaded_files: [],
    })
    await switchPromise

    assert.equal(store.currentMessages[1].content, 'local partial answer')
    assert.equal(store.currentMessages[1].isComplete, false)

    store.finishChatBusyRuntime('103')
    await store.switchChat('103')
    assert.equal(store.currentMessages[1].content, 'server final answer')
    assert.equal(store.currentMessages[1].isComplete, true)
  } finally {
    api.getConversationDetail = originalGetConversationDetail
  }
})
