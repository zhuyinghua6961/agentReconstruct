import test from 'node:test'
import assert from 'node:assert/strict'

import { createRecoverableTaskController } from './recoverableTaskController.js'

function createHarness(options = {}) {
  const chat = {
    id: '42',
    synced: true,
    messages: [],
    activeTask: null,
    lastTaskSeq: 0,
  }
  const chats = [chat]
  const runtimeMap = new Map()
  const attachLockMap = new Map()
  const apiCalls = {
    createTask: [],
    streamTaskEvents: [],
    getTaskEvents: [],
    getTask: [],
    cancelTask: [],
  }
  let currentChatId = '42'
  let finishBusyCount = 0
  let persistCount = 0

  function normalizeChatId(chatId) {
    return String(chatId || '').trim()
  }

  function getChatById(chatId) {
    const normalized = normalizeChatId(chatId)
    return chats.find((item) => normalizeChatId(item.id) === normalized) || null
  }

  const store = {
    ensureChatConversation: async (chatId, titleHint) => {
      if (typeof options.ensureChatConversation === 'function') {
        return await options.ensureChatConversation(chatId, titleHint, { chat, chats, getChatById })
      }
      return getChatById(chatId)
    },
    startChatBusyRuntime: (chatId, runtimeOptions = {}) => {
      if (typeof options.startChatBusyRuntime === 'function') {
        return options.startChatBusyRuntime(chatId, runtimeOptions, { chat, chats, getChatById })
      }
      return { ok: true, reason: '' }
    },
    finishChatBusyRuntime: () => {
      finishBusyCount += 1
    },
    setChatActiveTask: (chatId, summary) => {
      const target = getChatById(chatId)
      target.activeTask = summary ? { ...summary } : null
      if (summary && Number.isFinite(Number(summary.last_seq))) {
        target.lastTaskSeq = Number(summary.last_seq)
      }
      return target.activeTask
    },
    clearChatActiveTask: (chatId) => {
      const target = getChatById(chatId)
      if (target) target.activeTask = null
    },
    getChatLastTaskSeq: (chatId) => getChatById(chatId)?.lastTaskSeq || 0,
    updateChatTaskReplayCursor: (chatId, seq) => {
      const target = getChatById(chatId)
      if (target) target.lastTaskSeq = Number(seq || 0)
    },
    persistLocalState: () => {
      persistCount += 1
    },
  }

  const api = {
    createTask: async (...args) => {
      apiCalls.createTask.push(args)
      if (typeof options.createTask === 'function') {
        return await options.createTask(...args)
      }
      return {
        task_id: 'task_42',
        status: 'queued',
        last_seq: 0,
        replay_available: true,
      }
    },
    streamTaskEvents: async (taskId, afterSeq, streamOptions = {}) => {
      apiCalls.streamTaskEvents.push([taskId, afterSeq])
      if (typeof options.streamTaskEvents === 'function') {
        return await options.streamTaskEvents(taskId, afterSeq, streamOptions)
      }
      streamOptions.onEvent?.({ seq: 1, type: 'content', content: 'final answer' })
      streamOptions.onEvent?.({ seq: 2, type: 'done', final_answer: 'final answer' })
      return undefined
    },
    getTaskEvents: async (taskId, afterSeq) => {
      apiCalls.getTaskEvents.push([taskId, afterSeq])
      if (typeof options.getTaskEvents === 'function') {
        return await options.getTaskEvents(taskId, afterSeq)
      }
      if (afterSeq === 0) {
        return {
          events: [
            { seq: 1, type: 'content', content: 'final answer' },
            { seq: 2, type: 'done', final_answer: 'final answer' },
          ],
        }
      }
      return { events: [] }
    },
    getTask: async (taskId) => {
      apiCalls.getTask.push([taskId])
      if (typeof options.getTask === 'function') {
        return await options.getTask(taskId)
      }
      return {
        task_id: taskId,
        status: 'completed',
        last_seq: 2,
        replay_available: true,
      }
    },
    cancelTask: async (taskId) => {
      apiCalls.cancelTask.push([taskId])
      if (typeof options.cancelTask === 'function') {
        return await options.cancelTask(taskId)
      }
      return {
        task_id: taskId,
        status: 'canceled',
        last_seq: 5,
        replay_available: true,
      }
    },
  }

  let clearedInput = false
  const scrollCalls = []

  async function refreshConversationTruth(chatId) {
    if (typeof options.refreshConversationTruth === 'function') {
      return await options.refreshConversationTruth(chatId, { chat, chats })
    }
    const target = getChatById(chatId)
    target.messages = [
      { role: 'user', content: 'server question' },
      { role: 'assistant', content: '', status: 'queued' },
    ]
    target.activeTask = {
      task_id: 'task_42',
      status: 'running',
      last_seq: 0,
      replay_available: true,
    }
    return {
      active_task: { ...target.activeTask },
    }
  }

  async function refreshConversationTruthFallback(chatId, lastSeq) {
    if (typeof options.refreshConversationTruthFallback === 'function') {
      return await options.refreshConversationTruthFallback(chatId, lastSeq, {
        chat,
        chats,
        controller,
        store,
      })
    }
    const target = getChatById(chatId)
    target.lastTaskSeq = Number(lastSeq || 0)
    if (String(target.activeTask?.task_id || '').trim() === 'task_cancel') {
      target.messages = [
        { role: 'user', content: 'server question' },
        { role: 'assistant', content: '', status: 'canceled' },
      ]
    } else {
      target.messages = [
        { role: 'user', content: 'server question' },
        { role: 'assistant', content: 'final answer', status: 'completed' },
      ]
    }
    target.activeTask = null
    controller.detachRecoverableTask(chatId)
    store.finishChatBusyRuntime(chatId)
    store.persistLocalState()
    return {
      detail: { active_task: null },
      keepRecovering: false,
      cursor: {
        taskId: '',
        status: 'completed',
        lastSeq: Number(lastSeq || 0),
        recoverable: false,
        replayAvailable: true,
        terminal: true,
      },
    }
  }

  function getStreamRuntime(chatId) {
    return runtimeMap.get(normalizeChatId(chatId)) || null
  }

  function createStreamRuntime(chatId, requestId, targetIndex = -1, options = {}) {
    const runtime = {
      chatId: normalizeChatId(chatId),
      requestId: String(requestId || '').trim(),
      targetIndex,
      abortController: new AbortController(),
      mode: String(options?.mode || 'legacy'),
    }
    runtimeMap.set(runtime.chatId, runtime)
    return runtime
  }

  function clearStreamRuntime(chatId) {
    runtimeMap.delete(normalizeChatId(chatId))
  }

  function applyGatewayEvent(chatId, event) {
    const target = getChatById(chatId)
    const assistant = target.messages[1]
    if (!assistant) return
    if (event.type === 'content') {
      assistant.content = String(assistant.content || '') + String(event.content || '')
    }
    if (event.type === 'done') {
      assistant.content = String(event.final_answer || assistant.content || '')
      if (target.activeTask) {
        target.activeTask.status = 'completed'
      }
    }
  }

  const controller = createRecoverableTaskController({
    api,
    store,
    normalizeChatId,
    getChatById,
    getCurrentChatId: () => currentChatId,
    taskAttachInFlightByChatId: attachLockMap,
    streamRuntimeByChatId: runtimeMap,
    getStreamRuntime,
    createStreamRuntime,
    clearStreamRuntime,
    refreshConversationTruth,
    refreshConversationTruthFallback,
    applyGatewayEvent,
    sleepWithSignal: async () => {},
    clearInput: () => {
      clearedInput = true
    },
    scrollToBottom: (options = {}) => {
      scrollCalls.push(options)
    },
  })

  return {
    apiCalls,
    chat,
    chats,
    controller,
    getRuntimeMap: () => runtimeMap,
    getClearedInput: () => clearedInput,
    getFinishBusyCount: () => finishBusyCount,
    getPersistCount: () => persistCount,
    getScrollCalls: () => scrollCalls,
  }
}

