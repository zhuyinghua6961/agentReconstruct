import { advanceTaskReplayCursor, normalizeTaskReplayCursor, shouldFallBackToConversationTruth } from './taskReplayCursor.js'
import { beginTaskAttach, deriveRecoveredReplayCursor, endTaskAttach, shouldClearRecoveredActiveTask } from './taskRecoveryRuntime.js'
import { summarizeTaskEventBatch, summarizeTaskRecoveryDetail } from './taskRecoveryDebug.js'

function noop() {}

function defaultNormalizeChatId(chatId) {
  return String(chatId || '').trim()
}

export function createRecoverableTaskController(deps = {}) {
  const api = deps.api
  const store = deps.store
  const normalizeChatId = deps.normalizeChatId || defaultNormalizeChatId
  const getChatById = deps.getChatById || (() => null)
  const getCurrentChatId = deps.getCurrentChatId || (() => store?.currentChatId)
  const taskAttachInFlightByChatId = deps.taskAttachInFlightByChatId instanceof Map
    ? deps.taskAttachInFlightByChatId
    : new Map()
  const getStreamRuntime = deps.getStreamRuntime || (() => null)
  const createStreamRuntime = deps.createStreamRuntime || (() => null)
  const clearStreamRuntime = deps.clearStreamRuntime || noop
  const refreshConversationTruth = deps.refreshConversationTruth || (async () => null)
  const refreshConversationTruthFallback = deps.refreshConversationTruthFallback || (async () => ({
    detail: null,
    keepRecovering: false,
    cursor: normalizeTaskReplayCursor({}),
  }))
  const applyGatewayEvent = deps.applyGatewayEvent || noop
  const sleepWithSignal = deps.sleepWithSignal || (async () => {})
  const clearInput = deps.clearInput || noop
  const scrollToBottom = deps.scrollToBottom || noop
  const onError = deps.onError || ((scope, error) => console.error(`[${scope}]`, error))
  const debugLog = typeof deps.debugLog === 'function' ? deps.debugLog : noop

  function settleDetachedRecoveryFailure(chatId, cursorHint = null) {
    const targetChatId = normalizeChatId(chatId)
    if (!targetChatId) return
    clearStreamRuntime(targetChatId)

    const existingTask = getChatById(targetChatId)?.activeTask
    const cursor = cursorHint?.taskId
      ? normalizeTaskReplayCursor(
          {
            ...(existingTask && typeof existingTask === 'object' ? existingTask : {}),
            task_id: cursorHint.taskId,
            status: cursorHint.status,
            last_seq: cursorHint.lastSeq,
            replay_available: cursorHint.replayAvailable,
          },
          store.getChatLastTaskSeq(targetChatId),
        )
      : normalizeTaskReplayCursor(existingTask, store.getChatLastTaskSeq(targetChatId))

    if (cursor.recoverable) {
      store.setChatActiveTask(
        targetChatId,
        {
          ...(existingTask && typeof existingTask === 'object' ? existingTask : {}),
          task_id: cursor.taskId,
          status: cursor.status,
          last_seq: cursor.lastSeq,
          replay_available: cursor.replayAvailable,
        },
        { persist: false },
      )
      store.updateChatTaskReplayCursor(targetChatId, cursor.lastSeq, { persist: false, touch: false })
      store.persistLocalState()
      return
    }

    store.finishChatBusyRuntime(targetChatId)
    store.clearChatActiveTask(targetChatId, { persist: false, touch: false })
    store.persistLocalState()
  }

  function recoverReplayCursorFromDetail(chatId, detail, activeCursor, options = {}) {
    const recovered = deriveRecoveredReplayCursor(
      detail,
      store.getChatLastTaskSeq(chatId),
      activeCursor?.taskId,
    )
    const shouldClear = options?.allowClear !== false
      && shouldClearRecoveredActiveTask(detail, recovered.lastSeq)
    if (shouldClear) {
      return normalizeTaskReplayCursor({}, recovered.lastSeq)
    }

    if (Number(recovered.lastSeq || 0) > store.getChatLastTaskSeq(chatId)) {
      store.updateChatTaskReplayCursor(chatId, recovered.lastSeq, { persist: false, touch: false })
    }
    return normalizeTaskReplayCursor(
      getChatById(chatId)?.activeTask || {
        task_id: recovered.taskId || activeCursor?.taskId || '',
        status: activeCursor?.status,
        last_seq: recovered.lastSeq,
        replay_available: activeCursor?.replayAvailable !== false,
      },
      recovered.lastSeq,
    )
  }

  function recoverReplayCursorFromChatState(chatId, activeCursor) {
    const chat = getChatById(chatId)
    return recoverReplayCursorFromDetail(
      chatId,
      {
        active_task: chat?.activeTask || null,
        messages: Array.isArray(chat?.messages) ? chat.messages : [],
      },
      activeCursor,
      { allowClear: false },
    )
  }

  async function attachRecoverableTask({ chatId, taskSummary, replaceMessagesFromServer = false }) {
    const targetChatId = normalizeChatId(chatId)
    if (!targetChatId) return

    let activeCursor = normalizeTaskReplayCursor(taskSummary, store.getChatLastTaskSeq(targetChatId))
    if (!activeCursor.taskId) return
    const existingRuntime = getStreamRuntime(targetChatId)
    if (existingRuntime?.mode === 'task' && existingRuntime?.requestId === activeCursor.taskId && !replaceMessagesFromServer) {
      debugLog('attach:skip-active-runtime', {
        chatId: targetChatId,
        taskId: activeCursor.taskId,
        localLastSeq: store.getChatLastTaskSeq(targetChatId),
      })
      return
    }
    const lockedTaskId = activeCursor.taskId
    if (!beginTaskAttach(taskAttachInFlightByChatId, {
      chatId: targetChatId,
      taskId: lockedTaskId,
      replaceMessagesFromServer,
    })) {
      debugLog('attach:skip-locked', {
        chatId: targetChatId,
        taskId: lockedTaskId,
        localLastSeq: store.getChatLastTaskSeq(targetChatId),
      })
      return
    }
    debugLog('attach:start', {
      chatId: targetChatId,
      replaceMessagesFromServer: Boolean(replaceMessagesFromServer),
      requestedTask: {
        taskId: activeCursor.taskId,
        status: activeCursor.status,
        lastSeq: activeCursor.lastSeq,
        recoverable: activeCursor.recoverable,
      },
      localLastSeq: store.getChatLastTaskSeq(targetChatId),
    })

    try {
      store.setChatActiveTask(targetChatId, taskSummary, { persist: false })
      if (existingRuntime?.mode === 'task' && existingRuntime?.requestId === activeCursor.taskId && !replaceMessagesFromServer) {
        return
      }
      const shouldValidateRecoveredTaskFromServer = Boolean(
        !replaceMessagesFromServer
        && activeCursor.recoverable
        && !existingRuntime
        && getChatById(targetChatId)?.synced,
      )

      const previousRuntime = getStreamRuntime(targetChatId)
      previousRuntime?.abortController?.abort()
      clearStreamRuntime(targetChatId)

      const runtime = createStreamRuntime(targetChatId, activeCursor.taskId, -1, {
        strictRequestMatch: false,
        mode: 'task',
      })
      if (!runtime) return

      activeCursor = recoverReplayCursorFromChatState(targetChatId, activeCursor)
      debugLog('attach:after-local-recovery', {
        chatId: targetChatId,
        taskId: activeCursor.taskId,
        status: activeCursor.status,
        lastSeq: activeCursor.lastSeq,
        recoverable: activeCursor.recoverable,
      })
      if (!activeCursor.taskId) {
        settleDetachedRecoveryFailure(targetChatId, activeCursor)
        return
      }

      if (shouldValidateRecoveredTaskFromServer) {
        try {
          const detail = await refreshConversationTruth(targetChatId)
          debugLog('attach:plain-sync-detail', {
            chatId: targetChatId,
            taskId: activeCursor.taskId,
            detail: summarizeTaskRecoveryDetail(detail, activeCursor.taskId),
          })
          const refreshedCursor = recoverReplayCursorFromDetail(targetChatId, detail, activeCursor)
          if (refreshedCursor.taskId) {
            activeCursor = refreshedCursor
          } else {
            settleDetachedRecoveryFailure(targetChatId, refreshedCursor)
            return
          }
        } catch (error) {
          onError('task-recovery plain attach sync failed', error)
        }
      }

      if (replaceMessagesFromServer) {
        try {
          const detail = await refreshConversationTruth(targetChatId)
          debugLog('attach:replace-sync-detail', {
            chatId: targetChatId,
            taskId: activeCursor.taskId,
            detail: summarizeTaskRecoveryDetail(detail, activeCursor.taskId),
          })
          const refreshedCursor = recoverReplayCursorFromDetail(targetChatId, detail, activeCursor)
          if (refreshedCursor.taskId) {
            activeCursor = refreshedCursor
          } else {
            settleDetachedRecoveryFailure(targetChatId, refreshedCursor)
            return
          }
        } catch (error) {
          onError('task-recovery initial sync failed', error)
        }
      }

      if (shouldFallBackToConversationTruth(activeCursor)) {
        try {
          const fallback = await refreshConversationTruthFallback(targetChatId, activeCursor.lastSeq)
          debugLog('attach:fallback-before-stream', {
            chatId: targetChatId,
            taskId: activeCursor.taskId,
            keepRecovering: Boolean(fallback?.keepRecovering),
            cursor: fallback?.cursor || null,
            detail: summarizeTaskRecoveryDetail(fallback?.detail, activeCursor.taskId),
          })
          if (!fallback.keepRecovering) {
            return
          }
          activeCursor = recoverReplayCursorFromDetail(targetChatId, fallback.detail, fallback.cursor)
        } catch (error) {
          onError('task-recovery fallback refresh failed', error)
          settleDetachedRecoveryFailure(targetChatId, activeCursor)
          return
        }
      }

      while (!runtime.abortController.signal.aborted) {
        try {
          const afterSeq = store.getChatLastTaskSeq(targetChatId)
          let sawEvent = false
          let terminalCursor = null
          const batchEvents = []
          debugLog('attach:stream-open', {
            chatId: targetChatId,
            taskId: activeCursor.taskId,
            afterSeq,
            activeCursor,
          })
          await api.streamTaskEvents(activeCursor.taskId, afterSeq, {
            signal: runtime.abortController.signal,
            onEvent: (event) => {
              sawEvent = true
              batchEvents.push(event)
              applyGatewayEvent(targetChatId, event, runtime)
              const nextCursor = advanceTaskReplayCursor(
                {
                  taskId: activeCursor.taskId,
                  status: getChatById(targetChatId)?.activeTask?.status || activeCursor.status,
                  lastSeq: store.getChatLastTaskSeq(targetChatId),
                  replayAvailable: true,
                },
                [event],
              )
              store.updateChatTaskReplayCursor(targetChatId, nextCursor.lastSeq, { persist: false, touch: false })
              store.persistLocalState()
              activeCursor = normalizeTaskReplayCursor(
                getChatById(targetChatId)?.activeTask || {
                  task_id: activeCursor.taskId,
                  status: nextCursor.status,
                  last_seq: nextCursor.lastSeq,
                  replay_available: true,
                },
                nextCursor.lastSeq,
              )
              if (nextCursor.terminal) {
                terminalCursor = nextCursor
              }
            },
          })
          debugLog('attach:stream-batch', {
            chatId: targetChatId,
            taskId: activeCursor.taskId,
            afterSeq,
            batch: summarizeTaskEventBatch(batchEvents),
            localLastSeq: store.getChatLastTaskSeq(targetChatId),
            terminalCursor,
          })
          if (terminalCursor) {
            const fallback = await refreshConversationTruthFallback(targetChatId, terminalCursor.lastSeq)
            debugLog('attach:terminal-fallback', {
              chatId: targetChatId,
              taskId: activeCursor.taskId,
              keepRecovering: Boolean(fallback?.keepRecovering),
              cursor: fallback?.cursor || null,
              detail: summarizeTaskRecoveryDetail(fallback?.detail, activeCursor.taskId),
            })
            if (!fallback.keepRecovering) {
              return
            }
            activeCursor = recoverReplayCursorFromDetail(targetChatId, fallback.detail, fallback.cursor)
            await sleepWithSignal(120, runtime.abortController.signal)
            continue
          }
          if (sawEvent) {
            activeCursor = normalizeTaskReplayCursor(
              getChatById(targetChatId)?.activeTask || {
                task_id: activeCursor.taskId,
                status: activeCursor.status,
                last_seq: store.getChatLastTaskSeq(targetChatId),
                replay_available: true,
              },
              store.getChatLastTaskSeq(targetChatId),
            )
            await sleepWithSignal(120, runtime.abortController.signal)
            continue
          }

          const taskDetail = await api.getTask(activeCursor.taskId)
          const latestCursor = normalizeTaskReplayCursor(taskDetail, store.getChatLastTaskSeq(targetChatId))
          debugLog('attach:get-task', {
            chatId: targetChatId,
            taskId: activeCursor.taskId,
            summary: {
              status: latestCursor.status,
              lastSeq: latestCursor.lastSeq,
              terminal: latestCursor.terminal,
              replayAvailable: latestCursor.replayAvailable,
            },
          })
          if (latestCursor.taskId) {
            store.setChatActiveTask(targetChatId, taskDetail, { persist: false })
          }
          if (shouldFallBackToConversationTruth(latestCursor)) {
            const fallback = await refreshConversationTruthFallback(targetChatId, latestCursor.lastSeq)
            debugLog('attach:post-task-fallback', {
              chatId: targetChatId,
              taskId: activeCursor.taskId,
              keepRecovering: Boolean(fallback?.keepRecovering),
              cursor: fallback?.cursor || null,
              detail: summarizeTaskRecoveryDetail(fallback?.detail, activeCursor.taskId),
            })
            if (!fallback.keepRecovering) {
              return
            }
            activeCursor = recoverReplayCursorFromDetail(targetChatId, fallback.detail, fallback.cursor)
            await sleepWithSignal(800, runtime.abortController.signal)
            continue
          }
          activeCursor = latestCursor
          await sleepWithSignal(800, runtime.abortController.signal)
        } catch (error) {
          if (runtime.abortController.signal.aborted) {
            return
          }
          debugLog('attach:stream-error', {
            chatId: targetChatId,
            taskId: activeCursor.taskId,
            localLastSeq: store.getChatLastTaskSeq(targetChatId),
            error: {
              message: String(error?.message || error || ''),
              status: Number(error?.status || 0) || 0,
              code: String(error?.code || ''),
            },
          })
          onError('task-recovery replay failed', error)
          try {
            const fallback = await refreshConversationTruthFallback(targetChatId, store.getChatLastTaskSeq(targetChatId))
            debugLog('attach:error-fallback', {
              chatId: targetChatId,
              taskId: activeCursor.taskId,
              keepRecovering: Boolean(fallback?.keepRecovering),
              cursor: fallback?.cursor || null,
              detail: summarizeTaskRecoveryDetail(fallback?.detail, activeCursor.taskId),
            })
            if (!fallback.keepRecovering) {
              return
            }
            activeCursor = recoverReplayCursorFromDetail(targetChatId, fallback.detail, fallback.cursor)
            await sleepWithSignal(800, runtime.abortController.signal)
          } catch (fallbackError) {
            onError('task-recovery replay fallback failed', fallbackError)
            settleDetachedRecoveryFailure(targetChatId, activeCursor)
            return
          }
        }
      }
    } finally {
      endTaskAttach(taskAttachInFlightByChatId, {
        chatId: targetChatId,
        taskId: lockedTaskId,
      })
    }
  }

  async function cancelRecoverableTask(chatId, taskId) {
    const targetChatId = normalizeChatId(chatId)
    if (!targetChatId) return
    try {
      const summary = await api.cancelTask(taskId)
      await attachRecoverableTask({ chatId: targetChatId, taskSummary: summary })
    } catch (error) {
      onError('task-recovery cancel failed', error)
    } finally {
      store.finishChatBusyRuntime(targetChatId)
    }
  }

  async function sendTaskMessage({
    requestedChatId,
    message,
    titleHint,
    chatHistory = null,
    conversationId = null,
    requestChatContext = null,
    requestAskMode = 'thinking',
  }) {
    const targetRequestedChatId = normalizeChatId(requestedChatId)
    if (!targetRequestedChatId || !String(message || '').trim()) {
      return { ok: false, reason: 'invalid_request' }
    }

    const busyStart = store.startChatBusyRuntime(targetRequestedChatId, { phase: 'dispatching' })
    if (!busyStart?.ok) {
      return { ok: false, reason: String(busyStart?.reason || 'busy_rejected') }
    }

    let streamChatId = targetRequestedChatId
    try {
      const promotedChat = await store.ensureChatConversation(targetRequestedChatId, titleHint)
      streamChatId = normalizeChatId(promotedChat?.id || targetRequestedChatId)
      clearInput()
      if (normalizeChatId(getCurrentChatId()) === streamChatId) {
        scrollToBottom({ force: true })
      }

      const targetChat = getChatById(streamChatId)
      const effectiveChatHistory = Array.isArray(chatHistory) && chatHistory.length > 0
        ? chatHistory
        : (targetChat?.messages || [])
          .filter((messageItem) => String(messageItem?.content || '').trim().length > 0)
          .slice(-10)
          .map((messageItem) => ({ role: messageItem.role, content: messageItem.content }))
      const effectiveConversationId = conversationId ?? (targetChat?.synced ? parseInt(targetChat.id, 10) : null)

      const taskSummary = await api.createTask(
        message,
        effectiveChatHistory,
        effectiveConversationId,
        requestChatContext,
        requestAskMode,
      )
      store.finishChatBusyRuntime(streamChatId)
      await attachRecoverableTask({
        chatId: streamChatId,
        taskSummary,
        replaceMessagesFromServer: true,
      })
      return { ok: true, taskId: String(taskSummary?.task_id || '') }
    } catch (error) {
      store.finishChatBusyRuntime(streamChatId)
      return {
        ok: false,
        reason: 'task_create_failed',
        error,
      }
    }
  }

  function detachRecoverableTask(chatId) {
    const targetChatId = normalizeChatId(chatId)
    if (!targetChatId) return
    const runtime = getStreamRuntime(targetChatId)
    runtime?.abortController?.abort()
    clearStreamRuntime(targetChatId)
  }

  function detachAllRecoverableTasks() {
    if (typeof deps.listRuntimeChatIds === 'function') {
      deps.listRuntimeChatIds().forEach((chatId) => {
        detachRecoverableTask(chatId)
      })
    } else if (deps.streamRuntimeByChatId instanceof Map) {
      Array.from(deps.streamRuntimeByChatId.keys()).forEach((chatId) => {
        detachRecoverableTask(chatId)
      })
    }
    taskAttachInFlightByChatId.clear()
  }

  return {
    attachRecoverableTask,
    cancelRecoverableTask,
    sendTaskMessage,
    detachRecoverableTask,
    detachAllRecoverableTasks,
  }
}
