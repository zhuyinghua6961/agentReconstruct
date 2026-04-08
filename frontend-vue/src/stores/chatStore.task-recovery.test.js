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

async function createStore() {
  const { createPinia, setActivePinia } = await import('pinia')
  const { useChatStore } = await import('./chatStore.js')

  setActivePinia(createPinia())
  return useChatStore()
}

test('chat store restores recoverable active tasks from persisted chats and persists last seq updates', async () => {
  const localState = installLocalStorageMock({
    lfp_chats: JSON.stringify([
      {
        id: '42',
        title: '恢复中的会话',
        synced: true,
        syncStatus: 'synced',
        createdAt: '2026-04-06T10:00:00.000Z',
        updatedAt: '2026-04-06T10:05:00.000Z',
        lastTaskSeq: 2,
        activeTask: {
          task_id: 'task_persisted_42',
          status: 'queued',
          last_seq: 4,
          replay_available: true,
        },
        messages: [],
      },
    ]),
    lfp_current_chat_id: '42',
  })

  const store = await createStore()
  await store.loadChats()

  assert.equal(typeof store.getChatActiveTask, 'function')
  assert.equal(typeof store.getChatLastTaskSeq, 'function')
  assert.equal(typeof store.updateChatTaskReplayCursor, 'function')
  assert.equal(typeof store.clearChatActiveTask, 'function')

  assert.equal(store.getChatActiveTask('42')?.task_id, 'task_persisted_42')
  assert.equal(store.getChatLastTaskSeq('42'), 4)
  assert.equal(store.isChatBusy('42'), true)
  assert.equal(store.activeBusyCount, 1)

  store.updateChatTaskReplayCursor('42', 7)
  store.persistLocalState()

  const persisted = JSON.parse(String(localState.get('lfp_chats')))
  assert.equal(persisted[0].lastTaskSeq, 7)
})

test('chat store adopts server active_task during load and clears stale task state when detail no longer reports a live task', async () => {
  installLocalStorageMock()
  const { api } = await import('../services/api.js')
  const originalGetConversationList = api.getConversationList
  const originalGetConversationDetail = api.getConversationDetail

  api.getConversationList = async () => ({
    conversations: [
      {
        conversation_id: 12,
        title: 'Server Task Chat',
        message_count: 1,
        created_at: '2026-04-06T09:00:00.000Z',
        updated_at: '2026-04-06T09:05:00.000Z',
        active_task: {
          task_id: 'task_server_12',
          status: 'running',
          last_seq: 5,
          replay_available: true,
        },
      },
    ],
    pagination: {
      total: 1,
      page: 1,
      page_size: 20,
    },
  })
  api.getConversationDetail = async () => ({
    conversation_id: 12,
    title: 'Server Task Chat',
    created_at: '2026-04-06T09:00:00.000Z',
    updated_at: '2026-04-06T09:06:00.000Z',
    message_count: 2,
    messages: [
      {
        role: 'user',
        content: 'hello',
      },
      {
        role: 'assistant',
        content: 'terminal truth',
        isComplete: true,
      },
    ],
    uploaded_files: [],
    pdf_list: [],
    excel_list: [],
    active_task: null,
  })

  try {
    const store = await createStore()
    store.setUserId(99)
    await store.loadChats()

    assert.equal(store.getChatActiveTask('12')?.task_id, 'task_server_12')
    assert.equal(store.getChatLastTaskSeq('12'), 5)
    assert.equal(store.isChatBusy('12'), true)

    await store.switchChat('12')

    assert.equal(store.getChatActiveTask('12'), null)
    assert.equal(store.getChatLastTaskSeq('12'), 5)
    assert.equal(store.isChatBusy('12'), false)
    assert.equal(store.currentMessages.length, 2)
    assert.equal(store.currentMessages[1].content, 'terminal truth')
  } finally {
    api.getConversationList = originalGetConversationList
    api.getConversationDetail = originalGetConversationDetail
  }
})

test('chat store does not carry the previous task replay seq into a newly created follow-up task in the same chat', async () => {
  installLocalStorageMock()
  const store = await createStore()

  const chat = store.createChat()
  chat.synced = true
  chat.lastTaskSeq = 128
  chat.messages = [
    { role: 'user', content: 'first question' },
    {
      role: 'assistant',
      content: 'first answer',
      status: 'completed',
      metadata: {
        task_id: 'task_old',
        last_seq: 128,
        terminal_status: 'completed',
      },
    },
  ]

  store.setChatActiveTask(chat.id, {
    task_id: 'task_new_followup',
    status: 'queued',
    last_seq: 0,
    replay_available: true,
  }, { touch: false, persist: false })

  assert.equal(store.getChatActiveTask(chat.id)?.task_id, 'task_new_followup')
  assert.equal(store.getChatLastTaskSeq(chat.id), 0)
})