test('recoverable task controller creates task, replays server events, and avoids duplicate local messages', async () => {
  const harness = createHarness()

  await harness.controller.sendTaskMessage({
    requestedChatId: '42',
    message: 'hello controller',
    titleHint: 'hello controller',
    chatHistory: [],
    conversationId: 42,
    requestChatContext: { selected_ids: [] },
    requestAskMode: 'fast',
  })

  assert.equal(harness.apiCalls.createTask.length, 1)
  assert.deepEqual(harness.apiCalls.streamTaskEvents, [['task_42', 0]])
  assert.deepEqual(harness.apiCalls.getTaskEvents, [])
  assert.equal(harness.chat.messages.length, 2)
  assert.equal(harness.chat.messages[0].role, 'user')
  assert.equal(harness.chat.messages[1].role, 'assistant')
  assert.equal(harness.chat.messages[1].content, 'final answer')
  assert.equal(harness.chat.activeTask, null)
  assert.equal(harness.chat.lastTaskSeq, 2)
  assert.equal(harness.getRuntimeMap().size, 0)
  assert.equal(harness.getClearedInput(), true)
  assert.equal(harness.getFinishBusyCount(), 2)
  assert.ok(harness.getPersistCount() >= 1)
  assert.deepEqual(harness.getScrollCalls(), [{ force: true }])
})

