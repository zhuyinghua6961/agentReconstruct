import { normalizeTaskReplayCursor } from './taskReplayCursor.js'

function normalizeChatId(chatId) {
  return String(chatId || '').trim()
}

function normalizeTaskId(taskId) {
  return String(taskId || '').trim()
}

export function beginTaskAttach(lockMap, { chatId, taskId, replaceMessagesFromServer = false } = {}) {
  const normalizedChatId = normalizeChatId(chatId)
  const normalizedTaskId = normalizeTaskId(taskId)
  if (!normalizedChatId || !normalizedTaskId || !(lockMap instanceof Map)) {
    return false
  }

  const current = lockMap.get(normalizedChatId)
  if (current && current.taskId === normalizedTaskId) {
    return false
  }

  lockMap.set(normalizedChatId, {
    taskId: normalizedTaskId,
    replaceMessagesFromServer: Boolean(replaceMessagesFromServer),
  })
  return true
}

export function endTaskAttach(lockMap, { chatId, taskId } = {}) {
  const normalizedChatId = normalizeChatId(chatId)
  const normalizedTaskId = normalizeTaskId(taskId)
  if (!normalizedChatId || !(lockMap instanceof Map)) {
    return
  }
  const current = lockMap.get(normalizedChatId)
  if (!current) return
  if (normalizedTaskId && current.taskId !== normalizedTaskId) return
  lockMap.delete(normalizedChatId)
}

export function consumePendingStreamContent({ existingContent = '', pendingContent = '', targetFound = false } = {}) {
  const currentContent = String(existingContent || '')
  const nextPending = String(pendingContent || '')
  if (!nextPending) {
    return {
      nextContent: currentContent,
      remainingPending: '',
    }
  }
  if (!targetFound) {
    return {
      nextContent: currentContent,
      remainingPending: nextPending,
    }
  }
  return {
    nextContent: currentContent + nextPending,
    remainingPending: '',
  }
}

export function deriveRecoveredReplayCursor(detail = {}, cachedLastSeq = 0, taskId = '') {
  const activeCursor = normalizeTaskReplayCursor(detail?.active_task, cachedLastSeq)
  const lookupTaskId = String(activeCursor.taskId || taskId || '').trim()
  let lastSeq = activeCursor.lastSeq
  const messages = Array.isArray(detail?.messages) ? detail.messages : []

  messages.forEach((message) => {
    if (!message || typeof message !== 'object') return
    const role = String(message.role || '').trim().toLowerCase()
    if (!(role === 'assistant' || role === 'bot')) return
    const metadata = message.metadata && typeof message.metadata === 'object' ? message.metadata : {}
    const messageTaskId = String(metadata.task_id || message.task_id || '').trim()
    if (lookupTaskId && messageTaskId && messageTaskId !== lookupTaskId) return
    const messageLastSeq = Number(message.last_seq ?? metadata.last_seq ?? 0)
    if (!Number.isFinite(messageLastSeq) || messageLastSeq <= 0) return
    lastSeq = Math.max(lastSeq, Math.trunc(messageLastSeq))
  })

  return {
    taskId: lookupTaskId,
    lastSeq,
  }
}

export function shouldClearRecoveredActiveTask(detail = {}, cachedLastSeq = 0) {
  const cursor = normalizeTaskReplayCursor(detail?.active_task, cachedLastSeq)
  if (!cursor.recoverable) {
    return true
  }

  const lookupTaskId = String(cursor.taskId || '').trim()
  const minLastSeq = Number(cursor.lastSeq || 0) || 0
  const messages = Array.isArray(detail?.messages) ? detail.messages : []

  return messages.some((message) => {
    if (!message || typeof message !== 'object') return false
    const role = String(message.role || '').trim().toLowerCase()
    if (!(role === 'assistant' || role === 'bot')) return false

    const metadata = message.metadata && typeof message.metadata === 'object' ? message.metadata : {}
    const messageTaskId = String(metadata.task_id || message.task_id || '').trim()
    if (lookupTaskId && messageTaskId !== lookupTaskId) return false

    const terminalStatus = String(
      message.terminalStatus
      ?? message.terminal_status
      ?? message.status
      ?? metadata.terminal_status
      ?? metadata.status
      ?? ''
    ).trim().toLowerCase()
    const doneSeen = message.doneSeen ?? message.done_seen ?? metadata.done_seen
    const messageLastSeq = Number(message.last_seq ?? metadata.last_seq ?? 0) || 0

    const isTerminal = ['done', 'completed', 'failed', 'canceled', 'cancelled', 'expired'].includes(terminalStatus)
    if (!isTerminal && doneSeen !== true) return false
    if (messageLastSeq > 0 && messageLastSeq < minLastSeq) return false
    return true
  })
}