test('recoverable task controller cancels through gateway task cancel and settles from refreshed truth', async () => {
  const harness = createHarness()
  harness.chat.messages = [
    { role: 'user', content: 'server question' },
    { role: 'assistant', content: '', status: 'running' },
  ]
  harness.chat.activeTask = {
    task_id: 'task_cancel',
    status: 'running',
    last_seq: 3,
    replay_available: true,
  }
  harness.chat.lastTaskSeq = 3

  await harness.controller.cancelRecoverableTask('42', 'task_cancel')

  assert.deepEqual(harness.apiCalls.cancelTask, [['task_cancel']])
  assert.equal(harness.chat.activeTask, null)
  assert.equal(harness.chat.messages.length, 2)
  assert.equal(harness.chat.messages[1].status, 'canceled')
  assert.equal(harness.getRuntimeMap().size, 0)
  assert.equal(harness.getFinishBusyCount(), 2)
})

test('recoverable task controller detaches local runtimes without canceling backend tasks', () => {
  const harness = createHarness()
  const runtime = {
    chatId: '42',
    requestId: 'task_detach',
    abortController: new AbortController(),
  }
  harness.getRuntimeMap().set('42', runtime)

  harness.controller.detachAllRecoverableTasks()

  assert.equal(runtime.abortController.signal.aborted, true)
  assert.equal(harness.getRuntimeMap().size, 0)
  assert.equal(harness.apiCalls.cancelTask.length, 0)
})

test('recoverable task controller keeps created task live when replay and fallback refresh both fail', async () => {
  const harness = createHarness({
    streamTaskEvents: async () => {
      throw new Error('replay failed')
    },
    refreshConversationTruthFallback: async () => {
      throw new Error('fallback failed')
    },
  })

  const result = await harness.controller.sendTaskMessage({
    requestedChatId: '42',
    message: 'hello controller',
    titleHint: 'hello controller',
    requestChatContext: { selected_ids: [] },
    requestAskMode: 'fast',
  })

  assert.equal(result.ok, true)
  assert.equal(result.reason, undefined)
  assert.equal(harness.apiCalls.createTask.length, 1)
  assert.equal(harness.getRuntimeMap().size, 0)
  assert.equal(harness.chat.activeTask?.task_id, 'task_42')
  assert.equal(harness.chat.activeTask?.status, 'running')
  assert.equal(harness.getFinishBusyCount(), 1)
})

test('recoverable task controller marks the chat busy before awaiting conversation promotion', async () => {
  let releaseEnsure
  let startCalls = 0
  const ensureStarted = new Promise((resolve) => {
    releaseEnsure = resolve
  })
  const harness = createHarness({
    ensureChatConversation: async (chatId, _titleHint, helpers) => {
      await ensureStarted
      return helpers.getChatById(chatId)
    },
    startChatBusyRuntime: () => {
      startCalls += 1
      return { ok: true, reason: '' }
    },
  })

  const pending = harness.controller.sendTaskMessage({
    requestedChatId: '42',
    message: 'hello controller',
    titleHint: 'hello controller',
    requestChatContext: { selected_ids: [] },
    requestAskMode: 'fast',
  })

  await Promise.resolve()
  assert.equal(startCalls, 1)

  releaseEnsure()
  await pending
})

test('recoverable task controller resumes replay after the persisted assistant cursor to avoid duplicating server content', async () => {
  const harness = createHarness({
    createTask: async () => ({
      task_id: 'task_42',
      status: 'queued',
      last_seq: 1,
      replay_available: true,
    }),
    refreshConversationTruth: async (_chatId, { chat: target }) => {
      target.messages = [
        { role: 'user', content: 'server question' },
        {
          role: 'assistant',
          content: 'partial ',
          status: 'running',
          metadata: {
            task_id: 'task_42',
            last_seq: 4,
          },
        },
      ]
      target.activeTask = {
        task_id: 'task_42',
        status: 'running',
        last_seq: 1,
        replay_available: true,
      }
      return {
        active_task: { ...target.activeTask },
        messages: target.messages.map((message) => ({ ...message, metadata: { ...(message.metadata || {}) } })),
      }
    },
    streamTaskEvents: async (taskId, afterSeq, streamOptions = {}) => {
      streamOptions.onEvent?.({ seq: 5, type: 'content', content: 'answer' })
      streamOptions.onEvent?.({ seq: 6, type: 'done', final_answer: 'partial answer' })
    },
    refreshConversationTruthFallback: async (chatId, lastSeq, { chat, controller, store }) => {
      chat.lastTaskSeq = Number(lastSeq || 0)
      chat.messages = [
        { role: 'user', content: 'server question' },
        { role: 'assistant', content: 'partial answer', status: 'completed' },
      ]
      chat.activeTask = null
      controller.detachRecoverableTask(chatId)
      store.finishChatBusyRuntime(chatId)
      store.persistLocalState()
      return {
        detail: { active_task: null, messages: chat.messages },
        keepRecovering: false,
        cursor: {
          taskId: '',
          status: 'completed',
          lastSeq: Number(lastSeq || 0),
          recoverable: false,
          replayAvailable: true,
          terminal: true,
        },
      }
    },
  })

  await harness.controller.sendTaskMessage({
    requestedChatId: '42',
    message: 'hello controller',
    titleHint: 'hello controller',
    requestChatContext: { selected_ids: [] },
    requestAskMode: 'thinking',
  })

  assert.deepEqual(harness.apiCalls.streamTaskEvents, [['task_42', 4]])
  assert.equal(harness.chat.messages[1].content, 'partial answer')
})

test('recoverable task controller plain attach also resumes from the recovered local assistant cursor', async () => {
  const harness = createHarness({
    streamTaskEvents: async (_taskId, _afterSeq, streamOptions = {}) => {
      streamOptions.onEvent?.({ seq: 5, type: 'content', content: 'answer' })
      streamOptions.onEvent?.({ seq: 6, type: 'done', final_answer: 'partial answer' })
    },
    refreshConversationTruthFallback: async (chatId, lastSeq, { chat, controller, store }) => {
      chat.lastTaskSeq = Number(lastSeq || 0)
      chat.messages = [
        { role: 'user', content: 'server question' },
        { role: 'assistant', content: 'partial answer', status: 'completed' },
      ]
      chat.activeTask = null
      controller.detachRecoverableTask(chatId)
      store.finishChatBusyRuntime(chatId)
      store.persistLocalState()
      return {
        detail: { active_task: null, messages: chat.messages },
        keepRecovering: false,
        cursor: {
          taskId: '',
          status: 'completed',
          lastSeq: Number(lastSeq || 0),
          recoverable: false,
          replayAvailable: true,
          terminal: true,
        },
      }
    },
  })

  harness.chat.synced = false
  harness.chat.messages = [
    { role: 'user', content: 'server question' },
    {
      role: 'assistant',
      content: 'partial ',
      status: 'running',
      metadata: {
        task_id: 'task_attach_local',
        last_seq: 4,
      },
    },
  ]
  harness.chat.activeTask = {
    task_id: 'task_attach_local',
    status: 'running',
    last_seq: 1,
    replay_available: true,
  }
  harness.chat.lastTaskSeq = 1

  await harness.controller.attachRecoverableTask({
    chatId: '42',
    taskSummary: harness.chat.activeTask,
  })

  assert.deepEqual(harness.apiCalls.streamTaskEvents, [['task_attach_local', 4]])
  assert.equal(harness.chat.messages[1].content, 'partial answer')
})

test('recoverable task controller does not attach stale task replay when server truth already cleared active_task', async () => {
  const harness = createHarness({
    refreshConversationTruth: async (_chatId, { chat: target }) => {
      target.messages = [
        { role: 'user', content: 'server question' },
        {
          role: 'assistant',
          content: 'final answer',
          status: 'completed',
          metadata: {
            task_id: 'task_stale_server',
            terminal_status: 'completed',
            last_seq: 9,
          },
        },
      ]
      target.activeTask = null
      target.lastTaskSeq = 9
      return {
        active_task: null,
        messages: target.messages.map((message) => ({ ...message, metadata: { ...(message.metadata || {}) } })),
      }
    },
  })

  harness.chat.activeTask = {
    task_id: 'task_stale_server',
    status: 'running',
    last_seq: 3,
    replay_available: true,
  }
  harness.chat.lastTaskSeq = 3

  await harness.controller.attachRecoverableTask({
    chatId: '42',
    taskSummary: harness.chat.activeTask,
    replaceMessagesFromServer: true,
  })

  assert.deepEqual(harness.apiCalls.streamTaskEvents, [])
  assert.equal(harness.chat.activeTask, null)
  assert.equal(harness.chat.lastTaskSeq, 9)
})

test('recoverable task controller plain attach refreshes synced truth before replay and skips stale task events', async () => {
  let refreshCount = 0
  const harness = createHarness({
    refreshConversationTruth: async (_chatId, { chat: target }) => {
      refreshCount += 1
      target.messages = [
        { role: 'user', content: 'server question' },
        {
          role: 'assistant',
          content: 'final answer',
          status: 'completed',
          metadata: {
            task_id: 'task_stale_plain',
            terminal_status: 'completed',
            last_seq: 11,
          },
        },
      ]
      target.activeTask = null
      target.lastTaskSeq = 11
      return {
        active_task: null,
        messages: target.messages.map((message) => ({ ...message, metadata: { ...(message.metadata || {}) } })),
      }
    },
  })

  harness.chat.activeTask = {
    task_id: 'task_stale_plain',
    status: 'running',
    last_seq: 3,
    replay_available: true,
  }
  harness.chat.lastTaskSeq = 3

  await harness.controller.attachRecoverableTask({
    chatId: '42',
    taskSummary: harness.chat.activeTask,
  })

  assert.equal(refreshCount, 1)
  assert.deepEqual(harness.apiCalls.streamTaskEvents, [])
  assert.equal(harness.chat.activeTask, null)
  assert.equal(harness.chat.lastTaskSeq, 11)
})
